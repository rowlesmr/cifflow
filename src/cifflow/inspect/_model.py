"""inspect_model — pretty-print a CifFile or CIF source string."""

import sys
from typing import TextIO

from cifflow.inspect._common import (
    _Source, resolve_source, c, fmt_value,
    BOLD, DIM, RED, CYAN, GREEN, YELLOW,
)
from cifflow.inspect._parser import ParseHandler
from cifflow.inspect._lexer import inspect_lexer
from cifflow.parser.parser import CifParser
from cifflow.types import ParseError


def _print_namespace(ns, *, indent: int, file: TextIO) -> None:
    """Print tags and loops from a CifBlock or CifSaveFrame."""
    pad = '  ' * indent

    loop_tag_set: set[str] = set()
    for loop in ns.loops:
        loop_tag_set.update(loop)

    loop_by_first: dict[str, list[str]] = {}
    for loop in ns.loops:
        if loop:
            loop_by_first[loop[0]] = loop

    printed: set[str] = set()

    for tag in ns.tags:
        if tag in printed:
            continue

        if tag in loop_tag_set:
            if tag in loop_by_first:
                loop = loop_by_first[tag]
                cols_data = [ns[t] for t in loop]
                row_count = len(cols_data[0]) if cols_data else 0

                if row_count <= 5:
                    show = list(range(row_count))
                    ellipsis_after = -1
                else:
                    show = [0, 1, -1, row_count - 2, row_count - 1]
                    ellipsis_after = 1

                cells: list[list[str]] = []
                for ri in show:
                    if ri == -1:
                        cells.append([])
                    else:
                        cells.append([fmt_value(cols_data[ci][ri])
                                      for ci in range(len(loop))])

                widths = [len(t) for t in loop]
                for row_cells in cells:
                    for ci, cell in enumerate(row_cells):
                        widths[ci] = max(widths[ci], len(cell))

                rows_label = c(f'({row_count} rows)', DIM, file=file)
                print(f'{pad}{c("loop_", CYAN, file=file)}  {rows_label}', file=file)

                header = '  '.join(
                    c(t.ljust(widths[ci]), YELLOW, file=file)
                    for ci, t in enumerate(loop)
                )
                print(f'{pad}  {header}', file=file)

                for ri, row_cells in zip(show, cells):
                    if ri == -1:
                        print(f'{pad}  {c("...", DIM, file=file)}', file=file)
                    else:
                        row = '  '.join(
                            c(cell.ljust(widths[ci]), GREEN, file=file)
                            for ci, cell in enumerate(row_cells)
                        )
                        print(f'{pad}  {row}', file=file)

                for t in loop:
                    printed.add(t)
        else:
            values = ns[tag]
            first  = fmt_value(values[0])
            n      = len(values)
            suffix = ('  ' + c(f'({n} values)', DIM, file=file)) if n > 1 else ''
            print(
                f'{pad}{c(tag, YELLOW, file=file)}  {c(first, GREEN, file=file)}{suffix}',
                file=file,
            )
            printed.add(tag)

    if hasattr(ns, 'save_frames'):
        for sf_name in ns.save_frames:
            sf = ns[sf_name]
            print(f'{pad}{c(f"save: {sf_name}", CYAN, file=file)}', file=file)
            _print_namespace(sf, indent=indent + 1, file=file)


def _print_model(cif, *, file: TextIO) -> None:
    """Print a summary of a CifFile."""
    print(c('-- CifFile summary --', BOLD, DIM, file=file), file=file)

    if not cif.blocks:
        print(c('  (no blocks)', DIM, file=file), file=file)
        print(file=file)
        return

    for block_name in cif.blocks:
        block = cif[block_name]
        print(c(f'block: {block_name}', BOLD, file=file), file=file)
        _print_namespace(block, indent=1, file=file)

    print(file=file)


def inspect_model(
    source: _Source,
    *,
    mode: str = 'pad',
    file: TextIO = sys.stdout,
    show_values: bool = True,
    show_tokens: bool = True,
) -> None:
    """Run the full pipeline through the CIF model and print a summary.

    Prints (in order): token stream, parser events, CifFile summary, errors.

    Parameters
    ----------
    source:
        CIF source: a raw string, a ``pathlib.Path``, or an open text file object.
    mode:
        Loop row-count mismatch mode passed to ``CifBuilder``: ``'pad'``
        (default) or ``'strict'``.
    file:
        Output stream (default ``sys.stdout``).
    show_values:
        Forward to ``ParseHandler``; set False to suppress ``add_value`` lines.
    show_tokens:
        If True (default), also print the lexer token stream before events.
    """
    from cifflow.cifmodel.builder import CifBuilder

    source = resolve_source(source)

    if show_tokens:
        inspect_lexer(source, file=file)

    errors: list[ParseError] = []
    builder = CifBuilder(on_error=errors.append, mode=mode)
    handler = ParseHandler(builder, file=file, show_values=show_values)
    CifParser(handler).parse(source)
    print(file=file)

    _print_model(builder.result, file=file)

    if errors:
        print(c('-- errors --', BOLD, DIM, file=file), file=file)
        for err in errors:
            loc  = c(f'line {err.line} col {err.column}', DIM, file=file)
            kind = c(f'[{err.error_type.upper()}]', RED, BOLD, file=file)
            print(f'  {kind}  {loc}  {err.message}', file=file)
            if err.recovery_action:
                print(f'    {c("->", DIM, file=file)} {err.recovery_action}', file=file)
        print(file=file)
