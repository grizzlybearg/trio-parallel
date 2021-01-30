import os
import struct
import trio
from collections import deque
from itertools import count
from multiprocessing import Pipe, Process
from multiprocessing.reduction import ForkingPickler

_limiter_local = trio.lowlevel.RunVar("proc_limiter")

# How long a process will idle waiting for new work before gives up and exits.
# This should be longer than a thread timeout proportionately to startup time.
IDLE_TIMEOUT = 60 * 10

# Sane default might be to expect cpu-bound work
DEFAULT_LIMIT = os.cpu_count()
_proc_counter = count()

if os.name == "nt":
    # NOTE: This uses a thread per-process. Can we do better?
    wait_sentinel = trio.lowlevel.WaitForSingleObject
else:
    wait_sentinel = trio.lowlevel.wait_readable


class BrokenWorkerError(RuntimeError):
    """Raised when a worker process fails or dies unexpectedly.

    This error is not typically encountered in normal use, and indicates a severe
    failure of either Trio or the code that was executing in the worker.
    """

    pass


def current_default_process_limiter():
    """Get the default `~trio.CapacityLimiter` used by
    `trio.to_process.run_sync`.

    The most common reason to call this would be if you want to modify its
    :attr:`~trio.CapacityLimiter.total_tokens` attribute. This attribute
    is initialized to the number of CPUs reported by :func:`os.cpu_count`.

    """
    try:
        limiter = _limiter_local.get()
    except LookupError:
        limiter = trio.CapacityLimiter(DEFAULT_LIMIT)
        _limiter_local.set(limiter)
    return limiter


class ProcCache:
    def __init__(self):
        # The cache is a deque rather than dict here since processes can't remove
        # themselves anyways, so we don't need O(1) lookups
        self._cache = deque()
        # NOTE: avoid thread races between Trio runs by only interacting with
        # self._cache via thread-atomic actions like append, pop, del

    def prune(self):
        # take advantage of the oldest proc being on the left to
        # keep iteration O(dead workers)
        try:
            while True:
                proc = self._cache.popleft()
                if proc.is_alive():
                    self._cache.appendleft(proc)
                    return
        except IndexError:
            # Thread safety: it's necessary to end the iteration using this error
            # when the cache is empty, as opposed to `while self._cache`.
            pass

    def push(self, proc):
        self._cache.append(proc)

    def pop(self):
        """Get live worker process or raise IndexError"""
        while True:
            proc = self._cache.pop()
            if proc.is_alive():
                return proc

    def __len__(self):
        return len(self._cache)


PROC_CACHE = ProcCache()


class WorkerProc:
    def __init__(self):
        child_recv_pipe, self._send_pipe = Pipe(duplex=False)
        self._recv_pipe, child_send_pipe = Pipe(duplex=False)
        self._proc = Process(
            target=self._work,
            args=(child_recv_pipe, child_send_pipe),
            name=f"Trio worker process {next(_proc_counter)}",
            daemon=True,
        )
        # The following initialization methods may take a long time
        self._proc.start()

    @staticmethod
    def _work(recv_pipe, send_pipe):  # pragma: no cover

        import inspect
        import outcome

        def worker_fn():
            ret = fn(*args)
            if inspect.iscoroutine(ret):
                # Manually close coroutine to avoid RuntimeWarnings
                ret.close()
                raise TypeError(
                    "Trio expected a sync function, but {!r} appears to be "
                    "asynchronous".format(getattr(fn, "__qualname__", fn))
                )

            return ret

        try:
            while recv_pipe.poll(timeout=IDLE_TIMEOUT):
                fn, args = recv_pipe.recv()
                result = outcome.capture(worker_fn)
                # Unlike the thread cache, it's impossible to deliver the
                # result from the worker process. So shove it onto the queue
                # and hope the receiver delivers the result and marks us idle
                send_pipe.send(result)

                del fn
                del args
                del result
        finally:
            recv_pipe.close()
            send_pipe.close()

    async def run_sync(self, sync_fn, *args):
        # Neither this nor the child process should be waiting at this point
        self._rehabilitate_pipes()
        async with trio.open_nursery() as nursery:
            # Monitor needed for pypy and other platforms that don't
            # promptly raise EndOfChannel
            nursery.start_soon(self._child_monitor)
            try:
                await self._send(ForkingPickler.dumps((sync_fn, args)))
                result = ForkingPickler.loads(await self._recv())
            except trio.EndOfChannel:
                # Likely the worker died while we were waiting on a pipe
                self.kill()  # Just make sure
                # sleep and let the monitor raise the appropriate error to avoid
                # creating any MultiErrors in this codepath
                await trio.sleep_forever()
            except BaseException:
                # Cancellation leaves the process in an unknown state, so
                # there is no choice but to kill, anyway it frees the pipe threads.
                # For other unknown errors, it's best to clean up similarly.
                self.kill()
                raise
            # Must cancel the _child_monitor task to escape the nursery
            nursery.cancel_scope.cancel()
        return result.unwrap()

    async def _child_monitor(self):
        # If this handle becomes ready, raise a catchable error...
        await wait_sentinel(self._proc.sentinel)
        # but not if another error or cancel is incoming, those take priority!
        await trio.lowlevel.checkpoint_if_cancelled()
        raise BrokenWorkerError(f"{self._proc} died unexpectedly")

    def is_alive(self):
        # Even if the proc is alive, there is a race condition where it could
        # be dying, use join to make sure if necessary.
        return self._proc.is_alive()

    def kill(self):
        try:
            self._proc.kill()
        except AttributeError:
            self._proc.terminate()

    def join(self, timeout=None):
        # Needed for some tests. We have to reach in deeply because
        # _proc.join() doesn't report whether the join was successful
        return self._proc._popen.wait(timeout) is not None

    if os.name == "nt":

        def _rehabilitate_pipes(self):
            # These must be created in an async context, so defer so
            # that this object can be instantiated in e.g. a thread
            if not hasattr(self, "_send_chan"):
                from ._winows_pipes import PipeSendChannel, PipeReceiveChannel
                self._send_chan = PipeSendChannel(self._send_pipe.fileno())
                self._recv_chan = PipeReceiveChannel(self._recv_pipe.fileno())
                self._send = self._send_chan.send
                self._recv = self._recv_chan.receive

        def __del__(self):
            # Avoid __del__ errors on cleanup: GH#174, GH#1767
            # multiprocessing will close them for us
            if hasattr(self, "_send_chan"):
                self._send_chan._handle_holder.handle = -1
                self._recv_chan._handle_holder.handle = -1

    else:

        def _rehabilitate_pipes(self):
            # These must be created in an async context, so defer so
            # that this object can be instantiated in e.g. a thread
            if not hasattr(self, "_send_stream"):
                self._send_stream = trio.lowlevel.FdStream(self._send_pipe.fileno())
                self._recv_stream = trio.lowlevel.FdStream(self._recv_pipe.fileno())

        async def _recv(self):
            buf = await self._recv_exactly(4)
            (size,) = struct.unpack("!i", buf)
            if size == -1:
                buf = await self._recv_exactly(8)
                (size,) = struct.unpack("!Q", buf)
            return await self._recv_exactly(size)

        async def _recv_exactly(self, size):
            result_bytes = bytearray()
            while size:
                partial_result = await self._recv_stream.receive_some(size)
                num_recvd = len(partial_result)
                if not num_recvd:
                    raise trio.EndOfChannel("got end of file during message")
                result_bytes.extend(partial_result)
                if num_recvd > size:  # pragma: no cover
                    raise RuntimeError("Oversized response")
                else:
                    size -= num_recvd
            return result_bytes

        async def _send(self, buf):
            n = len(buf)
            if n > 0x7FFFFFFF:
                pre_header = struct.pack("!i", -1)
                header = struct.pack("!Q", n)
                await self._send_stream.send_all(pre_header)
                await self._send_stream.send_all(header)
                await self._send_stream.send_all(buf)
            else:
                # For wire compatibility with 3.7 and lower
                header = struct.pack("!i", n)
                if n > 16384:
                    # The payload is large so Nagle's algorithm won't be triggered
                    # and we'd better avoid the cost of concatenation.
                    await self._send_stream.send_all(header)
                    await self._send_stream.send_all(buf)
                else:
                    # Issue #20540: concatenate before sending, to avoid delays due
                    # to Nagle's algorithm on a TCP socket.
                    # Also note we want to avoid sending a 0-length buffer separately,
                    # to avoid "broken pipe" errors if the other end closed the pipe.
                    await self._send_stream.send_all(header + buf)

        def __del__(self):
            # Avoid __del__ errors on cleanup: GH#174, GH#1767
            # multiprocessing will close them for us
            if hasattr(self, "_send_stream"):
                self._send_stream._fd_holder.fd = -1
                self._recv_stream._fd_holder.fd = -1


async def to_process_run_sync(sync_fn, *args, cancellable=False, limiter=None):
    """Run sync_fn in a separate process

    This is a wrapping of :class:`multiprocessing.Process` that follows the API of
    :func:`trio.to_thread.run_sync`. The intended use of this function is limited:

    - Circumvent the GIL to run CPU-bound functions in parallel
    - Make blocking APIs or infinite loops truly cancellable through
      SIGKILL/TerminateProcess without leaking resources
    - Protect the main process from untrusted/unstable code without leaks

    Other :mod:`multiprocessing` features may work but are not officially
    supported by Trio, and all the normal :mod:`multiprocessing` caveats apply.

    Args:
      sync_fn: An importable or pickleable synchronous callable. See the
          :mod:`multiprocessing` documentation for detailed explanation of
          limitations.
      *args: Positional arguments to pass to sync_fn. If you need keyword
          arguments, use :func:`functools.partial`.
      cancellable (bool): Whether to allow cancellation of this operation.
          Cancellation always involves abrupt termination of the worker process
          with SIGKILL/TerminateProcess.
      limiter (None, or CapacityLimiter):
          An object used to limit the number of simultaneous processes. Most
          commonly this will be a `~trio.CapacityLimiter`, but any async
          context manager will succeed.

    Returns:
      Whatever ``sync_fn(*args)`` returns.

    Raises:
      Exception: Whatever ``sync_fn(*args)`` raises.

    """
    if limiter is None:
        limiter = current_default_process_limiter()

    async with limiter:
        PROC_CACHE.prune()

        while True:
            try:
                proc = PROC_CACHE.pop()
            except IndexError:
                proc = await trio.to_thread.run_sync(WorkerProc)

            try:
                with trio.CancelScope(shield=not cancellable):
                    return await proc.run_sync(sync_fn, *args)
            except trio.BrokenResourceError:
                # Rare case where proc timed out even though it was still alive
                # as we popped it. Just retry.
                pass
            finally:
                if proc.is_alive():
                    PROC_CACHE.push(proc)