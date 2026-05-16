"""generate_defaults — fill NULL columns with DDLm-defined defaults."""

import duckdb

from cifflow.dictionary.schema import ForeignKeyDef, SchemaSpec


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_str(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def generate_defaults(
    connection: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    max_iterations: int = 32,
) -> int:
    """Fill NULL columns in an ingested database with DDLm-defined defaults.

    Runs a fixed-point loop: each pass fills NULLs using scalar defaults
    (``_enumeration.default``) and keyed defaults (``_enumeration_defaults``
    lookup tables).  The loop repeats until no more rows change or
    *max_iterations* is reached, so that a filled index tag can unlock
    keyed defaults that were previously unresolvable.

    This function operates in-place on *connection* and adds no tracking
    columns.  Call it only after :func:`~cifflow.ingestion.ingest` and before
    any round-trip or fidelity work.

    Parameters
    ----------
    connection:
        Open DuckDB connection containing the ingested schema tables.
    schema:
        Schema descriptor produced by
        :func:`~cifflow.dictionary.schema.generate_schema`.
    max_iterations:
        Maximum number of fill passes before giving up.

    Returns
    -------
    int
        Total number of cells filled across all passes.
    """
    tag_to_col: dict[str, tuple[str, str]] = {
        v: k for k, v in schema.column_to_tag.items()
    }

    all_ops, keyed_ops = _build_fill_ops(schema, tag_to_col, connection)

    if not all_ops:
        return 0

    # Wrap in one transaction to avoid per-statement WAL flushes on file DBs.
    try:
        connection.execute('BEGIN TRANSACTION')
        own_txn = True
    except duckdb.Error:
        own_txn = False

    total_filled = 0
    try:
        for i in range(max_iterations):
            # Scalar defaults are monotone — once applied they never change.
            # After the first pass, only keyed ops need re-running.
            ops = all_ops if i == 0 else keyed_ops
            pass_filled = sum(op(connection) for op in ops)
            total_filled += pass_filled
            if pass_filled == 0:
                break
    except Exception:
        if own_txn:
            try:
                connection.execute('ROLLBACK')
            except duckdb.Error:
                pass
        raise
    else:
        if own_txn:
            connection.execute('COMMIT')

    return total_filled


def _build_fill_ops(
    schema: SchemaSpec,
    tag_to_col: dict[str, tuple[str, str]],
    connection: duckdb.DuckDBPyConnection,
) -> tuple[list, list]:
    """Return ``(all_ops, keyed_ops)``.

    *all_ops* preserves the per-table ordering (keyed before scalar) so that
    keyed defaults take priority over scalar defaults when both apply.
    *keyed_ops* is the subset used in iterations after the first.
    """
    all_ops: list = []
    keyed_ops: list = []

    for tbl_name, table in schema.tables.items():
        # --- keyed defaults: one UPDATE per column (higher priority) ---
        for col in table.columns:
            if col.is_synthetic:
                continue
            keyed = _make_keyed_op(tbl_name, col, table, schema, tag_to_col, connection)
            if keyed is not None:
                all_ops.append(keyed)
                keyed_ops.append(keyed)

        # --- scalar defaults: one UPDATE covering ALL columns in this table ---
        scalar_cols = [
            col for col in table.columns
            if not col.is_synthetic and col.enumeration_default is not None
        ]
        if scalar_cols:
            set_parts = [
                f'{_qi(c.name)} = COALESCE({_qi(c.name)}, ?)'
                for c in scalar_cols
            ]
            where_parts = [f'{_qi(c.name)} IS NULL' for c in scalar_cols]
            sql = (
                f'UPDATE {_qi(tbl_name)} SET {", ".join(set_parts)} '
                f'WHERE {" OR ".join(where_parts)}'
            )
            params = [c.enumeration_default for c in scalar_cols]
            all_ops.append(_make_exec(sql, params))

    return all_ops, keyed_ops


def _make_exec(sql: str, params: list):
    def _op(conn: duckdb.DuckDBPyConnection) -> int:
        conn.execute(sql, params)
        return conn.fetchone()[0]
    return _op


def _make_keyed_op(tbl_name, col, table, schema: SchemaSpec, tag_to_col, connection):
    """Build an UPDATE for all key entries of *col* backed by a temp table, or None."""
    if not col.enumeration_def_index_ids or not col.enumeration_defaults:
        return None

    # Resolve each index tag to (res_tbl, res_col).
    resolved: list[tuple[str, str]] = []
    for idx_tag in col.enumeration_def_index_ids:
        canonical = schema.alias_to_definition_id.get(idx_tag, idx_tag)
        loc = tag_to_col.get(canonical)
        if loc is None:
            return None
        resolved.append(loc)

    # Find FKs for any foreign-table index tags.
    foreign_fks: dict[str, ForeignKeyDef] = {}
    for res_tbl, _ in resolved:
        if res_tbl != tbl_name and res_tbl not in foreign_fks:
            fk = next((f for f in table.foreign_keys if f.target_table == res_tbl), None)
            if fk is None:
                return None
            foreign_fks[res_tbl] = fk

    n_keys = len(resolved)
    valid_entries = [
        (kc, dv) for kc, dv in col.enumeration_defaults if len(kc) == n_keys
    ]
    if not valid_entries:
        return None

    # Pre-load defaults into a temp table created once at build time.
    # Each iteration JOINs against this table instead of re-sending the full
    # VALUES list on every pass.  Values are embedded as SQL literals to avoid
    # the Python-to-C parameter-marshaling overhead for large lists.
    key_aliases = [f'_key{i}' for i in range(n_keys)]
    all_aliases = key_aliases + ['_dv']
    alias_list = ', '.join(_qi(a) for a in all_aliases)

    value_rows = [
        '(' + ', '.join(_sql_str(v) for v in (*kc, dv)) + ')'
        for kc, dv in valid_entries
    ]
    values_sql = 'VALUES ' + ', '.join(value_rows)

    # Double-underscore separator: CIF table/column names only use [a-z0-9_].
    temp_name = f'_def_{tbl_name}__{col.name}'
    temp_sql = (
        f'CREATE TEMP TABLE IF NOT EXISTS {_qi(temp_name)} AS '
        f'SELECT * FROM ({values_sql}) AS _t ({alias_list})'
    )
    connection.execute(temp_sql)

    # Per-iteration UPDATE: simple JOIN against the temp table, no inline params.
    where_parts = [f'{_qi(tbl_name)}.{_qi(col.name)} IS NULL']
    for res_tbl, fk in foreign_fks.items():
        for src_col, tgt_col in zip(fk.source_columns, fk.target_columns):
            where_parts.append(
                f'{_qi(tbl_name)}.{_qi(src_col)} = {_qi(res_tbl)}.{_qi(tgt_col)}'
            )
    for i, (res_tbl, res_col) in enumerate(resolved):
        where_parts.append(
            f'{_qi(res_tbl)}.{_qi(res_col)} = {_qi(temp_name)}.{_qi(key_aliases[i])}'
        )

    from_parts = [_qi(temp_name)]
    from_parts += [_qi(t) for t in foreign_fks]

    update_sql = (
        f'UPDATE {_qi(tbl_name)} SET {_qi(col.name)} = {_qi(temp_name)}.{_qi("_dv")} '
        f'FROM {", ".join(from_parts)} '
        f'WHERE {" AND ".join(where_parts)}'
    )
    return _make_exec(update_sql, [])
