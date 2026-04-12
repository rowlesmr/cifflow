"""
CIF emission from a populated SQLite database.

``emit(conn, schema, ...)`` reads structured tables and the ``_cif_fallback``
table and produces a valid CIF string.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from collections import deque

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
        Optional ordering specification.  ``None`` uses default ordering.
    reconstruct_su:
        When ``True``, paired ``(col, col_su)`` columns are merged into a
        single ``value(su)`` token.  Default ``False``.
    emit_defaults:
        When ``True`` (default), columns filled from ``enumeration_default``
        are emitted normally.  When ``False``, they would be suppressed; this
        requires per-value provenance tracking which is not yet implemented,
        so the flag is currently accepted but has no effect.

    Returns
    -------
    str
        Complete CIF text including magic line, terminated with a newline.
    """
    magic = '#\\#CIF_2.0' if version == CifVersion.CIF_2_0 else '#\\#CIF_1.1'

    if mode == EmitMode.ONE_BLOCK:
        blocks = _collect_one_block(conn, schema, version, plan, reconstruct_su)
    elif mode == EmitMode.ALL_BLOCKS:
        blocks = _collect_all_blocks(conn, schema, version, plan, reconstruct_su)
    elif mode == EmitMode.GROUPED:
        blocks = _collect_grouped(conn, schema, version, plan, reconstruct_su)
    else:  # ORIGINAL
        blocks = _collect_original(conn, schema, version, plan, reconstruct_su)

    parts = [magic]
    for i, block_text in enumerate(blocks):
        if i > 0:
            parts.append('')
            parts.append('')
        parts.append(block_text)

    return '\n'.join(parts) + '\n'


# ---------------------------------------------------------------------------
# Mode collectors — each returns list[str] of rendered block texts
# ---------------------------------------------------------------------------

def _collect_original(
    conn: sqlite3.Connection,
    schema: SchemaSpec,
    version: CifVersion,
    plan: OutputPlan | None,
    reconstruct_su: bool,
) -> list[str]:
    """ORIGINAL: one output block per distinct ``_block_id``."""
    block_ids = _all_block_ids(conn, schema)
    result = []
    for i, bid in enumerate(block_ids):
        table_rows = {}
        for table_name in schema.tables:
            rows = _fetch_rows(conn, table_name, '"_block_id" = ?', (bid,))
            if rows:
                table_rows[table_name] = rows
        fallback = _fetch_rows(conn, '_cif_fallback', '"_block_id" = ?', (bid,))
        spec = plan.spec_for(i) if plan else None
        result.append(_render_block(bid, table_rows, fallback, schema, version, spec, reconstruct_su, suppress_fk_pk=True))
    return result


def _collect_grouped(
    conn: sqlite3.Connection,
    schema: SchemaSpec,
    version: CifVersion,
    plan: OutputPlan | None,
    reconstruct_su: bool,
) -> list[str]:
    """GROUPED: one block per distinct Set-anchor key combination.

    For each table, the FK chain is followed transitively until a Set-class
    table is reached.  Tables that share the same Set anchor are placed in the
    same output block when their anchor row's domain primary-key values match.
    This merges rows from multiple original ``_block_id``\\s that share the
    same Set-level identity.

    Tables with no Set ancestor in their FK chain fall back to ``_block_id``
    grouping (equivalent to ORIGINAL for those tables).
    """
    # Map each table to its Set anchor (None if no Set ancestor)
    table_to_anchor: dict[str, str | None] = {
        t: _find_set_anchor(t, schema) for t in schema.tables
    }

    # Separate keyed Set anchors (have domain PK → can merge across blocks)
    # from everything else (no-anchor tables and keyless Set categories).
    # Keyless Sets use _pycifparse_id as their PK — a unique UUID with no
    # cross-block identity — so they are grouped by _block_id just like
    # Loop-only tables.
    keyed_anchor_to_tables: dict[str, list[str]] = {}
    block_id_tables: list[str] = []  # keyless Set anchors + no-anchor tables

    for t, anchor in table_to_anchor.items():
        if anchor is not None:
            anchor_def = schema.tables[anchor]
            domain_pks = [pk for pk in anchor_def.primary_keys if pk not in _SYNTHETIC]
            if domain_pks:
                keyed_anchor_to_tables.setdefault(anchor, []).append(t)
                continue
        block_id_tables.append(t)

    # Exclusive-target anchors: anchor tables that are FK-referenced from
    # exactly one other anchor group AND have no FK going out to any other
    # keyed anchor.  For example, space_group is referenced by structure (in
    # pd_phase group) with no FK back.  Such anchors carry no independent
    # grouping value — their rows co-occur in the same original blocks as the
    # referencing tables — so they fall back to _block_id grouping and are
    # absorbed by whichever keyed block covers those block IDs.
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

    result: list[str] = []
    block_idx = 0
    # absorbed_primary: anchor-row block_ids claimed by a keyed anchor — used
    #   to skip duplicate pk groups in later anchor iterations.
    # absorbed_all: all block_ids swept (anchor rows + FK-chained rows +
    #   block_id_tables) — used to suppress remaining-block emission.
    absorbed_primary: set[str] = set()
    absorbed_all: set[str] = set()

    for anchor_name in sorted(keyed_anchor_to_tables):
        anchor_def = schema.tables[anchor_name]
        domain_pks = [pk for pk in anchor_def.primary_keys if pk not in _SYNTHETIC]
        anchor_rows = _fetch_rows(conn, anchor_name)

        # If the anchor table itself has no rows its FK group cannot be keyed
        # (no PK values to pivot on).  Fall back all tables in the group to
        # _block_id grouping so they are not silently dropped.
        if not anchor_rows:
            block_id_tables.extend(keyed_anchor_to_tables[anchor_name])
            continue

        # Group anchor rows by domain PK values (merging across _block_ids).
        pk_groups: dict[tuple, list[dict]] = {}
        for row in anchor_rows:
            key = tuple(row.get(pk) for pk in domain_pks)
            pk_groups.setdefault(key, []).append(row)

        for pk_vals, grouped_anchor_rows in sorted(pk_groups.items()):
            block_name = grouped_anchor_rows[0].get('_block_id', 'output')
            # Seed covered_block_ids from anchor rows; extended below as
            # FK-chained table rows are fetched (they may carry additional
            # _block_id values when the anchor's PK prevented duplicate rows).
            primary_block_ids: set[str] = {
                r.get('_block_id') for r in grouped_anchor_rows if r.get('_block_id')
            }
            covered_block_ids: set[str] = set(primary_block_ids)

            # Skip if this anchor's primary block_ids are already claimed by a
            # prior keyed-anchor group.  (Using primary only avoids false skips
            # caused by FK-extended block_ids from unrelated anchor domains.)
            if primary_block_ids and primary_block_ids <= absorbed_primary:
                continue

            table_rows: dict[str, list[dict]] = {anchor_name: grouped_anchor_rows}

            for table_name in keyed_anchor_to_tables[anchor_name]:
                if table_name == anchor_name:
                    continue
                fk_path = _fk_chain(table_name, anchor_name, schema)
                if fk_path is None:
                    # No FK path found; fall back to _block_id filtering.
                    # covered_block_ids may grow below, so defer this table.
                    rows = []
                else:
                    rows = _fetch_rows_via_fk_path(conn, table_name, fk_path, domain_pks, pk_vals)
                    # Extend covered_block_ids: FK-chained rows may span more
                    # block_ids than the anchor table (e.g. when a Set PK
                    # conflict suppressed a duplicate anchor row).
                    for r in rows:
                        if r.get('_block_id'):
                            covered_block_ids.add(r.get('_block_id'))
                if rows:
                    table_rows[table_name] = rows

            # Second pass for tables whose FK path was None: now covered_block_ids
            # is fully expanded so _block_id filtering is correct.
            for table_name in keyed_anchor_to_tables[anchor_name]:
                if table_name == anchor_name or table_name in table_rows:
                    continue
                rows = []
                for bid in sorted(covered_block_ids):
                    rows.extend(_fetch_rows(conn, table_name, '"_block_id" = ?', (bid,)))
                if rows:
                    table_rows[table_name] = rows

            # Claim primary block_ids for this anchor group.
            absorbed_primary |= primary_block_ids
            # Record all swept block_ids (primary + FK-extended) so that
            # block_id_tables rows from the same original blocks are not
            # re-emitted in the remaining-blocks section below.
            absorbed_all |= covered_block_ids

            # Include block_id_tables (pure-cluster Sets, keyless Sets,
            # Loop-only tables) from all covered block IDs.  This absorbs
            # e.g. space_group rows that share the FK-extended block_ids.
            for t in block_id_tables:
                rows = []
                for bid in sorted(covered_block_ids):
                    rows.extend(_fetch_rows(conn, t, '"_block_id" = ?', (bid,)))
                if rows:
                    table_rows[t] = rows

            fallback: list[dict] = []
            for bid in sorted(covered_block_ids):
                fallback.extend(_fetch_rows(conn, '_cif_fallback', '"_block_id" = ?', (bid,)))

            spec = plan.spec_for(block_idx) if plan else None
            result.append(_render_block(block_name, table_rows, fallback, schema, version, spec, reconstruct_su, suppress_fk_pk=True))
            block_idx += 1

    # Keyless Set categories, Loop-only tables, and any fallback data whose
    # block IDs were not absorbed by a keyed-anchor group: one block per
    # distinct _block_id.
    #
    # Also sweep all keyed-anchor-group tables: a table whose rows have NULL
    # FK values for the anchor chain (e.g. diffrn_radiation_wavelength with
    # phase_id=NULL) is not reachable via _fetch_rows_via_fk_path and is also
    # not picked up by the covered_block_ids second-pass, so its rows would
    # otherwise be silently dropped.  Including all schema tables here is safe
    # because remaining_block_ids is already filtered to block_ids that were
    # never absorbed by any keyed-anchor group.
    all_table_names = list(schema.tables.keys())
    remaining_block_ids = [
        bid for bid in _all_block_ids_for_tables(conn, all_table_names)
        if bid not in absorbed_all
    ]
    # Also pick up any _cif_fallback block_ids not yet covered
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
            spec = plan.spec_for(block_idx) if plan else None
            result.append(_render_block(bid, table_rows, fallback, schema, version, spec, reconstruct_su, suppress_fk_pk=True))
            block_idx += 1

    return result


def _collect_one_block(
    conn: sqlite3.Connection,
    schema: SchemaSpec,
    version: CifVersion,
    plan: OutputPlan | None,
    reconstruct_su: bool,
) -> list[str]:
    """ONE_BLOCK: all data in a single block named 'output'."""
    table_rows = {}
    for table_name in schema.tables:
        rows = _fetch_rows(conn, table_name)
        if rows:
            table_rows[table_name] = rows
    fallback = _fetch_rows(conn, '_cif_fallback')
    spec = plan.spec_for(0) if plan else None
    return [_render_block('output', table_rows, fallback, schema, version, spec, reconstruct_su)]


def _collect_all_blocks(
    conn: sqlite3.Connection,
    schema: SchemaSpec,
    version: CifVersion,
    plan: OutputPlan | None,
    reconstruct_su: bool,
) -> list[str]:
    """ALL_BLOCKS: one block per category, plus one per fallback ``_block_id``.

    .. todo::
        Block granularity is currently one block per non-empty table, which is
        wrong when a table has rows from multiple original blocks.  The correct
        behaviour is:

        * **Set categories** — one output block *per row* (each row is a
          distinct Set instance that originated from a different ``_block_id``).
        * **Loop categories** — group rows by their Set-anchor key (domain PK
          of the nearest Set ancestor via FK chain).  Rows sharing the same
          anchor key values belong to the same output block.  Tables with no
          Set ancestor remain one block per table.

        When this is fixed, re-examine the ``_audit_dataset.id`` injection
        logic so the UUID is derived from the contributing ``_block_id``\\s
        rather than a single global session UUID.
    """
    # CIF 2.0: inject a shared _audit_dataset.id into every block so that a
    # reader can recognise that all blocks belong to the same dataset.
    # Reuse the existing dataset UUID when the source file declared one;
    # otherwise generate a fresh one for this emit session.
    dataset_id: str | None = None
    if version == CifVersion.CIF_2_0:
        mem_rows = _fetch_rows(conn, '_block_dataset_membership')
        real_ids = {
            r['_audit_dataset_id']
            for r in mem_rows
            if r.get('id_regime') == 'dataset' and r.get('_audit_dataset_id')
        }
        dataset_id = real_ids.pop() if len(real_ids) == 1 else str(uuid.uuid4())

    result = []
    i = 0
    for table_name, table_def in schema.tables.items():
        rows = _fetch_rows(conn, table_name)
        if not rows:
            continue
        spec = plan.spec_for(i) if plan else None
        block_name = table_name
        rendered = _render_block(
            block_name,
            {table_name: rows},
            [],
            schema,
            version,
            spec,
            reconstruct_su,
            dataset_id=dataset_id,
        )
        result.append(rendered)
        i += 1

    # Fallback blocks grouped by _block_id
    fallback_by_block: dict[str, list[dict]] = {}
    for row in _fetch_rows(conn, '_cif_fallback'):
        bid = row.get('_block_id', 'fallback')
        fallback_by_block.setdefault(bid, []).append(row)

    for bid, fb_rows in sorted(fallback_by_block.items()):
        spec = plan.spec_for(i) if plan else None
        rendered = _render_block(
            f'{bid}_fallback',
            {},
            fb_rows,
            schema,
            version,
            spec,
            reconstruct_su,
            dataset_id=dataset_id,
        )
        result.append(rendered)
        i += 1

    return result


# ---------------------------------------------------------------------------
# Block renderer
# ---------------------------------------------------------------------------

def _render_block(
    block_name: str,
    table_rows: dict[str, list[dict]],
    fallback_rows: list[dict],
    schema: SchemaSpec,
    version: CifVersion,
    spec: BlockSpec | None,
    reconstruct_su: bool,
    *,
    suppress_fk_pk: bool = False,
    dataset_id: str | None = None,
) -> str:
    """Render a single CIF block to a string."""
    lines: list[str] = [f'data_{block_name}']
    first_category = True

    # Inject _audit_dataset.id when requested, unless this block already carries
    # it via the audit_dataset structured table or _cif_fallback rows.
    if dataset_id is not None:
        audit_in_table = 'audit_dataset' in table_rows
        audit_in_fallback = any(
            (r.get('tag') or '').lower() == '_audit_dataset.id'
            for r in fallback_rows
        )
        if not audit_in_table and not audit_in_fallback:
            audit_tag = schema.column_to_tag.get(('audit_dataset', 'id'), '_audit_dataset.id')
            lines.append(f'{audit_tag}  {quote(dataset_id, version)}')
            first_category = False

    for table_name in _ordered_categories(schema, spec):
        rows = table_rows.get(table_name)
        if not rows:
            continue
        table_def = schema.tables[table_name]
        cols = _active_cols(table_def, rows, spec, reconstruct_su)
        if not cols:
            continue

        # FK-PK suppression only applies to Set categories rendered as scalar
        # tag-value pairs.  Loop categories must emit all PK columns explicitly;
        # a reader cannot recover a suppressed composite-key column from block scope.
        if suppress_fk_pk and table_def.category_class == 'Set' and len(rows) == 1:
            suppressed = _suppressed_fk_pk_cols(table_def, rows, table_rows, schema)
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

    if fallback_rows:
        if not first_category:
            lines.append('')
        lines.extend(_render_fallback(fallback_rows, version))

    return '\n'.join(lines)


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
            # This is a measurand; merge with its SU column
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
    """Emit ``_cif_fallback`` rows as tag–value pairs or single-column loops.

    Tags are grouped and emitted in alphabetical order.  Tags with a single
    value are emitted as scalar pairs; tags with multiple values (i.e. from
    a loop in the original CIF) are emitted as single-column ``loop_``
    constructs.
    """
    # Group by tag, preserving row_id order within each tag
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
    """Format one loop data row as a list of output lines.

    If all tokens are inline (no semicolon delimiters), produce one line.
    If any token is semicolon-delimited (starts with ``'\\n'``), each token
    is placed on its own line or set of lines.
    """
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

def _ordered_categories(schema: SchemaSpec, spec: BlockSpec | None) -> list[str]:
    """Return table names in emission order."""
    all_tables = list(schema.tables.keys())

    if spec and spec.categories:
        listed = [c for c in spec.categories if c in schema.tables]
        listed_set = set(listed)
        remaining = sorted(t for t in all_tables if t not in listed_set)
        return listed + remaining

    # Default: Set-class first (alphabetical), then Loop-class (alphabetical)
    set_tables = sorted(t for t, td in schema.tables.items() if td.category_class == 'Set')
    loop_tables = sorted(t for t, td in schema.tables.items() if td.category_class != 'Set')
    return set_tables + loop_tables


def _active_cols(
    table_def: TableDef,
    rows: list[dict],
    spec: BlockSpec | None,
    reconstruct_su: bool,
) -> list[str]:
    """Return columns with at least one non-NULL value, in emission order.

    Synthetic columns and (when ``reconstruct_su=True``) SU columns are
    excluded.  SU column values are merged into their measurand column.
    """
    su_col_names: set[str] = set()
    if reconstruct_su:
        for col in table_def.columns:
            if col.linked_item_id is not None:
                su_col_names.add(col.name)

    # Columns with at least one non-NULL value, excluding synthetic and SU
    active_set = {
        col.name for col in table_def.columns
        if not col.is_synthetic
        and col.name not in su_col_names
        and any(row.get(col.name) is not None for row in rows)
    }

    if not active_set:
        return []

    # Apply custom column ordering from plan
    if spec and table_def.name in spec.column_order:
        listed = [c for c in spec.column_order[table_def.name] if c in active_set]
        listed_set = set(listed)
        rest = sorted(c for c in active_set if c not in listed_set)
        return listed + rest

    # Default: PK columns first (non-synthetic), then alphabetical
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

    Only called for Set categories rendered as scalar tag-value pairs (single
    row).  Loop categories must always emit all PK columns explicitly; a reader
    cannot recover a suppressed composite-key column from block scope.

    A column can be suppressed when ALL of the following hold:

    1. It is part of the table's domain primary key.
    2. It is part of a FK that targets a Set-class table.
    3. That Set table is present in *table_rows* (being emitted in the same
       block) with exactly one row.
    4. Every row in *rows* carries the same FK value, and that value equals
       the target Set's PK value.

    In CIF, the block scope makes such FK-PK values implicit: a reader can
    derive ``_cell.diffrn_id`` from ``_diffrn.id`` in the same block.
    """
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

        # FK source columns must all be domain PK columns of this table.
        if not all(c in pk_cols for c in fk.source_columns):
            continue

        target_row = target_table_rows[0]
        expected = tuple(target_row.get(c) for c in fk.target_columns)

        # All rows must carry the same FK value matching the target's PK.
        if all(tuple(row.get(c) for c in fk.source_columns) == expected for row in rows):
            suppressed.update(fk.source_columns)

    return suppressed


def _col_tag(table_name: str, col_name: str, schema: SchemaSpec) -> str:
    """Return the CIF tag name (``_definition.id``) for a column."""
    return schema.column_to_tag.get((table_name, col_name), f'_{table_name}.{col_name}')


def _su_col_map(table_def: TableDef) -> dict[str, str]:
    """Return ``{measurand_col_name: su_col_name}`` for this table."""
    # Build definition_id → col_name for non-SU columns
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
        return value  # '.' or '?' — unquoted
    return quote(value, version)


def _merge_su(measurand: str, scaled_su: str) -> str:
    """Reconstruct ``value(su)`` from stored measurand and scaled SU strings.

    This is the inverse of ``split_su`` in ``ingest.py``.  If the conversion
    fails for any reason, the unmodified measurand is returned.
    """
    try:
        e_match = re.search(r'[eE]([+-]?\d+)$', measurand)
        exponent = int(e_match.group(1)) if e_match else 0
        mantissa = measurand[:e_match.start()] if e_match else measurand
        dot_idx = mantissa.find('.')
        decimal_places = (len(mantissa) - dot_idx - 1) if dot_idx >= 0 else 0
        total_power = exponent - decimal_places  # power of 10 for the last decimal place
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
    """Find the root Set-class ancestor reachable from *table_name* via FK links.

    Traverses the full FK graph (BFS) and collects every reachable Set-class
    table.  The *root* Set is the one that has no FK pointing to another
    reachable Set — i.e. the topmost node in the Set-level hierarchy.

    This ensures that intermediate Set categories (e.g. a ``CELL`` Set that
    FKs to a ``STRUCTURE`` Set) are not returned as the anchor; the shared
    root (``STRUCTURE``) is returned instead, so all tables that ultimately
    reduce to the same root are placed in the same output block.

    Tables with composite FK keys where only some paths lead to a Set are
    handled correctly: BFS explores all FK targets at each level.

    Returns ``None`` if no Set-class table is reachable (pure Loop chains).
    """
    # BFS over all FK-reachable tables; collect every reachable Set.
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

    # Root Set: a reachable Set with no FK to another reachable Set.
    reachable_set_names = set(reachable_sets)
    for s in reachable_sets:
        td = schema.tables[s]
        has_set_parent = any(
            fk.target_table in reachable_set_names and fk.target_table != s
            for fk in td.foreign_keys
        )
        if not has_set_parent:
            return s

    # Fallback (e.g. circular FK graph): return the last Set found.
    return reachable_sets[-1]


def _fk_chain(from_table: str, to_table: str, schema: SchemaSpec) -> list[ForeignKeyDef] | None:
    """BFS to find the FK-hop path from *from_table* to *to_table*.

    Returns the ordered list of :class:`ForeignKeyDef` hops, or ``None`` if
    *to_table* is not reachable.  Returns ``[]`` when ``from_table == to_table``.
    """
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
    """Fetch rows from *from_table* that transitively FK-link to the given anchor row.

    *fk_path* is the ordered list of FK hops from *from_table* to the anchor.
    *anchor_pk_cols* / *anchor_pk_vals* identify the anchor row.
    """
    if not fk_path or not anchor_pk_cols:
        return _fetch_rows(conn, from_table)

    # t0 = from_table alias, t1 = first hop target, ...
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
