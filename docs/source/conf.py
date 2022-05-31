#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Documentation build configuration file, created by
# sphinx-quickstart on Sat Jan 21 19:11:14 2017.
#
# This file is execfile()d with the current directory set to its
# containing dir.
#
# Note that not all possible configuration values are present in this
# autogenerated file.
#
# All configuration values have a default; values that are commented out
# serve to show the default.

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
import os
import sys
# So autodoc can import our package
sys.path.insert(0, os.path.abspath('../..'))


# Warn about all references to unknown targets
nitpicky = True
# Except for these ones, which we expect to point to unknown targets:
nitpick_ignore = [
    # Format is ("sphinx reference type", "string"), e.g.:
    ("py:class", "CapacityLimiter-like object"),
    ("py:class", "bytes-like"),
    ("py:class", "None"),
    # Was removed but still shows up in changelog
    ("py:class", "trio.lowlevel.RunLocal"),
    # trio.abc is documented at random places scattered throughout the docs
    ("py:mod", "trio.abc"),
    ("py:class", "math.inf"),
    ("py:exc", "Anything else"),
    ("py:class", "async function"),
    ("py:class", "sync function"),
    # https://github.com/sphinx-doc/sphinx/issues/7722
    ("py:class", "SendType"),
    ("py:class", "ReceiveType"),
]
autodoc_inherit_docstrings = False
default_role = "obj"

# -- General configuration ------------------------------------------------

# If your documentation needs a minimal Sphinx version, state it here.
#
# needs_sphinx = '1.0'

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.intersphinx',
    'sphinx.ext.coverage',
    'sphinx.ext.napoleon',
    'sphinxcontrib_trio',
]

intersphinx_mapping = {
    "python": ('https://docs.python.org/3', None),
    "trio": ('https://trio.readthedocs.io/en/stable', None),
}

autodoc_member_order = "bysource"

# Add any paths that contain templates here, relative to this directory.
templates_path = []

# The suffix(es) of source filenames.
# You can specify multiple suffix as a list of string:
#
# source_suffix = ['.rst', '.md']
source_suffix = '.rst'

# The master toctree document.
master_doc = 'index'

# General information about the project.
project = 'trio-parallel'
copyright = 'Richard J. Sheridan'
author = 'Richard J. Sheridan'

# The version info for the project you're documenting, acts as replacement for
# |version| and |release|, also used in various other places throughout the
# built documents.
#
# The short X.Y version.
from importlib.metadata import version
version = version('trio-parallel')
# The full version, including alpha/beta/rc tags.
release = version

# https://docs.readthedocs.io/en/stable/builds.html#build-environment
if "READTHEDOCS" in os.environ:
    import glob
    if glob.glob("../../newsfragments/*.*.rst"):
        print("-- Found newsfragments; running towncrier --", flush=True)
        import subprocess
        subprocess.run(
            ["towncrier", "build", "--yes", "--version", version],
            cwd="../..",
            check=True,
        )

# html_favicon = "_static/favicon-32.png"
# html_logo = "../../logo/wordmark-transparent.svg"
# & down below in html_theme_options we set logo_only=True

# The language for content autogenerated by Sphinx. Refer to documentation
# for a list of supported languages.
#
# This is also used if you do content translation via gettext catalogs.
# Usually you set "language" from the command line for these cases.
language = 'en'

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This patterns also effect to html_static_path and html_extra_path
exclude_patterns = []

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = 'sphinx'

# The default language for :: blocks
highlight_language = 'python3'

# If true, `todo` and `todoList` produce output, else they produce nothing.
todo_include_todos = False

# Fold return type into the "Returns:" section, rather than making
# a separate "Return type:" section
napoleon_use_rtype = False

# This avoids a warning by the epub builder that it can't figure out
# the MIME type for our favicon.
# suppress_warnings = ["epub.unknown_project_files"]


# -- Options for HTML output ----------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
#html_theme = 'alabaster'

# We have to set this ourselves, not only because it's useful for local
# testing, but also because if we don't then RTD will throw away our
# html_theme_options.
import sphinx_rtd_theme
html_theme = 'sphinx_rtd_theme'
html_theme_path = [sphinx_rtd_theme.get_html_theme_path()]

# Theme options are theme-specific and customize the look and feel of a theme
# further.  For a list of options available for each theme, see the
# documentation.
#
html_theme_options = {
    # default is 2
    # show deeper nesting in the RTD theme's sidebar TOC
    # https://stackoverflow.com/questions/27669376/
    # I'm not 100% sure this actually does anything with our current
    # versions/settings...
    "navigation_depth": 4,
    "logo_only": True,
    'prev_next_buttons_location': 'both'
}

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ['_static']

html_css_files = [
    'custom.css',
]

# -- Options for HTMLHelp output ------------------------------------------

# Output file base name for HTML help builder.
htmlhelp_basename = 'trio-paralleldoc'


# -- Options for LaTeX output ---------------------------------------------

latex_elements = {
    # The paper size ('letterpaper' or 'a4paper').
    #
    # 'papersize': 'letterpaper',

    # The font size ('10pt', '11pt' or '12pt').
    #
    # 'pointsize': '10pt',

    # Additional stuff for the LaTeX preamble.
    #
    # 'preamble': '',

    # Latex figure (float) alignment
    #
    # 'figure_align': 'htbp',
}

# Grouping the document tree into LaTeX files. List of tuples
# (source start file, target name, title,
#  author, documentclass [howto, manual, or own class]).
latex_documents = [
    (master_doc, 'trio-parallel.tex', 'Trio Documentation',
     author, 'manual'),
]


# -- Options for manual page output ---------------------------------------

# One entry per manual page. List of tuples
# (source start file, name, description, authors, manual section).
man_pages = [
    (master_doc, 'trio-parallel', 'trio-parallel Documentation',
     [author], 1)
]


# -- Options for Texinfo output -------------------------------------------

# Grouping the document tree into Texinfo files. List of tuples
# (source start file, target name, title, author,
#  dir menu entry, description, category)
texinfo_documents = [
    (master_doc, 'trio-parallel', 'trio-parallel Documentation',
     author, 'trio-parallel', 'CPU parallelism for Trio',
     'Miscellaneous'),
]
