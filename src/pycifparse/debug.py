"""
Debugging utilities for the lexer and parser.

Usage — token stream only::

    from pycifparse.debug import debug_lex
    debug_lex(source)

Usage — parser events only::

    from pycifparse.debug import DebugHandler
    from pycifparse.parser.parser import CIFParser

    CIFParser(DebugHandler()).parse(source)

Usage — parser events AND forwarding to a real handler::

    handler = MyHandler()
    CIFParser(DebugHandler(handler)).parse(source)

Usage — both token stream and parser events::

    from pycifparse.debug import debug_parse
    debug_parse(source)
"""

import sys
import textwrap
from typing import List, Optional, TextIO

from pycifparse.lexer.lexer import Lexer
from pycifparse.parser.parser import CIFParser
from pycifparse.parser.version import detect_version
from pycifparse.types import CIFParserEvents, CIFVersion, ParseError, ValueType


# -- ANSI colours (suppressed when stdout is not a tty) -----------------------

def _supports_colour(file: TextIO) -> bool:
    return hasattr(file, 'isatty') and file.isatty()


_RESET  = '\033[0m'
_BOLD   = '\033[1m'
_DIM    = '\033[2m'
_RED    = '\033[31m'
_YELLOW = '\033[33m'
_CYAN   = '\033[36m'
_GREEN  = '\033[32m'
_BLUE   = '\033[34m'
_MAGENTA = '\033[35m'


def _c(text: str, *codes: str, file: TextIO) -> str:
    if not _supports_colour(file):
        return text
    return ''.join(codes) + text + _RESET


# -- Token stream printer ------------------------------------------------------

def debug_lex(
    source: str,
    *,
    version: Optional[CIFVersion] = None,
    file: TextIO = sys.stdout,
) -> None:
    """Print the full token stream for *source* to *file*.

    If *version* is None it is auto-detected from the magic line.
    """
    if version is None:
        version, remaining, line_offset, v_errors = detect_version(source)
        if v_errors:
            for ve in v_errors:
                print(
                    _c(f'[VERSION ERROR] line {ve.line}: {ve.message}', _RED, _BOLD, file=file),
                    file=file,
                )
    else:
        remaining, line_offset = source, 0

    ver_label = version.value
    print(
        _c(f'-- token stream  (CIF {ver_label}) --', _BOLD, _DIM, file=file),
        file=file,
    )
    print(
        _c(
            f"{'line':>5} {'col':>4}  {'token_type':<10}  {'value_type':<22}  value",
            _DIM, file=file,
        ),
        file=file,
    )
    print(_c('-' * 72, _DIM, file=file), file=file)

    for tok in Lexer(remaining, version, line_offset).tokens():
        vtype = tok.value_type.value if tok.value_type else ''
        raw   = repr(tok.value)
        if len(raw) > 50:
            raw = raw[:47] + '…' + raw[-1]

        line_part = _c(f'{tok.line:>5} {tok.column:>4}', _DIM, file=file)
        type_part = _c(f'{tok.token_type.value:<10}', _CYAN, file=file)
        vtype_part = _c(f'{vtype:<22}', _BLUE, file=file)
        val_part  = _c(raw, _GREEN if tok.token_type.value == 'value' else _YELLOW, file=file)

        print(f'  {line_part}  {type_part}  {vtype_part}  {val_part}', file=file)

        for err in tok.errors:
            print(
                _c(
                    f'         ^ LEX ERROR  col {err.column}: {err.message}',
                    _RED, file=file,
                ),
                file=file,
            )

    print(file=file)


# -- Debug event handler -------------------------------------------------------

class DebugHandler:
    """
    A `CIFParserEvents` implementation that prints every event and error.

    Pass an optional *inner* handler to forward all events after printing.

    Parameters
    ----------
    inner:
        Optional downstream handler.  All events are forwarded to it after
        being printed.
    file:
        Output stream (default ``sys.stdout``).
    show_values:
        If False, ``add_value`` calls are printed as a short summary rather
        than one line each.  Useful for large loop tables.  Default True.
    """

    def __init__(
        self,
        inner: Optional[CIFParserEvents] = None,
        *,
        file: TextIO = sys.stdout,
        show_values: bool = True,
    ) -> None:
        self._inner       = inner
        self._file        = file
        self._show_values = show_values
        self._depth       = 0   # indentation depth

        print(
            _c('-- parser events --', _BOLD, _DIM, file=self._file),
            file=self._file,
        )

    # -- helpers ---------------------------------------------------------------

    def _indent(self) -> str:
        return '  ' * self._depth

    def _print(self, text: str, colour: str = '') -> None:
        prefix = _c(self._indent(), _DIM, file=self._file)
        body   = _c(text, colour, file=self._file) if colour else text
        print(prefix + body, file=self._file)

    def _fwd(self, name: str, *args, **kwargs) -> None:
        if self._inner is not None:
            getattr(self._inner, name)(*args, **kwargs)

    # -- CIFParserEvents -------------------------------------------------------

    def on_data_block(self, name: str) -> None:
        self._depth = 0
        self._print(f'on_data_block({name!r})', _BOLD)
        self._depth = 1
        self._fwd('on_data_block', name)

    def on_save_frame_start(self, name: str) -> None:
        self._print(f'on_save_frame_start({name!r})', _CYAN)
        self._depth += 1
        self._fwd('on_save_frame_start', name)

    def on_save_frame_end(self) -> None:
        self._depth = max(1, self._depth - 1)
        self._print('on_save_frame_end()', _CYAN)
        self._fwd('on_save_frame_end')

    def add_tag(self, tag_name: str) -> None:
        self._print(f'add_tag({tag_name!r})', _YELLOW)
        self._fwd('add_tag', tag_name)

    def add_value(self, value: str, value_type: ValueType) -> None:
        if self._show_values:
            raw = repr(value)
            if len(raw) > 60:
                raw = raw[:57] + '…' + raw[-1]
            self._print(f'add_value({raw}, {value_type.value})', _GREEN)
        self._fwd('add_value', value, value_type)

    def on_list_start(self) -> None:
        self._print('on_list_start()', _MAGENTA)
        self._depth += 1
        self._fwd('on_list_start')

    def on_list_end(self) -> None:
        self._depth = max(0, self._depth - 1)
        self._print('on_list_end()', _MAGENTA)
        self._fwd('on_list_end')

    def on_table_start(self) -> None:
        self._print('on_table_start()', _MAGENTA)
        self._depth += 1
        self._fwd('on_table_start')

    def on_table_end(self) -> None:
        self._depth = max(0, self._depth - 1)
        self._print('on_table_end()', _MAGENTA)
        self._fwd('on_table_end')

    def on_table_key(self, key: str, value_type: ValueType) -> None:
        self._print(f'on_table_key({key!r}, {value_type.value})', _BLUE)
        self._fwd('on_table_key', key, value_type)

    def on_loop_start(self, tags: List[str]) -> None:
        self._print(f'on_loop_start({tags!r})', _CYAN)
        self._depth += 1
        self._fwd('on_loop_start', tags)

    def on_loop_end(self) -> None:
        self._depth = max(1, self._depth - 1)
        self._print('on_loop_end()', _CYAN)
        self._fwd('on_loop_end')

    def on_error(self, error: ParseError) -> None:
        msg = (
            f'[{error.error_type.upper()}] '
            f'line {error.line} col {error.column}: '
            f'{error.message}'
        )
        if error.context:
            msg += f'  (context: {error.context!r})'
        if error.recovery_action:
            msg += f'  -> {error.recovery_action}'
        self._print(msg, _RED)
        self._fwd('on_error', error)


# -- Convenience function ------------------------------------------------------

def debug_parse(
    source: str,
    *,
    inner: Optional[CIFParserEvents] = None,
    file: TextIO = sys.stdout,
    show_values: bool = True,
    show_tokens: bool = True,
) -> None:
    """Run the full pipeline and print token stream then parser events.

    Parameters
    ----------
    source:
        Raw CIF source string.
    inner:
        Optional downstream handler to receive all events.
    file:
        Output stream (default ``sys.stdout``).
    show_values:
        Forward to ``DebugHandler``; set False to suppress ``add_value`` lines
        for large files.
    show_tokens:
        If True (default), also print the lexer token stream before events.
    """
    if show_tokens:
        debug_lex(source, file=file)

    handler = DebugHandler(inner, file=file, show_values=show_values)
    CIFParser(handler).parse(source)
    print(file=file)
