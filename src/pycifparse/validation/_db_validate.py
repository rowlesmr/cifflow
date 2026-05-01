"""Database-stage validation for pycifparse."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

import duckdb

from pycifparse.dictionary.schema import ColumnDef, SchemaSpec, TableDef
from pycifparse.validation._db_checks import (
    CheckResult,
    _NULL_LEAF,
    _SENTINELS,
    check_enumeration_range_leaf,
    check_enumeration_states_leaf,
    check_type_container,
    check_type_contents_leaf,
    check_type_dimension,
    extract_leaves,
)

_SYNTHETIC = frozenset({'_block_id', '_row_id', '_pycifparse_id'})


@dataclass
class DbValidationResult:
    table:      str
    column:     str
    tag:        str
    block_id:   str
    row_id:     int
    key_values: dict[str, str | None]
    value:      str
    check:      str
    severity:   Literal['Error', 'Warning']
    message:    str


def _fetchall_dicts(cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def validate_database(
    db: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    *,
    block_id: str | None = None,
    strict_container_nulls: bool = True,
) -> list[DbValidationResult]:
    """
    Validate a DuckDB database against a schema.

    Never raises; unexpected exceptions are returned as 'internal_error' results.
    """
    results: list[DbValidationResult] = []
    try:
        _run_validation(db, schema, block_id, strict_container_nulls, results)
    except Exception as exc:
        results.append(DbValidationResult(
            table='', column='', tag='', block_id='', row_id=0,
            key_values={}, value='',
            check='internal_error', severity='Error',
            message=str(exc),
        ))
    return results


def _run_validation(
    db: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    block_id: str | None,
    strict: bool,
    results: list[DbValidationResult],
) -> None:
    # Step 0 — unknown_tag check
    if block_id is not None:
        rows = _fetchall_dicts(db.execute(
            'SELECT DISTINCT "tag", "_block_id" FROM "_cif_fallback" '
            'WHERE "_block_id" = ? ORDER BY "_block_id", "tag"',
            [block_id],
        ))
    else:
        rows = _fetchall_dicts(db.execute(
            'SELECT DISTINCT "tag", "_block_id" FROM "_cif_fallback" '
            'ORDER BY "_block_id", "tag"',
        ))

    for row in rows:
        tag = row['tag']
        bid = row['_block_id']
        results.append(DbValidationResult(
            table='_cif_fallback',
            column='tag',
            tag=tag,
            block_id=bid,
            row_id=0,
            key_values={},
            value=tag,
            check='unknown_tag',
            severity='Warning',
            message=f"Tag '{tag}' is not defined in the schema and was routed to _cif_fallback",
        ))

    # Steps 1–4 — per-table column checks
    for table_name, table_def in schema.tables.items():
        pk_cols = table_def.primary_keys

        # Step 2 — keyless Set cardinality check
        if pk_cols == ['_pycifparse_id']:
            _check_keyless_cardinality(db, table_name, block_id, results)

        # Non-synthetic PK column names (used to build key_values)
        ns_pks = [pk for pk in pk_cols if pk not in _SYNTHETIC]

        # Steps 3–4 — per-domain-column checks
        for col_def in table_def.columns:
            if col_def.is_synthetic:
                continue
            _check_column(
                db, schema, table_name, col_def, ns_pks,
                block_id, strict, results,
            )


def _check_keyless_cardinality(
    db: duckdb.DuckDBPyConnection,
    table_name: str,
    block_id: str | None,
    results: list[DbValidationResult],
) -> None:
    if block_id is not None:
        rows = _fetchall_dicts(db.execute(
            f'SELECT "_block_id", COUNT(*) AS cnt FROM "{table_name}" '
            f'WHERE "_block_id" = ? GROUP BY "_block_id" HAVING COUNT(*) > 1',
            [block_id],
        ))
    else:
        rows = _fetchall_dicts(db.execute(
            f'SELECT "_block_id", COUNT(*) AS cnt FROM "{table_name}" '
            f'GROUP BY "_block_id" HAVING COUNT(*) > 1',
        ))

    for row in rows:
        bid = row['_block_id']
        count = row['cnt']
        results.append(DbValidationResult(
            table=table_name,
            column='_pycifparse_id',
            tag='',
            block_id=bid,
            row_id=0,
            key_values={},
            value=str(count),
            check='keyless_set_cardinality',
            severity='Error',
            message=(
                f"Keyless Set table '{table_name}' has {count} rows for "
                f"block '{bid}'; expected at most 1"
            ),
        ))


def _check_column(
    db: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    table_name: str,
    col_def: ColumnDef,
    ns_pks: list[str],
    block_id: str | None,
    strict: bool,
    results: list[DbValidationResult],
) -> None:
    col_name = col_def.name
    tag = col_def.definition_id or ''

    pk_select = ', '.join(f'"{pk}"' for pk in ns_pks)
    if pk_select:
        query = (
            f'SELECT "_block_id", "_row_id", {pk_select}, "{col_name}" '
            f'FROM "{table_name}"'
        )
    else:
        query = f'SELECT "_block_id", "_row_id", "{col_name}" FROM "{table_name}"'

    params: list = []
    if block_id is not None:
        query += ' WHERE "_block_id" = ?'
        params = [block_id]

    rows = _fetchall_dicts(db.execute(query, params))

    for row in rows:
        bid = row['_block_id']
        rid = row['_row_id']
        value = row[col_name]

        # Build key_values from non-synthetic PK columns.
        key_values: dict[str, str | None] = {
            schema.column_to_tag.get((table_name, pk), ''): row[pk]
            for pk in ns_pks
        }

        # Sentinel check — skip NULL, '.', '?'
        if value is None or value in _SENTINELS:
            continue

        _apply_checks(
            results, value, col_def, tag,
            table_name, col_name, bid, rid, key_values, strict,
        )


def _apply_checks(
    results: list[DbValidationResult],
    value: str,
    col_def: ColumnDef,
    tag: str,
    table_name: str,
    col_name: str,
    bid: str,
    rid: int,
    key_values: dict[str, str | None],
    strict: bool,
) -> None:
    def _make(check: str, severity: str, message: str, val: str) -> DbValidationResult:
        return DbValidationResult(
            table=table_name, column=col_name, tag=tag,
            block_id=bid, row_id=rid, key_values=key_values,
            value=val, check=check, severity=severity, message=message,
        )

    # Check A
    a_results, block_bce, parsed = check_type_container(value, col_def)
    for check, severity, msg, val in a_results:
        results.append(_make(check, severity, msg, val))

    if block_bce:
        return

    # Check B (non-Single containers only)
    tc = col_def.type_container
    if tc not in (None, 'Single') and parsed is not None:
        for check, severity, msg, val in check_type_dimension(parsed, col_def, strict):
            results.append(_make(check, severity, msg, val))

    # Checks C/D/E — raw value for Single; recursive leaves for containers
    if tc is None or tc == 'Single':
        leaves: list = [value]
    else:
        leaves = extract_leaves(parsed) if parsed is not None else []

    for leaf in leaves:
        if leaf is _NULL_LEAF:
            if strict:
                results.append(_make(
                    'type_contents', 'Error',
                    "Unexpected null element in container", 'null',
                ))
            # Always skip D and E for null leaves.
            continue

        leaf_str = leaf if isinstance(leaf, str) else str(leaf)

        if leaf_str in _SENTINELS:
            continue

        for check, severity, msg, val in check_type_contents_leaf(leaf_str, col_def):
            results.append(_make(check, severity, msg, val))

        for check, severity, msg, val in check_enumeration_range_leaf(leaf_str, col_def):
            results.append(_make(check, severity, msg, val))

        for check, severity, msg, val in check_enumeration_states_leaf(leaf_str, col_def):
            results.append(_make(check, severity, msg, val))
