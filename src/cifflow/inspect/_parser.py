"""inspect_parse + ParseHandler — pretty-print parser events for a CIF source."""

import sys
from typing import List, Optional, TextIO

from cifflow.inspect._common import (
    _Source, resolve_source, c,
    BOLD, DIM, RED, CYAN, GREEN, BLUE, YELLOW, MAGENTA,
)
from cifflow.inspect._lexer import inspect_lexer
from cifflow.parser.parser import CifParser
from cifflow.types import CifParserEvents, ParseError, ValueType


class ParseHandler:
    """A ``CifParserEvents`` implementation that prints every event and error.

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
        inner: Optional[CifParserEvents] = None,
        *,
        file: TextIO = sys.stdout,
        show_values: bool = True,
    ) -> None:
        self._inner       = inner
        self._file        = file
        self._show_values = show_values
        self._depth       = 0

        print(
            c('-- parser events --', BOLD, DIM, file=self._file),
            file=self._file,
        )

    # -- helpers ---------------------------------------------------------------

    def _indent(self) -> str:
        return '  ' * self._depth

    def _print(self, text: str, colour: str = '') -> None:
        prefix = c(self._indent(), DIM, file=self._file)
        body   = c(text, colour, file=self._file) if colour else text
        print(prefix + body, file=self._file)

    def _fwd(self, name: str, *args, **kwargs) -> None:
        if self._inner is not None:
            getattr(self._inner, name)(*args, **kwargs)

    # -- CifParserEvents -------------------------------------------------------

    def on_data_block(self, name: str) -> None:
        self._depth = 0
        self._print(f'on_data_block({name!r})', BOLD)
        self._depth = 1
        self._fwd('on_data_block', name)

    def on_save_frame_start(self, name: str) -> None:
        self._print(f'on_save_frame_start({name!r})', CYAN)
        self._depth += 1
        self._fwd('on_save_frame_start', name)

    def on_save_frame_end(self) -> None:
        self._depth = max(1, self._depth - 1)
        self._print('on_save_frame_end()', CYAN)
        self._fwd('on_save_frame_end')

    def add_tag(self, tag_name: str) -> None:
        self._print(f'add_tag({tag_name!r})', YELLOW)
        self._fwd('add_tag', tag_name)

    def add_value(self, value: str, value_type: ValueType) -> None:
        if self._show_values:
            raw = repr(value)
            if len(raw) > 60:
                raw = raw[:57] + '…' + raw[-1]
            self._print(f'add_value({raw}, {value_type.value})', GREEN)
        self._fwd('add_value', value, value_type)

    def on_list_start(self) -> None:
        self._print('on_list_start()', MAGENTA)
        self._depth += 1
        self._fwd('on_list_start')

    def on_list_end(self) -> None:
        self._depth = max(0, self._depth - 1)
        self._print('on_list_end()', MAGENTA)
        self._fwd('on_list_end')

    def on_table_start(self) -> None:
        self._print('on_table_start()', MAGENTA)
        self._depth += 1
        self._fwd('on_table_start')

    def on_table_end(self) -> None:
        self._depth = max(0, self._depth - 1)
        self._print('on_table_end()', MAGENTA)
        self._fwd('on_table_end')

    def on_table_key(self, key: str, value_type: ValueType) -> None:
        self._print(f'on_table_key({key!r}, {value_type.value})', BLUE)
        self._fwd('on_table_key', key, value_type)

    def on_loop_start(self, tags: List[str]) -> None:
        self._print(f'on_loop_start({tags!r})', CYAN)
        self._depth += 1
        self._fwd('on_loop_start', tags)

    def on_loop_end(self) -> None:
        self._depth = max(1, self._depth - 1)
        self._print('on_loop_end()', CYAN)
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
        self._print(msg, RED)
        self._fwd('on_error', error)


def inspect_parse(
    source: _Source,
    *,
    inner: Optional[CifParserEvents] = None,
    file: TextIO = sys.stdout,
    show_values: bool = True,
    show_tokens: bool = True,
) -> None:
    """Run the full pipeline and print token stream then parser events.

    Parameters
    ----------
    source:
        CIF source: a raw string, a ``pathlib.Path``, or an open text file object.
    inner:
        Optional downstream handler to receive all events.
    file:
        Output stream (default ``sys.stdout``).
    show_values:
        Forward to ``ParseHandler``; set False to suppress ``add_value`` lines
        for large files.
    show_tokens:
        If True (default), also print the lexer token stream before events.
    """
    source = resolve_source(source)
    if show_tokens:
        inspect_lexer(source, file=file)

    handler = ParseHandler(inner, file=file, show_values=show_values)
    CifParser(handler).parse(source)
    print(file=file)
