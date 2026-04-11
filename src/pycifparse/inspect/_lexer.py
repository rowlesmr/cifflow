"""inspect_lexer — pretty-print the lexer token stream for a CIF source."""

import sys
from typing import Optional, TextIO

from pycifparse.inspect._common import (
    _Source, resolve_source, c,
    BOLD, DIM, RED, CYAN, BLUE, GREEN, YELLOW,
)
from pycifparse.lexer.lexer import Lexer
from pycifparse.parser.version import detect_version
from pycifparse.types import CifVersion


def inspect_lexer(
    source: _Source,
    *,
    version: Optional[CifVersion] = None,
    file: TextIO = sys.stdout,
) -> None:
    """Print the full token stream for *source* to *file*.

    Parameters
    ----------
    source:
        CIF source: a raw string, a ``pathlib.Path``, or an open text file object.
    version:
        If None (default), auto-detected from the magic line.
    file:
        Output stream (default ``sys.stdout``).
    """
    source = resolve_source(source)
    if version is None:
        version, remaining, line_offset, v_errors = detect_version(source)
        if v_errors:
            for ve in v_errors:
                print(
                    c(f'[VERSION ERROR] line {ve.line}: {ve.message}', RED, BOLD, file=file),
                    file=file,
                )
    else:
        remaining, line_offset = source, 0

    ver_label = version.value
    print(
        c(f'-- token stream  (CIF {ver_label}) --', BOLD, DIM, file=file),
        file=file,
    )
    print(
        c(
            f"{'line':>5} {'col':>4}  {'token_type':<10}  {'value_type':<22}  value",
            DIM, file=file,
        ),
        file=file,
    )
    print(c('-' * 72, DIM, file=file), file=file)

    for tok in Lexer(remaining, version, line_offset).tokens():
        vtype = tok.value_type.value if tok.value_type else ''
        raw   = repr(tok.value)
        if len(raw) > 50:
            raw = raw[:47] + '…' + raw[-1]

        line_part  = c(f'{tok.line:>5} {tok.column:>4}', DIM, file=file)
        type_part  = c(f'{tok.token_type.value:<10}', CYAN, file=file)
        vtype_part = c(f'{vtype:<22}', BLUE, file=file)
        val_part   = c(raw, GREEN if tok.token_type.value == 'value' else YELLOW, file=file)

        print(f'  {line_part}  {type_part}  {vtype_part}  {val_part}', file=file)

        for err in tok.errors:
            print(
                c(
                    f'         ^ LEX ERROR  col {err.column}: {err.message}',
                    RED, file=file,
                ),
                file=file,
            )

    print(file=file)
