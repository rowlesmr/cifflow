"""
convert_database — one-way export that casts DuckDB columns to typed storage.
"""

from __future__ import annotations

import json as _json
import re as _re
from typing import TYPE_CHECKING, Literal

import duckdb

if TYPE_CHECKING:
    from cifflow.dictionary.schema import ColumnDef, SchemaSpec

# Fallback-tier table names that are always copied.
_FALLBACK_TABLES = (
    '_cif_fallback', '_block_dataset_membership', '_validation_result', '_block_order',
    '_tag_presence', '_metatable',
)

_INFRA = frozenset({'_cifflow_block_id', '_cifflow_row_id', '_cifflow_id'})


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

def _sql_type_for(col: 'ColumnDef') -> str:
    if col.name == '_cifflow_row_id':
        return 'INTEGER'
    if col.is_synthetic:
        return 'VARCHAR'
    if col.type_container and col.type_container.lower() != 'single':
        return 'VARCHAR'
    tc = (col.type_contents or '').strip()
    if tc == 'Integer':
        return 'INTEGER'
    if tc in ('Real', 'Float'):
        return 'DOUBLE'
    return 'VARCHAR'


def _leaf_sql_type(col: 'ColumnDef') -> str:
    tc = (col.type_contents or '').strip()
    if tc == 'Integer':
        return 'INTEGER'
    if tc in ('Real', 'Float'):
        return 'DOUBLE'
    return 'VARCHAR'


# ---------------------------------------------------------------------------
# SQL-path helpers
# ---------------------------------------------------------------------------

def _sql_cast_expr(col_name: str, sql_type: str, leaf_type: str, cast_fn: str) -> str:
    """SQL SELECT expression that casts a VARCHAR source column to its target type.

    *cast_fn* is ``'TRY_CAST'`` (null on failure) or ``'CAST'`` (raise on failure).
    ``_cifflow_row_id`` is already INTEGER in src and is passed through unchanged.
    """
    if col_name == '_cifflow_row_id':
        return f'"{col_name}"'
    q = f'"{col_name}"'
    sentinel = f"{q} IN ('.', '?')"

    if sql_type == 'VARCHAR' and leaf_type == 'VARCHAR':
        # Plain string column — only strip sentinels.
        return f'CASE WHEN {sentinel} THEN NULL ELSE {q} END'

    if sql_type == 'VARCHAR' and leaf_type != 'VARCHAR':
        # JSON container column with numeric leaves.
        # Strip all SU suffixes (nn) from the raw JSON string, parse as a typed
        # array via from_json, then re-serialise to VARCHAR with to_json.
        # The LIKE guard routes plain scalar fallbacks through without JSON parsing.
        json_leaf = 'DOUBLE' if leaf_type == 'DOUBLE' else 'INTEGER'
        su_stripped = f"regexp_replace({q}, '\\(\\d+\\)', '', 'g')"
        parsed = f"from_json({su_stripped}, '\"VARCHAR[]\"')"
        cast_elem = f"list_transform({parsed}, x -> {cast_fn}(x AS {json_leaf}))"
        return (
            f'CASE WHEN {sentinel} THEN NULL'
            f" WHEN {q} LIKE '[%' THEN to_json({cast_elem})::VARCHAR"
            f' ELSE {q} END'
        )

    # Scalar numeric column.
    # Only apply regexp_replace when the value contains a parenthesis (SU suffix).
    stripped = f"regexp_replace({q}, '\\(\\d+\\)$', '')"
    has_su   = f"{q} LIKE '%(%'"
    direct   = f'{cast_fn}({q} AS {sql_type})'
    su_cast  = f'{cast_fn}({stripped} AS {sql_type})'
    return (
        f'CASE WHEN {sentinel} THEN NULL'
        f' WHEN {has_su} THEN {su_cast}'
        f' ELSE {direct} END'
    )


def _scan_for_su(
    src: duckdb.DuckDBPyConnection,
    tbl_name: str,
    cols: list,
    sql_types: dict[str, str],
    leaf_types: dict[str, str],
) -> list[str]:
    """Return 'SU dropped' messages for any SU-suffixed values in *src*."""
    msgs: list[str] = []
    for col in cols:
        col_name = col.name
        if col_name == '_cifflow_row_id':
            continue
        sql_type = sql_types[col_name]
        leaf_type = leaf_types[col_name]
        if sql_type == 'VARCHAR' and leaf_type == 'VARCHAR':
            continue
        if sql_type == 'VARCHAR':
            # JSON container with numeric leaves: check each JSON element.
            rows = src.execute(f"""
                SELECT "{col_name}" FROM "{tbl_name}"
                WHERE "{col_name}" LIKE '[%'
                  AND "{col_name}" LIKE '%(%'
                  AND "{col_name}" NOT IN ('.', '?')
            """).fetchall()
            for (val,) in rows:
                if val is None:
                    continue
                try:
                    elements = _json.loads(val)
                except Exception:
                    continue
                for elem in elements:
                    s = str(elem)
                    if '(' in s:
                        stripped = _re.sub(r'\(\d+\)$', '', s)
                        msgs.append(f"SU dropped: '{s}' → '{stripped}'")
        else:
            # Scalar numeric column.
            rows = src.execute(f"""
                SELECT DISTINCT "{col_name}",
                    regexp_replace("{col_name}", '\\(\\d+\\)$', '')
                FROM "{tbl_name}"
                WHERE "{col_name}" LIKE '%(%'
                  AND "{col_name}" NOT IN ('.', '?')
            """).fetchall()
            for orig, stripped in rows:
                if orig != stripped:
                    msgs.append(f"SU dropped: '{orig}' → '{stripped}'")
    return msgs


def _scan_for_cast_failures(
    src: duckdb.DuckDBPyConnection,
    tbl_name: str,
    cols: list,
    sql_types: dict[str, str],
) -> list[str]:
    """Return 'coercion failed' messages for values that cannot be cast."""
    msgs: list[str] = []
    for col in cols:
        col_name = col.name
        if col_name == '_cifflow_row_id':
            continue
        sql_type = sql_types[col_name]
        if sql_type == 'VARCHAR':
            continue
        rows = src.execute(f"""
            SELECT DISTINCT "{col_name}" FROM "{tbl_name}"
            WHERE "{col_name}" IS NOT NULL
              AND "{col_name}" NOT IN ('.', '?')
              AND "{col_name}" NOT LIKE '%(%'
              AND TRY_CAST("{col_name}" AS {sql_type}) IS NULL
        """).fetchall()
        for (val,) in rows:
            msgs.append(
                f"coercion failed: '{val}' cannot be cast to {sql_type}"
                f" in '{tbl_name}'.'{col_name}'"
            )
    return msgs


def _transfer_arrow(src: duckdb.DuckDBPyConnection,
                    dst: duckdb.DuckDBPyConnection,
                    select_sql: str,
                    tbl_name: str) -> None:
    """Execute *select_sql* against *src*, transfer result into *dst* via Arrow."""
    arrow_tbl = src.execute(select_sql).fetch_arrow_table()
    if arrow_tbl.num_rows == 0:
        return
    dst.register('_conv_tmp', arrow_tbl)
    try:
        dst.execute(f'INSERT INTO "{tbl_name}" SELECT * FROM _conv_tmp')
    finally:
        dst.unregister('_conv_tmp')


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert_database(
    src: duckdb.DuckDBPyConnection,
    dst: duckdb.DuckDBPyConnection,
    schema: 'SchemaSpec',
    on_coercion_failure: Literal['null', 'keep', 'error'] = 'null',
) -> list[str]:
    """Copy *src* into *dst*, casting columns to typed DuckDB storage.

    All columns in the source database are stored as ``VARCHAR`` (the ingest
    layer never writes typed values).  This function creates the destination
    tables with proper ``INTEGER`` / ``DOUBLE`` / ``VARCHAR`` types and casts
    each value on the way across.

    Casting is performed entirely inside DuckDB's SQL engine via ``TRY_CAST``,
    ``regexp_replace``, and ``from_json`` / ``to_json`` for JSON container
    columns.  Destination tables are created without ``NOT NULL`` or
    ``PRIMARY KEY`` constraints; all SQL joins and queries work normally.

    Parameters
    ----------
    src:
        Source DuckDB connection populated by ``ingest()``.
    dst:
        Destination DuckDB connection (must be empty).
    schema:
        ``SchemaSpec`` used when *src* was populated.
    on_coercion_failure:
        ``'null'`` (default) — failed cast → NULL via ``TRY_CAST``.
        ``'keep'``           — same as ``'null'`` (typed columns cannot store
                              non-castable strings; stored as NULL).
        ``'error'``          — raise ``ValueError`` on first failure.

    Returns
    -------
    list[str]
        Warning messages: SU-dropped values and coercion failures (null/keep
        policy only — error policy raises instead of returning).
    """
    messages: list[str] = []

    # Topological sort: FK parents before children.
    all_tables = set(schema.tables)

    def _topo_order(names: set[str]) -> list[str]:
        deps: dict[str, set[str]] = {t: set() for t in names}
        for t in names:
            for fk in schema.tables[t].foreign_keys:
                if fk.target_table in names:
                    deps[t].add(fk.target_table)
        order: list[str] = []
        seen: set[str] = set()

        def _visit(name: str) -> None:
            if name in seen:
                return
            seen.add(name)
            for parent in sorted(deps[name]):
                _visit(parent)
            order.append(name)

        for name in sorted(names):
            _visit(name)
        return order

    ordered_tables = _topo_order(all_tables)

    # Pre-compute column metadata for every table once.
    table_meta: dict[str, tuple] = {}
    for tbl_name in ordered_tables:
        table = schema.tables[tbl_name]
        cols = [c for c in table.columns if not c.is_synthetic or c.name in _INFRA]
        sql_types  = {c.name: _sql_type_for(c)  for c in cols}
        leaf_types = {c.name: _leaf_sql_type(c) for c in cols}
        table_meta[tbl_name] = (cols, sql_types, leaf_types)

    # Phase 1 — DDL: create all destination tables in one transaction.
    dst.begin()
    try:
        for tbl_name in ordered_tables:
            cols, sql_types, _ = table_meta[tbl_name]
            col_defs = [f'    "{c.name}"  {sql_types[c.name]}' for c in cols]
            dst.execute(
                f'CREATE TABLE "{tbl_name}" (\n'
                + ',\n'.join(col_defs)
                + '\n)'
            )

        # Fallback-tier DDL.
        for tbl in _FALLBACK_TABLES:
            try:
                info_rows = src.execute(
                    "SELECT column_name, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_name = ? ORDER BY ordinal_position",
                    [tbl],
                ).fetchall()
            except Exception:
                continue
            if not info_rows:
                continue
            col_ddl = ', '.join(
                f'"{r[0]}" VARCHAR' + ('' if r[1] == 'YES' else ' NOT NULL')
                for r in info_rows
            )
            try:
                dst.execute(f'CREATE TABLE IF NOT EXISTS "{tbl}" ({col_ddl})')
            except Exception:
                pass

        dst.commit()
    except Exception:
        dst.rollback()
        raise

    # Phase 2 — Data: one transaction per populated table, skipping empty ones.
    for tbl_name in ordered_tables:
        if src.execute(f'SELECT 1 FROM "{tbl_name}" LIMIT 1').fetchone() is None:
            continue

        cols, sql_types, leaf_types = table_meta[tbl_name]

        # Detect SU stripping and coercion failures before the transfer.
        messages.extend(_scan_for_su(src, tbl_name, cols, sql_types, leaf_types))
        fail_msgs = _scan_for_cast_failures(src, tbl_name, cols, sql_types)
        if fail_msgs:
            if on_coercion_failure == 'error':
                raise ValueError(fail_msgs[0])
            messages.extend(fail_msgs)

        cast_exprs = [
            _sql_cast_expr(c.name, sql_types[c.name], leaf_types[c.name], 'TRY_CAST')
            for c in cols
        ]
        select_sql = f'SELECT {", ".join(cast_exprs)} FROM "{tbl_name}"'

        dst.begin()
        try:
            try:
                _transfer_arrow(src, dst, select_sql, tbl_name)
            except Exception as exc:
                raise type(exc)(f"converting '{tbl_name}': {exc}") from exc
            dst.commit()
        except Exception:
            dst.rollback()
            raise

    # Fallback-tier data: one transaction per non-empty table.
    for tbl in _FALLBACK_TABLES:
        try:
            cols_fb = [d[0] for d in
                       src.execute(f'SELECT * FROM "{tbl}" LIMIT 0').description]
        except Exception:
            continue
        if src.execute(f'SELECT 1 FROM "{tbl}" LIMIT 1').fetchone() is None:
            continue
        q_cols = ', '.join(f'"{c}"' for c in cols_fb)
        dst.begin()
        try:
            _transfer_arrow(src, dst, f'SELECT {q_cols} FROM "{tbl}"', tbl)
            dst.commit()
        except Exception:
            dst.rollback()

    return messages
