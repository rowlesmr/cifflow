"""
SQLite ingestion for CifFile objects.

See prompts/Stage4_Ingestion_Prompt.md for the full specification.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid as _uuid_module
from typing import Any, Callable

from pycifparse.cifmodel.model import CifBlock, CifFile
from pycifparse.cifmodel.scalar import CifScalar
from pycifparse.dictionary.schema import BridgeColumnDef, SchemaSpec, TableDef
from pycifparse.types import ValueType


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class IngestionError(Exception):
    """Raised when one or more semantic errors prevent successful ingestion.

    All errors are collected before raising so that the full set is reported
    in a single pass.  The database may contain partial data from rows that
    preceded the first conflict.

    Attributes
    ----------
    errors:
        Ordered list of error message strings, one per conflict.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        summary = errors[0] if errors else '(no details)'
        extra = f' (and {len(errors) - 1} more)' if len(errors) > 1 else ''
        super().__init__(f'{len(errors)} semantic error(s): {summary}{extra}')


# ---------------------------------------------------------------------------
# Value encoding
# ---------------------------------------------------------------------------

def encode_value(value: CifScalar | list | dict) -> tuple[str | None, str]:
    """Encode a CIF value for SQLite storage.

    Returns ``(stored_string, value_type_str)``.  ``value_type_str`` is only
    used when writing to ``_cif_fallback``; callers writing to structured
    tables may ignore it.

    Applies the Lesson 19 presence-state encoding:
    - PLACEHOLDER ``.`` / ``?``  -> ``'.'`` / ``'?'``
    - Quoted ``.`` / ``?``       -> ``'"."'`` / ``'"?"'``
    - Container                  -> JSON text
    - Anything else              -> raw string
    """
    if isinstance(value, list):
        s, vt = encode_container(value)
        return s, vt
    if isinstance(value, dict):
        s, vt = encode_container(value)
        return s, vt
    # CifScalar
    vt = value.value_type
    raw = str(value)
    if vt == ValueType.PLACEHOLDER:
        return raw, 'placeholder'
    if raw in ('.', '?'):
        # Quoted "." or "?" — store with delimiters to distinguish from PLACEHOLDER
        return f'"{raw}"', vt.value
    return raw, vt.value


# Sentinel prefix that marks a stored value as a JSON-encoded CIF container.
# A null byte cannot appear in any valid CIF string value, so this is unambiguous.
_CONTAINER_PREFIX = '\x00'


def encode_container(value: list | dict) -> tuple[str, str]:
    """Return ``(stored_string, 'list'|'table')`` for a CIF container value.

    The stored string is prefixed with ``_CONTAINER_PREFIX`` so that the output
    layer can identify containers unambiguously without guessing from content.
    """
    def _encode(v: Any) -> Any:
        if isinstance(v, list):
            return [_encode(item) for item in v]
        if isinstance(v, dict):
            return {k: _encode(val) for k, val in v.items()}
        raw = str(v)
        if (raw in ('.', '?')
                and hasattr(v, 'value_type')
                and v.value_type != ValueType.PLACEHOLDER):
            return f'"{raw}"'
        return raw

    vtype = 'list' if isinstance(value, list) else 'table'
    return _CONTAINER_PREFIX + json.dumps(_encode(value), ensure_ascii=False), vtype


def decode_container(stored: str) -> list | dict:
    """Decode a stored container string back to a Python list or dict."""
    if stored.startswith(_CONTAINER_PREFIX):
        stored = stored[len(_CONTAINER_PREFIX):]
    return json.loads(stored)


_SU_RE = re.compile(
    r'^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\((\d+)\)$'
)


def split_su(raw: str) -> tuple[str, str] | None:
    """Split ``'numeric(su)'`` ->``(measurand, scaled_su)`` or ``None``.

    The SU is scaled to the precision of the measurand so that the stored
    value represents the actual uncertainty:
      '3.992(4)'   ->('3.992',   '0.004')
      '1234(5)'    ->('1234',    '5')
      '12.34(56)'  ->('12.34',   '0.56')
      '1.23e-4(5)' ->('1.23e-4', '0.000005')
    """
    m = _SU_RE.match(raw)
    if not m:
        return None
    measurand, su_digits = m.group(1), m.group(2)

    # Strip any exponent from the measurand
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
    """Build ``measurand_def_id ->su_column_name`` reverse map from *schema*."""
    result: dict[str, str] = {}
    for table in schema.tables.values():
        for col in table.columns:
            if col.linked_item_id is not None:
                result[col.linked_item_id] = col.name
    return result


def build_tag_to_column(schema: SchemaSpec) -> dict[str, tuple[str, str]]:
    """Invert ``schema.column_to_tag`` to ``canonical_def_id ->(table, col)``."""
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
    """Return ordered list of blocks to ingest; raises ``ValueError`` if incoherent."""
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
        # Preserve original file order
        selected = {id(b) for b in matching + general_blocks}
        return [b for b in all_blocks if id(b) in selected]

    # Auto-detection: compute intersection of dataset block ID sets
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
# _row_id helpers
# ---------------------------------------------------------------------------

def _next_row_id(counters: dict[str, int], table: str) -> int:
    """Claim the next _row_id for *table* and advance the counter."""
    if table not in counters:
        counters[table] = 1
    val = counters[table]
    counters[table] += 1
    return val


# ---------------------------------------------------------------------------
# Merge algorithm
# ---------------------------------------------------------------------------

def _pk_tuple(row: dict, table: TableDef) -> tuple:
    return tuple(row.get(k) for k in table.primary_keys)


_SYNTHETIC_PK_COLS = frozenset({'_block_id', '_row_id', '_pycifparse_id'})


def _merge_into(
    merged_rows: dict[str, dict[tuple, dict]],
    table_name: str,
    row: dict,
    table: TableDef,
    row_id_counters: dict[str, int],
    emit: Callable[..., None],
    emit_error: Callable[..., None] | None = None,
    block_pk_values: 'dict[str, list[str]] | None' = None,
    pk_keys: 'tuple | None' = None,
) -> int:
    """Merge *row* into *merged_rows[table_name]*.  Returns the row's ``_row_id``.

    When two rows share the same primary key:
    - If all non-PK column values are identical the duplicate is silently dropped.
    - If any value differs AND the rows originate from the same data block,
      *emit_error* is called (a semantic error — ingestion will fail).
    - If the values differ across different data blocks, *emit* is called
      (a warning — cross-block merging is a known limitation).
    """
    if table_name not in merged_rows:
        merged_rows[table_name] = {}
    tbl_rows = merged_rows[table_name]
    pk = tuple(map(row.get, pk_keys)) if pk_keys is not None else _pk_tuple(row, table)

    if pk not in tbl_rows:
        row['_row_id'] = _next_row_id(row_id_counters, table_name)
        tbl_rows[pk] = dict(row)
        if block_pk_values is not None:
            bid = row.get('_block_id')
            if bid is not None:
                entry = block_pk_values.get(bid)
                if entry is None:
                    block_pk_values[bid] = entry = []
                for pk_col in table.primary_keys:
                    if pk_col not in _SYNTHETIC_PK_COLS:
                        v = row.get(pk_col)
                        if v is not None:
                            entry.append(v)
        return row['_row_id']
    else:
        existing = tbl_rows[pk]
        pk_values = dict(zip(table.primary_keys, pk))
        for col, val in row.items():
            if col in ('_row_id', '_block_id'):
                continue
            if val is None:
                continue
            if existing.get(col) is None:
                existing[col] = val
            elif existing[col] != val:
                msg = (
                    f"merge conflict on '{table_name}'.'{col}': "
                    f"keeping '{existing[col]}', ignoring '{val}'"
                )
                if emit_error is not None:
                    emit_error(msg, table=table_name, column=col, key_values=pk_values)
                else:
                    emit(msg, table=table_name, column=col, key_values=pk_values)
        return existing['_row_id']


# ---------------------------------------------------------------------------
# FK propagation
# ---------------------------------------------------------------------------

def _apply_fk(
    row: dict,
    table: TableDef,
    schema: SchemaSpec,
    loop_row_by_defid: dict[str, str] | None,
    fk_accumulator: dict[str, str],
    propagate_fk: bool,
    emit: Callable[..., None],
    block_id: str | None = None,
    merged_rows: dict[str, dict[tuple, dict]] | None = None,
    row_id_counters: dict[str, int] | None = None,
    block_pk_values: 'dict[str, list[str]] | None' = None,
    col_by_name: 'dict[str, object] | None' = None,
) -> None:
    """Fill missing FK/PK columns in *row* in-place.

    When *block_id*, *merged_rows*, and *row_id_counters* are all supplied and
    a UUID must be generated for a key-FK column, a minimal stub row is also
    inserted into the parent table so that deferred FK constraints can be
    satisfied at COMMIT.
    """
    if col_by_name is None:
        col_by_name = {c.name: c for c in table.columns if not c.is_synthetic}

    for fk in table.foreign_keys:
        # Skip FK filling and stub creation when all source cols are already
        # populated and we're not in a loop context that may supply missing vals.
        if (loop_row_by_defid is None
                and all(row.get(sc) is not None for sc in fk.source_columns
                        if col_by_name.get(sc) is not None)):
            continue
        is_composite = len(fk.source_columns) > 1

        if not is_composite:
            # ── Single-column FK ──────────────────────────────────────────────
            src_col = fk.source_columns[0]
            tgt_col = fk.target_columns[0]
            col = col_by_name.get(src_col)
            if col is None:
                continue

            is_key_fk = col.is_primary_key
            val: str | None = row.get(src_col)

            # Value assignment: fill missing FK column from loop/accumulator/UUID
            if val is None:
                if not is_key_fk and not propagate_fk:
                    continue

                target_def_id = schema.column_to_tag.get((fk.target_table, tgt_col))
                if target_def_id is None:
                    if is_key_fk:
                        emit(
                            f"FK target '{fk.target_table}'.'{tgt_col}' "
                            f"is not in structured schema; leaving NULL"
                        )
                    continue

                # Source 1: within-loop
                if loop_row_by_defid is not None:
                    val = loop_row_by_defid.get(target_def_id)
                # Source 2: fk_accumulator
                if val is None:
                    val = fk_accumulator.get(target_def_id)
                # Source 3: UUID fallback (key-FK only)
                if val is None and is_key_fk:
                    val = str(_uuid_module.uuid4())
                    # Persist in accumulator only in scalar context.  In loop
                    # context each iteration must get a fresh UUID so that
                    # rows do not collapse into one via the merge key.
                    if loop_row_by_defid is None:
                        fk_accumulator[target_def_id] = val
                    emit(
                        f"key-FK '{col.definition_id}' propagation source "
                        f"not found; generated UUID"
                    )

                if val is not None:
                    row[src_col] = val

            # Stub creation: ensure parent row exists for deferred FK check
            if (val is not None
                    and merged_rows is not None
                    and row_id_counters is not None
                    and block_id is not None):
                parent_table = schema.tables.get(fk.target_table)
                if parent_table is not None:
                    stub_pk_keys = tuple(parent_table.primary_keys)
                    stub: dict = {'_block_id': block_id, tgt_col: val}
                    stub_pk = tuple(map(stub.get, stub_pk_keys))
                    tbl_rows = merged_rows.get(fk.target_table)
                    if tbl_rows is None or stub_pk not in tbl_rows:
                        _merge_into(merged_rows, fk.target_table, stub,
                                    parent_table, row_id_counters, emit,
                                    block_pk_values=block_pk_values,
                                    pk_keys=stub_pk_keys)

        else:
            # ── Composite FK ──────────────────────────────────────────────────
            # All source columns must be non-NULL for the stub to be created.
            # Values come from: CIF data already in the row, loop iter_by_defid,
            # or the fk_accumulator.  UUID generation is not applied to composite
            # FKs — their values must always originate from data.
            is_key_fk = all(
                col_by_name.get(sc) is not None
                and col_by_name[sc].is_primary_key
                for sc in fk.source_columns
            )

            # Fill any missing source columns from loop values or accumulator
            for src_col, tgt_col in zip(fk.source_columns, fk.target_columns):
                if row.get(src_col) is not None:
                    continue
                if not is_key_fk and not propagate_fk:
                    continue
                target_def_id = schema.column_to_tag.get((fk.target_table, tgt_col))
                if target_def_id is None:
                    continue
                val = None
                if loop_row_by_defid is not None:
                    val = loop_row_by_defid.get(target_def_id)
                if val is None:
                    val = fk_accumulator.get(target_def_id)
                # Transitive lookup: follow the single-column FK chain from the
                # target column up to 15 levels.  Covers cases like
                # pd_calc_component ->pd_data.diffractogram_id ->pd_diffractogram.id
                # where only _pd_diffractogram.id is in the fk_accumulator.
                if val is None:
                    _cur_table, _cur_col = fk.target_table, tgt_col
                    for _depth in range(15):
                        _tbl = schema.tables.get(_cur_table)
                        if _tbl is None:
                            break
                        _next = next(
                            (
                                (tfk.target_table, tfk.target_columns[0])
                                for tfk in _tbl.foreign_keys
                                if len(tfk.source_columns) == 1
                                and tfk.source_columns[0] == _cur_col
                            ),
                            None,
                        )
                        if _next is None:
                            break
                        _cur_table, _cur_col = _next
                        trans_def_id = schema.column_to_tag.get((_cur_table, _cur_col))
                        if trans_def_id is not None:
                            if loop_row_by_defid is not None:
                                val = loop_row_by_defid.get(trans_def_id)
                            if val is None:
                                val = fk_accumulator.get(trans_def_id)
                        if val is not None:
                            break
                    else:
                        emit(
                            f"composite FK '{fk.target_table}'.'{tgt_col}': "
                            f"transitive lookup reached depth limit; "
                            f"possible FK cycle — leaving NULL"
                        )
                if val is not None:
                    row[src_col] = val

            # Stub creation: only when all FK columns are present
            if (merged_rows is not None
                    and row_id_counters is not None
                    and block_id is not None):
                col_vals = [
                    (sc, tc, row.get(sc))
                    for sc, tc in zip(fk.source_columns, fk.target_columns)
                ]
                if all(v is not None for _, _, v in col_vals):
                    parent_table = schema.tables.get(fk.target_table)
                    if parent_table is not None:
                        stub = {'_block_id': block_id}
                        for _, tc, v in col_vals:
                            stub[tc] = v
                        stub_pk_keys = tuple(parent_table.primary_keys)
                        stub_pk = tuple(map(stub.get, stub_pk_keys))
                        tbl_rows = merged_rows.get(fk.target_table)
                        if tbl_rows is None or stub_pk not in tbl_rows:
                            _merge_into(merged_rows, fk.target_table, stub,
                                        parent_table, row_id_counters, emit,
                                        block_pk_values=block_pk_values,
                                        pk_keys=stub_pk_keys)

    # ── Propagation links ─────────────────────────────────────────────────────
    # PK columns that are DDLm Link items but have no FK constraint (e.g. the
    # FK was skipped because the target is not a PK of the target table).
    # Fill from loop values / fk_accumulator, then enumeration_default.
    # No UUID fallback: these columns are nullable in the schema, so NULL is
    # valid when no value can be found.
    for col_name, target_def_id, default_val in schema.propagation_links.get(table.name, []):
        if row.get(col_name) is not None:
            continue  # already filled by FK handler or CIF data
        col = col_by_name.get(col_name)
        is_key_col = col is not None and col.is_primary_key
        if not is_key_col and not propagate_fk:
            continue
        val = None
        if loop_row_by_defid is not None:
            val = loop_row_by_defid.get(target_def_id)
        if val is None:
            val = fk_accumulator.get(target_def_id)
        if val is None:
            val = default_val  # e.g. '.' for null-variant convention
        if val is not None:
            row[col_name] = val


# ---------------------------------------------------------------------------
# Bridge column fill
# ---------------------------------------------------------------------------

def _build_chain_lookups(
    hops: list[tuple[str, str, str]],
    bridge_value_column: str,
    merged_rows: dict[str, dict[tuple, dict]],
) -> list[dict[str, str]]:
    """Build one lookup dict per hop for a single resolution chain.

    For hop *i* (not the last), the value column is the ``via_col`` of hop
    *i+1*, so each lookup's output feeds into the next lookup's key.  For the
    final hop the value column is *bridge_value_column*.

    The lookup key is the bridge table's PK value only (no ``_block_id``
    prefix).  ``merged_rows`` is already scoped to a single dataset, so
    cross-dataset contamination cannot occur, and omitting ``_block_id``
    allows resolution to work correctly when source and target rows originate
    from different CIF data blocks within the same dataset.
    """
    hop_lookups: list[dict[str, str]] = []
    for i, (via_col, bridge_tbl, bridge_pk) in enumerate(hops):
        bridge_rows = merged_rows.get(bridge_tbl, {})
        is_last = (i == len(hops) - 1)
        val_col = bridge_value_column if is_last else hops[i + 1][0]
        lookup: dict[str, str] = {}
        for row in bridge_rows.values():
            pk_val = row.get(bridge_pk)
            val = row.get(val_col)
            if pk_val is not None and val is not None:
                lookup[pk_val] = val
        hop_lookups.append(lookup)
    return hop_lookups


def _resolve_chain(
    hop_lookups: list[dict[str, str]],
    first_via_col: str,
    row: dict,
) -> 'str | None':
    """Follow *hop_lookups* from the row's *first_via_col* value.

    Returns the resolved value, or ``None`` if any lookup in the chain misses.
    """
    current: str | None = row.get(first_via_col)
    if current is None:
        return None
    for hop_lookup in hop_lookups:
        current = hop_lookup.get(current)
        if current is None:
            return None
    return current


def _chain_label(hops: list[tuple[str, str, str]], bridge_value_column: str) -> str:
    """Return a readable path string, e.g. 'diffractogram_id ->pd_diffractogram.diffrn_id ->diffrn.diffrn_radiation_id'."""
    parts = [hops[0][0]]
    for i, (_, bridge_tbl, _) in enumerate(hops):
        val_col = bridge_value_column if i == len(hops) - 1 else hops[i + 1][0]
        parts.append(f"{bridge_tbl}.{val_col}")
    return ' -> '.join(parts)


def _diagnose_chain(
    hops: list[tuple[str, str, str]],
    bridge_value_column: str,
    merged_rows: dict,
    start_val: 'str | None',
) -> str:
    """Trace a resolution chain and return a description of the first failure point."""
    if start_val is None:
        return f"starting column '{hops[0][0]}' is NULL"
    current = start_val
    for i, (_, bridge_tbl, bridge_pk) in enumerate(hops):
        val_col = bridge_value_column if i == len(hops) - 1 else hops[i + 1][0]
        found_row = next(
            (r for r in merged_rows.get(bridge_tbl, {}).values()
             if r.get(bridge_pk) == current),
            None,
        )
        if found_row is None:
            return f"no row in '{bridge_tbl}' with '{bridge_pk}' = {current!r}"
        next_val = found_row.get(val_col)
        if next_val is None:
            return f"'{bridge_tbl}'.'{val_col}' is NULL ('{bridge_pk}' = {current!r})"
        current = next_val
    return "ok"


def _fill_bridge_columns(
    merged_rows: dict[str, dict[tuple, dict]],
    bridge_columns: list[BridgeColumnDef],
    emit: 'Callable[..., None] | None' = None,
    emit_info: 'Callable[..., None] | None' = None,
) -> None:
    """Populate bridge-derived columns in *merged_rows* in place.

    For each :class:`BridgeColumnDef`, resolves the derived column value by
    trying all chains (primary + fallbacks) and collecting every non-None
    result.  If all chains agree the common value is used.  If chains disagree,
    *emit* is called with a warning and the first non-None result is used.
    If no chain resolves a value, *emit_info* is called to report the gap.
    Must be called after all blocks have been processed and before
    :py:meth:`_Ingester._flush`.
    """
    for bd in bridge_columns:
        if bd.table_name not in merged_rows:
            continue

        # Pre-build lookup dicts for every chain (primary + fallbacks).
        all_chains: list[tuple[list, str]] = (
            [(bd.hops, bd.bridge_value_column)] + list(bd.fallback_chains)
        )
        chain_lookups = [
            _build_chain_lookups(hops, val_col, merged_rows)
            for hops, val_col in all_chains
        ]
        chain_via_cols = [hops[0][0] for hops, _ in all_chains]
        first_via_col = bd.hops[0][0]

        for row in merged_rows[bd.table_name].values():
            if row.get(bd.column_name) is not None:
                continue
            resolved: list[str] = []
            for hop_lookups, via_col in zip(chain_lookups, chain_via_cols):
                result = _resolve_chain(hop_lookups, via_col, row)
                if result is not None:
                    resolved.append(result)
            if not resolved:
                if emit_info is not None:
                    via_val = row.get(first_via_col)
                    kv: dict[str, str | None] = {
                        first_via_col: via_val,
                        '_block_id': row.get('_block_id'),
                    }
                    chain_details = '\n'.join(
                        f"  chain {i + 1} ({_chain_label(hops, val_col)}): "
                        f"{_diagnose_chain(hops, val_col, merged_rows, row.get(hops[0][0]))}"
                        for i, (hops, val_col) in enumerate(all_chains)
                    )
                    emit_info(
                        f"bridge column '{bd.table_name}'.'{bd.column_name}': "
                        f"no value resolved for '{first_via_col}' = {via_val!r}; "
                        f"column will be NULL\n{chain_details}",
                        table=bd.table_name,
                        column=bd.column_name,
                        key_values=kv,
                    )
                continue
            if len(set(resolved)) > 1 and emit is not None:
                pk_vals = {k: row.get(k) for k in ['id', '_block_id'] if row.get(k)}
                emit(
                    f"bridge column '{bd.table_name}'.'{bd.column_name}': "
                    f"chains disagree on value {resolved!r} for row {pk_vals}; "
                    f"using first resolved value {resolved[0]!r}"
                )
            row[bd.column_name] = resolved[0]


# ---------------------------------------------------------------------------
# Compatibility check for multi-category loops
# ---------------------------------------------------------------------------

def _loops_compatible(table_names: list[str], schema: SchemaSpec) -> bool:
    """Return True if all tables may appear together in the same CIF loop.

    Tables are compatible when they share the same set of non-synthetic PK
    column names.  DDLm multi-category loops (e.g. pd_data / pd_meas /
    pd_proc / pd_calc) always share their key structure; the column names are
    the authoritative signal rather than FK-resolved targets, because FK
    constraints may be intentionally omitted for composite-PK targets that
    SQLite cannot reference individually.
    """
    if len(table_names) <= 1:
        return True
    pk_name_sets = [
        frozenset(
            col.name for col in schema.tables[t].columns
            if col.is_primary_key and not col.is_synthetic
        )
        for t in table_names
    ]
    return all(pks == pk_name_sets[0] for pks in pk_name_sets[1:])


# ---------------------------------------------------------------------------
# Core ingestion class
# ---------------------------------------------------------------------------

class _Ingester:
    def __init__(
        self,
        cif: CifFile,
        conn: sqlite3.Connection,
        schema: SchemaSpec | None,
        propagate_fk: bool,
        dataset_id: str | None,
        on_error: Callable[..., None] | None,
    ) -> None:
        self.cif = cif
        self.conn = conn
        self.schema = schema
        self.propagate_fk = propagate_fk
        self.dataset_id = dataset_id
        self._on_error = on_error
        self._current_block_id: str | None = None
        self.errors: list[str] = []
        self._semantic_errors: list[str] = []

        # Build routing infrastructure once
        self.tag_to_column: dict[str, tuple[str, str]] = (
            build_tag_to_column(schema) if schema else {}
        )
        self.su_map: dict[str, str] = build_su_map(schema) if schema else {}
        self._col_by_name: dict[str, dict[str, object]] = (
            {tbl_name: {c.name: c for c in tbl.columns if not c.is_synthetic}
             for tbl_name, tbl in schema.tables.items()}
            if schema else {}
        )
        self._pk_keys: dict[str, tuple] = (
            {tbl_name: tuple(tbl.primary_keys)
             for tbl_name, tbl in schema.tables.items()}
            if schema else {}
        )

        # Per-ingest accumulators
        self.row_id_counters: dict[str, int] = {}
        self.merged_rows: dict[str, dict[tuple, dict]] = {}
        self._block_pk_values: dict[str, list[str]] = {}
        self.fallback_rows: list[dict] = []
        # (block_id, audit_dataset_id, id_regime) rows for _block_dataset_membership
        self.membership_rows: list[dict] = []
        # (block_id, position) rows for _block_order
        self.block_order_rows: list[tuple[str, int]] = []
        # (check_name, severity, block_id, detail, id_regime) for _validation_result
        self.validation_rows: list[dict] = []
        # (block_id, table_name, column_name, pk_json) for _tag_presence
        self.tag_presence_rows: list[tuple[str, str, str, str]] = []

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self, _pre_commit_hook=None) -> list[str]:
        blocks = _select_blocks(self.cif, self.dataset_id)

        old_isolation = self.conn.isolation_level
        self.conn.isolation_level = None
        old_synchronous = self.conn.execute('PRAGMA synchronous').fetchone()[0]
        old_journal_mode = self.conn.execute('PRAGMA journal_mode').fetchone()[0]
        self.conn.execute('PRAGMA synchronous = OFF')
        self.conn.execute('PRAGMA journal_mode = MEMORY')
        self.conn.execute('PRAGMA cache_size = -65536')
        self.conn.execute('PRAGMA temp_store = MEMORY')
        self.conn.execute('BEGIN')
        try:
            for position, block in enumerate(blocks):
                self._block_position = position
                self._process_block(block)
            if self._semantic_errors:
                raise IngestionError(self._semantic_errors)
            if self.schema and self.schema.bridge_columns:
                _fill_bridge_columns(
                    self.merged_rows, self.schema.bridge_columns,
                    emit=self._emit,
                    emit_info=self._emit_info,
                )
            self._post_validate()
            self._flush()
            if _pre_commit_hook is not None:
                _pre_commit_hook(self)
            try:
                self.conn.execute('COMMIT')
            except sqlite3.Error as commit_exc:
                # Failed COMMIT leaves the transaction open in SQLite, so we can
                # still interrogate the database before rolling back.
                messages: list[str] = [f'COMMIT failed: {commit_exc}']
                try:
                    for tbl, rowid, parent, fkid in self.conn.execute(
                        'PRAGMA foreign_key_check'
                    ):
                        messages.append(
                            f"foreign key violation: '{tbl}' rowid={rowid}"
                            f" ->'{parent}' (constraint #{fkid})"
                        )
                except sqlite3.Error:
                    pass
                self.conn.execute('ROLLBACK')
                raise IngestionError(messages) from commit_exc
        except ValueError:
            self.conn.execute('ROLLBACK')
            raise
        except Exception:
            self.conn.execute('ROLLBACK')
            raise
        finally:
            self.conn.execute(f'PRAGMA synchronous = {old_synchronous}')
            self.conn.execute(f'PRAGMA journal_mode = {old_journal_mode}')
            self.conn.isolation_level = old_isolation

        return self.errors

    def _emit(self, msg: str, *, table: str | None = None, column: str | None = None, key_values: dict[str, str | None] | None = None) -> None:
        self.errors.append(msg)
        if self._on_error:
            self._on_error(msg, self._current_block_id, severity='Warning', table=table, column=column, key_values=key_values)

    def _emit_error(self, msg: str, *, table: str | None = None, column: str | None = None, key_values: dict[str, str | None] | None = None) -> None:
        """Record a semantic error. After all blocks are processed, any
        semantic errors will cause :exc:`IngestionError` to be raised and
        the transaction to be rolled back."""
        self._semantic_errors.append(msg)
        self.errors.append(msg)
        if self._on_error:
            self._on_error(msg, self._current_block_id, severity='Warning', table=table, column=column, key_values=key_values)

    def _emit_info(self, msg: str, *, table: str | None = None, column: str | None = None, key_values: dict[str, str | None] | None = None) -> None:
        """Emit an informational notice (not a warning, not an error).
        Does not append to self.errors and does not cause IngestionError."""
        if self._on_error:
            self._on_error(msg, None, severity='Info', table=table, column=column, key_values=key_values)

    # ── Block processing ──────────────────────────────────────────────────────

    def _process_block(self, block: CifBlock) -> None:
        block_id = block.name
        self._current_block_id = block_id

        # Per-block state
        loop_id_counter = 1
        set_buffers: dict[str, dict[str, Any]] = {}
        set_row_reservations: dict[str, int] = {}
        loop_scalar_buffers: dict[str, dict[str, Any]] = {}
        loop_scalar_row_reservations: dict[str, int] = {}
        fk_accumulator: dict[str, str] = {}
        deprecated_warned: set[str] = set()

        if self.schema is None:
            loop_id_counter = self._process_block_no_schema(
                block, block_id, loop_id_counter
            )
            self._record_membership(block, block_id, 'assumed')
            return

        # Map each loop tag to its loop index
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
                loop_tags = block.loops[loop_idx]
                loop_id_counter = self._process_loop(
                    block, block_id, loop_tags, loop_id_counter,
                    fk_accumulator, deprecated_warned,
                )
            else:
                self._process_scalar(
                    block, block_id, tag,
                    set_buffers, set_row_reservations,
                    loop_scalar_buffers, loop_scalar_row_reservations,
                    fk_accumulator, deprecated_warned,
                )

        # Flush Set buffers accumulated during this block
        for tbl_name, col_dict in set_buffers.items():
            row: dict[str, Any] = dict(col_dict)
            row['_block_id'] = block_id
            row['_row_id'] = set_row_reservations[tbl_name]
            table = self.schema.tables[tbl_name]
            if table.primary_keys == ['_pycifparse_id']:
                row['_pycifparse_id'] = str(_uuid_module.uuid4())
            # Apply FK propagation before merging
            _apply_fk(row, table, self.schema, None,
                      fk_accumulator, self.propagate_fk, self._emit,
                      block_id, self.merged_rows, self.row_id_counters,
                      self._block_pk_values,
                      self._col_by_name.get(tbl_name))
            if tbl_name not in self.merged_rows:
                self.merged_rows[tbl_name] = {}
            pk_keys = self._pk_keys[tbl_name]
            pk = tuple(map(row.get, pk_keys))
            pk_json_str = json.dumps(list(pk))
            for col, val in col_dict.items():
                if val is not None:
                    self.tag_presence_rows.append((block_id, tbl_name, col, pk_json_str))
            if pk not in self.merged_rows[tbl_name]:
                self.merged_rows[tbl_name][pk] = dict(row)
                entry = self._block_pk_values.get(block_id)
                if entry is None:
                    self._block_pk_values[block_id] = entry = []
                for pk_col in table.primary_keys:
                    if pk_col not in _SYNTHETIC_PK_COLS:
                        v = row.get(pk_col)
                        if v is not None:
                            entry.append(v)
            else:
                existing = self.merged_rows[tbl_name][pk]
                pk_values = dict(zip(table.primary_keys, pk))
                for col, val in row.items():
                    if col in ('_row_id', '_block_id') or val is None:
                        continue
                    if existing.get(col) is None:
                        existing[col] = val
                    elif existing[col] != val:
                        msg = (
                            f"merge conflict on '{tbl_name}'.'{col}': "
                            f"keeping '{existing[col]}', ignoring '{val}'"
                        )
                        self._emit_error(msg, table=tbl_name, column=col, key_values=pk_values)

        # Flush Loop-class scalar buffers accumulated during this block
        for tbl_name, col_dict in loop_scalar_buffers.items():
            row = dict(col_dict)
            row['_block_id'] = block_id
            row['_row_id'] = loop_scalar_row_reservations[tbl_name]
            table = self.schema.tables[tbl_name]
            if table.primary_keys == ['_pycifparse_id']:
                row['_pycifparse_id'] = str(_uuid_module.uuid4())
            _apply_fk(row, table, self.schema, None,
                      fk_accumulator, self.propagate_fk, self._emit,
                      block_id, self.merged_rows, self.row_id_counters,
                      self._block_pk_values,
                      self._col_by_name.get(tbl_name))
            pk_keys = self._pk_keys[tbl_name]
            pk = tuple(map(row.get, pk_keys))
            pk_json_str = json.dumps(list(pk))
            for col, val in col_dict.items():
                if val is not None:
                    self.tag_presence_rows.append((block_id, tbl_name, col, pk_json_str))
            _merge_into(
                self.merged_rows, tbl_name, row, table,
                self.row_id_counters, self._emit, self._emit_error,
                self._block_pk_values,
                pk_keys,
            )

        self._record_membership(block, block_id, self._id_regime(block_id))

    # ── No-schema block processing ────────────────────────────────────────────

    def _process_block_no_schema(
        self,
        block: CifBlock,
        block_id: str,
        loop_id_counter: int,
    ) -> int:
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
                loop_tags = block.loops[loop_idx]
                n_iters = len(block[loop_tags[0]]) if loop_tags else 0
                for iter_idx in range(n_iters):
                    row_id = _next_row_id(self.row_id_counters, '_cif_fallback')
                    for col_idx, ltag in enumerate(loop_tags):
                        val = block[ltag][iter_idx]
                        stored, vtype = encode_value(val)
                        self.fallback_rows.append({
                            '_block_id': block_id,
                            '_row_id': row_id,
                            'tag': ltag.lower(),
                            'value': stored,
                            'value_type': vtype,
                            'loop_id': loop_id_counter,
                            'col_index': col_idx,
                        })
                loop_id_counter += 1
            else:
                values = block[tag]
                val = values[0]
                stored, vtype = encode_value(val)
                self.fallback_rows.append({
                    '_block_id': block_id,
                    '_row_id': 1,
                    'tag': tag.lower(),
                    'value': stored,
                    'value_type': vtype,
                    'loop_id': None,
                    'col_index': None,
                })

        return loop_id_counter

    # ── Scalar processing ─────────────────────────────────────────────────────

    def _process_scalar(
        self,
        block: CifBlock,
        block_id: str,
        tag: str,
        set_buffers: dict,
        set_row_reservations: dict,
        loop_scalar_buffers: dict,
        loop_scalar_row_reservations: dict,
        fk_accumulator: dict,
        deprecated_warned: set,
    ) -> None:
        values = block[tag]
        val = values[0]
        canonical, location = self._route_tag(tag, deprecated_warned)

        if location is None:
            # Fallback
            stored, vtype = encode_value(val)
            self.fallback_rows.append({
                '_block_id': block_id,
                '_row_id': 1,
                'tag': canonical,
                'value': stored,
                'value_type': vtype,
                'loop_id': None,
                'col_index': None,
            })
            return

        tbl_name, col_name = location
        table = self.schema.tables[tbl_name]

        stored, _ = encode_value(val)
        # SU splitting
        stored, su_val = self._maybe_split_su(val, stored, canonical)

        if table.category_class == 'Set':
            # Reserve _row_id on first scalar tag for this Set table
            if tbl_name not in set_row_reservations:
                set_row_reservations[tbl_name] = _next_row_id(
                    self.row_id_counters, tbl_name
                )
            if tbl_name not in set_buffers:
                set_buffers[tbl_name] = {}
            set_buffers[tbl_name][col_name] = stored
            if su_val is not None:
                set_buffers[tbl_name][self.su_map[canonical]] = su_val
            # Write to fk_accumulator immediately (Lesson 28)
            fk_accumulator[canonical] = stored
        else:
            # Loop-class tag as scalar: accumulate into per-block buffer so that
            # all sibling scalars (including the PK column) are flushed together.
            if tbl_name not in loop_scalar_row_reservations:
                loop_scalar_row_reservations[tbl_name] = _next_row_id(
                    self.row_id_counters, tbl_name
                )
            if tbl_name not in loop_scalar_buffers:
                loop_scalar_buffers[tbl_name] = {}
            loop_scalar_buffers[tbl_name][col_name] = stored
            if su_val is not None:
                loop_scalar_buffers[tbl_name][self.su_map[canonical]] = su_val
            # Write to fk_accumulator immediately
            fk_accumulator[canonical] = stored

    # ── Loop processing ───────────────────────────────────────────────────────

    def _process_loop(
        self,
        block: CifBlock,
        block_id: str,
        loop_tags: list[str],
        loop_id_counter: int,
        fk_accumulator: dict,
        deprecated_warned: set,
    ) -> int:
        # Route all loop tags
        routing: dict[str, tuple[str, tuple[str, str] | None]] = {}
        for tag in loop_tags:
            canonical, location = self._route_tag(tag, deprecated_warned)
            routing[tag] = (canonical, location)

        # Classify: structured tables vs fallback
        loop_tables: dict[str, list[tuple[str, str, str]]] = {}  # tbl ->[(col, tag, canonical)]
        fallback_loop_tags: list[tuple[str, str, int]] = []  # (tag, canonical, col_index)

        for col_idx, tag in enumerate(loop_tags):
            canonical, location = routing[tag]
            if location:
                tbl, col = location
                if tbl not in loop_tables:
                    loop_tables[tbl] = []
                loop_tables[tbl].append((col, tag, canonical))
            else:
                fallback_loop_tags.append((tag, canonical, col_idx))

        # Compatibility check for multi-category loops
        table_names = sorted(loop_tables.keys())
        if len(table_names) > 1 and not _loops_compatible(table_names, self.schema):
            self._emit(
                f"incompatible multi-category loop; routing all tags to _cif_fallback"
            )
            # Route everything to fallback
            fallback_loop_tags = [
                (tag, routing[tag][0], i) for i, tag in enumerate(loop_tags)
            ]
            loop_tables = {}
            table_names = []

        n_iters = len(block[loop_tags[0]]) if loop_tags else 0

        # First structured table alphabetically (for fallback _row_id in multi-category)
        first_structured_table = table_names[0] if table_names else None

        # Pre-loop analysis: which tables need _apply_fk at all?
        # A table can skip _apply_fk when:
        #   - every FK source column is provided by the loop (no value-filling needed), AND
        #   - every FK target table is also in this loop (no external parent stubs needed), AND
        #   - every propagation_link column is provided by the loop (or irrelevant).
        tables_needing_fk: set[str] = set()
        for tbl_name in table_names:
            table = self.schema.tables[tbl_name]
            provided = {col for col, _, _ in loop_tables[tbl_name]}
            col_by_name_t = self._col_by_name.get(tbl_name, {})
            _needs = False
            for fk in table.foreign_keys:
                # Case 1: any source col might need value-filling
                for sc in fk.source_columns:
                    col = col_by_name_t.get(sc)
                    if col is None:
                        continue
                    if sc not in provided and (col.is_primary_key or self.propagate_fk):
                        _needs = True
                        break
                if _needs:
                    break
                # Case 2: all source cols are provided → stub will be created;
                # if parent table is outside this loop, we still need the stub call.
                if (self.schema.tables.get(fk.target_table) is not None
                        and fk.target_table not in loop_tables
                        and all(sc in provided
                                for sc in fk.source_columns
                                if col_by_name_t.get(sc) is not None)):
                    _needs = True
                    break
            if not _needs:
                for col_name, _, _ in self.schema.propagation_links.get(tbl_name, []):
                    if col_name not in provided:
                        col = col_by_name_t.get(col_name)
                        if col is not None and (col.is_primary_key or self.propagate_fk):
                            _needs = True
                            break
            if _needs:
                tables_needing_fk.add(tbl_name)

        # Per-iteration processing
        all_iter_rows: list[dict[str, dict]] = []  # per-iteration {tbl: row}

        for iter_idx in range(n_iters):
            # Encode each structured tag value once; reuse for iter_by_defid and row building.
            encoded_iter: dict[str, tuple] = {}  # tag → (raw_val, stored)
            iter_by_defid: dict[str, str] = {}
            for tag in loop_tags:
                canonical, location = routing[tag]
                if location:
                    val = block[tag][iter_idx]
                    stored, _ = encode_value(val)
                    encoded_iter[tag] = (val, stored)
                    iter_by_defid[canonical] = stored

            iter_rows: dict[str, dict] = {}
            for tbl_name in table_names:
                table = self.schema.tables[tbl_name]
                row: dict[str, Any] = {'_block_id': block_id}
                for col_name, tag, canonical in loop_tables[tbl_name]:
                    val, stored = encoded_iter[tag]
                    stored, su_val = self._maybe_split_su(val, stored, canonical)
                    row[col_name] = stored
                    if su_val is not None:
                        row[self.su_map[canonical]] = su_val

                if tbl_name in tables_needing_fk:
                    _apply_fk(row, table, self.schema, iter_by_defid,
                              fk_accumulator, self.propagate_fk, self._emit,
                              block_id, self.merged_rows, self.row_id_counters,
                              self._block_pk_values,
                              self._col_by_name.get(tbl_name))
                iter_rows[tbl_name] = row

            # ── Per-iteration UUID fill for missing PKs ───────────────────────
            # _apply_fk handles single-column key-FKs; it does not cover pure-key
            # PKs (no FK at all) or composite-key-FK components.  Fill any
            # remaining NULL non-synthetic PK columns with fresh UUIDs, sharing
            # one UUID per column name across all sibling tables in this iteration
            # so that multi-category loop rows remain joinable.
            if table_names:
                pk_uuid_pool: dict[str, str] = {}
                for tbl_name in table_names:
                    tbl = self.schema.tables[tbl_name]
                    row = iter_rows[tbl_name]
                    for col in tbl.columns:
                        if (col.is_primary_key and not col.is_synthetic
                                and row.get(col.name) is None):
                            if col.name not in pk_uuid_pool:
                                pk_uuid_pool[col.name] = str(_uuid_module.uuid4())
                            row[col.name] = pk_uuid_pool[col.name]
                # For composite FKs that are now fully specified after UUID fill,
                # create parent stubs and call _apply_fk on them so that
                # grandparent stubs are also created, preserving topological
                # insertion order in _flush.
                if pk_uuid_pool:
                    for tbl_name in table_names:
                        tbl = self.schema.tables[tbl_name]
                        row = iter_rows[tbl_name]
                        for fk in tbl.foreign_keys:
                            if len(fk.source_columns) <= 1:
                                continue  # Single-column stubs handled by _apply_fk.
                            col_vals = [
                                (sc, tc, row.get(sc))
                                for sc, tc in zip(fk.source_columns, fk.target_columns)
                            ]
                            if all(v is not None for _, _, v in col_vals):
                                parent_table = self.schema.tables.get(fk.target_table)
                                if parent_table is not None:
                                    stub: dict[str, Any] = {'_block_id': block_id}
                                    for _, tc, v in col_vals:
                                        stub[tc] = v
                                    # _apply_fk on the stub creates grandparent stubs,
                                    # ensuring parents precede children in merged_rows.
                                    _apply_fk(stub, parent_table, self.schema, None,
                                              fk_accumulator, self.propagate_fk,
                                              self._emit, block_id,
                                              self.merged_rows, self.row_id_counters,
                                              self._block_pk_values,
                                              self._col_by_name.get(fk.target_table))
                                    _merge_into(self.merged_rows, fk.target_table,
                                                stub, parent_table,
                                                self.row_id_counters, self._emit,
                                                block_pk_values=self._block_pk_values,
                                                pk_keys=self._pk_keys.get(fk.target_table))

            # Cross-table PK propagation for multi-category loops.
            # All compatible tables share the same PK column names.  After
            # _apply_fk has run for every table, propagate non-NULL PK values
            # across sibling rows so that tables whose PK columns have no
            # direct FK (because the link targets a column of a composite PK,
            # which cannot be a SQL FK target) still receive the correct key.
            if len(table_names) > 1:
                shared_pk: dict[str, Any] = {}
                for tbl_name in table_names:
                    tbl = self.schema.tables[tbl_name]
                    row = iter_rows[tbl_name]
                    for col in tbl.columns:
                        if col.is_primary_key and not col.is_synthetic:
                            if row.get(col.name) is not None:
                                shared_pk[col.name] = row[col.name]
                for tbl_name in table_names:
                    tbl = self.schema.tables[tbl_name]
                    row = iter_rows[tbl_name]
                    for col in tbl.columns:
                        if col.is_primary_key and not col.is_synthetic:
                            if row.get(col.name) is None and col.name in shared_pk:
                                row[col.name] = shared_pk[col.name]

            all_iter_rows.append(iter_rows)

        # Merge all rows and collect _row_ids for fallback
        iter_row_ids: list[dict[str, int]] = []  # per-iteration {tbl: row_id}
        for iter_idx, iter_rows in enumerate(all_iter_rows):
            ids: dict[str, int] = {}
            for tbl_name, row in iter_rows.items():
                table = self.schema.tables[tbl_name]
                # Record tag presence when this loop row merges into an existing
                # row owned by a different block (contributed-but-not-owned case).
                pk_keys = self._pk_keys[tbl_name]
                pk = tuple(map(row.get, pk_keys))
                existing = self.merged_rows.get(tbl_name, {}).get(pk)
                if existing is not None and existing.get('_block_id') != block_id:
                    pk_json_str = json.dumps(list(pk))
                    direct_cols = {col_name for col_name, _, _ in loop_tables.get(tbl_name, [])}
                    for col in direct_cols:
                        if row.get(col) is not None:
                            self.tag_presence_rows.append((block_id, tbl_name, col, pk_json_str))
                rid = _merge_into(
                    self.merged_rows, tbl_name, row, table,
                    self.row_id_counters, self._emit, self._emit_error,
                    self._block_pk_values,
                    pk_keys,
                )
                ids[tbl_name] = rid
            iter_row_ids.append(ids)

        # Fallback cells
        if fallback_loop_tags or (loop_tables and fallback_loop_tags is not None):
            # (re-check: only write fallback rows if there are actually fallback tags)
            pass

        for iter_idx in range(n_iters):
            if not fallback_loop_tags:
                continue

            # Determine _row_id for fallback cells
            if first_structured_table:
                fb_row_id = iter_row_ids[iter_idx].get(first_structured_table,
                                                        _next_row_id(self.row_id_counters, '_cif_fallback'))
            else:
                # Pure-fallback loop
                fb_row_id = _next_row_id(self.row_id_counters, '_cif_fallback')

            for tag, canonical, col_idx in fallback_loop_tags:
                val = block[tag][iter_idx]
                stored, vtype = encode_value(val)
                self.fallback_rows.append({
                    '_block_id': block_id,
                    '_row_id': fb_row_id,
                    'tag': canonical,
                    'value': stored,
                    'value_type': vtype,
                    'loop_id': loop_id_counter,
                    'col_index': col_idx,
                    'ref_table': first_structured_table,
                })

        # Single-iteration fk_accumulator rule
        if n_iters == 1 and table_names:
            for tbl_name in table_names:
                row = all_iter_rows[0][tbl_name]
                for k, v in row.items():
                    if v is not None and k not in ('_block_id', '_row_id'):
                        col_def_id = self.schema.column_to_tag.get((tbl_name, k))
                        if col_def_id:
                            fk_accumulator[col_def_id] = v

        return loop_id_counter + 1

    # ── Tag routing ───────────────────────────────────────────────────────────

    def _route_tag(
        self,
        tag: str,
        deprecated_warned: set[str],
    ) -> tuple[str, tuple[str, str] | None]:
        """Return ``(canonical_def_id, (table, col))`` or ``(canonical, None)``."""
        tag_lc = tag.lower()
        canonical = self.schema.alias_to_definition_id.get(tag_lc, tag_lc)

        if canonical in self.schema.deprecated_ids and tag_lc not in deprecated_warned:
            deprecated_warned.add(tag_lc)
            if canonical != tag_lc:
                self._emit(f"tag '{tag_lc}' is deprecated (canonical: '{canonical}')")
            else:
                self._emit(f"tag '{tag_lc}' is deprecated")

        location = self.tag_to_column.get(canonical)
        return canonical, location

    # ── SU splitting ──────────────────────────────────────────────────────────

    def _maybe_split_su(
        self,
        val: Any,
        stored: str | None,
        canonical: str,
    ) -> tuple[str | None, str | None]:
        """Return ``(measurand_stored, su_stored)`` applying SU split if applicable."""
        if (isinstance(val, CifScalar)
                and val.value_type == ValueType.STRING
                and canonical in self.su_map
                and stored is not None):
            parts = split_su(stored)
            if parts:
                measurand, su_digits = parts
                return measurand, su_digits
            else:
                # No SU sub-expression — leave SU column NULL
                return stored, None
        elif (isinstance(val, CifScalar)
              and val.value_type == ValueType.STRING
              and canonical not in self.su_map):
            # Check if this is a measurand that has NO su column
            # (su_map already handles the opposite direction)
            pass
        return stored, None

    # ── id_regime determination ───────────────────────────────────────────────

    def _id_regime(self, block_id: str) -> str:
        """Determine id_regime for a general block (no _audit_dataset.id)."""
        pk_values = self._block_pk_values.get(block_id)
        if not pk_values:
            return 'assumed'
        return 'uuid' if all(_is_uuid(v) for v in pk_values) else 'assumed'

    def _record_membership(
        self,
        block: CifBlock,
        block_id: str,
        id_regime: str,
    ) -> None:
        self.block_order_rows.append((block_id, self._block_position))
        dataset_ids = _read_dataset_ids(block)
        if dataset_ids:
            for did in sorted(dataset_ids):
                self.membership_rows.append({
                    '_block_id': block_id,
                    '_audit_dataset_id': did,
                    'id_regime': 'dataset',
                })
        else:
            self.membership_rows.append({
                '_block_id': block_id,
                '_audit_dataset_id': '',
                'id_regime': id_regime,
            })

    # ── Flush ─────────────────────────────────────────────────────────────────

    def _flush(self) -> None:
        cur = self.conn.cursor()

        # Structured tables
        for tbl_name, tbl_rows in self.merged_rows.items():
            if not tbl_rows:
                continue
            rows = list(tbl_rows.values())
            # Determine columns as the union of all row keys (rows merged from stubs
            # may have fewer keys than fully-populated rows).
            seen: dict[str, None] = {}
            for r in rows:
                seen.update(dict.fromkeys(r.keys()))
            cols = list(seen)
            placeholders = ', '.join('?' for _ in cols)
            col_list = ', '.join(f'"{c}"' for c in cols)
            sql = (
                f'INSERT OR REPLACE INTO "{tbl_name}" ({col_list}) '
                f'VALUES ({placeholders})'
            )
            try:
                cur.executemany(sql, [[r.get(c) for c in cols] for r in rows])
            except sqlite3.Error as e:
                self._emit(f"sqlite3 error inserting into '{tbl_name}': {e}")

        # _cif_fallback
        if self.fallback_rows:
            cur.executemany(
                'INSERT INTO "_cif_fallback" '
                '("_block_id", "_row_id", "tag", "value", "value_type", '
                '"loop_id", "col_index", "ref_table") VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                [
                    (r['_block_id'], r['_row_id'], r['tag'], r['value'],
                     r['value_type'], r.get('loop_id'), r.get('col_index'),
                     r.get('ref_table'))
                    for r in self.fallback_rows
                ],
            )

        # _tag_presence
        if self.tag_presence_rows:
            cur.executemany(
                'INSERT OR IGNORE INTO "_tag_presence" '
                '("_block_id", "table_name", "column_name", "pk_json") VALUES (?, ?, ?, ?)',
                self.tag_presence_rows,
            )

        # _block_order
        if self.block_order_rows:
            cur.executemany(
                'INSERT OR IGNORE INTO "_block_order" ("_block_id", "position") VALUES (?, ?)',
                self.block_order_rows,
            )

        # _block_dataset_membership
        if self.membership_rows:
            cur.executemany(
                'INSERT OR IGNORE INTO "_block_dataset_membership" '
                '("_block_id", "_audit_dataset_id", "id_regime") VALUES (?, ?, ?)',
                [(r['_block_id'], r['_audit_dataset_id'], r['id_regime'])
                 for r in self.membership_rows],
            )

        # _validation_result
        if self.validation_rows:
            cur.executemany(
                'INSERT INTO "_validation_result" '
                '("check_name", "severity", "block_id", "detail", "id_regime") '
                'VALUES (?, ?, ?, ?, ?)',
                [(r['check_name'], r['severity'], r['block_id'],
                  r['detail'], r['id_regime'])
                 for r in self.validation_rows],
            )

    # ── Post-ingestion validation ─────────────────────────────────────────────

    def _post_validate(self) -> None:
        # uuid_regime: warn for general blocks with non-UUID PKs
        for row in self.membership_rows:
            if row['id_regime'] == 'assumed' and row['_audit_dataset_id'] == '':
                self.validation_rows.append({
                    'check_name': 'uuid_regime',
                    'severity': 'Warning',
                    'block_id': row['_block_id'],
                    'detail': (
                        f"general block '{row['_block_id']}' has non-UUID PK values "
                        f"(or no structured rows); assumed coherence"
                    ),
                    'id_regime': 'assumed',
                })
        # uuid_reference_check: stub — not implemented in Stage 4
        # (no rows written; tested by asserting no rows for this check_name)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def ingest(
    cif: CifFile,
    conn: sqlite3.Connection,
    schema: SchemaSpec | None = None,
    *,
    propagate_fk: bool = False,
    dataset_id: str | None = None,
    on_error: Callable[..., None] | None = None,
) -> list[str]:
    """Ingest a parsed ``CifFile`` into a SQLite database.

    Parameters
    ----------
    cif:
        Parsed ``CifFile`` from ``build()``. May contain one or more blocks.
    conn:
        Open ``sqlite3.Connection`` with the schema already applied
        (``apply_schema`` + ``apply_fallback_schema`` must have been called).
    schema:
        ``SchemaSpec`` used to route tags to structured tables.  If ``None``,
        all tags are routed to ``_cif_fallback``.
    propagate_fk:
        When ``True``, non-key FK columns absent from the CIF data inherit
        their value from the FK target already known in the same block.
    dataset_id:
        The ``_audit_dataset.id`` value to ingest.  When ``None``,
        auto-detected from the blocks.  Raises ``ValueError`` if specified
        but not found in any dataset block.
    on_error:
        Optional callback for non-fatal semantic errors/warnings.  Called as
        ``on_error(message, block_id, *, table=None, column=None, key_values=None)``
        where ``block_id`` is the name of the data block being processed (or
        ``None`` for errors outside block processing), and ``table``,
        ``column``, ``key_values`` carry structured context when available
        (e.g. merge conflicts).

    Returns
    -------
    list[str]
        Semantic error/warning strings in emission order.

    Raises
    ------
    ValueError
        If the blocks belong to incompatible datasets and no ``dataset_id``
        is provided, or if ``dataset_id`` is not found.
    """
    return _Ingester(cif, conn, schema, propagate_fk, dataset_id, on_error).run()
