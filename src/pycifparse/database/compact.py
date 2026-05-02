"""
convert_database — one-way export that casts DuckDB columns to typed storage.
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING, Literal

import duckdb

if TYPE_CHECKING:
    from pycifparse.dictionary.schema import ColumnDef, SchemaSpec

# Fallback-tier table names that are always copied.
_FALLBACK_TABLES = (
    '_cif_fallback', '_block_dataset_membership', '_validation_result', '_block_order',
    '_tag_presence', '_metatable',
)

_INFRA = frozenset({'_block_id', '_row_id', '_pycifparse_id'})


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

def _sql_type_for(col: 'ColumnDef') -> str:
    if col.name == '_row_id':
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
# SQL-path helpers (fast: casting done inside DuckDB engine)
# ---------------------------------------------------------------------------

def _sql_cast_expr(col_name: str, sql_type: str, cast_fn: str) -> str:
    """SQL SELECT expression that casts a VARCHAR source column to *sql_type*.

    *cast_fn* is ``'TRY_CAST'`` (null on failure) or ``'CAST'`` (raise on failure).
    ``_row_id`` is already INTEGER in src and is passed through unchanged.
    """
    if col_name == '_row_id':
        return f'"{col_name}"'
    q = f'"{col_name}"'
    sentinel = f"{q} IN ('.', '?')"
    if sql_type == 'VARCHAR':
        return f'CASE WHEN {sentinel} THEN NULL ELSE {q} END'
    # Numeric: only apply regexp_replace (slow) when the value actually contains
    # a parenthesis — i.e. has an SU suffix like '1.23(5)'.  The LIKE pre-filter
    # is a fast vectorised string scan; most values won't match and take the
    # cheaper direct-cast branch.
    stripped = f"regexp_replace({q}, '\\(\\d+\\)$', '')"
    has_su   = f"{q} LIKE '%(%'"
    direct   = f'{cast_fn}({q} AS {sql_type})'
    su_cast  = f'{cast_fn}({stripped} AS {sql_type})'
    return (
        f'CASE WHEN {sentinel} THEN NULL'
        f' WHEN {has_su} THEN {su_cast}'
        f' ELSE {direct} END'
    )


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
# Python-path helpers (slow: used only for JSON containers with numeric leaves)
# ---------------------------------------------------------------------------

import re as _re
import time as _time
_SU_RE = _re.compile(r'\(\d+\)$')


def _cast_scalar(raw, leaf_type, tbl, col_name, on_failure, messages):
    if raw in ('.', '?'):
        return None
    if leaf_type == 'VARCHAR':
        return raw
    stripped = _SU_RE.sub('', raw)
    if stripped != raw:
        messages.append(f"SU dropped: {tbl!r}.{col_name!r} = {raw!r} -> {stripped!r}")
    try:
        return int(stripped) if leaf_type == 'INTEGER' else float(stripped)
    except (ValueError, TypeError):
        msg = (f"coercion failed: {tbl!r}.{col_name!r} = {raw!r} "
               f"could not be cast to {leaf_type}")
        if on_failure == 'error':
            raise ValueError(msg) from None
        messages.append(msg)
        if on_failure == 'keep':
            messages.append(f"coercion failed: {tbl!r}.{col_name!r} = {raw!r} stored as NULL"
                            f" (keep unsupported for {leaf_type} columns in DuckDB)")
        return None


def _cast_json_leaves(obj, leaf_type, tbl, col_name, on_failure, messages):
    if isinstance(obj, list):
        return [_cast_json_leaves(v, leaf_type, tbl, col_name, on_failure, messages)
                for v in obj]
    if isinstance(obj, dict):
        return {k: _cast_json_leaves(v, leaf_type, tbl, col_name, on_failure, messages)
                for k, v in obj.items()}
    if isinstance(obj, str):
        return _cast_scalar(obj, leaf_type, tbl, col_name, on_failure, messages)
    return obj


def _cast_value(raw, sql_type, leaf_type, tbl, col_name, on_failure, messages):
    if not isinstance(raw, str):
        return raw
    if raw in ('.', '?'):
        return None
    if sql_type == 'VARCHAR' and leaf_type != 'VARCHAR' and raw and raw[0] in ('[', '{'):
        try:
            decoded = _json.loads(raw)
        except _json.JSONDecodeError:
            return raw
        casted = _cast_json_leaves(decoded, leaf_type, tbl, col_name, on_failure, messages)
        return _json.dumps(casted, separators=(',', ':'))
    if sql_type == 'VARCHAR':
        return raw
    return _cast_scalar(raw, sql_type, tbl, col_name, on_failure, messages)


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

    Casting is performed inside DuckDB's SQL engine via ``TRY_CAST`` and
    ``regexp_replace`` for the common case (scalar columns).  JSON container
    columns whose leaves need numeric casting fall back to Python-level
    processing.  Destination tables are created without ``NOT NULL`` or
    ``PRIMARY KEY`` constraints; all SQL joins and queries work normally.

    Per-value SU-dropped and coercion-failure warnings are only emitted on
    the Python path (JSON container columns).  The SQL path silently coerces.

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
        ``'error'``          — raise on first failure via ``CAST``.

    Returns
    -------
    list[str]
        Warning messages from the Python path (JSON container coercions).
    """
    messages: list[str] = []

    # SQL function for numeric casting: CAST raises, TRY_CAST silently nulls.
    cast_fn = 'CAST' if on_coercion_failure == 'error' else 'TRY_CAST'

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
    _phase2_times: list[tuple[str, float, int, str]] = []  # (table, seconds, rows, path)
    for tbl_name in ordered_tables:
        # Quick existence check — avoids full scan on empty tables.
        row_count = src.execute(f'SELECT COUNT(*) FROM "{tbl_name}"').fetchone()[0]
        if row_count == 0:
            continue

        cols, sql_types, leaf_types = table_meta[tbl_name]
        col_names = [c.name for c in cols]

        python_cols = {
            c.name for c in cols
            if sql_types[c.name] == 'VARCHAR' and leaf_types[c.name] != 'VARCHAR'
        }

        _t0 = _time.perf_counter()
        dst.begin()
        try:
            if not python_cols:
                # Fast path: casting done inside DuckDB via SQL.
                cast_exprs = [
                    _sql_cast_expr(c.name, sql_types[c.name], cast_fn)
                    for c in cols
                ]
                select_sql = f'SELECT {", ".join(cast_exprs)} FROM "{tbl_name}"'
                try:
                    _transfer_arrow(src, dst, select_sql, tbl_name)
                except Exception as exc:
                    raise type(exc)(f"converting '{tbl_name}': {exc}") from exc

            else:
                # Slow path: JSON containers with numeric leaves.
                q_cols = ', '.join(f'"{c}"' for c in col_names)
                rows = src.execute(f'SELECT {q_cols} FROM "{tbl_name}"').fetchall()
                if rows:
                    casted: list[tuple] = []
                    for row in rows:
                        casted.append(tuple(
                            _cast_value(
                                raw,
                                sql_types[col_names[i]],
                                leaf_types[col_names[i]],
                                tbl_name,
                                col_names[i],
                                on_coercion_failure,
                                messages,
                            ) if raw is not None else None
                            for i, raw in enumerate(row)
                        ))
                    placeholders = ', '.join('?' * len(col_names))
                    col_list_sql = ', '.join(f'"{c}"' for c in col_names)
                    try:
                        dst.executemany(
                            f'INSERT INTO "{tbl_name}" ({col_list_sql}) VALUES ({placeholders})',
                            casted,
                        )
                    except Exception as exc:
                        raise type(exc)(f"inserting into '{tbl_name}': {exc}") from exc

            dst.commit()
        except Exception:
            dst.rollback()
            raise

        _elapsed = _time.perf_counter() - _t0
        _path = 'python' if python_cols else 'sql'
        _phase2_times.append((tbl_name, _elapsed, row_count, _path))
        _py_info = f"  python_cols={sorted(python_cols)}" if python_cols else ""
        print(f"  {tbl_name:<50} {_path:<6} {row_count:>8} rows  {_elapsed:>8.3f}s{_py_info}", flush=True)

    _phase2_times.sort(key=lambda x: x[1], reverse=True)
    print(f"\n{'Table':<50} {'Path':<6} {'Rows':>8} {'Time (s)':>10}")
    print('-' * 78)
    for _tbl, _secs, _rows, _path in _phase2_times[:30]:
        print(f"{_tbl:<50} {_path:<6} {_rows:>8} {_secs:>10.3f}")
    _total = sum(s for _, s, _, _ in _phase2_times)
    print(f"\nTotal (populated tables): {_total:.3f}s  ({len(_phase2_times)} tables)")

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
