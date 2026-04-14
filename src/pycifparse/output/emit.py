"""
CIF emission from a populated SQLite database.

``emit(conn, schema, ...)`` reads structured tables and the ``_cif_fallback``
table and produces a valid CIF string.

Assumption: by emission time, all data in the database is assumed to belong to
a single coherent dataset.  Namespace conflicts (e.g. short identifiers from
unrelated sources) are not detected or resolved by the output layer.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
import warnings as _warnings
from collections import deque
from dataclasses import dataclass

from pycifparse.dictionary.schema import ForeignKeyDef, SchemaSpec, TableDef
from pycifparse.output.plan import BlockSpec, EmitMode, OutputPlan
from pycifparse.output.quote import quote
from pycifparse.types import CifVersion

# Synthetic infrastructure columns — never emitted as CIF tags.
_SYNTHETIC = frozenset({'_block_id', '_row_id', '_pycifparse_id'})

# Regex for SU reconstruction (mirrors split_su in ingest.py).
_SU_RE = re.compile(
    r'^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\((\d+)\)$'
)


# ---------------------------------------------------------------------------
# Internal block representation
# ---------------------------------------------------------------------------

@dataclass
class _BlockData:
    """All data needed to render one output CIF block."""
    name: str
    table_rows: dict[str, list[dict]]
    fallback_rows: list[dict]
    anchor_frozenset: frozenset[str]
    anchor_key_dict: dict[str, list[str]]
    suppress_fk_pk: bool
    dataset_id: str | None = None


def _make_block_data(
    name: str,
    table_rows: dict[str, list[dict]],
    fallback_rows: list[dict],
    schema: SchemaSpec,
    suppress_fk_pk: bool,
    dataset_id: str | None = None,
) -> _BlockData:
    anchor_fs = frozenset(
        t for t in table_rows
        if schema.tables.get(t) and schema.tables[t].category_class == 'Set'
    )
    anchor_kd: dict[str, list[str]] = {}
    for tbl_name, rows in table_rows.items():
        tdef = schema.tables.get(tbl_name)
        if tdef is None or tdef.category_class != 'Set':
            continue
        domain_pks = [pk for pk in tdef.primary_keys if pk not in _SYNTHETIC]
        for pk_col in domain_pks:
            key = f'{tbl_name}.{pk_col}'
            values = [str(r[pk_col]) for r in rows if r.get(pk_col) is not None]
            if values:
                anchor_kd[key] = values
    return _BlockData(
        name=name,
        table_rows=table_rows,
        fallback_rows=fallback_rows,
        anchor_frozenset=anchor_fs,
        anchor_key_dict=anchor_kd,
        suppress_fk_pk=suppress_fk_pk,
        dataset_id=dataset_id,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def emit(
    conn: sqlite3.Connection,
    schema: SchemaSpec,
    *,
    mode: EmitMode = EmitMode.ORIGINAL,
    version: CifVersion = CifVersion.CIF_2_0,
    plan: OutputPlan | None = None,
    reconstruct_su: bool = False,
    emit_defaults: bool = True,
    line_ending: str = '\n',
) -> str:
    """Emit CIF text from a populated SQLite database.

    Parameters
    ----------
    conn:
        Open ``sqlite3.Connection`` populated by ``ingest()``.  Read-only
        during emission.
    schema:
        The ``SchemaSpec`` used when the database was ingested.
    mode:
        How the database is partitioned into CIF blocks.
    version:
        CIF version to emit.  Controls quoting strategy.
    plan:
        Optional ordering and grouping specification.  ``None`` uses default
        ordering.
    reconstruct_su:
        When ``True``, paired ``(col, col_su)`` columns are merged into a
        single ``value(su)`` token.  Default ``False``.
    emit_defaults:
        When ``True`` (default), columns filled from ``enumeration_default``
        are emitted normally.  When ``False``, they would be suppressed; this
        requires per-value provenance tracking which is not yet implemented,
        so the flag is currently accepted but has no effect.
    line_ending:
        Line terminator sequence written between every line and at the end of
        the output.  Use ``'\\n'`` (default, Unix LF), ``'\\r\\n'`` (Windows
        CRLF), or ``'\\r'`` (legacy CR).  The 2048-character line-length limit
        is measured on content before line endings are applied.

    Returns
    -------
    str
        Complete CIF text including magic line, terminated with ``line_ending``.
    """
    magic = '#\\#CIF_2.0' if version == CifVersion.CIF_2_0 else '#\\#CIF_1.1'

    if mode == EmitMode.ONE_BLOCK:
        raw_blocks = _collect_one_block(conn, schema)
    elif mode == EmitMode.ALL_BLOCKS:
        raw_blocks = _collect_all_blocks(conn, schema, version)
    elif mode == EmitMode.GROUPED:
        raw_blocks = _collect_grouped(conn, schema)
    else:  # ORIGINAL
        raw_blocks = _collect_original(conn, schema)

    ordered = _sort_and_merge(raw_blocks, plan)

    # Disambiguate block names; collect all output lines flat.
    used_names: dict[str, int] = {}
    lines = [magic]
    for i, (data, spec) in enumerate(ordered):
        base = data.name
        count = used_names.get(base, 0) + 1
        used_names[base] = count
        name = f'{base}_{count}' if count > 1 else base

        if i > 0:
            lines.append('')
            lines.append('')
        lines.extend(_render_block(name, data, schema, version, spec, reconstruct_su))

    return line_ending.join(lines) + line_ending


# ---------------------------------------------------------------------------
# Sorting, merging, and block naming
# ---------------------------------------------------------------------------

def _sort_and_merge(
    blocks: list[_BlockData],
    plan: OutputPlan | None,
) -> list[tuple[_BlockData, BlockSpec | None]]:
    """Match blocks to specs, merge single_block groups, sort for emission."""
    if not plan or not plan.specs:
        return [(b, None) for b in blocks]

    matched: dict[int, list[_BlockData]] = {}
    unmatched: list[_BlockData] = []

    for block in blocks:
        spec_idx, _spec = plan.match(block.anchor_frozenset)
        if spec_idx is None:
            unmatched.append(block)
        else:
            matched.setdefault(spec_idx, []).append(block)

    result: list[tuple[_BlockData, BlockSpec | None]] = []

    for spec_idx in sorted(matched.keys()):
        spec = plan.specs[spec_idx]
        group = matched[spec_idx]

        if spec.single_block:
            merged = _merge_blocks(group, spec, plan)
            result.append((merged, spec))
        else:
            for block in sorted(group, key=lambda b: b.name):
                name = _resolve_block_name(block.anchor_key_dict, spec, plan, block.name)
                result.append((_replace_name(block, name), spec))

    for block in sorted(unmatched, key=lambda b: b.name):
        result.append((block, None))

    return result


def _merge_blocks(
    blocks: list[_BlockData],
    spec: BlockSpec,
    plan: OutputPlan,
) -> _BlockData:
    """Merge multiple blocks into one for ``single_block=True`` specs."""
    merged_table_rows: dict[str, list[dict]] = {}
    merged_fallback: list[dict] = []
    merged_anchor_kd: dict[str, list[str]] = {}
    anchor_fs: frozenset[str] = frozenset()

    for block in blocks:
        for tbl, rows in block.table_rows.items():
            merged_table_rows.setdefault(tbl, []).extend(rows)
        merged_fallback.extend(block.fallback_rows)
        for k, vals in block.anchor_key_dict.items():
            existing = merged_anchor_kd.setdefault(k, [])
            for v in vals:
                if v not in existing:
                    existing.append(v)
        anchor_fs = anchor_fs | block.anchor_frozenset

    name = _resolve_block_name(merged_anchor_kd, spec, plan, 'block')
    dataset_id = blocks[0].dataset_id if blocks else None

    return _BlockData(
        name=name,
        table_rows=merged_table_rows,
        fallback_rows=merged_fallback,
        anchor_frozenset=anchor_fs,
        anchor_key_dict=merged_anchor_kd,
        suppress_fk_pk=False,  # spec says no FK-PK suppression for single_block
        dataset_id=dataset_id,
    )


def _resolve_block_name(
    anchor_key_dict: dict[str, list[str]],
    spec: BlockSpec | None,
    plan: OutputPlan | None,
    fallback: str,
) -> str:
    namer = None
    if spec is not None:
        namer = spec.block_namer
    if namer is None and plan is not None:
        namer = plan.block_namer

    if namer is not None:
        raw = namer(anchor_key_dict) or ''
    elif anchor_key_dict:
        raw = _default_block_name(anchor_key_dict)
    else:
        raw = fallback

    name = _sanitize_block_name(raw)
    return name if name else 'block'


def _default_block_name(anchor_key_dict: dict[str, list[str]]) -> str:
    parts = []
    for key, values in sorted(anchor_key_dict.items()):
        obj_id = key.split('.', 1)[-1]
        for val in values:
            parts.append(f'{obj_id}_{val}')
    return '_'.join(parts)


def _sanitize_block_name(name: str) -> str:
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    name = re.sub(r'_+', '_', name)
    return name.strip('_')


def _replace_name(block: _BlockData, name: str) -> _BlockData:
    return _BlockData(
        name=name,
        table_rows=block.table_rows,
        fallback_rows=block.fallback_rows,
        anchor_frozenset=block.anchor_frozenset,
        anchor_key_dict=block.anchor_key_dict,
        suppress_fk_pk=block.suppress_fk_pk,
        dataset_id=block.dataset_id,
    )


# ---------------------------------------------------------------------------
# Mode collectors — each returns list[_BlockData]
# ---------------------------------------------------------------------------

def _collect_original(
    conn: sqlite3.Connection,
    schema: SchemaSpec,
) -> list[_BlockData]:
    """ORIGINAL: one output block per distinct ``_block_id``."""
    block_ids = _all_block_ids(conn, schema)
    result = []
    for bid in block_ids:
        table_rows = {}
        for table_name in schema.tables:
            rows = _fetch_rows(conn, table_name, '"_block_id" = ?', (bid,))
            if rows:
                table_rows[table_name] = rows
        fallback = _fetch_rows(conn, '_cif_fallback', '"_block_id" = ?', (bid,))
        result.append(_make_block_data(bid, table_rows, fallback, schema, suppress_fk_pk=True))
    return result


def _collect_grouped(
    conn: sqlite3.Connection,
    schema: SchemaSpec,
) -> list[_BlockData]:
    """GROUPED: one block per distinct Set-anchor key combination."""
    table_to_anchor: dict[str, str | None] = {
        t: _find_set_anchor(t, schema) for t in schema.tables
    }

    keyed_anchor_to_tables: dict[str, list[str]] = {}
    block_id_tables: list[str] = []

    for t, anchor in table_to_anchor.items():
        if anchor is not None:
            anchor_def = schema.tables[anchor]
            domain_pks = [pk for pk in anchor_def.primary_keys if pk not in _SYNTHETIC]
            if domain_pks:
                keyed_anchor_to_tables.setdefault(anchor, []).append(t)
                continue
        block_id_tables.append(t)

    # Exclusive-target anchors fall back to _block_id grouping.
    keyed_anchor_set = set(keyed_anchor_to_tables.keys())
    for anchor in list(keyed_anchor_to_tables.keys()):
        referencing_groups: set[str] = {
            other_anchor
            for other_anchor, other_tables in keyed_anchor_to_tables.items()
            if other_anchor != anchor
            for t in other_tables
            for fk in schema.tables[t].foreign_keys
            if fk.target_table == anchor
        }
        anchor_fks_out = [
            fk.target_table
            for fk in schema.tables[anchor].foreign_keys
            if fk.target_table in keyed_anchor_set and fk.target_table != anchor
        ]
        if len(referencing_groups) == 1 and not anchor_fks_out:
            block_id_tables.extend(keyed_anchor_to_tables.pop(anchor))

    result: list[_BlockData] = []
    absorbed_primary: set[str] = set()
    absorbed_all: set[str] = set()

    for anchor_name in sorted(keyed_anchor_to_tables):
        anchor_def = schema.tables[anchor_name]
        domain_pks = [pk for pk in anchor_def.primary_keys if pk not in _SYNTHETIC]
        anchor_rows = _fetch_rows(conn, anchor_name)

        if not anchor_rows:
            block_id_tables.extend(keyed_anchor_to_tables[anchor_name])
            continue

        pk_groups: dict[tuple, list[dict]] = {}
        for row in anchor_rows:
            key = tuple(row.get(pk) for pk in domain_pks)
            pk_groups.setdefault(key, []).append(row)

        for pk_vals, grouped_anchor_rows in sorted(pk_groups.items()):
            primary_block_ids: set[str] = {
                r.get('_block_id') for r in grouped_anchor_rows if r.get('_block_id')
            }
            covered_block_ids: set[str] = set(primary_block_ids)

            if primary_block_ids and primary_block_ids <= absorbed_primary:
                continue

            table_rows: dict[str, list[dict]] = {anchor_name: grouped_anchor_rows}

            for table_name in keyed_anchor_to_tables[anchor_name]:
                if table_name == anchor_name:
                    continue
                fk_path = _fk_chain(table_name, anchor_name, schema)
                if fk_path is None:
                    rows = []
                else:
                    rows = _fetch_rows_via_fk_path(conn, table_name, fk_path, domain_pks, pk_vals)
                    for r in rows:
                        if r.get('_block_id'):
                            covered_block_ids.add(r.get('_block_id'))
                if rows:
                    table_rows[table_name] = rows

            for table_name in keyed_anchor_to_tables[anchor_name]:
                if table_name == anchor_name or table_name in table_rows:
                    continue
                rows = []
                for bid in sorted(covered_block_ids):
                    rows.extend(_fetch_rows(conn, table_name, '"_block_id" = ?', (bid,)))
                if rows:
                    table_rows[table_name] = rows

            absorbed_primary |= primary_block_ids
            absorbed_all |= covered_block_ids

            for t in block_id_tables:
                rows = []
                for bid in sorted(covered_block_ids):
                    rows.extend(_fetch_rows(conn, t, '"_block_id" = ?', (bid,)))
                if rows:
                    table_rows[t] = rows

            fallback: list[dict] = []
            for bid in sorted(covered_block_ids):
                fallback.extend(_fetch_rows(conn, '_cif_fallback', '"_block_id" = ?', (bid,)))

            # Block name: from anchor key dict (default rule), falling back to _block_id.
            anchor_kd: dict[str, list[str]] = {
                f'{anchor_name}.{pk}': [str(v) for v in pk_vals if v is not None]
                for pk in domain_pks
                for v in [pk_vals[domain_pks.index(pk)]]
                if v is not None
            }
            default_name = _default_block_name(anchor_kd) if anchor_kd else (
                grouped_anchor_rows[0].get('_block_id', 'output')
            )
            fallback_name = _sanitize_block_name(default_name) or 'block'

            block = _make_block_data(fallback_name, table_rows, fallback, schema, suppress_fk_pk=True)
            result.append(block)

    # Remaining blocks (keyless Sets, Loop-only, unabsorbed).
    all_table_names = list(schema.tables.keys())
    remaining_block_ids = [
        bid for bid in _all_block_ids_for_tables(conn, all_table_names)
        if bid not in absorbed_all
    ]
    for bid_row in _fetch_rows(conn, '_cif_fallback'):
        bid = bid_row.get('_block_id')
        if bid and bid not in absorbed_all and bid not in remaining_block_ids:
            remaining_block_ids.append(bid)
    remaining_block_ids = sorted(set(remaining_block_ids))

    for bid in remaining_block_ids:
        table_rows = {}
        for t in all_table_names:
            rows = _fetch_rows(conn, t, '"_block_id" = ?', (bid,))
            if rows:
                table_rows[t] = rows
        fallback = _fetch_rows(conn, '_cif_fallback', '"_block_id" = ?', (bid,))
        if table_rows or fallback:
            result.append(_make_block_data(bid, table_rows, fallback, schema, suppress_fk_pk=True))

    return result


def _collect_one_block(
    conn: sqlite3.Connection,
    schema: SchemaSpec,
) -> list[_BlockData]:
    """ONE_BLOCK: all data in a single block named 'output'."""
    table_rows = {}
    for table_name in schema.tables:
        rows = _fetch_rows(conn, table_name)
        if rows:
            table_rows[table_name] = rows
    fallback = _fetch_rows(conn, '_cif_fallback')
    return [_make_block_data('output', table_rows, fallback, schema, suppress_fk_pk=False)]


def _collect_all_blocks(
    conn: sqlite3.Connection,
    schema: SchemaSpec,
    version: CifVersion,
) -> list[_BlockData]:
    """ALL_BLOCKS: one block per Set-anchor key combination.

    Set categories: one output block per row (each row is a distinct instance).
    Loop categories: grouped by the domain PK of the nearest Set ancestor via FK
    chain.  Tables with no Set ancestor are grouped by ``_block_id``.

    Mirrors ``_collect_grouped`` logic.  In CIF 2.0, each block receives a
    shared ``_audit_dataset.id`` UUID that links all blocks to the one dataset
    produced by this ``emit()`` call.
    """
    dataset_id: str | None = None
    if version == CifVersion.CIF_2_0:
        dataset_id = str(uuid.uuid4())

    grouped = _collect_grouped(conn, schema)

    result = []
    for block in grouped:
        # Drop `audit_dataset` from table_rows so the emission UUID is always
        # injected as `_audit_dataset.id` for every block.  This ensures all
        # output blocks share the same dataset identifier when re-ingested.
        table_rows = {k: v for k, v in block.table_rows.items()
                      if k != 'audit_dataset'}
        result.append(_BlockData(
            name=block.name,
            table_rows=table_rows,
            fallback_rows=block.fallback_rows,
            anchor_frozenset=block.anchor_frozenset,
            anchor_key_dict=block.anchor_key_dict,
            suppress_fk_pk=False,
            dataset_id=dataset_id,
        ))
    return result


# ---------------------------------------------------------------------------
# Block renderer
# ---------------------------------------------------------------------------

def _render_block(
    block_name: str,
    data: _BlockData,
    schema: SchemaSpec,
    version: CifVersion,
    spec: BlockSpec | None,
    reconstruct_su: bool,
) -> list[str]:
    """Render a single CIF block as a flat list of output lines."""
    lines: list[str] = [f'data_{block_name}']
    first_category = True

    # Inject _audit_dataset.id when requested.
    if data.dataset_id is not None:
        audit_in_table = 'audit_dataset' in data.table_rows
        audit_in_fallback = any(
            (r.get('tag') or '').lower() == '_audit_dataset.id'
            for r in data.fallback_rows
        )
        if not audit_in_table and not audit_in_fallback:
            audit_tag = schema.column_to_tag.get(('audit_dataset', 'id'), '_audit_dataset.id')
            lines.append(f'{audit_tag}  {quote(data.dataset_id, version)}')
            first_category = False

    for item in _ordered_categories(schema, spec, data.table_rows):
        if isinstance(item, list):
            # Merge group
            cat_lines = _render_merge_group(item, data.table_rows, schema, version, spec, reconstruct_su)
            if cat_lines:
                if not first_category:
                    lines.append('')
                first_category = False
                lines.extend(cat_lines)
        else:
            table_name = item
            rows = data.table_rows.get(table_name)
            if not rows:
                continue
            table_def = schema.tables[table_name]
            cols = _active_cols(table_def, rows, spec, reconstruct_su)
            if not cols:
                continue

            if data.suppress_fk_pk and table_def.category_class == 'Set' and len(rows) == 1:
                suppressed = _suppressed_fk_pk_cols(table_def, rows, data.table_rows, schema)
                cols = [c for c in cols if c not in suppressed]
            if not cols:
                continue

            if not first_category:
                lines.append('')
            first_category = False

            if table_def.category_class == 'Set' and len(rows) == 1:
                lines.extend(_render_set_category(rows[0], cols, table_name, schema, version, table_def, reconstruct_su))
            else:
                lines.extend(_render_loop_category(rows, cols, table_name, schema, version, table_def, reconstruct_su))

    if data.fallback_rows:
        if not first_category:
            lines.append('')
        lines.extend(_render_fallback(data.fallback_rows, version))

    return lines


# ---------------------------------------------------------------------------
# Category ordering and wildcard expansion
# ---------------------------------------------------------------------------

def _ordered_categories(
    schema: SchemaSpec,
    spec: BlockSpec | None,
    table_rows: dict[str, list[dict]],
) -> list[str | list[str]]:
    """Return table names (and merge groups) in emission order.

    Plain ``str`` entries are single categories; ``list[str]`` entries are
    merge groups to be emitted as a single ``loop_`` (if compatible).
    """
    all_tables = set(schema.tables.keys())
    result: list[str | list[str]] = []
    listed: set[str] = set()

    if spec and spec.category_order:
        for item in spec.category_order:
            if isinstance(item, list):
                # Merge group: expand wildcards within group members
                expanded: list[str] = []
                for name in item:
                    if name.endswith('*'):
                        for t in _expand_wildcard(name, schema):
                            if t in all_tables and t not in listed:
                                expanded.append(t)
                                listed.add(t)
                    else:
                        if name in all_tables and name not in listed:
                            expanded.append(name)
                            listed.add(name)
                if expanded:
                    result.append(expanded)
            else:
                # Plain string or wildcard
                if item.endswith('*'):
                    for t in _expand_wildcard(item, schema):
                        if t in all_tables and t not in listed:
                            result.append(t)
                            listed.add(t)
                else:
                    if item in all_tables and item not in listed:
                        result.append(item)
                        listed.add(item)

    # Append remaining: Set-class first (alphabetical), then Loop-class (alphabetical).
    set_rem = sorted(t for t in all_tables if t not in listed and schema.tables[t].category_class == 'Set')
    loop_rem = sorted(t for t in all_tables if t not in listed and schema.tables[t].category_class != 'Set')
    result.extend(set_rem)
    result.extend(loop_rem)

    return result


def _expand_wildcard(pattern: str, schema: SchemaSpec) -> list[str]:
    """Expand ``'CATEGORY*'`` to the base category plus all schema descendants.

    The base name is the pattern with the trailing ``'*'`` stripped (lowercased).
    If the base is not in the schema, emits a warning and returns an empty list.
    Descendants are found by BFS over ``schema.category_parent`` and returned
    sorted alphabetically (including the base category itself).
    """
    base = pattern[:-1].lower()
    if base not in schema.tables:
        _warnings.warn(
            f"OutputPlan wildcard {pattern!r}: base category {base!r} not in schema — skipped"
        )
        return []

    # Build children map from category_parent.
    children: dict[str, list[str]] = {}
    for tbl, parent in schema.category_parent.items():
        if parent is not None:
            children.setdefault(parent, []).append(tbl)

    # BFS to collect base + all descendants.
    found: set[str] = {base}
    queue = [base]
    while queue:
        current = queue.pop(0)
        for child in children.get(current, []):
            if child not in found:
                found.add(child)
                queue.append(child)

    return sorted(found)


# ---------------------------------------------------------------------------
# Merge group renderer
# ---------------------------------------------------------------------------

def _render_merge_group(
    group: list[str],
    table_rows: dict[str, list[dict]],
    schema: SchemaSpec,
    version: CifVersion,
    spec: BlockSpec | None,
    reconstruct_su: bool,
) -> list[str]:
    """Render a merge group as a single loop_ or as plain loops.

    Categories sharing identical non-synthetic PK columns are joined via a
    FULL OUTER JOIN (implemented in Python) and emitted as one ``loop_``.
    Categories that are not key-compatible are emitted as plain loops in the
    listed order.
    """
    # Collect tables present in this block.
    present = [cat for cat in group if table_rows.get(cat)]
    if not present:
        return []

    # Determine PK sets for key-compatibility check.
    pk_sets: list[frozenset[str]] = []
    for cat in present:
        tdef = schema.tables[cat]
        domain_pks = frozenset(pk for pk in tdef.primary_keys if pk not in _SYNTHETIC)
        pk_sets.append(domain_pks)

    # All present categories must share the same non-synthetic PK column set.
    compatible = len(set(pk_sets)) <= 1 and pk_sets

    if not compatible:
        # Fall back to plain loops in listed order.
        lines: list[str] = []
        first = True
        for cat in present:
            rows = table_rows[cat]
            tdef = schema.tables[cat]
            cols = _active_cols(tdef, rows, spec, reconstruct_su)
            if not cols:
                continue
            if not first:
                lines.append('')
            first = False
            lines.extend(_render_loop_category(rows, cols, cat, schema, version, tdef, reconstruct_su))
        return lines

    # Key-compatible: FULL OUTER JOIN in Python.
    shared_pks = sorted(pk_sets[0])

    # Index each table by PK tuple; collect all unique PK tuples in encounter order.
    all_pk_vals: list[tuple] = []
    seen_pk: set[tuple] = set()
    table_index: dict[str, dict[tuple, dict]] = {}
    for cat in present:
        table_index[cat] = {}
        for row in table_rows[cat]:
            pk_tuple = tuple(row.get(pk) for pk in shared_pks)
            if pk_tuple not in seen_pk:
                seen_pk.add(pk_tuple)
                all_pk_vals.append(pk_tuple)
            table_index[cat][pk_tuple] = row

    # Determine active (non-PK) columns per table.
    cat_active: dict[str, list[str]] = {}
    for cat in present:
        tdef = schema.tables[cat]
        all_rows = list(table_index[cat].values())
        cols = _active_cols(tdef, all_rows, spec, reconstruct_su)
        # Exclude shared PKs; they appear once at the start.
        non_pk_cols = [c for c in cols if c not in pk_sets[0]]
        if non_pk_cols or cols:
            cat_active[cat] = non_pk_cols

    # Build merged column list: shared PKs (from first present cat), then each table's non-PK cols.
    first_cat = present[0]
    first_tdef = schema.tables[first_cat]
    first_active = _active_cols(first_tdef, list(table_index[first_cat].values()), spec, reconstruct_su)
    pk_in_first = [pk for pk in shared_pks if pk in set(first_active)]

    merged_cols: list[tuple[str, str]] = [(first_cat, pk) for pk in pk_in_first]
    pk_set = set(shared_pks)
    for cat in present:
        for col in cat_active.get(cat, []):
            if col not in pk_set:
                merged_cols.append((cat, col))

    if not merged_cols:
        return []

    lines = ['loop_']
    for cat, col in merged_cols:
        lines.append(f'  {_col_tag(cat, col, schema)}')

    su_maps = {cat: (_su_col_map(schema.tables[cat]) if reconstruct_su else {}) for cat in present}

    for pk_vals in all_pk_vals:
        tokens = []
        for cat, col in merged_cols:
            row = table_index[cat].get(pk_vals, {})
            value = row.get(col)
            if value is None:
                token = '.'
            else:
                su_map = su_maps[cat]
                if reconstruct_su and col in su_map:
                    su_val = row.get(su_map[col])
                    if su_val is not None:
                        value = _merge_su(value, su_val)
                token = quote(value, version)
            tokens.append(token)
        lines.extend(_format_row(tokens))

    return lines


# ---------------------------------------------------------------------------
# Category renderers
# ---------------------------------------------------------------------------

def _render_set_category(
    row: dict,
    cols: list[str],
    table_name: str,
    schema: SchemaSpec,
    version: CifVersion,
    table_def: TableDef,
    reconstruct_su: bool,
) -> list[str]:
    """Emit a Set-class category as scalar tag–value pairs."""
    lines = []
    su_map = _su_col_map(table_def) if reconstruct_su else {}

    for col in cols:
        tag = _col_tag(table_name, col, schema)
        value = row.get(col)
        if value is None:
            continue

        if reconstruct_su and col in su_map:
            su_col = su_map[col]
            su_val = row.get(su_col)
            if su_val is not None:
                value = _merge_su(value, su_val)

        token = quote(value, version)
        if token.startswith('\n'):
            lines.append(tag)
            lines.extend(token.split('\n')[1:])
        else:
            lines.append(f'{tag}  {token}')

    return lines


def _render_loop_category(
    rows: list[dict],
    cols: list[str],
    table_name: str,
    schema: SchemaSpec,
    version: CifVersion,
    table_def: TableDef,
    reconstruct_su: bool,
) -> list[str]:
    """Emit a Loop-class category as a ``loop_`` construct."""
    su_map = _su_col_map(table_def) if reconstruct_su else {}

    lines = ['loop_']
    for col in cols:
        tag = _col_tag(table_name, col, schema)
        lines.append(f'  {tag}')

    for row in rows:
        tokens = []
        for col in cols:
            value = row.get(col)
            if value is None:
                token = '.'
            else:
                if reconstruct_su and col in su_map:
                    su_col = su_map[col]
                    su_val = row.get(su_col)
                    if su_val is not None:
                        value = _merge_su(value, su_val)
                token = quote(value, version)
            tokens.append(token)
        lines.extend(_format_row(tokens))

    return lines


def _render_fallback(rows: list[dict], version: CifVersion) -> list[str]:
    """Emit ``_cif_fallback`` rows as tag–value pairs or single-column loops."""
    tag_values: dict[str, list[tuple[str, str]]] = {}
    for row in sorted(rows, key=lambda r: (r.get('tag', ''), r.get('_row_id', 0))):
        tag = row.get('tag', '')
        value = row.get('value', '')
        vtype = row.get('value_type', '')
        tag_values.setdefault(tag, []).append((value, vtype))

    lines = []
    for tag in sorted(tag_values):
        entries = tag_values[tag]
        if len(entries) == 1:
            value, vtype = entries[0]
            token = _fallback_token(value, vtype, version)
            if token.startswith('\n'):
                lines.append(tag)
                lines.extend(token.split('\n')[1:])
            else:
                lines.append(f'{tag}  {token}')
        else:
            lines.append('loop_')
            lines.append(f'  {tag}')
            for value, vtype in entries:
                token = _fallback_token(value, vtype, version)
                lines.extend(_format_row([token]))

    return lines


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_row(tokens: list[str]) -> list[str]:
    """Format one loop data row as a list of output lines."""
    if not any(t.startswith('\n') for t in tokens):
        row_line = '  ' + '  '.join(tokens)
        return [row_line.rstrip()]

    result: list[str] = []
    inline_buf: list[str] = []

    for t in tokens:
        if t.startswith('\n'):
            if inline_buf:
                result.append(('  ' + '  '.join(inline_buf)).rstrip())
                inline_buf = []
            result.extend(t.split('\n')[1:])
        else:
            inline_buf.append(t)

    if inline_buf:
        result.append(('  ' + '  '.join(inline_buf)).rstrip())

    return result


# ---------------------------------------------------------------------------
# Column and tag helpers
# ---------------------------------------------------------------------------

def _active_cols(
    table_def: TableDef,
    rows: list[dict],
    spec: BlockSpec | None,
    reconstruct_su: bool,
) -> list[str]:
    """Return columns with at least one non-NULL value, in emission order."""
    su_col_names: set[str] = set()
    if reconstruct_su:
        for col in table_def.columns:
            if col.linked_item_id is not None:
                su_col_names.add(col.name)

    active_set = {
        col.name for col in table_def.columns
        if not col.is_synthetic
        and col.name not in su_col_names
        and any(row.get(col.name) is not None for row in rows)
    }

    if not active_set:
        return []

    if spec and table_def.name in spec.column_order:
        listed = [c for c in spec.column_order[table_def.name] if c in active_set]
        listed_set = set(listed)
        rest = sorted(c for c in active_set if c not in listed_set)
        return listed + rest

    pk_non_syn = [pk for pk in table_def.primary_keys if pk not in _SYNTHETIC and pk in active_set]
    other = sorted(c for c in active_set if c not in set(table_def.primary_keys))
    return pk_non_syn + other


def _suppressed_fk_pk_cols(
    table_def: TableDef,
    rows: list[dict],
    table_rows: dict[str, list[dict]],
    schema: SchemaSpec,
) -> set[str]:
    """Return FK-PK columns that are implicit from a co-emitted Set category."""
    pk_cols: set[str] = set(table_def.primary_keys) - _SYNTHETIC
    suppressed: set[str] = set()

    for fk in table_def.foreign_keys:
        target_name = fk.target_table
        target_def = schema.tables.get(target_name)
        if target_def is None or target_def.category_class != 'Set':
            continue
        target_table_rows = table_rows.get(target_name)
        if not target_table_rows or len(target_table_rows) != 1:
            continue

        if not all(c in pk_cols for c in fk.source_columns):
            continue

        target_row = target_table_rows[0]
        expected = tuple(target_row.get(c) for c in fk.target_columns)

        if all(tuple(row.get(c) for c in fk.source_columns) == expected for row in rows):
            suppressed.update(fk.source_columns)

    return suppressed


def _col_tag(table_name: str, col_name: str, schema: SchemaSpec) -> str:
    """Return the CIF tag name for a column."""
    return schema.column_to_tag.get((table_name, col_name), f'_{table_name}.{col_name}')


def _su_col_map(table_def: TableDef) -> dict[str, str]:
    """Return ``{measurand_col_name: su_col_name}`` for this table."""
    def_to_col: dict[str, str] = {
        col.definition_id: col.name
        for col in table_def.columns
        if col.linked_item_id is None and col.definition_id
    }
    result = {}
    for col in table_def.columns:
        if col.linked_item_id is not None:
            measurand_col = def_to_col.get(col.linked_item_id)
            if measurand_col:
                result[measurand_col] = col.name
    return result


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------

def _fallback_token(value: str, vtype: str, version: CifVersion) -> str:
    """Produce a CIF token for a ``_cif_fallback`` value."""
    if vtype == 'placeholder':
        return value
    return quote(value, version)


def _merge_su(measurand: str, scaled_su: str) -> str:
    """Reconstruct ``value(su)`` from stored measurand and scaled SU strings."""
    try:
        e_match = re.search(r'[eE]([+-]?\d+)$', measurand)
        exponent = int(e_match.group(1)) if e_match else 0
        mantissa = measurand[:e_match.start()] if e_match else measurand
        dot_idx = mantissa.find('.')
        decimal_places = (len(mantissa) - dot_idx - 1) if dot_idx >= 0 else 0
        total_power = exponent - decimal_places
        su_float = float(scaled_su)
        if total_power >= 0:
            su_int = round(su_float / (10 ** total_power))
        else:
            su_int = round(su_float * (10 ** (-total_power)))
        return f'{measurand}({su_int})'
    except (ValueError, AttributeError, ZeroDivisionError):
        return measurand


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _fetch_rows(
    conn: sqlite3.Connection,
    table_name: str,
    where: str | None = None,
    params: tuple = (),
) -> list[dict]:
    """Fetch all rows from *table_name* as a list of column→value dicts."""
    try:
        sql = f'SELECT * FROM "{table_name}"'
        if where:
            sql += f' WHERE {where}'
        cursor = conn.execute(sql, params)
        col_names = [d[0] for d in cursor.description]
        return [dict(zip(col_names, row)) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []


def _find_set_anchor(table_name: str, schema: SchemaSpec) -> str | None:
    """Find the root Set-class ancestor reachable from *table_name* via FK links."""
    visited: set[str] = {table_name}
    queue: deque[str] = deque([table_name])
    reachable_sets: list[str] = []

    td0 = schema.tables.get(table_name)
    if td0 is None:
        return None
    if td0.category_class == 'Set':
        reachable_sets.append(table_name)

    while queue:
        current = queue.popleft()
        td = schema.tables.get(current)
        if td is None:
            continue
        for fk in td.foreign_keys:
            target = fk.target_table
            if target not in visited and target in schema.tables:
                visited.add(target)
                target_td = schema.tables[target]
                if target_td.category_class == 'Set':
                    reachable_sets.append(target)
                queue.append(target)

    if not reachable_sets:
        return None

    reachable_set_names = set(reachable_sets)
    for s in reachable_sets:
        td = schema.tables[s]
        has_set_parent = any(
            fk.target_table in reachable_set_names and fk.target_table != s
            for fk in td.foreign_keys
        )
        if not has_set_parent:
            return s

    return reachable_sets[-1]


def _fk_chain(from_table: str, to_table: str, schema: SchemaSpec) -> list[ForeignKeyDef] | None:
    """BFS to find the FK-hop path from *from_table* to *to_table*."""
    if from_table == to_table:
        return []
    visited: set[str] = {from_table}
    queue: deque[tuple[str, list[ForeignKeyDef]]] = deque([(from_table, [])])
    while queue:
        current, path = queue.popleft()
        td = schema.tables.get(current)
        if td is None:
            continue
        for fk in td.foreign_keys:
            if fk.target_table not in visited and fk.target_table in schema.tables:
                new_path = path + [fk]
                if fk.target_table == to_table:
                    return new_path
                visited.add(fk.target_table)
                queue.append((fk.target_table, new_path))
    return None


def _fetch_rows_via_fk_path(
    conn: sqlite3.Connection,
    from_table: str,
    fk_path: list[ForeignKeyDef],
    anchor_pk_cols: list[str],
    anchor_pk_vals: tuple,
) -> list[dict]:
    """Fetch rows from *from_table* that transitively FK-link to the anchor row."""
    if not fk_path or not anchor_pk_cols:
        return _fetch_rows(conn, from_table)

    aliases = [f't{i}' for i in range(len(fk_path) + 1)]
    sql = f'SELECT {aliases[0]}.* FROM "{from_table}" AS {aliases[0]}'
    for i, fk in enumerate(fk_path):
        src_alias = aliases[i]
        dst_alias = aliases[i + 1]
        conds = ' AND '.join(
            f'{src_alias}."{sc}" = {dst_alias}."{tc}"'
            for sc, tc in zip(fk.source_columns, fk.target_columns)
        )
        sql += f' JOIN "{fk.target_table}" AS {dst_alias} ON {conds}'

    anchor_alias = aliases[-1]
    where = ' AND '.join(f'{anchor_alias}."{col}" = ?' for col in anchor_pk_cols)
    sql += f' WHERE {where}'

    try:
        cursor = conn.execute(sql, anchor_pk_vals)
        col_names = [d[0] for d in cursor.description]
        return [dict(zip(col_names, row)) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []


def _all_block_ids_for_tables(conn: sqlite3.Connection, table_names: list[str]) -> list[str]:
    """Return sorted distinct ``_block_id`` values across the given tables."""
    seen: set[str] = set()
    ids: list[str] = []
    for table_name in table_names:
        try:
            cursor = conn.execute(f'SELECT DISTINCT "_block_id" FROM "{table_name}"')
            for (bid,) in cursor:
                if bid not in seen:
                    seen.add(bid)
                    ids.append(bid)
        except sqlite3.OperationalError:
            pass
    return sorted(ids)


def _all_block_ids(conn: sqlite3.Connection, schema: SchemaSpec) -> list[str]:
    """Return sorted list of all distinct ``_block_id`` values across all tables."""
    seen: set[str] = set()
    ids: list[str] = []

    for table_name in list(schema.tables.keys()) + ['_cif_fallback']:
        try:
            cursor = conn.execute(f'SELECT DISTINCT "_block_id" FROM "{table_name}"')
            for (bid,) in cursor:
                if bid not in seen:
                    seen.add(bid)
                    ids.append(bid)
        except sqlite3.OperationalError:
            pass

    return sorted(ids)
