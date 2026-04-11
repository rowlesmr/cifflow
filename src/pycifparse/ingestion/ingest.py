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
# Value encoding
# ---------------------------------------------------------------------------

def encode_value(value: CifScalar | list | dict) -> tuple[str | None, str]:
    """Encode a CIF value for SQLite storage.

    Returns ``(stored_string, value_type_str)``.  ``value_type_str`` is only
    used when writing to ``_cif_fallback``; callers writing to structured
    tables may ignore it.

    Applies the Lesson 19 presence-state encoding:
    - PLACEHOLDER ``.`` / ``?``  →  ``'.'`` / ``'?'``
    - Quoted ``.`` / ``?``       →  ``'"."'`` / ``'"?"'``
    - Container                  →  JSON text
    - Anything else              →  raw string
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


def encode_container(value: list | dict) -> tuple[str, str]:
    """Return ``(json_string, 'list'|'table')`` for a CIF container value."""
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
    return json.dumps(_encode(value), ensure_ascii=False), vtype


def decode_container(json_str: str) -> list | dict:
    """Decode a stored JSON container back to a Python list or dict."""
    return json.loads(json_str)


_SU_RE = re.compile(
    r'^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\((\d+)\)$'
)


def split_su(raw: str) -> tuple[str, str] | None:
    """Split ``'numeric(su)'`` → ``(measurand, scaled_su)`` or ``None``.

    The SU is scaled to the precision of the measurand so that the stored
    value represents the actual uncertainty:
      '3.992(4)'   → ('3.992',   '0.004')
      '1234(5)'    → ('1234',    '5')
      '12.34(56)'  → ('12.34',   '0.56')
      '1.23e-4(5)' → ('1.23e-4', '0.000005')
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
    """Build ``measurand_def_id → su_column_name`` reverse map from *schema*."""
    result: dict[str, str] = {}
    for table in schema.tables.values():
        for col in table.columns:
            if col.linked_item_id is not None:
                result[col.linked_item_id] = col.name
    return result


def build_tag_to_column(schema: SchemaSpec) -> dict[str, tuple[str, str]]:
    """Invert ``schema.column_to_tag`` to ``canonical_def_id → (table, col)``."""
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


def _merge_into(
    merged_rows: dict[str, dict[tuple, dict]],
    table_name: str,
    row: dict,
    table: TableDef,
    row_id_counters: dict[str, int],
    emit: Callable[[str], None],
) -> int:
    """Merge *row* into *merged_rows[table_name]*.  Returns the row's ``_row_id``."""
    if table_name not in merged_rows:
        merged_rows[table_name] = {}
    tbl_rows = merged_rows[table_name]
    pk = _pk_tuple(row, table)

    if pk not in tbl_rows:
        row['_row_id'] = _next_row_id(row_id_counters, table_name)
        tbl_rows[pk] = dict(row)
        return row['_row_id']
    else:
        existing = tbl_rows[pk]
        for col, val in row.items():
            if col in ('_row_id', '_block_id'):
                continue
            if val is None:
                continue
            if existing.get(col) is None:
                existing[col] = val
            elif existing[col] != val:
                emit(
                    f"merge conflict on '{table_name}'.'{col}': "
                    f"keeping '{existing[col]}', ignoring '{val}'"
                )
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
    emit: Callable[[str], None],
    block_id: str | None = None,
    merged_rows: dict[str, dict[tuple, dict]] | None = None,
    row_id_counters: dict[str, int] | None = None,
) -> None:
    """Fill missing FK/PK columns in *row* in-place.

    When *block_id*, *merged_rows*, and *row_id_counters* are all supplied and
    a UUID must be generated for a key-FK column, a minimal stub row is also
    inserted into the parent table so that deferred FK constraints can be
    satisfied at COMMIT.
    """
    col_by_name = {c.name: c for c in table.columns if not c.is_synthetic}

    for fk in table.foreign_keys:
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
                    stub: dict = {'_block_id': block_id, tgt_col: val}
                    _merge_into(merged_rows, fk.target_table, stub,
                                parent_table, row_id_counters, emit)

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
                        _merge_into(merged_rows, fk.target_table, stub,
                                    parent_table, row_id_counters, emit)

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

def _fill_bridge_columns(
    merged_rows: dict[str, dict[tuple, dict]],
    bridge_columns: list[BridgeColumnDef],
) -> None:
    """Populate bridge-derived columns in *merged_rows* in place.

    For each :class:`BridgeColumnDef`, builds a lookup from the bridge table
    and fills any NULL values in the target table.  Must be called after all
    blocks have been processed and before :py:meth:`_Ingester._flush`.
    """
    for bd in bridge_columns:
        if bd.table_name not in merged_rows:
            continue
        bridge_rows = merged_rows.get(bd.bridge_table, {})
        # Build (block_id, bridge_pk_val) → bridge_val lookup
        lookup: dict[tuple[str | None, str], str] = {}
        for row in bridge_rows.values():
            pk_val = row.get(bd.bridge_pk_column)
            val = row.get(bd.bridge_value_column)
            if pk_val is not None and val is not None:
                lookup[(row.get('_block_id'), pk_val)] = val

        for row in merged_rows[bd.table_name].values():
            if row.get(bd.column_name) is not None:
                continue
            via_val = row.get(bd.via_column)
            if via_val is None:
                continue
            derived = lookup.get((row.get('_block_id'), via_val))
            if derived is not None:
                row[bd.column_name] = derived


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
        on_error: Callable[[str], None] | None,
    ) -> None:
        self.cif = cif
        self.conn = conn
        self.schema = schema
        self.propagate_fk = propagate_fk
        self.dataset_id = dataset_id
        self._on_error = on_error
        self.errors: list[str] = []

        # Build routing infrastructure once
        self.tag_to_column: dict[str, tuple[str, str]] = (
            build_tag_to_column(schema) if schema else {}
        )
        self.su_map: dict[str, str] = build_su_map(schema) if schema else {}

        # Per-ingest accumulators
        self.row_id_counters: dict[str, int] = {}
        self.merged_rows: dict[str, dict[tuple, dict]] = {}
        self.fallback_rows: list[dict] = []
        # (block_id, audit_dataset_id, id_regime) rows for _block_dataset_membership
        self.membership_rows: list[dict] = []
        # (check_name, severity, block_id, detail, id_regime) for _validation_result
        self.validation_rows: list[dict] = []

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self, _pre_commit_hook=None) -> list[str]:
        blocks = _select_blocks(self.cif, self.dataset_id)

        old_isolation = self.conn.isolation_level
        self.conn.isolation_level = None
        self.conn.execute('BEGIN')
        try:
            for block in blocks:
                self._process_block(block)
            if self.schema and self.schema.bridge_columns:
                _fill_bridge_columns(self.merged_rows, self.schema.bridge_columns)
            self._post_validate()
            self._flush()
            if _pre_commit_hook is not None:
                _pre_commit_hook(self)
            self.conn.execute('COMMIT')
        except ValueError:
            self.conn.execute('ROLLBACK')
            raise
        except Exception:
            self.conn.execute('ROLLBACK')
            raise
        finally:
            self.conn.isolation_level = old_isolation

        return self.errors

    def _emit(self, msg: str) -> None:
        self.errors.append(msg)
        if self._on_error:
            self._on_error(msg)

    # ── Block processing ──────────────────────────────────────────────────────

    def _process_block(self, block: CifBlock) -> None:
        block_id = block.name

        # Per-block state
        loop_id_counter = 1
        set_buffers: dict[str, dict[str, Any]] = {}
        set_row_reservations: dict[str, int] = {}
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
                      block_id, self.merged_rows, self.row_id_counters)
            if tbl_name not in self.merged_rows:
                self.merged_rows[tbl_name] = {}
            pk = _pk_tuple(row, table)
            if pk not in self.merged_rows[tbl_name]:
                self.merged_rows[tbl_name][pk] = dict(row)
            else:
                existing = self.merged_rows[tbl_name][pk]
                for col, val in row.items():
                    if col in ('_row_id', '_block_id') or val is None:
                        continue
                    if existing.get(col) is None:
                        existing[col] = val
                    elif existing[col] != val:
                        self._emit(
                            f"merge conflict on '{tbl_name}'.'{col}': "
                            f"keeping '{existing[col]}', ignoring '{val}'"
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
            # Loop-class tag as scalar: treat as single-row loop
            row: dict[str, Any] = {
                '_block_id': block_id,
                col_name: stored,
            }
            if su_val is not None:
                row[self.su_map[canonical]] = su_val
            _apply_fk(row, table, self.schema, None,
                      fk_accumulator, self.propagate_fk, self._emit,
                      block_id, self.merged_rows, self.row_id_counters)
            _merge_into(
                self.merged_rows, tbl_name, row, table,
                self.row_id_counters, self._emit,
            )
            # Single-iteration fk_accumulator rule: write non-NULL column values
            for k, v in row.items():
                if v is not None and k not in ('_block_id', '_row_id'):
                    col_def_id = self.schema.column_to_tag.get((tbl_name, k))
                    if col_def_id:
                        fk_accumulator[col_def_id] = v

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
        loop_tables: dict[str, list[tuple[str, str, str]]] = {}  # tbl → [(col, tag, canonical)]
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

        # Per-iteration processing
        all_iter_rows: list[dict[str, dict]] = []  # per-iteration {tbl: row}

        for iter_idx in range(n_iters):
            # Encoded values keyed by canonical def_id (for FK propagation)
            iter_by_defid: dict[str, str] = {}
            for tag in loop_tags:
                canonical, location = routing[tag]
                if location:
                    val = block[tag][iter_idx]
                    stored, _ = encode_value(val)
                    iter_by_defid[canonical] = stored

            iter_rows: dict[str, dict] = {}
            for tbl_name in table_names:
                table = self.schema.tables[tbl_name]
                row: dict[str, Any] = {'_block_id': block_id}
                for col_name, tag, canonical in loop_tables[tbl_name]:
                    val = block[tag][iter_idx]
                    stored, _ = encode_value(val)
                    stored, su_val = self._maybe_split_su(val, stored, canonical)
                    row[col_name] = stored
                    if su_val is not None:
                        row[self.su_map[canonical]] = su_val

                _apply_fk(row, table, self.schema, iter_by_defid,
                          fk_accumulator, self.propagate_fk, self._emit,
                          block_id, self.merged_rows, self.row_id_counters)
                iter_rows[tbl_name] = row

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
                rid = _merge_into(
                    self.merged_rows, tbl_name, row, table,
                    self.row_id_counters, self._emit,
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
        # Collect PK values of rows first contributed by this block
        pk_values: list[str] = []
        for tbl_name, tbl_rows in self.merged_rows.items():
            table = self.schema.tables[tbl_name]
            non_synthetic_pks = [
                k for k in table.primary_keys
                if k not in ('_block_id', '_row_id', '_pycifparse_id')
            ]
            if not non_synthetic_pks:
                continue
            for row in tbl_rows.values():
                if row.get('_block_id') != block_id:
                    continue
                for pk_col in non_synthetic_pks:
                    v = row.get(pk_col)
                    if v is not None:
                        pk_values.append(v)

        if not pk_values:
            return 'assumed'
        return 'uuid' if all(_is_uuid(v) for v in pk_values) else 'assumed'

    def _record_membership(
        self,
        block: CifBlock,
        block_id: str,
        id_regime: str,
    ) -> None:
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
            # Determine columns from first row (all rows have same keys by construction)
            cols = list(rows[0].keys())
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
                '"loop_id", "col_index") VALUES (?, ?, ?, ?, ?, ?, ?)',
                [
                    (r['_block_id'], r['_row_id'], r['tag'], r['value'],
                     r['value_type'], r['loop_id'], r['col_index'])
                    for r in self.fallback_rows
                ],
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
    on_error: Callable[[str], None] | None = None,
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
        Optional callback for non-fatal semantic errors/warnings.

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
