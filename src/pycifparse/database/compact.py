"""
compactify_database — one-way export that removes empty tables and columns.
convert_database    — one-way export that casts columns to typed SQLite storage.
"""

from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pycifparse.dictionary.schema import SchemaSpec

# Matches a trailing SU suffix of the form (digits), e.g. '1.23(5)' or '100(3)'.
_SU_RE = re.compile(r'\(\d+\)$')

# Fallback-tier table names that are always copied, never dropped.
_FALLBACK_TABLES = ('_cif_fallback', '_block_dataset_membership', '_validation_result')

_FALLBACK_DDL = [
    (
        'CREATE TABLE IF NOT EXISTS "_cif_fallback" (\n'
        '    "_block_id"   TEXT     NOT NULL,\n'
        '    "_row_id"     INTEGER  NOT NULL,\n'
        '    "tag"         TEXT     NOT NULL,\n'
        '    "value"       TEXT,\n'
        '    "value_type"  TEXT     NOT NULL,\n'
        '    "loop_id"     INTEGER,\n'
        '    "col_index"   INTEGER,\n'
        '    PRIMARY KEY ("_block_id", "_row_id", "tag")\n'
        ')'
    ),
    (
        'CREATE INDEX IF NOT EXISTS "_idx_cif_fallback_tag_block" '
        'ON "_cif_fallback" ("tag", "_block_id")'
    ),
    (
        'CREATE TABLE IF NOT EXISTS "_block_dataset_membership" (\n'
        '    "_block_id"           TEXT  NOT NULL,\n'
        '    "_audit_dataset_id"   TEXT  NOT NULL,\n'
        '    "id_regime"           TEXT  NOT NULL,\n'
        '    PRIMARY KEY ("_block_id", "_audit_dataset_id")\n'
        ')'
    ),
    (
        'CREATE TABLE IF NOT EXISTS "_validation_result" (\n'
        '    "check_name"  TEXT  NOT NULL,\n'
        '    "severity"    TEXT  NOT NULL,\n'
        '    "block_id"    TEXT,\n'
        '    "detail"      TEXT,\n'
        '    "id_regime"   TEXT\n'
        ')'
    ),
]


def compactify_database(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    schema: 'SchemaSpec',
) -> list[str]:
    """Copy *src* into *dst*, dropping empty tables and all-NULL columns.

    *src* must be a database previously populated by ``ingest()``.
    *dst* must be an empty, open connection.  The caller owns both connections.

    A table is dropped when it contains zero rows.  A column is dropped when
    every value in that column is ``NULL`` across all rows, subject to the
    constraints below.

    Columns that are **never** dropped regardless of nullability:

    - Primary-key columns (including ``_block_id``, ``_row_id``,
      ``_pycifparse_id``).
    - Synthetic columns (``_block_id``, ``_row_id``, ``_pycifparse_id``).

    FK constraints are preserved only when both the source and target tables
    are kept and the target column is also kept.  If a FK's target table is
    dropped, the constraint is omitted from the destination DDL.

    The three fallback-tier tables (``_cif_fallback``,
    ``_block_dataset_membership``, ``_validation_result``) are always copied
    with their full schema, regardless of whether they contain rows.

    Parameters
    ----------
    src:
        Source connection (schema + data already applied).
    dst:
        Destination connection (must be empty).
    schema:
        ``SchemaSpec`` used when *src* was populated.

    Returns
    -------
    list[str]
        Info messages describing every dropped table and column, in order.
    """
    messages: list[str] = []

    # ------------------------------------------------------------------
    # 1. Determine which structured tables have rows
    # ------------------------------------------------------------------
    tables_with_rows: set[str] = set()
    for tbl_name in schema.tables:
        count = src.execute(
            f'SELECT COUNT(*) FROM "{tbl_name}"'
        ).fetchone()[0]
        if count > 0:
            tables_with_rows.add(tbl_name)
        else:
            messages.append(f"dropped table: {tbl_name!r} (0 rows)")

    # Topological sort: parent tables must be created before child tables so
    # that FK references resolve when foreign_keys=ON is active in dst.
    def _topo_order(kept: set[str]) -> list[str]:
        deps: dict[str, set[str]] = {t: set() for t in kept}
        for t in kept:
            for fk in schema.tables[t].foreign_keys:
                if fk.target_table in kept:
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

        for name in sorted(kept):
            _visit(name)
        return order

    ordered_tables = _topo_order(tables_with_rows)

    # ------------------------------------------------------------------
    # 2. For each kept table, determine which columns are non-empty
    # ------------------------------------------------------------------
    kept_columns: dict[str, list[str]] = {}  # tbl_name -> ordered list

    for tbl_name in ordered_tables:
        table = schema.tables[tbl_name]
        kept: list[str] = []
        for col in table.columns:
            if col.is_primary_key or col.is_synthetic:
                kept.append(col.name)
                continue
            has_value = src.execute(
                f'SELECT 1 FROM "{tbl_name}" '
                f'WHERE "{col.name}" IS NOT NULL LIMIT 1'
            ).fetchone()
            if has_value:
                kept.append(col.name)
            else:
                messages.append(
                    f"dropped column: {tbl_name!r}.{col.name!r} (all NULL)"
                )
        kept_columns[tbl_name] = kept

    # ------------------------------------------------------------------
    # 3. Apply pragmas and begin transaction on dst
    # ------------------------------------------------------------------
    old_isolation = dst.isolation_level
    dst.isolation_level = None
    dst.execute('PRAGMA foreign_keys = ON')
    dst.execute('PRAGMA journal_mode = WAL')
    dst.execute('BEGIN')

    try:
        # ------------------------------------------------------------------
        # 4. Create and populate structured tables
        # ------------------------------------------------------------------
        for tbl_name in ordered_tables:
            table = schema.tables[tbl_name]
            cols = kept_columns[tbl_name]
            col_set = set(cols)

            # Column definitions
            col_defs: list[str] = []
            for col in table.columns:
                if col.name not in col_set:
                    continue
                sql_type = 'INTEGER' if col.name == '_row_id' else 'TEXT'
                null_clause = '' if col.nullable else ' NOT NULL'
                col_defs.append(f'    "{col.name}"  {sql_type}{null_clause}')

            # Primary key
            pk_cols = [c for c in table.primary_keys if c in col_set]
            if pk_cols:
                col_defs.append(
                    '    PRIMARY KEY ('
                    + ', '.join(f'"{c}"' for c in pk_cols)
                    + ')'
                )

            # FK constraints — only when all FK columns and target table are kept
            for fk in table.foreign_keys:
                if not all(c in col_set for c in fk.source_columns):
                    continue
                if fk.target_table not in tables_with_rows:
                    continue
                tgt_kept = kept_columns.get(fk.target_table, ())
                if not all(c in tgt_kept for c in fk.target_columns):
                    continue
                src_cols = ', '.join(f'"{c}"' for c in fk.source_columns)
                tgt_cols = ', '.join(f'"{c}"' for c in fk.target_columns)
                col_defs.append(
                    f'    FOREIGN KEY ({src_cols}) '
                    f'REFERENCES "{fk.target_table}" ({tgt_cols}) '
                    f'DEFERRABLE INITIALLY DEFERRED'
                )

            # UNIQUE(_block_id, _row_id) when _row_id is not already in the PK
            if '_row_id' in col_set and '_row_id' not in table.primary_keys:
                col_defs.append('    UNIQUE ("_block_id", "_row_id")')

            dst.execute(
                f'CREATE TABLE "{tbl_name}" (\n'
                + ',\n'.join(col_defs)
                + '\n)'
            )

            # Fetch rows from src and insert into dst
            col_list_sql = ', '.join(f'"{c}"' for c in cols)
            rows = src.execute(
                f'SELECT {col_list_sql} FROM "{tbl_name}"'
            ).fetchall()
            if rows:
                placeholders = ', '.join('?' * len(cols))
                dst.executemany(
                    f'INSERT INTO "{tbl_name}" ({col_list_sql}) '
                    f'VALUES ({placeholders})',
                    rows,
                )

        # ------------------------------------------------------------------
        # 5. Copy fallback-tier tables (always, full schema)
        # ------------------------------------------------------------------
        for ddl in _FALLBACK_DDL:
            dst.execute(ddl)

        for tbl in _FALLBACK_TABLES:
            try:
                cols_info = src.execute(
                    f'PRAGMA table_info("{tbl}")'
                ).fetchall()
            except sqlite3.Error:
                continue
            if not cols_info:
                continue
            cols = [row[1] for row in cols_info]
            col_list_sql = ', '.join(f'"{c}"' for c in cols)
            rows = src.execute(
                f'SELECT {col_list_sql} FROM "{tbl}"'
            ).fetchall()
            if rows:
                placeholders = ', '.join('?' * len(cols))
                dst.executemany(
                    f'INSERT INTO "{tbl}" ({col_list_sql}) '
                    f'VALUES ({placeholders})',
                    rows,
                )

        dst.execute('COMMIT')

    except Exception:
        dst.execute('ROLLBACK')
        raise
    finally:
        dst.isolation_level = old_isolation

    return messages


# ---------------------------------------------------------------------------
# Helpers for convert_database
# ---------------------------------------------------------------------------

def _sql_type_for(col: 'ColumnDef') -> str:  # type: ignore[name-defined]
    """Return the SQLite affinity keyword for *col*."""
    if col.name == '_row_id':
        return 'INTEGER'
    if col.is_synthetic:
        return 'TEXT'
    tc = (col.type_contents or '').strip()
    if tc == 'Integer':
        return 'INTEGER'
    if tc in ('Real', 'Float'):
        return 'REAL'
    return 'TEXT'


def _cast_value(
    raw: str,
    sql_type: str,
    tbl: str,
    col_name: str,
    on_failure: str,
    messages: list[str],
) -> object:
    """Cast *raw* to the Python type matching *sql_type*.

    CIF sentinels ``'.'`` and ``'?'`` always become ``None``.
    SU suffixes are stripped before numeric casts (with a warning).
    Failed casts are handled according to *on_failure*.
    """
    if not isinstance(raw, str):
        return raw  # already typed (e.g. _row_id fetched as int from source)

    if raw in ('.', '?'):
        return None

    if sql_type == 'TEXT':
        return raw

    # Strip SU suffix before numeric coercion.
    stripped = _SU_RE.sub('', raw)
    if stripped != raw:
        messages.append(
            f"SU dropped: {tbl!r}.{col_name!r} = {raw!r} -> {stripped!r}"
        )

    try:
        if sql_type == 'INTEGER':
            return int(stripped)
        else:  # REAL
            return float(stripped)
    except (ValueError, TypeError):
        msg = (
            f"coercion failed: {tbl!r}.{col_name!r} = {raw!r} "
            f"could not be cast to {sql_type}"
        )
        if on_failure == 'error':
            raise ValueError(msg) from None
        messages.append(msg)
        if on_failure == 'keep':
            return raw   # leave original TEXT value
        return None      # 'null'


def convert_database(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    schema: 'SchemaSpec',
    on_coercion_failure: Literal['null', 'keep', 'error'] = 'null',
) -> list[str]:
    """Copy *src* into *dst*, casting columns to typed SQLite storage.

    All columns in the source database are stored as ``TEXT`` (the ingest
    layer never writes typed values).  This function creates the destination
    tables with proper ``INTEGER`` / ``REAL`` / ``TEXT`` affinities and casts
    each value on the way across.

    **Type mapping** (from ``ColumnDef.type_contents``):

    +-----------------+------------------+
    | type_contents   | SQLite affinity  |
    +=================+==================+
    | ``"Integer"``   | ``INTEGER``      |
    | ``"Real"``      | ``REAL``         |
    | ``"Float"``     | ``REAL``         |
    | anything else   | ``TEXT``         |
    +-----------------+------------------+

    The synthetic ``_row_id`` column is always ``INTEGER``; ``_block_id`` and
    ``_pycifparse_id`` are always ``TEXT``.

    **Special values:**

    - CIF sentinels ``'.'`` and ``'?'`` are always converted to ``NULL``
      regardless of column type, without a warning.
    - SU suffixes (e.g. ``'1.23(5)'``) are stripped before numeric casting
      and a warning is appended.  The stripped value is then cast normally;
      if that cast also fails, *on_coercion_failure* applies.

    **Coercion failures** (non-sentinel, non-castable values):

    - ``'null'`` (default): store ``NULL``, append a warning message.
    - ``'keep'``: leave the original ``TEXT`` value, append a warning message.
    - ``'error'``: raise ``ValueError`` immediately.

    Unlike ``compactify_database``, this function preserves all tables and
    columns (including empty tables and all-NULL columns).  The two functions
    may be chained: compact first to remove empties, then convert for typing.

    The fallback-tier tables (``_cif_fallback``, ``_block_dataset_membership``,
    ``_validation_result``) are always copied verbatim with ``TEXT`` storage;
    they carry no schema-defined type information.

    Parameters
    ----------
    src:
        Source connection populated by ``ingest()``.
    dst:
        Destination connection (must be empty).
    schema:
        ``SchemaSpec`` used when *src* was populated.
    on_coercion_failure:
        Policy for non-castable values: ``'null'``, ``'keep'``, or
        ``'error'``.

    Returns
    -------
    list[str]
        Warning messages: SU-dropped values and coercion failures (when
        policy is ``'null'`` or ``'keep'``).
    """
    messages: list[str] = []

    # ------------------------------------------------------------------
    # 1. Topological sort (FK parents before children, same as compactify)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 2. Apply pragmas and begin transaction on dst
    # ------------------------------------------------------------------
    old_isolation = dst.isolation_level
    dst.isolation_level = None
    dst.execute('PRAGMA foreign_keys = ON')
    dst.execute('PRAGMA journal_mode = WAL')
    dst.execute('BEGIN')

    try:
        # ------------------------------------------------------------------
        # 3. Create and populate structured tables with typed DDL
        # ------------------------------------------------------------------
        for tbl_name in ordered_tables:
            table = schema.tables[tbl_name]
            cols = table.columns
            col_names = [c.name for c in cols]
            sql_types = {c.name: _sql_type_for(c) for c in cols}

            # Column definitions with proper type affinities
            col_defs: list[str] = []
            for col in cols:
                sql_type = sql_types[col.name]
                null_clause = '' if col.nullable else ' NOT NULL'
                col_defs.append(f'    "{col.name}"  {sql_type}{null_clause}')

            # Primary key
            pk_cols = table.primary_keys
            if pk_cols:
                col_defs.append(
                    '    PRIMARY KEY ('
                    + ', '.join(f'"{c}"' for c in pk_cols)
                    + ')'
                )

            # FK constraints
            col_name_set = set(col_names)
            for fk in table.foreign_keys:
                if not all(c in col_name_set for c in fk.source_columns):
                    continue
                if fk.target_table not in all_tables:
                    continue
                src_cols = ', '.join(f'"{c}"' for c in fk.source_columns)
                tgt_cols = ', '.join(f'"{c}"' for c in fk.target_columns)
                col_defs.append(
                    f'    FOREIGN KEY ({src_cols}) '
                    f'REFERENCES "{fk.target_table}" ({tgt_cols}) '
                    f'DEFERRABLE INITIALLY DEFERRED'
                )

            # UNIQUE(_block_id, _row_id) when _row_id not already in PK
            if '_row_id' in col_name_set and '_row_id' not in table.primary_keys:
                col_defs.append('    UNIQUE ("_block_id", "_row_id")')

            dst.execute(
                f'CREATE TABLE "{tbl_name}" (\n'
                + ',\n'.join(col_defs)
                + '\n)'
            )

            # Fetch and cast rows
            col_list_sql = ', '.join(f'"{c}"' for c in col_names)
            rows = src.execute(
                f'SELECT {col_list_sql} FROM "{tbl_name}"'
            ).fetchall()
            if rows:
                casted: list[tuple] = []
                for row in rows:
                    casted.append(tuple(
                        _cast_value(
                            raw,
                            sql_types[col_names[i]],
                            tbl_name,
                            col_names[i],
                            on_coercion_failure,
                            messages,
                        ) if raw is not None else None
                        for i, raw in enumerate(row)
                    ))
                placeholders = ', '.join('?' * len(col_names))
                dst.executemany(
                    f'INSERT INTO "{tbl_name}" ({col_list_sql}) '
                    f'VALUES ({placeholders})',
                    casted,
                )

        # ------------------------------------------------------------------
        # 4. Copy fallback-tier tables verbatim (TEXT storage)
        # ------------------------------------------------------------------
        for ddl in _FALLBACK_DDL:
            dst.execute(ddl)

        for tbl in _FALLBACK_TABLES:
            try:
                cols_info = src.execute(
                    f'PRAGMA table_info("{tbl}")'
                ).fetchall()
            except sqlite3.Error:
                continue
            if not cols_info:
                continue
            cols = [row[1] for row in cols_info]
            col_list_sql = ', '.join(f'"{c}"' for c in cols)
            rows = src.execute(
                f'SELECT {col_list_sql} FROM "{tbl}"'
            ).fetchall()
            if rows:
                placeholders = ', '.join('?' * len(cols))
                dst.executemany(
                    f'INSERT INTO "{tbl}" ({col_list_sql}) '
                    f'VALUES ({placeholders})',
                    rows,
                )

        dst.execute('COMMIT')

    except Exception:
        dst.execute('ROLLBACK')
        raise
    finally:
        dst.isolation_level = old_isolation

    return messages
