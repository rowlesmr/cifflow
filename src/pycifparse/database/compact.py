"""
compactify_database — one-way export that removes empty tables and columns.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pycifparse.dictionary.schema import SchemaSpec

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
                null_clause = '' if col.nullable else ' NOT NULL'
                col_defs.append(f'    "{col.name}"  TEXT{null_clause}')

            # Primary key
            pk_cols = [c for c in table.primary_keys if c in col_set]
            if pk_cols:
                col_defs.append(
                    '    PRIMARY KEY ('
                    + ', '.join(f'"{c}"' for c in pk_cols)
                    + ')'
                )

            # FK constraints — only when target table and column are kept
            for fk in table.foreign_keys:
                if fk.source_column not in col_set:
                    continue
                if fk.target_table not in tables_with_rows:
                    continue
                if fk.target_column not in kept_columns.get(fk.target_table, ()):
                    continue
                col_defs.append(
                    f'    FOREIGN KEY ("{fk.source_column}") '
                    f'REFERENCES "{fk.target_table}" ("{fk.target_column}") '
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
