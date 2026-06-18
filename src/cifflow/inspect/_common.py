"""Shared utilities for the inspect package."""

import pathlib
import sys
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


# -- ANSI colours (suppressed when stdout is not a tty or VT is unavailable) --

def supports_colour(file: TextIO) -> bool:
    """Return True if *file* is a TTY that can render ANSI colour sequences."""
    if not (hasattr(file, 'isatty') and file.isatty()):
        return False
    if sys.platform == 'win32':
        try:
            import ctypes
            import ctypes.wintypes
            std_handles = {0: -10, 1: -11, 2: -12}
            handle_id = std_handles.get(file.fileno())
            if handle_id is None:
                # Non-standard fd (e.g. a pipe or test double) — trust isatty().
                return True
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.wintypes.DWORD()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            return bool(mode.value & 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            # fileno() unsupported or ctypes unavailable — trust isatty().
            return True
    return True


RESET   = '\033[0m'
BOLD    = '\033[1m'
DIM     = '\033[2m'
RED     = '\033[31m'
YELLOW  = '\033[33m'
CYAN    = '\033[36m'
GREEN   = '\033[32m'
BLUE    = '\033[34m'
MAGENTA = '\033[35m'


def c(text: str, *codes: str, file: TextIO, use_colour: bool = True) -> str:
    """Return *text* wrapped in ANSI *codes*, or plain *text* if colour is off."""
    if not use_colour or not supports_colour(file):
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
