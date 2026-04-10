"""
Debugging utilities for the lexer, parser, CIF model, and schema.

Usage — token stream only::

    from pycifparse.debug import debug_lex
    debug_lex(source)

Usage — parser events only::

    from pycifparse.debug import DebugHandler
    from pycifparse.parser.parser import CifParser

    CifParser(DebugHandler()).parse(source)

Usage — parser events AND forwarding to a real handler::

    handler = MyHandler()
    CifParser(DebugHandler(handler)).parse(source)

Usage — both token stream and parser events::

    from pycifparse.debug import debug_parse
    debug_parse(source)

Usage — full pipeline through CIF model::

    from pycifparse.debug import debug_build
    debug_build(source)

Usage — schema from a dictionary file::

    from pycifparse.debug import debug_schema
    debug_schema(pathlib.Path('data/dictionaries/cif_core.dic'))
    debug_schema(pathlib.Path('data/dictionaries/cif_core.dic'), show_ddl=True)
"""

import pathlib
import sys
from typing import IO, List, Optional, TextIO, Union

from pycifparse.lexer.lexer import Lexer
from pycifparse.parser.parser import CifParser
from pycifparse.parser.version import detect_version
from pycifparse.types import CifParserEvents, CifVersion, ParseError, ValueType

_Source = Union[str, pathlib.Path, IO[str]]


def _resolve_source(source: _Source) -> str:
    """Return CIF source as a string.

    Accepts a raw string, a ``pathlib.Path`` (or any ``os.PathLike``), or an
    already-open text file object.
    """
    if isinstance(source, str):
        return source
    if isinstance(source, pathlib.Path) or hasattr(source, '__fspath__'):
        return pathlib.Path(source).read_text(encoding='utf-8')
    # Assume file-like object
    return source.read()


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


# -- Value formatting for model summary ---------------------------------------

def _fmt_value(v) -> str:
    """Format a CifValue as a single-line string, truncated to 25 chars."""
    if isinstance(v, list):
        inner = ', '.join(_fmt_value(x) for x in v)
        s = f'[{inner}]'
    elif isinstance(v, dict):
        inner = ', '.join(f'{k}: {_fmt_value(vv)}' for k, vv in v.items())
        s = f'{{{inner}}}'
    else:
        s = str(v).replace('\n', '␤')

    if len(s) <= 25:
        return s
    return s[:15] + ' ... ' + s[-5:]


# -- Token stream printer ------------------------------------------------------

def debug_lex(
    source: _Source,
    *,
    version: Optional[CifVersion] = None,
    file: TextIO = sys.stdout,
) -> None:
    """Print the full token stream for *source* to *file*.

    *source* may be a raw CIF string, a ``pathlib.Path``, or an open text
    file object.  If *version* is None it is auto-detected from the magic line.
    """
    source = _resolve_source(source)
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

        line_part  = _c(f'{tok.line:>5} {tok.column:>4}', _DIM, file=file)
        type_part  = _c(f'{tok.token_type.value:<10}', _CYAN, file=file)
        vtype_part = _c(f'{vtype:<22}', _BLUE, file=file)
        val_part   = _c(raw, _GREEN if tok.token_type.value == 'value' else _YELLOW, file=file)

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
    A `CifParserEvents` implementation that prints every event and error.

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

    # -- CifParserEvents -------------------------------------------------------

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


# -- Model summary printer -----------------------------------------------------

def _print_namespace(ns, *, indent: int, file: TextIO) -> None:
    """Print tags and loops from a CifBlock or CifSaveFrame."""
    pad = '  ' * indent

    loop_tag_set: set[str] = set()
    for loop in ns.loops:
        loop_tag_set.update(loop)

    # Map first tag of each loop → loop tag list, for printing the header once
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

                # Decide which row indices to display
                if row_count <= 5:
                    show = list(range(row_count))
                    ellipsis_after = -1   # no ellipsis
                else:
                    show = [0, 1, -1, row_count - 2, row_count - 1]
                    ellipsis_after = 1    # insert '...' after index 1

                # Format all displayed cells to compute column widths
                cells: list[list[str]] = []  # cells[row_idx][col_idx]
                for ri in show:
                    if ri == -1:
                        cells.append([])      # sentinel for ellipsis row
                    else:
                        cells.append([_fmt_value(cols_data[ci][ri])
                                      for ci in range(len(loop))])

                # Column widths: max of tag name and displayed cell widths
                widths = [len(t) for t in loop]
                for row_cells in cells:
                    for ci, cell in enumerate(row_cells):
                        widths[ci] = max(widths[ci], len(cell))

                rows_label = _c(f'({row_count} rows)', _DIM, file=file)
                print(f'{pad}{_c("loop_", _CYAN, file=file)}  {rows_label}', file=file)

                # Header
                header = '  '.join(
                    _c(t.ljust(widths[ci]), _YELLOW, file=file)
                    for ci, t in enumerate(loop)
                )
                print(f'{pad}  {header}', file=file)

                # Data rows
                for ri, row_cells in zip(show, cells):
                    if ri == -1:
                        print(f'{pad}  {_c("...", _DIM, file=file)}', file=file)
                    else:
                        row = '  '.join(
                            _c(cell.ljust(widths[ci]), _GREEN, file=file)
                            for ci, cell in enumerate(row_cells)
                        )
                        print(f'{pad}  {row}', file=file)

                for t in loop:
                    printed.add(t)
        else:
            values = ns[tag]
            first  = _fmt_value(values[0])
            n      = len(values)
            suffix = ('  ' + _c(f'({n} values)', _DIM, file=file)) if n > 1 else ''
            print(
                f'{pad}{_c(tag, _YELLOW, file=file)}  {_c(first, _GREEN, file=file)}{suffix}',
                file=file,
            )
            printed.add(tag)

    # Save frames (CifBlock only)
    if hasattr(ns, 'save_frames'):
        for sf_name in ns.save_frames:
            sf = ns[sf_name]
            print(f'{pad}{_c(f"save: {sf_name}", _CYAN, file=file)}', file=file)
            _print_namespace(sf, indent=indent + 1, file=file)


def _print_model(cif, *, file: TextIO) -> None:
    """Print a summary of a CifFile."""
    print(_c('-- CifFile summary --', _BOLD, _DIM, file=file), file=file)

    if not cif.blocks:
        print(_c('  (no blocks)', _DIM, file=file), file=file)
        print(file=file)
        return

    for block_name in cif.blocks:
        block = cif[block_name]
        print(_c(f'block: {block_name}', _BOLD, file=file), file=file)
        _print_namespace(block, indent=1, file=file)

    print(file=file)


# -- Convenience functions -----------------------------------------------------

def debug_parse(
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
        CIF source: a raw string, a ``pathlib.Path``, or an open text file
        object.
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
    source = _resolve_source(source)
    if show_tokens:
        debug_lex(source, file=file)

    handler = DebugHandler(inner, file=file, show_values=show_values)
    CifParser(handler).parse(source)
    print(file=file)


def debug_build(
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
        CIF source: a raw string, a ``pathlib.Path``, or an open text file
        object.
    mode:
        Loop row-count mismatch mode passed to ``CifBuilder``: ``'pad'``
        (default) or ``'strict'``.
    file:
        Output stream (default ``sys.stdout``).
    show_values:
        Forward to ``DebugHandler``; set False to suppress ``add_value`` lines.
    show_tokens:
        If True (default), also print the lexer token stream before events.
    """
    from pycifparse.cifmodel.builder import CifBuilder

    source = _resolve_source(source)

    if show_tokens:
        debug_lex(source, file=file)

    errors: list[ParseError] = []
    builder = CifBuilder(on_error=errors.append, mode=mode)
    handler = DebugHandler(builder, file=file, show_values=show_values)
    CifParser(handler).parse(source)
    print(file=file)

    _print_model(builder.result, file=file)

    if errors:
        print(_c('-- errors --', _BOLD, _DIM, file=file), file=file)
        for err in errors:
            loc  = _c(f'line {err.line} col {err.column}', _DIM, file=file)
            kind = _c(f'[{err.error_type.upper()}]', _RED, _BOLD, file=file)
            print(f'  {kind}  {loc}  {err.message}', file=file)
            if err.recovery_action:
                print(f'    {_c("->", _DIM, file=file)} {err.recovery_action}', file=file)
        print(file=file)


# -- Schema printer ------------------------------------------------------------

def debug_schema(
    source: 'Union[str, pathlib.Path, SchemaSpec]',
    *,
    show_ddl: bool = False,
    file: TextIO = sys.stdout,
) -> None:
    """Print a structured summary of a ``SchemaSpec`` to *file*.

    *source* may be:

    - A :class:`~pycifparse.dictionary.schema.SchemaSpec` — used directly.
    - A ``pathlib.Path`` to a DDLm dictionary file — loaded via
      :class:`~pycifparse.dictionary.loader.DictionaryLoader` with
      ``directory_resolver(path.parent)`` so ``_import.get`` directives
      resolve from the same directory.
    - A raw CIF source string — parsed with no resolver (imports that require
      external files are silently skipped).

    Only schema-level information is shown.  Lex, parse, and loader warnings
    are suppressed; fix those with ``debug_build`` before inspecting the schema.

    Parameters
    ----------
    source:
        Dictionary source or a pre-built ``SchemaSpec``.
    show_ddl:
        If ``True``, append the raw ``CREATE TABLE`` DDL under each table.
        Default ``False``.
    file:
        Output stream.  Default ``sys.stdout``.
    """
    from pycifparse.dictionary.loader import DictionaryLoader, directory_resolver
    from pycifparse.dictionary.schema import SchemaSpec, generate_schema, emit_create_statements

    if isinstance(source, SchemaSpec):
        schema = source
    elif isinstance(source, pathlib.Path) or (
        isinstance(source, str) and not source.lstrip().startswith('#')
        and '\n' not in source.strip()
    ):
        # Treat as a file path.
        path = pathlib.Path(source)
        raw = path.read_text(encoding='utf-8')
        loader = DictionaryLoader(resolver=directory_resolver(path.parent))
        dictionary = loader.load(raw)
        schema = generate_schema(dictionary)
    else:
        # Raw CIF source string.
        loader = DictionaryLoader(resolver=None)
        dictionary = loader.load(source)
        schema = generate_schema(dictionary)

    n_tables = len(schema.tables)
    n_set    = sum(1 for t in schema.tables.values() if t.category_class == 'Set')
    n_loop   = sum(1 for t in schema.tables.values() if t.category_class == 'Loop')
    n_fk     = sum(len(t.foreign_keys) for t in schema.tables.values())
    n_warn   = len(schema.warnings)

    summary = (
        f'{n_tables} table{"s" if n_tables != 1 else ""}'
        f'  ({n_set} Set, {n_loop} Loop)'
        f'  {n_fk} FK{"s" if n_fk != 1 else ""}'
        f'  {n_warn} warning{"s" if n_warn != 1 else ""}'
    )
    print(_c('-- schema --', _BOLD, _DIM, file=file), file=file)
    print(_c(summary, _DIM, file=file), file=file)
    print(file=file)

    ddl_stmts = emit_create_statements(schema) if show_ddl else []
    ddl_by_table: dict[str, str] = {}
    if show_ddl:
        for stmt, table in zip(ddl_stmts, schema.tables.values()):
            ddl_by_table[table.name] = stmt

    for table in sorted(schema.tables.values(), key=lambda t: t.name):
        cls_colour = _CYAN if table.category_class == 'Loop' else _BLUE
        header = (
            _c(table.name, _BOLD, file=file)
            + '  '
            + _c(f'[{table.category_class}]', cls_colour, file=file)
        )
        print(header, file=file)

        # PK line
        pk_str = ', '.join(_c(k, _YELLOW, file=file) for k in table.primary_keys)
        print(f'  PK  {pk_str}', file=file)

        # Columns — compute widths for alignment
        def _col_display_type(col) -> str:
            if col.name == '_row_id':
                return 'INTEGER'
            return col.type_contents or 'TEXT'

        col_name_w = max((len(c.name) for c in table.columns), default=8)
        type_w     = max((len(_col_display_type(c)) for c in table.columns), default=4)

        print(f'  {_c("columns", _DIM, file=file)}', file=file)
        for col in table.columns:
            name_part = _c(col.name.ljust(col_name_w), _YELLOW, file=file)
            type_part = _c(_col_display_type(col).ljust(type_w), _GREEN, file=file)

            flags: list[str] = []
            if not col.nullable:
                flags.append(_c('NOT NULL', _DIM, file=file))
            if col.is_synthetic and col.name == '_row_id':
                flags.append(_c('UNIQUE', _DIM, file=file))
            if col.is_primary_key:
                flags.append(_c('PK', _YELLOW, file=file))
            if col.is_synthetic:
                flags.append(_c('synthetic', _DIM, file=file))

            tag_part = ''
            if not col.is_synthetic:
                tag_part = '  ' + _c(col.definition_id, _DIM, file=file)
            if col.linked_item_id:
                tag_part += '  ' + _c(f'->su {col.linked_item_id}', _MAGENTA, file=file)

            flag_str = '  '.join(flags)
            print(f'    {name_part}  {type_part}  {flag_str}{tag_part}', file=file)

        # FKs
        if table.foreign_keys:
            print(f'  {_c("foreign keys", _DIM, file=file)}', file=file)
            for fk in table.foreign_keys:
                src = _c(fk.source_column, _YELLOW, file=file)
                tgt = _c(f'{fk.target_table}.{fk.target_column}', _CYAN, file=file)
                print(f'    {src} -> {tgt}  DEFERRABLE', file=file)

        # Optional DDL
        if show_ddl and table.name in ddl_by_table:
            print(f'  {_c("ddl", _DIM, file=file)}', file=file)
            for ddl_line in ddl_by_table[table.name].splitlines():
                print(f'    {_c(ddl_line, _DIM, file=file)}', file=file)

        print(file=file)

    # Schema-level warnings
    if schema.warnings:
        print(_c('-- schema warnings --', _BOLD, _DIM, file=file), file=file)
        for w in schema.warnings:
            print(f'  {_c("!", _YELLOW, file=file)}  {w}', file=file)
        print(file=file)


if __name__ == "__main__":
    import sys as _sys

    _args = _sys.argv[1:]
    _use_build   = '-b'          in _args or '--build'      in _args
    _use_schema  = '-s'          in _args or '--schema'     in _args
    _no_tokens   = '--no-tokens' in _args
    _show_ddl    = '--ddl'       in _args
    _args = [a for a in _args
             if a not in ('-b', '--build', '-s', '--schema', '--no-tokens', '--ddl')]

    _path = (
        pathlib.Path(_args[0])
        if _args
        else pathlib.Path(r"C:\Users\User\Documents\github\pycifparse\data\dictionaries\cif_core.dic")
    )

    if _use_schema:
        debug_schema(_path, show_ddl=_show_ddl)
    else:
        debug_build(_path, show_tokens=not _no_tokens)
        #debug_schema(_path, show_ddl=_show_ddl)
