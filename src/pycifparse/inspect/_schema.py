"""inspect_schema — pretty-print a SchemaSpec derived from a DDLm dictionary."""

import pathlib
import sys
from typing import TextIO, Union

from pycifparse.inspect._common import (
    c, BOLD, DIM, RED, CYAN, GREEN, BLUE, YELLOW, MAGENTA,
)


def inspect_schema(
    source: 'Union[str, pathlib.Path, SchemaSpec, DdlmDictionary]',
    *,
    show_ddl: bool = False,
    file: TextIO = sys.stdout,
) -> None:
    """Print a structured summary of a ``SchemaSpec`` to *file*.

    *source* may be:

    - A :class:`~pycifparse.dictionary.schema.SchemaSpec` — used directly.
    - A :class:`~pycifparse.dictionary.loader.DdlmDictionary` — schema generated from it.
    - A ``pathlib.Path`` to a DDLm dictionary file — loaded via
      :class:`~pycifparse.dictionary.loader.DictionaryLoader` with
      ``directory_resolver(path.parent)`` so ``_import.get`` directives
      resolve from the same directory.
    - A raw CIF source string — parsed with no resolver (imports that require
      external files are silently skipped).

    Parameters
    ----------
    source:
        Dictionary source, a pre-built ``DdlmDictionary``, or a ``SchemaSpec``.
    show_ddl:
        If ``True``, append the raw ``CREATE TABLE`` DDL under each table.
        Default ``False``.
    file:
        Output stream.  Default ``sys.stdout``.
    """
    from pycifparse.dictionary.loader import DictionaryLoader, directory_resolver
    from pycifparse.dictionary.schema import SchemaSpec, generate_schema, emit_create_statements

    try:
        from pycifparse.dictionary.loader import DdlmDictionary
    except ImportError:
        DdlmDictionary = None

    if isinstance(source, SchemaSpec):
        schema = source
    elif DdlmDictionary is not None and isinstance(source, DdlmDictionary):
        schema = generate_schema(source)
    elif isinstance(source, pathlib.Path) or (
        isinstance(source, str) and not source.lstrip().startswith('#')
        and '\n' not in source.strip()
    ):
        path = pathlib.Path(source)
        raw = path.read_text(encoding='utf-8')
        loader = DictionaryLoader(resolver=directory_resolver(path.parent))
        dictionary = loader.load(raw)
        schema = generate_schema(dictionary)
    else:
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
    print(c('-- schema --', BOLD, DIM, file=file), file=file)
    print(c(summary, DIM, file=file), file=file)
    print(file=file)

    ddl_stmts = emit_create_statements(schema) if show_ddl else []
    ddl_by_table: dict[str, str] = {}
    if show_ddl:
        for stmt, table in zip(ddl_stmts, schema.tables.values()):
            ddl_by_table[table.name] = stmt

    for table in sorted(schema.tables.values(), key=lambda t: t.name):
        cls_colour = CYAN if table.category_class == 'Loop' else BLUE
        header = (
            c(table.name, BOLD, file=file)
            + '  '
            + c(f'[{table.category_class}]', cls_colour, file=file)
        )
        print(header, file=file)

        pk_str = ', '.join(c(k, YELLOW, file=file) for k in table.primary_keys)
        print(f'  PK  {pk_str}', file=file)

        def _col_display_type(col) -> str:
            if col.name == '_row_id':
                return 'INTEGER'
            return col.type_contents or 'TEXT'

        col_name_w = max((len(col.name) for col in table.columns), default=8)
        type_w     = max((len(_col_display_type(col)) for col in table.columns), default=4)

        print(f'  {c("columns", DIM, file=file)}', file=file)
        for col in table.columns:
            name_part = c(col.name.ljust(col_name_w), YELLOW, file=file)
            type_part = c(_col_display_type(col).ljust(type_w), GREEN, file=file)

            flags: list[str] = []
            if not col.nullable:
                flags.append(c('NOT NULL', DIM, file=file))
            if col.is_synthetic and col.name == '_row_id':
                flags.append(c('UNIQUE', DIM, file=file))
            if col.is_primary_key:
                flags.append(c('PK', YELLOW, file=file))
            if col.is_synthetic:
                flags.append(c('synthetic', DIM, file=file))

            tag_part = ''
            if not col.is_synthetic:
                tag_part = '  ' + c(col.definition_id, DIM, file=file)
            if col.linked_item_id:
                tag_part += '  ' + c(f'->su {col.linked_item_id}', MAGENTA, file=file)

            flag_str = '  '.join(flags)
            print(f'    {name_part}  {type_part}  {flag_str}{tag_part}', file=file)

        if table.foreign_keys:
            print(f'  {c("foreign keys", DIM, file=file)}', file=file)
            for fk in table.foreign_keys:
                if len(fk.source_columns) == 1:
                    src = c(fk.source_columns[0], YELLOW, file=file)
                    tgt = c(
                        f'{fk.target_table}.{fk.target_columns[0]}',
                        CYAN, file=file,
                    )
                else:
                    src = c(
                        '(' + ', '.join(fk.source_columns) + ')',
                        YELLOW, file=file,
                    )
                    tgt = c(
                        f'{fk.target_table}.(' + ', '.join(fk.target_columns) + ')',
                        CYAN, file=file,
                    )
                print(f'    {src} -> {tgt}  DEFERRABLE', file=file)

        if show_ddl and table.name in ddl_by_table:
            print(f'  {c("ddl", DIM, file=file)}', file=file)
            for ddl_line in ddl_by_table[table.name].splitlines():
                print(f'    {c(ddl_line, DIM, file=file)}', file=file)

        print(file=file)

    if schema.warnings:
        print(c('-- schema warnings --', BOLD, DIM, file=file), file=file)
        for w in schema.warnings:
            print(f'  {c("!", YELLOW, file=file)}  {w}', file=file)
        print(file=file)



if __name__ == '__main__':
    from pathlib import Path
    p = Path(r"C:\Users\User\Documents\github\pycifparse\data\dictionaries\cif_pow.dic")
    inspect_schema(p)
