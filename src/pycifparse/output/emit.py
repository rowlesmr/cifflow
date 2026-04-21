"""
CIF emission from a populated SQLite database.

``emit(conn, schema, ...)`` reads structured tables and the ``_cif_fallback``
table and produces a valid CIF string.

Assumption: by emission time, all data in the database is assumed to belong to
a single coherent dataset.  Namespace conflicts (e.g. short identifiers from
unrelated sources) are not detected or resolved by the output layer.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
import warnings as _warnings
from collections import deque
from dataclasses import dataclass

from pycifparse.dictionary.schema import ForeignKeyDef, SchemaSpec, TableDef
from pycifparse.output.plan import BlockSpec, EmitMode, OutputPlan
from pycifparse.output.quote import make_text_field, quote
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
    suppress_loop_fk_pk: bool = False  # ORIGINAL mode only: suppress FK cols from Loop categories
    dataset_id: str | list[str] | None = None
    preferred_category_order: list[str] | None = None  # ALL_BLOCKS: parent tables before child
    conformance_tags: list[tuple[str, str]] | None = None  # ONE_BLOCK: injected before all data


def _make_block_data(
    name: str,
    table_rows: dict[str, list[dict]],
    fallback_rows: list[dict],
    schema: SchemaSpec,
    suppress_fk_pk: bool,
    suppress_loop_fk_pk: bool = False,
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
        suppress_loop_fk_pk=suppress_loop_fk_pk,
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
    pretty: bool = True,
    line_limit: int | None = 2048,
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
    pretty:
        When ``True`` (default), tag–value pairs are column-aligned within
        each Set category and loop column values are padded to the widest
        value in that column.  When ``False``, output is compact (two spaces
        between tag and value / between tokens) — recommended for very large
        loop tables where the alignment pass would be expensive.
    line_limit:
        Maximum physical line length (in characters, before line endings are
        applied).  Default ``2048``.  Use ``None`` to disable.  Values below
        ``40`` are accepted but emit a ``UserWarning``; very small limits may
        produce degenerate output for long tokens.

        When a content line inside a semicolon-delimited text field exceeds
        *line_limit*, the CIF 2.0 line-folding protocol (§5.3) is applied.
        When ``'\\n;'`` is also present in the value, the text-prefix protocol
        (§5.2) is combined with folding.

        Inline scalar values whose formatted line (tag + separator + token)
        would exceed *line_limit* are converted to semicolon-delimited fields.

        Loop data rows that exceed *line_limit* are wrapped across multiple
        physical lines using greedy token packing (tokens cannot be split).

        CIF 1.1 block codes, data names, and frame codes are independently
        limited to 75 characters by the CIF 1.1 specification; an exception
        is raised if this limit would be violated.

    Returns
    -------
    str
        Complete CIF text including magic line, terminated with ``line_ending``.
    """
    if line_limit is not None and line_limit < 40:
        _warnings.warn(
            f'line_limit={line_limit} is very small; output may be degenerate for long tokens',
            UserWarning,
            stacklevel=2,
        )

    magic = '#\\#CIF_2.0' if version == CifVersion.CIF_2_0 else '#\\#CIF_1.1'

    if mode == EmitMode.ONE_BLOCK:
        raw_blocks = _collect_one_block(conn, schema)
    elif mode == EmitMode.ALL_BLOCKS:
        raw_blocks = _collect_all_blocks(conn, schema, version, plan)
    elif mode == EmitMode.GROUPED:
        raw_blocks = _collect_grouped(conn, schema)
    else:  # ORIGINAL
        raw_blocks = _collect_original(conn, schema)

    if mode == EmitMode.ALL_BLOCKS:
        plan_spec = plan.specs[0] if plan and plan.specs else None
        ordered = [(b, plan_spec) for b in raw_blocks]
    else:
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
        lines.extend(_render_block(name, data, schema, version, spec, reconstruct_su, pretty, line_limit))

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
        suppress_loop_fk_pk=block.suppress_loop_fk_pk,
        dataset_id=block.dataset_id,
        conformance_tags=block.conformance_tags,
        preferred_category_order=block.preferred_category_order,
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
        for table_name, table_def in schema.tables.items():
            rows = _fetch_rows_for_block(conn, bid, table_name, table_def)
            if rows:
                table_rows[table_name] = rows

        fallback = _fetch_rows(conn, '_cif_fallback', '"_block_id" = ?', (bid,))
        result.append(_make_block_data(bid, table_rows, fallback, schema, suppress_fk_pk=True, suppress_loop_fk_pk=True))
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
    """ONE_BLOCK: all data in a single block.

    The anchor_key_dict is intentionally empty: the block name is not derived
    from key values (which would concatenate every anchor key from the entire
    database).  The name is resolved by block_namer if provided, otherwise
    falls back to ``'output'``.
    """
    table_rows = {}
    for table_name in schema.tables:
        rows = _fetch_rows(conn, table_name)
        if rows:
            table_rows[table_name] = rows
    fallback = _fetch_rows(conn, '_cif_fallback')

    # Build conformance tags — only inject when dictionary metadata is available
    # and the relevant tables are not already present in the database.
    conformance: list[tuple[str, str]] = []
    audit_conform_present = 'audit_conform' in table_rows or any(
        (r.get('tag') or '').lower().startswith('_audit_conform.') for r in fallback
    )
    if not audit_conform_present:
        conform_block: list[tuple[str, str]] = []
        if schema.dictionary_title:
            conform_block.append(('_audit_conform.dict_name', schema.dictionary_title))
        if schema.dictionary_version:
            conform_block.append(('_audit_conform.dict_version', schema.dictionary_version))
        if schema.dictionary_uri:
            conform_block.append(('_audit_conform.dict_location', schema.dictionary_uri))
        if conform_block:
            # Only emit _audit.schema when there are conformance entries to accompany it.
            audit_present = 'audit' in table_rows or any(
                (r.get('tag') or '').lower() == '_audit.schema' for r in fallback
            )
            if not audit_present:
                conformance.append(('_audit.schema', 'Custom'))
            conformance.extend(conform_block)

    return [_BlockData(
        name='output',
        table_rows=table_rows,
        fallback_rows=fallback,
        anchor_frozenset=frozenset(),
        anchor_key_dict={},
        suppress_fk_pk=False,
        conformance_tags=conformance or None,
    )]


def _classify_pk_cols(
    tdef: TableDef,
    schema: SchemaSpec,
) -> list[tuple[str, bool, str | None, str | None, str | None]]:
    """Classify each domain PK column as Set-key or Loop-key.

    Returns a list of ``(col_name, is_set_key, parent_tag, set_table, set_col)``
    in primary-key declaration order.  For Set-key columns, ``parent_tag`` is
    the canonical tag of the ultimate Set PK column, ``set_table`` and
    ``set_col`` identify where to inject synthetic parent rows.  All three are
    ``None`` for Loop-key columns.

    Handles both single- and multi-column FKs, and follows one hop through a
    Loop-class intermediate to reach the Set (e.g. pd_calc → pd_data →
    pd_diffractogram).
    """
    # Build col → [(target_table, target_col)] from every FK (single or composite).
    col_to_targets: dict[str, list[tuple[str, str]]] = {}
    for fk in tdef.foreign_keys:
        for src, tgt in zip(fk.source_columns, fk.target_columns):
            col_to_targets.setdefault(src, []).append((fk.target_table, tgt))

    result: list[tuple[str, bool, str | None, str | None, str | None]] = []
    for col_name in tdef.primary_keys:
        if col_name in _SYNTHETIC:
            continue
        found = False
        for target_table, target_col in col_to_targets.get(col_name, []):
            target_tdef = schema.tables.get(target_table)
            if target_tdef is None:
                continue
            if target_tdef.category_class == 'Set':
                parent_tag = schema.column_to_tag.get((target_table, target_col))
                result.append((col_name, True, parent_tag, target_table, target_col))
                found = True
                break
            # One hop through a Loop intermediate.
            for hop_fk in target_tdef.foreign_keys:
                if target_col not in hop_fk.source_columns:
                    continue
                idx = hop_fk.source_columns.index(target_col)
                ult_table = hop_fk.target_table
                ult_col = hop_fk.target_columns[idx]
                ult_tdef = schema.tables.get(ult_table)
                if ult_tdef and ult_tdef.category_class == 'Set':
                    parent_tag = schema.column_to_tag.get((ult_table, ult_col))
                    result.append((col_name, True, parent_tag, ult_table, ult_col))
                    found = True
                    break
            if found:
                break
        if not found:
            result.append((col_name, False, None, None, None))
    return result


def _ordered_tables_all_blocks(
    schema: SchemaSpec,
    plan: 'OutputPlan | None',
) -> list[str]:
    """Return table names in ALL_BLOCKS emission order.

    If *plan* supplies a category order (taken from the first BlockSpec that
    has one), tables are emitted in that order (with wildcard expansion);
    remaining tables follow alphabetically, Set categories before Loop.
    """
    all_tables = set(schema.tables.keys())
    result: list[str] = []
    listed: set[str] = set()

    category_order = None
    if plan:
        for spec in plan.specs:
            if spec.category_order:
                category_order = spec.category_order
                break

    if category_order:
        for item in category_order:
            names = item if isinstance(item, list) else [item]
            for name in names:
                if name.endswith('*'):
                    for t in _expand_wildcard(name, schema):
                        if t in all_tables and t not in listed:
                            result.append(t)
                            listed.add(t)
                else:
                    if name in all_tables and name not in listed:
                        result.append(name)
                        listed.add(name)

    set_rem = sorted(t for t in all_tables if t not in listed and schema.tables[t].category_class == 'Set')
    loop_rem = sorted(t for t in all_tables if t not in listed and schema.tables[t].category_class != 'Set')
    result.extend(set_rem)
    result.extend(loop_rem)
    return result


def _resolve_dataset_id(
    conn: sqlite3.Connection,
    block_ids: set[str],
    fallback: 'str | None',
) -> 'str | list[str] | None':
    """Return the _audit_dataset_id(s) for the given originating _block_id values.

    Ignores synthetic empty-string block_ids.  If _block_dataset_membership is
    absent or has no matching rows, returns *fallback* (a shared UUID generated
    once per emit call, or None for CIF 1.1).
    Returns a plain str for one match, a sorted list for multiple.
    """
    real_bids = {b for b in block_ids if b}
    if real_bids:
        try:
            placeholders = ','.join('?' * len(real_bids))
            rows = conn.execute(
                f'SELECT DISTINCT "_audit_dataset_id" FROM "_block_dataset_membership" '
                f'WHERE "_block_id" IN ({placeholders})',
                tuple(sorted(real_bids)),
            ).fetchall()
            ids = sorted(r[0] for r in rows if r[0])
            if len(ids) == 1:
                return ids[0]
            if len(ids) > 1:
                return ids
        except sqlite3.OperationalError:
            pass
    return fallback


def _collect_all_blocks(
    conn: sqlite3.Connection,
    schema: SchemaSpec,
    version: CifVersion,
    plan: 'OutputPlan | None' = None,
) -> list[_BlockData]:
    """ALL_BLOCKS: one block per table per Set-key combination.

    Each table is emitted independently into its own block(s):

    - **Set category**: one block per row.  Block name =
      ``{table_name}_{pk_val...}``.
    - **Loop category, only Loop-category keys**: one block for all rows.
      Block name = ``{table_name}``.
    - **Loop category, one or more Set-category keys**: one block per unique
      combination of Set-key values.  Block name =
      ``{table_name}_{set_val...}``.  Set-key values are emitted as scalar
      tag-value pairs above the loop using the parent category's tag name;
      the corresponding FK columns are suppressed from the loop header.

    Table emission order follows *plan*'s category order (same wildcard
    notation as GROUPED); unspecified tables follow alphabetically
    (Set categories before Loop).

    Raises ``ValueError`` if the database contains fallback rows or rows in
    keyless Set tables — neither can be unambiguously assigned to a
    dictionary-split block.
    """
    # Guard: fallback rows
    fallback_count = conn.execute('SELECT COUNT(*) FROM "_cif_fallback"').fetchone()[0]
    if fallback_count:
        raise ValueError(
            f"ALL_BLOCKS requires all tags to be known to the dictionary, but "
            f"{fallback_count} fallback row(s) are present in _cif_fallback. "
            f"Unknown tags cannot be reliably assigned to a dictionary-split block."
        )

    # Guard: keyless Set tables (Set tables with no domain primary key)
    keyless_problems: list[str] = []
    for table_name, tdef in schema.tables.items():
        if tdef.category_class != 'Set':
            continue
        domain_pks = [pk for pk in tdef.primary_keys if pk not in _SYNTHETIC]
        if domain_pks:
            continue
        count = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        if count:
            keyless_problems.append(f"{table_name} ({count} row(s))")
    if keyless_problems:
        raise ValueError(
            f"ALL_BLOCKS requires every Set category to have a domain primary key, "
            f"but the following keyless Set table(s) contain data: "
            f"{', '.join(keyless_problems)}. "
            f"Rows in keyless Sets cannot be unambiguously associated with a "
            f"dictionary-split block."
        )

    fallback_id: str | None = str(uuid.uuid4()) if version == CifVersion.CIF_2_0 else None
    result: list[_BlockData] = []

    for table_name in _ordered_tables_all_blocks(schema, plan):
        tdef = schema.tables[table_name]
        rows = _fetch_rows(conn, table_name)
        if not rows:
            continue

        domain_pks = [pk for pk in tdef.primary_keys if pk not in _SYNTHETIC]

        if tdef.category_class == 'Set':
            # One block per row.
            # Classify PK columns: some may FK to a parent Set category.
            col_info = _classify_pk_cols(tdef, schema)
            set_key_cols = [(col, tag, st, sc) for col, is_set, tag, st, sc in col_info if is_set]

            for row in sorted(rows, key=lambda r: tuple(r.get(pk) or '' for pk in domain_pks)):
                pk_vals = [str(row.get(pk) or '') for pk in domain_pks]
                block_name = _sanitize_block_name('_'.join([table_name] + pk_vals)) or table_name

                block_table_rows: dict[str, list[dict]] = {table_name: [row]}
                parent_tables: list[str] = []
                if set_key_cols:
                    # Inject synthetic parent rows so _suppressed_fk_pk_cols
                    # suppresses the FK column and the parent tag is emitted as a scalar.
                    for (col, _parent_tag, set_table, set_col) in set_key_cols:
                        val = row.get(col)
                        if set_table and val is not None:
                            block_table_rows[set_table] = [
                                {'_block_id': '', '_row_id': 0, set_col: val}
                            ]
                            parent_tables.append(set_table)

                cat_order = sorted(parent_tables) + [table_name] if parent_tables else None
                did = _resolve_dataset_id(conn, {row.get('_block_id')}, fallback_id)
                result.append(_BlockData(
                    name=block_name,
                    table_rows=block_table_rows,
                    fallback_rows=[],
                    anchor_frozenset=frozenset(),
                    anchor_key_dict={},
                    suppress_fk_pk=bool(set_key_cols),
                    dataset_id=did,
                    preferred_category_order=cat_order,
                ))
        else:
            # Loop category: classify PK columns.
            col_info = _classify_pk_cols(tdef, schema)
            set_key_cols = [(col, tag, st, sc) for col, is_set, tag, st, sc in col_info if is_set]

            if not set_key_cols:
                # Pure Loop — one block for all rows.
                block_name = _sanitize_block_name(table_name) or table_name
                did = _resolve_dataset_id(conn, {row.get('_block_id') for row in rows}, fallback_id)
                result.append(_BlockData(
                    name=block_name,
                    table_rows={table_name: rows},
                    fallback_rows=[],
                    anchor_frozenset=frozenset(),
                    anchor_key_dict={},
                    suppress_fk_pk=False,
                    dataset_id=did,
                ))
            else:
                # Group rows by Set-key tuple.
                groups: dict[tuple, list[dict]] = {}
                for row in rows:
                    key = tuple(row.get(col) for col, _, _, _ in set_key_cols)
                    groups.setdefault(key, []).append(row)

                for set_vals in sorted(groups, key=lambda t: tuple(v or '' for v in t)):
                    group_rows = groups[set_vals]
                    val_strs = [str(v or '') for v in set_vals]
                    block_name = _sanitize_block_name('_'.join([table_name] + val_strs)) or table_name

                    # Inject synthetic single-row Set parent entries so that
                    # _suppressed_fk_pk_cols can find them (suppressing the FK
                    # columns from the loop) and _render_set_category emits them
                    # as scalar tag-value pairs above the loop.
                    block_table_rows = {table_name: group_rows}
                    parent_tables = []
                    for (col, _parent_tag, set_table, set_col), val in zip(set_key_cols, set_vals):
                        if set_table and val is not None:
                            block_table_rows[set_table] = [
                                {'_block_id': '', '_row_id': 0, set_col: val}
                            ]
                            parent_tables.append(set_table)

                    cat_order = sorted(parent_tables) + [table_name]
                    did = _resolve_dataset_id(conn, {row.get('_block_id') for row in group_rows}, fallback_id)
                    result.append(_BlockData(
                        name=block_name,
                        table_rows=block_table_rows,
                        fallback_rows=[],
                        anchor_frozenset=frozenset(),
                        anchor_key_dict={},
                        suppress_fk_pk=True,
                        suppress_loop_fk_pk=True,
                        dataset_id=did,
                        preferred_category_order=cat_order,
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
    pretty: bool,
    line_limit: int | None = None,
) -> list[str]:
    """Render a single CIF block as a flat list of output lines."""
    if version == CifVersion.CIF_1_1 and len(block_name) > 75:
        raise ValueError(
            f"CIF 1.1 block code {block_name!r} exceeds the 75-character identifier "
            f"limit (length {len(block_name)})"
        )
    lines: list[str] = [f'data_{block_name}']
    first_category = True

    # Partition fallback rows into three groups:
    #   mixed_fallback  — unknown tags that were in a loop alongside known tags;
    #                     keyed ref_table -> loop_id -> col_index -> {row_id: (value, vtype)}
    #   pure_loop_rows  — unknown tags in a loop with no known tags; keyed by loop_id
    #   remnant_rows    — scalar fallback (loop_id is None) and anything not injected
    mixed_fallback: dict[str, dict[int, dict[int, dict[int, tuple]]]] = {}
    pure_loop_rows: dict[int, list[dict]] = {}
    remnant_rows: list[dict] = []

    for r in data.fallback_rows:
        lid = r.get('loop_id')
        ref = r.get('ref_table')
        if lid is None:
            remnant_rows.append(r)
        elif ref is not None:
            col_idx = r.get('col_index', 0) or 0
            row_id = r.get('_row_id', 0)
            val = r.get('value', '')
            vtype = r.get('value_type', '')
            tag = r.get('tag', '')
            (mixed_fallback
             .setdefault(ref, {})
             .setdefault(lid, {})
             .setdefault(col_idx, {}))[row_id] = (tag, val, vtype)
        else:
            pure_loop_rows.setdefault(lid, []).append(r)

    # Build per-table extra-column list:
    # ref_table -> list of (tag, col_index, {row_id: (value, vtype)})
    # ordered by (loop_id, col_index) to preserve original column ordering.
    extra_cols_for: dict[str, list[tuple[str, int, dict[int, tuple]]]] = {}
    for ref, loop_dict in mixed_fallback.items():
        cols_list: list[tuple[str, int, dict[int, tuple]]] = []
        for lid in sorted(loop_dict):
            for col_idx in sorted(loop_dict[lid]):
                cell_map = loop_dict[lid][col_idx]
                # All cells for this (loop_id, col_idx) share the same tag.
                sample = next(iter(cell_map.values()))
                tag = sample[0]
                # row_id -> (value, vtype)
                row_vals = {rid: (v, vt) for rid, (_, v, vt) in cell_map.items()}
                cols_list.append((tag, col_idx, row_vals))
        extra_cols_for[ref] = cols_list

    # Inject conformance tags (ONE_BLOCK) before all other content.
    if data.conformance_tags:
        for ctag, cval in data.conformance_tags:
            lines.append(f'{ctag}  {quote(cval, version)}')
        first_category = False

    # Inject _audit_dataset.id when requested.
    if data.dataset_id is not None:
        audit_in_table = 'audit_dataset' in data.table_rows
        audit_in_fallback = any(
            (r.get('tag') or '').lower() == '_audit_dataset.id'
            for r in data.fallback_rows
        )
        if not audit_in_table and not audit_in_fallback:
            audit_tag = schema.column_to_tag.get(('audit_dataset', 'id'), '_audit_dataset.id')
            if isinstance(data.dataset_id, list):
                lines.append('loop_')
                lines.append(f'  {audit_tag}')
                for did in data.dataset_id:
                    lines.append(f'  {quote(did, version)}')
            else:
                lines.append(f'{audit_tag}  {quote(data.dataset_id, version)}')
            first_category = False

    effective_spec = spec
    if data.preferred_category_order:
        effective_spec = BlockSpec(
            category_order=data.preferred_category_order,
            column_order=spec.column_order if spec is not None else {},
        )

    for item in _ordered_categories(schema, effective_spec, data.table_rows):
        if isinstance(item, list):
            # Merge group
            cat_lines = _render_merge_group(item, data.table_rows, schema, version, spec, reconstruct_su, pretty, line_limit)
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

            if data.suppress_fk_pk and (
                (table_def.category_class == 'Set' and len(rows) == 1)
                or data.suppress_loop_fk_pk
            ):
                suppressed = _suppressed_fk_pk_cols(table_def, rows, data.table_rows, schema)
                cols = [c for c in cols if c not in suppressed]
            if not cols:
                continue

            if not first_category:
                lines.append('')
            first_category = False

            extra = extra_cols_for.get(table_name)
            if table_def.category_class == 'Set' and len(rows) == 1:
                lines.extend(_render_set_category(rows[0], cols, table_name, schema, version, table_def, reconstruct_su, pretty, line_limit))
            else:
                lines.extend(_render_loop_category(rows, cols, table_name, schema, version, table_def, reconstruct_su, pretty, line_limit, extra_fallback_cols=extra))

    # Pure-fallback loops: emit each loop_id group as a standalone loop_.
    for lid in sorted(pure_loop_rows):
        loop_rows = pure_loop_rows[lid]
        if not first_category:
            lines.append('')
        first_category = False
        lines.extend(_render_pure_fallback_loop(loop_rows, version, pretty, line_limit))

    # Scalar fallback and any remnant rows (scalars, or loop rows whose ref_table
    # is not present in this block's table_rows — treated as plain fallback).
    actual_remnant = remnant_rows
    for ref, cols_list in extra_cols_for.items():
        if ref not in data.table_rows:
            # ref_table not rendered in this block: fall back to plain fallback.
            for tag, _ci, row_vals in cols_list:
                for _rid, (val, vtype) in row_vals.items():
                    actual_remnant.append({'tag': tag, 'value': val, 'value_type': vtype})
    if actual_remnant:
        if not first_category:
            lines.append('')
        lines.extend(_render_fallback(actual_remnant, version, pretty, line_limit))

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
    pretty: bool,
    line_limit: int | None = None,
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
            lines.extend(_render_loop_category(rows, cols, cat, schema, version, tdef, reconstruct_su, pretty, line_limit))
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

    # Build token matrix.
    matrix: list[list[str]] = []
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
                if line_limit is not None:
                    token = _apply_line_limit(value, token, line_limit)
            tokens.append(token)
        matrix.append(tokens)

    if pretty:
        real_idx = _real_col_indices_merged(merged_cols, schema)
        if real_idx:
            matrix = _apply_decimal_align(matrix, real_idx)

    col_widths = _col_widths(matrix) if pretty else None

    for tokens in matrix:
        lines.extend(_format_row(tokens, col_widths, line_limit))

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
    pretty: bool,
    line_limit: int | None = None,
) -> list[str]:
    """Emit a Set-class category as scalar tag–value pairs."""
    lines = []
    su_map = _su_col_map(table_def) if reconstruct_su else {}

    # Build (tag, col, value, token) quads; apply folding to any multiline tokens.
    quads: list[tuple[str, str, str, str]] = []
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
        if line_limit is not None and token.startswith('\n'):
            token = make_text_field(value, line_limit)
        quads.append((tag, col, value, token))

    if pretty:
        tag_width = max(
            (len(tag) for tag, _c, _v, token in quads if not token.startswith('\n')),
            default=0,
        )
    else:
        tag_width = 0

    # Re-quote inline tokens whose formatted line would exceed line_limit.
    if line_limit is not None:
        new_quads: list[tuple[str, str, str, str]] = []
        for tag, col, value, token in quads:
            if not token.startswith('\n'):
                line_str = f'{tag:<{tag_width}}  {token}' if pretty else f'{tag}  {token}'
                if len(line_str) > line_limit:
                    token = make_text_field(value, line_limit)
            new_quads.append((tag, col, value, token))
        quads = new_quads
        # Recompute tag_width now that some inline tokens may have become multiline.
        if pretty:
            tag_width = max(
                (len(tag) for tag, _c, _v, token in quads if not token.startswith('\n')),
                default=0,
            )

    # Decimal-align all inline Real/Float tokens within this Set category.
    if pretty:
        col_type = {c.name: c.type_contents for c in table_def.columns}
        real_positions = [
            i for i, (tag, col, _v, token) in enumerate(quads)
            if col_type.get(col) in ('Real', 'Float') and not token.startswith('\n')
        ]
        if real_positions:
            real_tokens = [quads[i][3] for i in real_positions]
            aligned = _decimal_align_column(real_tokens)
            quads = list(quads)
            for pos, new_tok in zip(real_positions, aligned):
                tag, col, val, _old = quads[pos]
                quads[pos] = (tag, col, val, new_tok)

    for tag, _col, _value, token in quads:
        if token.startswith('\n'):
            lines.append(tag)
            lines.extend(token.split('\n')[1:])
        elif pretty:
            lines.append(f'{tag:<{tag_width}}  {token}')
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
    pretty: bool,
    line_limit: int | None = None,
    extra_fallback_cols: 'list[tuple[str, int, dict[int, tuple]]] | None' = None,
) -> list[str]:
    """Emit a Loop-class category as a ``loop_`` construct.

    *extra_fallback_cols* is a list of ``(tag, col_index, {row_id: (value, vtype)})``
    tuples for unknown tags that were in the same source loop as this category.
    They are appended as additional columns after the structured ones, aligned
    by the row's ``_row_id``.
    """
    su_map = _su_col_map(table_def) if reconstruct_su else {}

    lines = ['loop_']
    for col in cols:
        tag = _col_tag(table_name, col, schema)
        lines.append(f'  {tag}')
    if extra_fallback_cols:
        for tag, _ci, _row_vals in extra_fallback_cols:
            lines.append(f'  {tag}')

    # Build token matrix: one quote() call per cell.
    matrix: list[list[str]] = []
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
                if line_limit is not None:
                    token = _apply_line_limit(value, token, line_limit)
            tokens.append(token)
        if extra_fallback_cols:
            row_id = row.get('_row_id')
            for _tag, _ci, row_vals in extra_fallback_cols:
                cell = row_vals.get(row_id)
                if cell is None:
                    tokens.append('.')
                else:
                    val, vtype = cell
                    token = _fallback_token(val, vtype, version)
                    if line_limit is not None and token.startswith('\n') and vtype != 'placeholder':
                        token = make_text_field(val, line_limit)
                    tokens.append(token)
        matrix.append(tokens)

    if pretty:
        real_idx = _real_col_indices(cols, table_def)
        if real_idx:
            matrix = _apply_decimal_align(matrix, real_idx)

    col_widths = _col_widths(matrix) if pretty else None

    for tokens in matrix:
        lines.extend(_format_row(tokens, col_widths, line_limit))

    return lines


def _render_pure_fallback_loop(
    rows: list[dict],
    version: CifVersion,
    pretty: bool = False,
    line_limit: int | None = None,
) -> list[str]:
    """Emit a group of unknown tags that shared a loop with no structured columns."""
    # Group by tag, ordered by col_index within the loop then by row_id.
    tag_order: list[str] = []
    seen_tags: set[str] = set()
    for r in sorted(rows, key=lambda r: (r.get('col_index') or 0, r.get('_row_id') or 0)):
        t = r.get('tag', '')
        if t not in seen_tags:
            tag_order.append(t)
            seen_tags.add(t)

    # Build per-tag ordered value list (sorted by _row_id).
    tag_values: dict[str, list[tuple[str, str]]] = {t: [] for t in tag_order}
    for t in tag_order:
        for r in sorted(
            (r for r in rows if r.get('tag') == t),
            key=lambda r: r.get('_row_id') or 0,
        ):
            tag_values[t].append((r.get('value', ''), r.get('value_type', '')))

    if not tag_order:
        return []

    lines = ['loop_']
    for t in tag_order:
        lines.append(f'  {t}')

    n_rows = len(tag_values[tag_order[0]])
    matrix: list[list[str]] = []
    for i in range(n_rows):
        tokens = []
        for t in tag_order:
            entries = tag_values[t]
            if i < len(entries):
                val, vtype = entries[i]
                token = _fallback_token(val, vtype, version)
                if line_limit is not None and token.startswith('\n') and vtype != 'placeholder':
                    token = make_text_field(val, line_limit)
            else:
                token = '.'
            tokens.append(token)
        matrix.append(tokens)

    col_widths = _col_widths(matrix) if pretty else None
    for tokens in matrix:
        lines.extend(_format_row(tokens, col_widths, line_limit))
    return lines


def _render_fallback(
    rows: list[dict],
    version: CifVersion,
    pretty: bool = False,
    line_limit: int | None = None,
) -> list[str]:
    """Emit ``_cif_fallback`` rows as tag–value pairs or single-column loops."""
    tag_values: dict[str, list[tuple[str, str]]] = {}
    for row in sorted(rows, key=lambda r: (r.get('tag', ''), r.get('_row_id', 0))):
        tag = row.get('tag', '')
        value = row.get('value', '')
        vtype = row.get('value_type', '')
        tag_values.setdefault(tag, []).append((value, vtype))

    # Scalar tags: build (tag, value, vtype, token) tuples first for alignment.
    scalar_tuples: list[tuple[str, str, str, str]] = []
    for tag in sorted(tag_values):
        entries = tag_values[tag]
        if len(entries) == 1:
            value, vtype = entries[0]
            token = _fallback_token(value, vtype, version)
            if line_limit is not None and token.startswith('\n') and vtype != 'placeholder':
                token = make_text_field(value, line_limit)
            scalar_tuples.append((tag, value, vtype, token))

    if pretty and scalar_tuples:
        tag_width = max(
            (len(tag) for tag, _v, _vt, token in scalar_tuples if not token.startswith('\n')),
            default=0,
        )
    else:
        tag_width = 0

    # Re-quote inline tokens whose formatted line would exceed line_limit.
    if line_limit is not None:
        new_tuples: list[tuple[str, str, str, str]] = []
        for tag, value, vtype, token in scalar_tuples:
            if not token.startswith('\n') and vtype != 'placeholder':
                line_str = f'{tag:<{tag_width}}  {token}' if pretty else f'{tag}  {token}'
                if len(line_str) > line_limit:
                    token = make_text_field(value, line_limit)
            new_tuples.append((tag, value, vtype, token))
        scalar_tuples = new_tuples
        if pretty:
            tag_width = max(
                (len(tag) for tag, _v, _vt, token in scalar_tuples if not token.startswith('\n')),
                default=0,
            )

    lines = []
    scalar_map = {tag: token for tag, _v, _vt, token in scalar_tuples}

    for tag in sorted(tag_values):
        entries = tag_values[tag]
        if len(entries) == 1:
            token = scalar_map[tag]
            if token.startswith('\n'):
                lines.append(tag)
                lines.extend(token.split('\n')[1:])
            elif pretty:
                lines.append(f'{tag:<{tag_width}}  {token}')
            else:
                lines.append(f'{tag}  {token}')
        else:
            lines.append('loop_')
            lines.append(f'  {tag}')
            for value, vtype in entries:
                token = _fallback_token(value, vtype, version)
                if line_limit is not None and token.startswith('\n') and vtype != 'placeholder':
                    token = make_text_field(value, line_limit)
                lines.extend(_format_row([token], None, line_limit))

    return lines


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _col_widths(matrix: list[list[str]]) -> list[int]:
    """Compute per-column max token width for pretty-printing.

    Columns that contain any multiline token (``token.startswith('\\n')``)
    are given width 0 — they cannot be padded inline.
    """
    if not matrix:
        return []
    n_cols = len(matrix[0])
    widths = [0] * n_cols
    for j in range(n_cols):
        col = [row[j] for row in matrix]
        if any(t.startswith('\n') for t in col):
            widths[j] = 0  # unaligned: multiline values present
        else:
            widths[j] = max(len(t) for t in col)
    return widths


def _pack_tokens(padded: list[str], line_limit: int) -> list[str]:
    """Pack padded inline tokens of one loop row into lines ≤ *line_limit* chars.

    Uses a greedy left-to-right algorithm: accumulate tokens onto the current
    physical line; start a new line when adding the next token would cause the
    line to exceed *line_limit* (measured after ``.rstrip()`` to ignore trailing
    column-padding spaces).  A single token that exceeds *line_limit* by itself
    is placed on its own line regardless.
    """
    if not padded:
        return []
    result: list[str] = []
    current: list[str] = []
    for token in padded:
        if not current:
            current.append(token)
        else:
            trial = ('  ' + '  '.join(current + [token])).rstrip()
            if len(trial) > line_limit:
                result.append(('  ' + '  '.join(current)).rstrip())
                current = [token]
            else:
                current.append(token)
    if current:
        result.append(('  ' + '  '.join(current)).rstrip())
    return result


def _apply_line_limit(value: str, token: str, line_limit: int) -> str:
    """Re-quote *token* when its content or token length would exceed *line_limit*.

    For multiline tokens (already semicolon-delimited): re-produce with folding
    if any content line of *value* is longer than *line_limit*.

    For inline tokens: switch to a semicolon field when the token itself
    (not including loop row indentation) is longer than *line_limit* − 2
    characters (the ``'  '`` prefix reserved for loop data indentation).
    """
    if token.startswith('\n'):
        if any(len(line) > line_limit for line in value.split('\n')):
            return make_text_field(value, line_limit)
    else:
        if len(token) > line_limit - 2:
            return make_text_field(value, line_limit)
    return token


# ---------------------------------------------------------------------------
# Decimal-alignment helpers
# ---------------------------------------------------------------------------

_NUMERIC_RE = re.compile(
    r'^[+-]?'                  # optional sign
    r'(?:\d+\.?\d*|\.\d+)'    # digits with optional '.', or '.digits'
    r'(?:\(\d+\))?'           # optional SU  e.g. (5)
    r'(?:[eE][+-]?\d+)?$'     # optional exponent
)


def _parse_numeric(token: str) -> tuple[str, str] | None:
    """Classify *token* as a numeric bare word and return ``(int_part, frac_part)``.

    Split priority:

    1. If ``.`` is present → split on first ``.``; *int_part* = before, *frac_part* = after
       (including any SU suffix and exponent).
    2. If no ``.`` but ``e``/``E`` is present → split before first ``e``/``E``; *int_part* =
       before the exponent letter, *frac_part* = ``e``/``E`` + remainder.
    3. Otherwise (pure integer, possibly with SU) → *int_part* = whole token,
       *frac_part* = ``''``.

    Returns ``None`` for: multiline tokens, placeholders (``.`` / ``?``), quoted tokens,
    or bare words that do not match the numeric pattern.
    """
    if token.startswith(('\n', "'", '"')) or token in ('.', '?'):
        return None
    if not _NUMERIC_RE.match(token):
        return None
    dot = token.find('.')
    if dot >= 0:
        return token[:dot], token[dot + 1:]
    e_pos = next((i for i, c in enumerate(token) if c in ('e', 'E')), -1)
    if e_pos > 0:
        return token[:e_pos], token[e_pos:]
    return token, ''


def _decimal_align_column(tokens: list[str]) -> list[str]:
    """Return *tokens* with numeric values aligned on the decimal (or exponent) point.

    Each token is classified by :func:`_parse_numeric`.  From the numeric tokens,
    ``int_width`` (max chars before the separator) and ``frac_width`` (max chars
    after the separator) are computed.  Numeric tokens are formatted as:

    - with separator: ``f'{int_part:>{int_width}}.{frac_part:<{frac_width}}'``
    - without separator (``frac_part == ''``) in a column that *has* a separator:
      ``f'{int_part:>{int_width}}' + ' ' * (1 + frac_width)``
    - without separator in a column with no separator at all:
      ``f'{int_part:>{int_width}}'``

    Non-numeric tokens are returned unchanged; :func:`_col_widths` and
    :func:`_format_row` handle left-justification to the column max width.
    """
    parsed = [_parse_numeric(t) for t in tokens]
    numeric = [(p, i) for i, p in enumerate(parsed) if p is not None]
    if not numeric:
        return list(tokens)

    int_width = max(len(p[0]) for p, _ in numeric)
    frac_width = max(len(p[1]) for p, _ in numeric)
    has_sep = any(p[1] for p, _ in numeric)

    result = list(tokens)
    for (int_part, frac_part), idx in numeric:
        if has_sep:
            if frac_part:
                result[idx] = f'{int_part:>{int_width}}.{frac_part:<{frac_width}}'
            else:
                result[idx] = f'{int_part:>{int_width}}' + ' ' * (1 + frac_width)
        else:
            result[idx] = f'{int_part:>{int_width}}'
    return result


def _apply_decimal_align(
    matrix: list[list[str]],
    real_indices: set[int],
) -> list[list[str]]:
    """Apply decimal alignment to the specified columns of *matrix* in-place.

    Returns a new matrix (rows are new lists; original is not mutated).
    """
    if not matrix or not real_indices:
        return matrix
    n_cols = len(matrix[0])
    result = [list(row) for row in matrix]
    for j in real_indices:
        if j >= n_cols:
            continue
        col_tokens = [row[j] for row in result]
        aligned = _decimal_align_column(col_tokens)
        for i, tok in enumerate(aligned):
            result[i][j] = tok
    return result


def _real_col_indices(cols: list[str], table_def: 'TableDef') -> set[int]:
    """Return the set of column indices whose ``type_contents`` is Real or Float."""
    col_type = {c.name: c.type_contents for c in table_def.columns}
    return {
        i for i, col in enumerate(cols)
        if col_type.get(col) in ('Real', 'Float')
    }


def _real_col_indices_merged(
    merged_cols: list[tuple[str, str]],
    schema: 'SchemaSpec',
) -> set[int]:
    """Return Real/Float column indices for a merge-group ``(table, col)`` list."""
    result: set[int] = set()
    for i, (cat, col) in enumerate(merged_cols):
        tdef = schema.tables.get(cat)
        if tdef is None:
            continue
        col_type = {c.name: c.type_contents for c in tdef.columns}
        if col_type.get(col) in ('Real', 'Float'):
            result.add(i)
    return result


def _format_row(
    tokens: list[str],
    col_widths: list[int] | None,
    line_limit: int | None = None,
) -> list[str]:
    """Format one loop data row as a list of output lines.

    When ``col_widths`` is provided (pretty mode), each inline token is
    left-padded to the column width.  Multiline tokens (col_width == 0) are
    never padded.

    When ``line_limit`` is given, inline tokens are packed greedily: a new
    physical line is started whenever adding the next token would cause the
    line to exceed *line_limit* characters.
    """
    def _pad(token: str, j: int) -> str:
        if col_widths and col_widths[j]:
            return f'{token:<{col_widths[j]}}'
        return token

    if not any(t.startswith('\n') for t in tokens):
        padded = [_pad(t, j) for j, t in enumerate(tokens)]
        if line_limit is None:
            return [('  ' + '  '.join(padded)).rstrip()]
        return _pack_tokens(padded, line_limit)

    result: list[str] = []
    inline_buf: list[str] = []

    def _flush() -> None:
        if inline_buf:
            if line_limit is not None:
                result.extend(_pack_tokens(inline_buf, line_limit))
            else:
                result.append(('  ' + '  '.join(inline_buf)).rstrip())
            inline_buf.clear()

    for j, t in enumerate(tokens):
        if t.startswith('\n'):
            _flush()
            result.extend(t.split('\n')[1:])
        else:
            inline_buf.append(_pad(t, j))

    _flush()
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
    """Return FK-PK columns that are implicit from a co-emitted Set category.

    Handles both direct FKs to a Set table and one-hop chains through a
    Loop-class intermediate (e.g. pd_meas.diffractogram_id →
    pd_data.diffractogram_id → pd_diffractogram.id).
    """
    pk_cols: set[str] = set(table_def.primary_keys) - _SYNTHETIC
    suppressed: set[str] = set()

    for fk in table_def.foreign_keys:
        if not all(c in pk_cols for c in fk.source_columns):
            continue

        target_name = fk.target_table
        target_def = schema.tables.get(target_name)
        if target_def is None:
            continue

        if target_def.category_class == 'Set':
            # Direct FK to a Set table — existing logic.
            target_table_rows = table_rows.get(target_name)
            if not target_table_rows or len(target_table_rows) != 1:
                continue
            target_row = target_table_rows[0]
            expected = tuple(target_row.get(c) for c in fk.target_columns)
            if all(tuple(row.get(c) for c in fk.source_columns) == expected for row in rows):
                suppressed.update(fk.source_columns)

        else:
            # Loop-class intermediate: check each column individually for a
            # single-column onward FK to a Set table.
            for src_col, tgt_col in zip(fk.source_columns, fk.target_columns):
                for hop_fk in target_def.foreign_keys:
                    if hop_fk.source_columns != [tgt_col]:
                        continue
                    ultimate_def = schema.tables.get(hop_fk.target_table)
                    if ultimate_def is None or ultimate_def.category_class != 'Set':
                        continue
                    ultimate_rows = table_rows.get(hop_fk.target_table)
                    if not ultimate_rows or len(ultimate_rows) != 1:
                        continue
                    expected_val = ultimate_rows[0].get(hop_fk.target_columns[0])
                    if all(row.get(src_col) == expected_val for row in rows):
                        suppressed.add(src_col)

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

def _fetch_rows_for_block(
    conn: sqlite3.Connection,
    block_id: str,
    table_name: str,
    table_def: 'TableDef',
) -> list[dict]:
    """Return rows that *block_id* contributed to, for ORIGINAL mode emission.

    Rows owned by this block (_block_id = block_id, including stubs and actual
    loop rows) are returned unmasked.  Rows that this block contributed to as a
    scalar tag but does not own (a later block contributed to a shared Set/Loop
    scalar key) are returned with non-contributed columns masked to None.
    """
    owned_rows: dict[tuple, dict] = {
        tuple(row.get(pk) for pk in table_def.primary_keys): row
        for row in _fetch_rows(conn, table_name, '"_block_id" = ?', (block_id,))
    }

    try:
        presence = conn.execute(
            'SELECT "column_name", "pk_json" FROM "_tag_presence" '
            'WHERE "_block_id" = ? AND "table_name" = ?',
            (block_id, table_name),
        ).fetchall()
    except sqlite3.OperationalError:
        return list(owned_rows.values())

    pk_to_cols: dict[str, set[str]] = {}
    for col_name, pk_json in presence:
        pk_to_cols.setdefault(pk_json, set()).add(col_name)

    result: list[dict] = list(owned_rows.values())
    for pk_json, contrib_cols in pk_to_cols.items():
        pk_vals = json.loads(pk_json)
        pk_key = tuple(pk_vals)
        if pk_key in owned_rows:
            continue  # Already included unmasked
        where = ' AND '.join(f'"{c}" = ?' for c in table_def.primary_keys)
        pk_set = set(table_def.primary_keys)
        for row in _fetch_rows(conn, table_name, where, tuple(pk_vals)):
            masked = {
                k: (v if k in contrib_cols or k in _SYNTHETIC or k in pk_set else None)
                for k, v in row.items()
            }
            masked['_block_id'] = block_id
            result.append(masked)
    return result


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
    """Return all distinct ``_block_id`` values in original ingestion order.

    Falls back to sorted order if ``_block_order`` is absent (e.g. legacy databases).
    """
    try:
        cursor = conn.execute('SELECT "_block_id" FROM "_block_order" ORDER BY "position"')
        return [row[0] for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        pass

    # Legacy fallback: collect from all tables and sort
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
