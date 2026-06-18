"""inspect_lexer — pretty-print the lexer token stream for a CIF source."""

import sys
from typing import Optional, TextIO

from cifflow.inspect._common import (
    _Source, resolve_source, c,
    BOLD, DIM, RED, CYAN, BLUE, GREEN, YELLOW,
)
from cifflow.types import CifVersion


def inspect_lexer(
    source: _Source,
    *,
    version: Optional[CifVersion] = None,
    file: TextIO = sys.stdout,
    use_colour: bool = True,
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
    use_colour:
        If False, suppress all ANSI colour codes regardless of terminal type.
        Default True.
    """
    from cifflow import cifflow_core
    from cifflow.parser.version import detect_version

    source = resolve_source(source)

    mode = None
    if version is CifVersion.CIF_1_1:
        mode = 'cif1'
    elif version is CifVersion.CIF_2_0:
        mode = 'cif2'
    else:
        # Run Python detect_version solely to surface version errors in output.
        _ver, _rem, _off, v_errors = detect_version(source)
        if v_errors:
            for ve in v_errors:
                print(
                    c(f'[VERSION ERROR] line {ve.line}: {ve.message}', RED, BOLD,
                      file=file, use_colour=use_colour),
                    file=file,
                )

    tokens, detected_version = cifflow_core.lex_cif(source, mode)

    ver_label = detected_version.value
    print(
        c(f'-- token stream  (CIF {ver_label}) --', BOLD, DIM,
          file=file, use_colour=use_colour),
        file=file,
    )
    print(
        c(
            f"{'line':>5} {'col':>4}  {'token_type':<10}  {'value_type':<22}  value",
            DIM, file=file, use_colour=use_colour,
        ),
        file=file,
    )
    print(c('-' * 72, DIM, file=file, use_colour=use_colour), file=file)

    for tok in tokens:
        vtype = tok['value_type'].value if tok['value_type'] is not None else ''
        raw   = repr(tok['value'])
        if len(raw) > 50:
            raw = raw[:47] + '…' + raw[-1]

        line_part  = c(f'{tok["line"]:>5} {tok["column"]:>4}', DIM,
                       file=file, use_colour=use_colour)
        type_part  = c(f'{tok["token_type"].value:<10}', CYAN,
                       file=file, use_colour=use_colour)
        vtype_part = c(f'{vtype:<22}', BLUE, file=file, use_colour=use_colour)
        val_part   = c(raw, GREEN if tok['token_type'].value == 'value' else YELLOW,
                       file=file, use_colour=use_colour)

        print(f'  {line_part}  {type_part}  {vtype_part}  {val_part}', file=file)

        for err in tok['errors']:
            print(
                c(
                    f'         ^ LEX ERROR  col {err["column"]}: {err["message"]}',
                    RED, file=file, use_colour=use_colour,
                ),
                file=file,
            )

    print(file=file)
