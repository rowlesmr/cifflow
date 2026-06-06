"""inspect_schema — pretty-print a SchemaSpec derived from a DDLm dictionary."""

import pathlib
import sys
from typing import TextIO, Union

from cifflow.inspect._common import (
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

    - A :class:`~cifflow.dictionary.schema.SchemaSpec` — used directly.
    - A :class:`~cifflow.dictionary.loader.DdlmDictionary` — schema generated from it.
    - A ``pathlib.Path`` to a DDLm dictionary file — loaded via
      :class:`~cifflow.dictionary.loader.DictionaryLoader` with
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
    from cifflow.dictionary.loader import DictionaryLoader, directory_resolver
    from cifflow.dictionary.schema import SchemaSpec, generate_schema, emit_create_statements

    try:
        from cifflow.dictionary.loader import DdlmDictionary
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

    def _depr_suffix(definition_id: str) -> str:
        if definition_id not in schema.deprecated_ids:
            return ''
        replacements = [r for r in schema.deprecated_replacements.get(definition_id, []) if r]
        if replacements:
            return '  ' + c('DEPRECATED -> ' + ', '.join(replacements), RED, file=file)
        return '  ' + c('DEPRECATED', RED, file=file)

    for table in sorted(schema.tables.values(), key=lambda t: t.name):
        cls_colour = CYAN if table.category_class == 'Loop' else BLUE
        header = (
            c(table.name, BOLD, file=file)
            + '  '
            + c(f'[{table.category_class}]', cls_colour, file=file)
            + _depr_suffix(table.definition_id)
        )
        print(header, file=file)

        pk_str = ', '.join(c(k, YELLOW, file=file) for k in table.primary_keys)
        print(f'  PK  {pk_str}', file=file)

        def _col_display_type(col) -> str:
            if col.name == '_cifflow_row_id':
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
            if col.is_synthetic and col.name == '_cifflow_row_id':
                flags.append(c('UNIQUE', DIM, file=file))
            if col.is_primary_key:
                flags.append(c('PK', YELLOW, file=file))
            if col.is_synthetic:
                flags.append(c('synthetic', DIM, file=file))

            tag_part = ''
            if not col.is_synthetic:
                tag_part = '  ' + c(col.definition_id, DIM, file=file)
            if col.linked_item_id and not col.is_primary_key:
                tag_part += '  ' + c(f'->su {col.linked_item_id}', MAGENTA, file=file)
            tag_part += _depr_suffix(col.definition_id)

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

    set_tables = {name for name, t in schema.tables.items() if t.category_class == 'Set'}

    # Reverse map: definition_id → (table_name, col_name), for transitive chain-following.
    tag_to_table_col: dict[str, tuple[str, str]] = {
        defn_id: (tbl, col_name)
        for (tbl, col_name), defn_id in schema.column_to_tag.items()
    }
    col_by_key: dict[tuple[str, str], object] = {
        (tbl, col.name): col
        for tbl, tbl_def in schema.tables.items()
        for col in tbl_def.columns
    }

    def _resolves_to_set(linked_item_id: str, visited: set) -> bool:
        """Return True if linked_item_id transitively reaches a Set category."""
        if not linked_item_id or linked_item_id in visited:
            return False
        visited.add(linked_item_id)
        canonical = schema.alias_to_definition_id.get(linked_item_id, linked_item_id)
        cls = schema.tag_to_category_class.get(canonical)
        if cls == 'Set':
            return True
        if cls != 'Loop':
            return False
        entry = tag_to_table_col.get(canonical)
        if entry is None:
            return False
        target_col = col_by_key.get(entry)
        if target_col is not None and target_col.linked_item_id:
            return _resolves_to_set(target_col.linked_item_id, visited)
        return False

    bridge_by_table: dict[str, list] = {}
    for bc in schema.bridge_columns:
        bridge_by_table.setdefault(bc.table_name, []).append(bc)

    floating_loops = []
    for table in schema.tables.values():
        if table.category_class != 'Loop':
            continue
        pk_set = set(table.primary_keys)

        has_set_link = any(
            _resolves_to_set(col.linked_item_id, set())
            for col in table.columns
            if col.is_primary_key and not col.is_synthetic and col.linked_item_id
        )

        has_set_bridge = any(
            bc.column_name in pk_set and bc.hops[-1][1] in set_tables
            for bc in bridge_by_table.get(table.name, [])
        )

        if not has_set_link and not has_set_bridge:
            floating_loops.append(table)

    if floating_loops:
        print(c('-- loop tables without Set-derived category key --', BOLD, DIM, file=file), file=file)
        for table in sorted(floating_loops, key=lambda t: t.name):
            pk_str = ', '.join(c(k, YELLOW, file=file) for k in table.primary_keys)
            print(f'  {c(table.name, BOLD, file=file)}  PK: {pk_str}', file=file)
        print(file=file)

    if schema.warnings:
        print(c('-- schema warnings --', BOLD, DIM, file=file), file=file)
        for w in schema.warnings:
            print(f'  {c("!", YELLOW, file=file)}  {w}', file=file)
        print(file=file)



def inspect_fk_path(
    schema: 'SchemaSpec',
    source: str,
    target: str,
    *,
    file: TextIO = sys.stdout,
) -> bool:
    """Print all FK and bridge-column chains that connect *source* to *target*.

    Shows two kinds of paths:

    - **Direct FK edges** (``ForeignKeyDef``) stored on each table — the
      declared ``FOREIGN KEY`` constraints in the schema.
    - **Bridge column chains** (``BridgeColumnDef``) computed during schema
      generation — the transitive multi-hop lookups that ingest uses to
      propagate FK values through intermediate tables.

    Parameters
    ----------
    schema:
        The ``SchemaSpec`` to search.
    source:
        Table name to start from.
    target:
        Table name to reach.
    file:
        Output stream.  Default ``sys.stdout``.

    Returns
    -------
    bool
        ``True`` if at least one path was found, ``False`` otherwise.
    """
    if source not in schema.tables:
        print(c(f'unknown table: {source!r}', RED, file=file), file=file)
        return False
    if target not in schema.tables:
        print(c(f'unknown table: {target!r}', RED, file=file), file=file)
        return False

    found = False

    # --- 1. Direct FK chains (BFS over ForeignKeyDef edges) ---
    # Find ALL simple paths (DFS with depth limit to avoid explosions).
    def _all_fk_paths(src: str, tgt: str) -> list[list[tuple]]:
        """Return all simple FK-edge paths from src to tgt."""
        results: list[list[tuple]] = []
        stack = [(src, [], {src})]
        while stack:
            cur, path, visited = stack.pop()
            for fk in schema.tables[cur].foreign_keys:
                nxt = fk.target_table
                step = (cur, fk.source_columns, nxt, fk.target_columns)
                if nxt == tgt:
                    results.append(path + [step])
                elif nxt not in visited and nxt in schema.tables:
                    stack.append((nxt, path + [step], visited | {nxt}))
        return results

    # Build a lookup: (table, column) → BridgeColumnDef for synthetic columns.
    bridge_by_col: dict[tuple[str, str], 'BridgeColumnDef'] = {
        (bc.table_name, bc.column_name): bc
        for bc in schema.bridge_columns
    }
    # Build a set of synthetic column names per table for quick lookup.
    synthetic_cols: dict[str, set[str]] = {
        tbl: {col.name for col in td.columns if col.is_synthetic}
        for tbl, td in schema.tables.items()
    }

    def _print_fk_step(from_t: str, src_cols: list[str], tgt_t: str, tgt_cols: list[str], indent: str = '  ') -> None:
        """Print one FK hop, annotating synthetic source columns with their bridge derivation."""
        for src_col, tgt_col in zip(src_cols, tgt_cols):
            bc = bridge_by_col.get((from_t, src_col))
            if bc is not None:
                # Synthetic: show all bridge chains (primary + fallbacks) that derive this column.
                all_chains = [(bc.hops, bc.bridge_value_column)] + list(bc.fallback_chains)
                n = len(all_chains)
                for i, (chain_hops, chain_val) in enumerate(all_chains):
                    label_str = 'primary' if i == 0 else f'fallback {i}'
                    suffix = f' [{label_str}]' if n > 1 else ''
                    print(
                        f'{indent}{c(src_col, YELLOW, file=file)}'
                        f' {c(f"(synthetic, derived via bridge{suffix}):", DIM, file=file)}',
                        file=file,
                    )
                    prev = from_t
                    for via_col, bridge_tbl, bridge_pk in chain_hops:
                        print(
                            f'{indent}  {c(prev, CYAN, file=file)}.{c(via_col, YELLOW, file=file)}'
                            f'  ->  {c(bridge_tbl, CYAN, file=file)}.{c(bridge_pk, GREEN, file=file)}',
                            file=file,
                        )
                        prev = bridge_tbl
                    print(
                        f'{indent}  {c("value:", DIM, file=file)} {c(prev, CYAN, file=file)}.{c(chain_val, GREEN, file=file)}'
                        f'  =>  {c(from_t, CYAN, file=file)}.{c(src_col, YELLOW, file=file)}',
                        file=file,
                    )
            print(
                f'{indent}{c(from_t, CYAN, file=file)}.{c(src_col, YELLOW, file=file)}'
                f'  ->  {c(tgt_t, CYAN, file=file)}.{c(tgt_col, GREEN, file=file)}',
                file=file,
            )

    fk_paths = _all_fk_paths(source, target)
    if fk_paths:
        found = True
        label = 'FK edge' + ('s' if len(fk_paths) > 1 else '')
        print(c(f'-- {label}: {source}  ->  {target} --', BOLD, file=file), file=file)
        for path in fk_paths:
            for from_t, src_cols, tgt_t, tgt_cols in path:
                _print_fk_step(from_t, src_cols, tgt_t, tgt_cols)
            if len(fk_paths) > 1:
                print(file=file)

    # --- 2. Bridge column chains (BridgeColumnDef) ---
    # A bridge chain on 'source' that passes through 'target' at some hop.
    def _render_chain(hops: list[tuple], val_col: str, from_table: str) -> None:
        prev = from_table
        for via_col, bridge_tbl, bridge_pk in hops:
            print(
                f'    {c(prev, CYAN, file=file)}.{c(via_col, YELLOW, file=file)}'
                f'  ->  {c(bridge_tbl, CYAN, file=file)}.{c(bridge_pk, GREEN, file=file)}',
                file=file,
            )
            prev = bridge_tbl
        print(
            f'    {c("value:", DIM, file=file)} {c(prev, CYAN, file=file)}.{c(val_col, GREEN, file=file)}',
            file=file,
        )

    bridge_hits = [
        bc for bc in schema.bridge_columns
        if bc.table_name == source and any(bt == target for _, bt, _ in bc.hops)
    ]
    if bridge_hits:
        found = True
        print(c(f'-- bridge columns: {source}  ->  {target} --', BOLD, file=file), file=file)
        for bc in bridge_hits:
            print(
                f'  {c(bc.table_name, CYAN, file=file)}.{c(bc.column_name, YELLOW, file=file)}'
                f'  (primary chain):',
                file=file,
            )
            _render_chain(bc.hops, bc.bridge_value_column, bc.table_name)
            for i, (fb_hops, fb_val) in enumerate(bc.fallback_chains, 1):
                print(f'  {c(f"fallback {i}:", DIM, file=file)}', file=file)
                _render_chain(fb_hops, fb_val, bc.table_name)

    if not found:
        print(
            c(f'no FK or bridge path from {source!r} to {target!r}', RED, file=file),
            file=file,
        )
        # Show partial links (unresolved DDLm Link items) as a hint.
        partials = [
            pl for pl in schema.partial_links
            if pl.source_table == source and pl.target_table == target
        ]
        if partials:
            print(
                c(f'-- partial links: {source}  ~>  {target} (incomplete FK) --', BOLD, file=file),
                file=file,
            )
            for pl in partials:
                print(
                    f'  {c(pl.source_table, CYAN, file=file)}.{c(pl.source_column, YELLOW, file=file)}'
                    f'  ~>  {c(pl.target_table, CYAN, file=file)}.{c(pl.target_column, GREEN, file=file)}',
                    file=file,
                )
                covered = ', '.join(pl.covered_pk_cols) or '(none)'
                missing = ', '.join(pl.missing_pk_cols)
                print(
                    f'    {c("covered PKs:", DIM, file=file)} {covered}'
                    f'  {c("missing PKs:", DIM, file=file)} {c(missing, RED, file=file)}',
                    file=file,
                )
                print(f'    {c("reason:", DIM, file=file)} {pl.reason}', file=file)

    return found


if __name__ == '__main__':
    from pathlib import Path
    p = Path(r"C:\Users\User\Documents\github\pycifparse\data\dictionaries\testing\cif_core.dic")
    inspect_schema(p)
