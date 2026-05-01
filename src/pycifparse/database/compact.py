"""
convert_database — one-way export that casts DuckDB columns to typed storage.
"""

from __future__ import annotations

import json as _json
import re
from typing import TYPE_CHECKING, Literal

import duckdb

if TYPE_CHECKING:
    from pycifparse.dictionary.schema import ColumnDef, SchemaSpec

# Matches a trailing SU suffix of the form (digits), e.g. '1.23(5)' or '100(3)'.
_SU_RE = re.compile(r'\(\d+\)$')

# Fallback-tier table names that are always copied.
_FALLBACK_TABLES = (
    '_cif_fallback', '_block_dataset_membership', '_validation_result', '_block_order',
    '_tag_presence', '_metatable',
)


# ---------------------------------------------------------------------------
# Helpers for convert_database
# ---------------------------------------------------------------------------

def _sql_type_for(col: 'ColumnDef') -> str:
    """Return the DuckDB type keyword for *col*."""
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
    """Return the cast type for scalar leaves inside a JSON container column."""
    tc = (col.type_contents or '').strip()
    if tc == 'Integer':
        return 'INTEGER'
    if tc in ('Real', 'Float'):
        return 'DOUBLE'
    return 'VARCHAR'


def _cast_scalar(
    raw: str,
    leaf_type: str,
    tbl: str,
    col_name: str,
    on_failure: str,
    messages: list[str],
) -> object:
    """Cast a single scalar *raw* string to *leaf_type*."""
    if raw in ('.', '?'):
        return None

    if leaf_type == 'VARCHAR':
        return raw

    stripped = _SU_RE.sub('', raw)
    if stripped != raw:
        messages.append(
            f"SU dropped: {tbl!r}.{col_name!r} = {raw!r} -> {stripped!r}"
        )

    try:
        if leaf_type == 'INTEGER':
            return int(stripped)
        else:  # DOUBLE
            return float(stripped)
    except (ValueError, TypeError):
        msg = (
            f"coercion failed: {tbl!r}.{col_name!r} = {raw!r} "
            f"could not be cast to {leaf_type}"
        )
        if on_failure == 'error':
            raise ValueError(msg) from None
        messages.append(msg)
        if on_failure == 'keep':
            # DuckDB enforces column types; a non-castable string cannot be
            # stored in an INTEGER/DOUBLE column.  Store NULL and note it.
            messages.append(
                f"coercion failed: {tbl!r}.{col_name!r} = {raw!r} stored as NULL"
                f" (keep unsupported for {leaf_type} columns in DuckDB)"
            )
            return None
        return None


def _cast_json_leaves(obj: object, leaf_type: str, tbl: str, col_name: str,
                      on_failure: str, messages: list[str]) -> object:
    """Recursively cast every string leaf in *obj*."""
    if isinstance(obj, list):
        return [_cast_json_leaves(v, leaf_type, tbl, col_name, on_failure, messages)
                for v in obj]
    if isinstance(obj, dict):
        return {k: _cast_json_leaves(v, leaf_type, tbl, col_name, on_failure, messages)
                for k, v in obj.items()}
    if isinstance(obj, str):
        return _cast_scalar(obj, leaf_type, tbl, col_name, on_failure, messages)
    return obj


def _cast_value(
    raw: str,
    sql_type: str,
    leaf_type: str,
    tbl: str,
    col_name: str,
    on_failure: str,
    messages: list[str],
) -> object:
    """Cast *raw* to the Python type matching *sql_type*."""
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

    Parameters
    ----------
    src:
        Source DuckDB connection populated by ``ingest()``.
    dst:
        Destination DuckDB connection (must be empty).
    schema:
        ``SchemaSpec`` used when *src* was populated.
    on_coercion_failure:
        Policy for non-castable values: ``'null'``, ``'keep'``, or ``'error'``.

    Returns
    -------
    list[str]
        Warning messages: SU-dropped values and coercion failures.
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

    dst.begin()
    try:
        # Structured tables with typed DDL
        for tbl_name in ordered_tables:
            table = schema.tables[tbl_name]
            cols = table.columns
            col_names = [c.name for c in cols]
            sql_types  = {c.name: _sql_type_for(c)  for c in cols}
            leaf_types = {c.name: _leaf_sql_type(c) for c in cols}

            col_defs: list[str] = []
            for col in cols:
                sql_type = sql_types[col.name]
                null_clause = '' if col.nullable else ' NOT NULL'
                col_defs.append(f'    "{col.name}"  {sql_type}{null_clause}')

            pk_cols = table.primary_keys
            if pk_cols:
                col_defs.append(
                    '    PRIMARY KEY ('
                    + ', '.join(f'"{c}"' for c in pk_cols)
                    + ')'
                )

            dst.execute(
                f'CREATE TABLE "{tbl_name}" (\n'
                + ',\n'.join(col_defs)
                + '\n)'
            )

            cursor = src.execute(
                f'SELECT {", ".join(f"{chr(34)}{c}{chr(34)}" for c in col_names)} FROM "{tbl_name}"'
            )
            rows = cursor.fetchall()
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
                dst.executemany(
                    f'INSERT INTO "{tbl_name}" ({col_list_sql}) VALUES ({placeholders})',
                    casted,
                )

        # Fallback-tier tables copied verbatim (VARCHAR storage)
        for tbl in _FALLBACK_TABLES:
            try:
                cursor = src.execute(f'SELECT * FROM "{tbl}" LIMIT 0')
                cols = [d[0] for d in cursor.description]
            except Exception:
                continue

            # Get full column info from src for DDL
            try:
                info_rows = src.execute(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_name = ? ORDER BY ordinal_position",
                    [tbl],
                ).fetchall()
            except Exception:
                continue

            if not info_rows:
                continue

            col_ddl = ', '.join(
                f'"{row[0]}" VARCHAR' + ('' if row[2] == 'YES' else ' NOT NULL')
                for row in info_rows
            )
            try:
                dst.execute(f'CREATE TABLE IF NOT EXISTS "{tbl}" ({col_ddl})')
            except Exception:
                pass

            col_list_sql = ', '.join(f'"{c}"' for c in cols)
            rows = src.execute(f'SELECT {col_list_sql} FROM "{tbl}"').fetchall()
            if rows:
                placeholders = ', '.join('?' * len(cols))
                dst.executemany(
                    f'INSERT INTO "{tbl}" ({col_list_sql}) VALUES ({placeholders})',
                    rows,
                )

        dst.commit()

    except Exception:
        dst.rollback()
        raise

    return messages
