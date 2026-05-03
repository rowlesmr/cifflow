"""Shared utilities for the inspect package."""

import pathlib
from typing import IO, TextIO, Union

_Source = Union[str, pathlib.Path, IO[str]]


def resolve_source(source: _Source) -> str:
    """Return CIF source as a string.

    Accepts a raw string, a ``pathlib.Path`` (or any ``os.PathLike``), or an
    already-open text file object.
    """
    if isinstance(source, str):
        return source
    if isinstance(source, pathlib.Path) or hasattr(source, '__fspath__'):
        return pathlib.Path(source).read_text(encoding='utf-8')
    return source.read()


# -- ANSI colours (suppressed when stdout is not a tty) -----------------------

def supports_colour(file: TextIO) -> bool:
    return hasattr(file, 'isatty') and file.isatty()


RESET   = '\033[0m'
BOLD    = '\033[1m'
DIM     = '\033[2m'
RED     = '\033[31m'
YELLOW  = '\033[33m'
CYAN    = '\033[36m'
GREEN   = '\033[32m'
BLUE    = '\033[34m'
MAGENTA = '\033[35m'


def c(text: str, *codes: str, file: TextIO) -> str:
    if not supports_colour(file):
        return text
    return ''.join(codes) + text + RESET


# -- Value formatting for model summary ---------------------------------------

def fmt_value(v) -> str:
    """Format a CifValue as a single-line string, truncated to 25 chars."""
    if isinstance(v, list):
        inner = ', '.join(fmt_value(x) for x in v)
        s = f'[{inner}]'
    elif isinstance(v, dict):
        inner = ', '.join(f'{k}: {fmt_value(vv)}' for k, vv in v.items())
        s = f'{{{inner}}}'
    else:
        s = str(v).replace('\n', '␤')

    if len(s) <= 25:
        return s
    return s[:15] + ' ... ' + s[-5:]
