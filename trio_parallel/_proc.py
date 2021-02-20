import os
import platform
import struct
from itertools import count
from multiprocessing import get_context

from multiprocessing.reduction import ForkingPickler
from threading import BrokenBarrierError

import trio

from ._util import BrokenWorkerError

# How long a process will idle waiting for new work before gives up and exits.
# This should be longer than a thread timeout proportionately to startup time.
IDLE_TIMEOUT = 60 * 10

_proc_counter = count()


class WorkerProcBase:
    def __init__(self, mp_context=get_context("spawn")):
        # It is almost possible to synchronize on the pipe alone but on Pypy
        # the _send_pipe doesn't raise the correct error on death. Anyway,
        # this Barrier strategy is more obvious to understand.
        self._barrier = mp_context.Barrier(2)
        child_recv_pipe, self._send_pipe = mp_context.Pipe(duplex=False)
        self._recv_pipe, child_send_pipe = mp_context.Pipe(duplex=False)
        self._proc = mp_context.Process(
            target=self._work,
            args=(self._barrier, child_recv_pipe, child_send_pipe),
            name=f"trio-parallel worker process {next(_proc_counter)}",
            daemon=True,
        )
        # keep our own state flag for quick checks
        self._started = False
        self._rehabilitate_pipes()

    @staticmethod
    def _work(barrier, recv_pipe, send_pipe):  # pragma: no cover

        import inspect
        import outcome

        def coroutine_checker(fn, args):
            ret = fn(*args)
            if inspect.iscoroutine(ret):
                # Manually close coroutine to avoid RuntimeWarnings
                ret.close()
                raise TypeError(
                    "trio-parallel worker expected a sync function, but {!r} appears "
                    "to be asynchronous".format(getattr(fn, "__qualname__", fn))
                )

            return ret

        while True:
            try:
                # Return value is party #, not whether awoken within timeout
                barrier.wait(timeout=IDLE_TIMEOUT)
            except BrokenBarrierError:
                # Timeout waiting for job, so we can exit.
                return
            # We got a job, and we are "woken"
            fn, args = recv_pipe.recv()
            result = outcome.capture(coroutine_checker, fn, args)
            # Tell the cache that we're done and available for a job
            # Unlike the thread cache, it's impossible to deliver the
            # result from the worker process. So shove it onto the queue
            # and hope the receiver delivers the result and marks us idle
            send_pipe.send(result)

            del fn
            del args
            del result

    async def run_sync(self, sync_fn, *args):
        # Neither this nor the child process should be waiting at this point
        assert not self._barrier.n_waiting, "Must first wake_up() the worker"
        try:
            await self._send(ForkingPickler.dumps((sync_fn, args)))
            result = ForkingPickler.loads(await self._recv())
        except trio.EndOfChannel:
            # Likely the worker died while we were waiting on a pipe
            self.kill()  # NOTE: must reap zombie child elsewhere
            raise BrokenWorkerError(f"{self._proc} died unexpectedly")
        except BaseException:
            # Cancellation or other unknown errors leave the process in an
            # unknown state, so there is no choice but to kill.
            self.kill()  # NOTE: must reap zombie child elsewhere
            raise
        else:
            return result.unwrap()

    def is_alive(self):
        # if the proc is alive, there is a race condition where it could be
        # dying, but the the barrier should be broken at that time. This
        # call reaps zombie children on Unix.
        return self._proc.is_alive()

    def wake_up(self, timeout=None):
        if not self._started:
            self._proc.start()
            self._started = True
        try:
            self._barrier.wait(timeout)
        except BrokenBarrierError:
            # raise our own flavor of exception and reap child
            if self._proc.is_alive():  # pragma: no cover - rare race condition
                self.kill()
                self._proc.join()  # this will block for ms, but it should be rare
            raise BrokenWorkerError(f"{self._proc} died unexpectedly") from None

    def kill(self):
        self._barrier.abort()
        try:
            self._proc.kill()
        except AttributeError:
            self._proc.terminate()

    def _rehabilitate_pipes(self):  # pragma: no cover
        pass

    async def _recv(self):  # pragma: no cover
        pass

    async def _send(self, buf):  # pragma: no cover
        pass

    async def wait(self):  # pragma: no cover
        pass


class WindowsWorkerProc(WorkerProcBase):
    async def wait(self):
        await trio.lowlevel.WaitForSingleObject(self._proc.sentinel)
        return self._proc.exitcode

    def _rehabilitate_pipes(self):
        # These must be created in an async context
        from ._windows_pipes import PipeSendChannel, PipeReceiveChannel

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
        else:  # pragma: no cover
            pass


class PosixWorkerProc(WorkerProcBase):
    async def wait(self):
        await trio.lowlevel.wait_readable(self._proc.sentinel)
        return self._proc.exitcode

    def _rehabilitate_pipes(self):
        # These must be created in an async context
        self._send_stream = trio.lowlevel.FdStream(self._send_pipe.fileno())
        self._recv_stream = trio.lowlevel.FdStream(self._recv_pipe.fileno())

    async def _recv(self):
        buf = await self._recv_exactly(4)
        (size,) = struct.unpack("!i", buf)
        if size == -1:  # pragma: no cover # can't go this big on CI
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
        if n > 0x7FFFFFFF:  # pragma: no cover # can't go this big on CI
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
        else:  # pragma: no cover
            pass


class PypyWorkerProc(WorkerProcBase):
    async def run_sync(self, sync_fn, *args):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(self.child_monitor)
            result = await super().run_sync(sync_fn, *args)
            nursery.cancel_scope.cancel()
        return result

    async def child_monitor(self):
        # If this worker dies, raise a catchable error...
        await self.wait()
        # but not if another error or cancel is incoming, those take priority!
        await trio.lowlevel.checkpoint_if_cancelled()
        raise BrokenWorkerError(f"{self._proc} died unexpectedly")


if os.name == "nt":

    class WorkerProc(WindowsWorkerProc):
        pass


elif platform.python_implementation() == "PyPy":

    class WorkerProc(PypyWorkerProc, PosixWorkerProc):
        pass


else:

    class WorkerProc(PosixWorkerProc):
        pass