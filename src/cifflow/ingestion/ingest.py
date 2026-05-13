"""DuckDB ingestion for CifFile objects."""

from __future__ import annotations

import json
import pathlib
import re
import unicodedata
from typing import Any


def _casefold(s: str) -> str:
    return unicodedata.normalize('NFC', unicodedata.normalize('NFD', s).casefold())

import duckdb
import pyarrow as pa

from cifflow.cifmodel.model import CifBlock, CifFile
from cifflow.dictionary.schema import SchemaSpec
from cifflow.ingestion.duckdb_ingest import (
    _create_infrastructure_tables,
    _non_synthetic_pks,
    create_final_tables,
    flush_table_batches,
    load_block_data,
    propagate_fk_sql,
    setup_duckdb,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class IngestionError(Exception):
    """Raised when one or more semantic errors prevent successful ingestion.

    Parameters
    ----------
    errors
        Ordered list of error message strings.

    Attributes
    ----------
    errors
        Ordered list of error message strings.
    """

    errors: list[str]

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        summary = errors[0] if errors else '(no details)'
        extra = f' (and {len(errors) - 1} more)' if len(errors) > 1 else ''
        super().__init__(f'{len(errors)} semantic error(s): {summary}{extra}')


# ---------------------------------------------------------------------------
# Value encoding
# ---------------------------------------------------------------------------

_CONTAINER_PREFIX = '\x00'
_SU_RE = re.compile(
    r'^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\((\d+)\)$'
)


def encode_value(value: str | list | dict) -> tuple[str | None, str]:
    """Encode a CIF value for storage. Returns (stored_string, value_type_str)."""
    if isinstance(value, list):
        return encode_container(value)
    if isinstance(value, dict):
        return encode_container(value)
    if value in ('.', '?'):
        return value, 'placeholder'
    if value in ('"."', '"?"'):
        return value, 'double_quoted'
    return value, 'string'


def encode_container(value: list | dict) -> tuple[str, str]:
    """Return (stored_string, 'list'|'table') for a CIF container value."""
    def _encode(v: Any) -> Any:
        if isinstance(v, list):
            return [_encode(item) for item in v]
        if isinstance(v, dict):
            return {k: _encode(val) for k, val in v.items()}
        return str(v)
    vtype = 'list' if isinstance(value, list) else 'table'
    return _CONTAINER_PREFIX + json.dumps(_encode(value), ensure_ascii=False), vtype


def decode_container(stored: str) -> list | dict:
    """Decode a stored container string back to a Python list or dict."""
    if stored.startswith(_CONTAINER_PREFIX):
        stored = stored[len(_CONTAINER_PREFIX):]
    return json.loads(stored)


def split_su(raw: str) -> tuple[str, str] | None:
    """Split 'numeric(su)' -> (measurand, scaled_su) or None."""
    m = _SU_RE.match(raw)
    if not m:
        return None
    measurand, su_digits = m.group(1), m.group(2)
    e_match = re.search(r'[eE]([+-]?\d+)$', measurand)
    exponent = int(e_match.group(1)) if e_match else 0
    mantissa = measurand[:e_match.start()] if e_match else measurand
    dot_idx = mantissa.find('.')
    decimal_places = (len(mantissa) - dot_idx - 1) if dot_idx >= 0 else 0
    total_power = exponent - decimal_places
    su_int = int(su_digits)
    if total_power >= 0:
        scaled = str(su_int * (10 ** total_power))
    else:
        abs_power = -total_power
        s = str(su_int)
        if abs_power >= len(s):
            scaled = '0.' + '0' * (abs_power - len(s)) + s
        else:
            pos = len(s) - abs_power
            scaled = s[:pos] + '.' + s[pos:]
    return measurand, scaled


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def build_su_map(schema: SchemaSpec) -> dict[str, str]:
    """Build measurand_def_id -> su_column_name reverse map from schema."""
    result: dict[str, str] = {}
    for table in schema.tables.values():
        for col in table.columns:
            if col.linked_item_id is not None:
                result[col.linked_item_id] = col.name
    return result


def build_tag_to_column(schema: SchemaSpec) -> dict[str, tuple[str, str]]:
    """Invert schema.column_to_tag to canonical_def_id -> (table, col)."""
    return {
        def_id: (tbl, col)
        for (tbl, col), def_id in schema.column_to_tag.items()
    }


# ---------------------------------------------------------------------------
# Dataset / namespace helpers
# ---------------------------------------------------------------------------

_DATASET_TAG = '_audit_dataset.id'
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def _read_dataset_ids(block: CifBlock) -> set[str]:
    if _DATASET_TAG not in block:
        return set()
    return {str(v) for v in block[_DATASET_TAG]}


def _is_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value))


def _select_blocks(cif: CifFile, dataset_id: str | None) -> list[CifBlock]:
    """Return ordered list of blocks to ingest; raises ValueError if incoherent."""
    all_blocks = cif._block_list
    dataset_blocks: list[tuple[CifBlock, set[str]]] = []
    general_blocks: list[CifBlock] = []
    for block in all_blocks:
        ids = _read_dataset_ids(block)
        if ids:
            dataset_blocks.append((block, ids))
        else:
            general_blocks.append(block)
    if dataset_id is not None:
        matching = [b for b, ids in dataset_blocks if dataset_id in ids]
        if not matching:
            raise ValueError(
                f"dataset_id {dataset_id!r} not found in any dataset block"
            )
        selected = {id(b) for b in matching + general_blocks}
        return [b for b in all_blocks if id(b) in selected]
    if dataset_blocks:
        intersection = dataset_blocks[0][1].copy()
        for _, ids in dataset_blocks[1:]:
            intersection &= ids
        if not intersection:
            raise ValueError(
                "CifFile blocks belong to incompatible datasets "
                "(no common _audit_dataset.id); "
                "provide dataset_id= to select one"
            )
    return list(all_blocks)


# ---------------------------------------------------------------------------
# id_regime detection via DuckDB
# ---------------------------------------------------------------------------

_UUID_SQL_RE = (
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
)


def _compute_id_regimes(
    db: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    block_ids: list[str],
    populated: set[str],
) -> dict[str, str]:
    """Query final DuckDB tables to determine id_regime per block.

    Returns 'uuid' when all non-synthetic PK values in a block are UUID-shaped,
    'assumed' otherwise (or when the block has no structured rows).
    """
    non_uuid: set[str] = set()
    has_pk: set[str] = set()
    for tbl_name, table in schema.tables.items():
        if tbl_name not in populated:
            continue
        ns_pks = _non_synthetic_pks(table)
        if not ns_pks:
            continue
        pk_col = ns_pks[0]
        try:
            rows = db.execute(f"""
                SELECT _cifflow_block_id,
                       COUNT(*) FILTER (
                           WHERE NOT regexp_matches("{pk_col}", '{_UUID_SQL_RE}', 'i')
                       ) AS non_uuid_count,
                       COUNT(*) AS total
                FROM "{tbl_name}"
                GROUP BY _cifflow_block_id
            """).fetchall()
            for bid, non_uuid_count, total in rows:
                if total > 0:
                    has_pk.add(bid)
                    if non_uuid_count > 0:
                        non_uuid.add(bid)
        except Exception:
            pass
    return {
        bid: ('assumed' if (bid in non_uuid or bid not in has_pk) else 'uuid')
        for bid in block_ids
    }


# ---------------------------------------------------------------------------
# No-schema fallback path
# ---------------------------------------------------------------------------

def _next_cifflow_row_id(counters: dict[str, int], table: str) -> int:
    val = counters.get(table, 1)
    counters[table] = val + 1
    return val


def _process_block_no_schema(
    block: CifBlock,
    block_id: str,
    row_id_counters: dict[str, int],
    loop_id_counter: int,
    fallback_rows: list[dict],
) -> int:
    """Collect all tags from block into fallback_rows. Returns next loop_id_counter."""
    loop_tag_to_idx: dict[str, int] = {}
    for i, loop_tags in enumerate(block.loops):
        for tag in loop_tags:
            loop_tag_to_idx[tag] = i
    processed_loops: set[int] = set()
    for tag in block.tags:
        if tag in loop_tag_to_idx:
            loop_idx = loop_tag_to_idx[tag]
            if loop_idx in processed_loops:
                continue
            processed_loops.add(loop_idx)
            loop_tags_list = block.loops[loop_idx]
            n_iters = len(block[loop_tags_list[0]]) if loop_tags_list else 0
            for iter_idx in range(n_iters):
                row_id = _next_cifflow_row_id(row_id_counters, '_cif_fallback')
                for col_idx, ltag in enumerate(loop_tags_list):
                    val = block[ltag][iter_idx]
                    stored, vtype = encode_value(val)
                    fallback_rows.append({
                        '_cifflow_block_id': block_id,
                        '_cifflow_row_id': row_id,
                        'tag': _casefold(ltag),
                        'value': stored,
                        'value_type': vtype,
                        'loop_id': loop_id_counter,
                        'col_index': col_idx,
                    })
            loop_id_counter += 1
        else:
            val = block[tag][0]
            stored, vtype = encode_value(val)
            fallback_rows.append({
                '_cifflow_block_id': block_id,
                '_cifflow_row_id': 1,
                'tag': _casefold(tag),
                'value': stored,
                'value_type': vtype,
                'loop_id': None,
                'col_index': None,
            })
    return loop_id_counter


# ---------------------------------------------------------------------------
# DuckDB flush helpers
# ---------------------------------------------------------------------------

def _flush_infra(
    db: duckdb.DuckDBPyConnection,
    fallback_rows: list[dict],
    block_order_rows: list[tuple],
    membership_rows: list[tuple],
    validation_rows: list[tuple],
) -> None:
    if fallback_rows:
        arrow_batch = pa.record_batch({
            '_cifflow_block_id':  pa.array([r['_cifflow_block_id']      for r in fallback_rows], type=pa.string()),
            '_cifflow_row_id':    pa.array([r['_cifflow_row_id']         for r in fallback_rows], type=pa.int32()),
            'tag':        pa.array([r['tag']             for r in fallback_rows], type=pa.string()),
            'value':      pa.array([r.get('value')       for r in fallback_rows], type=pa.string()),
            'value_type': pa.array([r['value_type']      for r in fallback_rows], type=pa.string()),
            'loop_id':    pa.array([r.get('loop_id')     for r in fallback_rows], type=pa.int32()),
            'col_index':  pa.array([r.get('col_index')   for r in fallback_rows], type=pa.int32()),
            'ref_table':  pa.array([r.get('ref_table')   for r in fallback_rows], type=pa.string()),
        })
        db.register('__fb__', arrow_batch)
        db.execute(
            'INSERT INTO "_cif_fallback" '
            '("_cifflow_block_id", "_cifflow_row_id", "tag", "value", "value_type", "loop_id", "col_index", "ref_table") '
            'SELECT "_cifflow_block_id", "_cifflow_row_id", "tag", "value", "value_type", "loop_id", "col_index", "ref_table" '
            'FROM __fb__ ON CONFLICT DO NOTHING'
        )
        db.unregister('__fb__')
    if block_order_rows:
        db.executemany(
            'INSERT INTO "_block_order" ("_cifflow_block_id", "position") VALUES (?, ?) ON CONFLICT DO NOTHING',
            block_order_rows,
        )
    if membership_rows:
        db.executemany(
            'INSERT INTO "_block_dataset_membership" '
            '("_cifflow_block_id", "_audit_dataset_id", "id_regime") VALUES (?, ?, ?) ON CONFLICT DO NOTHING',
            membership_rows,
        )
    if validation_rows:
        db.executemany(
            'INSERT INTO "_validation_result" '
            '("check_name", "severity", "block_id", "detail", "id_regime") VALUES (?, ?, ?, ?, ?)',
            validation_rows,
        )


# ---------------------------------------------------------------------------
# Connection resolution
# ---------------------------------------------------------------------------

def _resolve_db(db: Any) -> duckdb.DuckDBPyConnection:
    if db is None:
        return duckdb.connect()
    if isinstance(db, (str, pathlib.Path)):
        return duckdb.connect(str(db))
    return db


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def ingest(
    cif: CifFile,
    db: duckdb.DuckDBPyConnection | str | pathlib.Path | None = None,
    schema: SchemaSpec | None = None,
    *,
    propagate_fk: bool = False,
    dataset_id: str | None = None,
) -> tuple[duckdb.DuckDBPyConnection, list[str]]:
    """Ingest a parsed CifFile into a DuckDB database.

    Parameters
    ----------
    cif:
        Parsed CifFile from build(). May contain one or more blocks.
    db:
        DuckDB connection target. None (default) creates an in-memory connection.
        str or Path opens (or creates) a file-backed database. An existing
        DuckDBPyConnection is used directly (caller retains ownership).
    schema:
        SchemaSpec used to route tags to structured tables. If None, all tags
        are routed to _cif_fallback.
    propagate_fk:
        When True, non-key FK columns absent from the CIF data inherit their
        value from the FK target already known in the same block.
    dataset_id:
        The _audit_dataset.id value to ingest. When None, auto-detected.
        Raises ``ValueError`` if specified but not found in any dataset block,
        or if None and the file contains blocks belonging to incompatible
        datasets (no common ``_audit_dataset.id``).

    Returns
    -------
    tuple[duckdb.DuckDBPyConnection, list[str]]
        The DuckDB connection and a list of error/warning strings.
    """
    db = _resolve_db(db)
    errors: list[str] = []

    def emit(msg: str, **kw: Any) -> None:
        errors.append(msg)

    blocks = _select_blocks(cif, dataset_id)

    tag_to_column = build_tag_to_column(schema) if schema else {}
    su_map = build_su_map(schema) if schema else {}
    fallback_rows: list[dict] = []

    if schema is not None:
        setup_duckdb(schema, db)
        populated: set[str] = set()
        global_batch: dict[str, list[tuple]] = {}
        all_loop_group_entries: list[tuple] = []
        for position, block in enumerate(blocks):
            fallback, table_batch, blk_entries = load_block_data(
                block, block.name, position, schema, tag_to_column, su_map,
                set(), emit,
            )
            fallback_rows.extend(fallback)
            all_loop_group_entries.extend(blk_entries)
            for tbl, rows in table_batch.items():
                if tbl in global_batch:
                    global_batch[tbl].extend(rows)
                else:
                    global_batch[tbl] = rows
        flush_table_batches(db, global_batch, populated)
        if all_loop_group_entries:
            db.executemany(
                'INSERT INTO "_loop_groups" ("_cifflow_block_id", "table_name", "loop_id", "min_row_id") '
                'VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING',
                all_loop_group_entries,
            )
        propagate_fk_sql(db, schema, tag_to_column, propagate_fk, emit, populated)
        create_final_tables(db, schema, populated, errors)
        id_regimes = _compute_id_regimes(db, schema, [b.name for b in blocks], populated)
    else:
        _create_infrastructure_tables(db)
        id_regimes: dict[str, str] = {}
        row_id_counters: dict[str, int] = {}
        loop_id_counter = 1
        for block in blocks:
            loop_id_counter = _process_block_no_schema(
                block, block.name, row_id_counters, loop_id_counter, fallback_rows,
            )

    block_order_rows = [(block.name, i) for i, block in enumerate(blocks)]
    membership_rows: list[tuple] = []
    for block in blocks:
        dataset_ids = _read_dataset_ids(block)
        if dataset_ids:
            for did in sorted(dataset_ids):
                membership_rows.append((block.name, did, 'dataset'))
        else:
            regime = id_regimes.get(block.name, 'assumed')
            membership_rows.append((block.name, '', regime))

    validation_rows: list[tuple] = []
    for bid, did, regime in membership_rows:
        if regime == 'assumed' and did == '':
            validation_rows.append((
                'uuid_regime', 'Warning', bid,
                f"general block '{bid}' has non-UUID PK values "
                f"(or no structured rows); assumed coherence",
                'assumed',
            ))

    _flush_infra(db, fallback_rows, block_order_rows, membership_rows, validation_rows)

    return db, errors
