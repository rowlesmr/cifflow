"""
Schema visualisation: DOT and HTML output for SchemaSpec.

Public functions
----------------
visualise_schema(schema, ...)        -> Graphviz DOT string
visualise_schema_html(schema, ...)   -> self-contained HTML string
"""

from __future__ import annotations

import html
from collections import deque
from importlib.resources import files
from typing import Literal

from cifflow.dictionary.schema import BridgeColumnDef, SchemaSpec, TableDef


# ---------------------------------------------------------------------------
# JS helpers
# ---------------------------------------------------------------------------

def _read_js(name: str) -> str:
    return (
        files('cifflow.dictionary')
        .joinpath('js')
        .joinpath(name)
        .read_text(encoding='utf-8')
    )


# ---------------------------------------------------------------------------
# Connectivity analysis
# ---------------------------------------------------------------------------

def _classify_tables(
    schema: SchemaSpec,
) -> tuple[set[str], set[str], list[frozenset[str]]]:
    """
    Return (bridge_only, orphans, pass1_components).

    pass1_components: list of frozensets, each a connected component from pass 1.
    bridge_only: table names reachable only via bridge columns.
    orphans: table names with no inter-table relationship of any kind.
    """
    all_tables = set(schema.tables)

    # Pass 1 — undirected adjacency from FK + category_parent
    adjacency: dict[str, set[str]] = {t: set() for t in all_tables}
    for tbl in schema.tables.values():
        for fk in tbl.foreign_keys:
            if fk.target_table in adjacency:
                adjacency[fk.source_table].add(fk.target_table)
                adjacency[fk.target_table].add(fk.source_table)
    for child, parent in schema.category_parent.items():
        if parent and child in adjacency and parent in adjacency:
            adjacency[child].add(parent)
            adjacency[parent].add(child)

    # BFS to find connected components
    visited: set[str] = set()
    pass1_components: list[frozenset[str]] = []
    for start in all_tables:
        if start in visited:
            continue
        component: set[str] = set()
        queue: deque[str] = deque([start])
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            for neighbour in adjacency[node]:
                if neighbour not in visited:
                    queue.append(neighbour)
        pass1_components.append(frozenset(component))

    directly_isolated: set[str] = {
        next(iter(c)) for c in pass1_components if len(c) == 1
    }

    # Pass 2 — bridge reachability (each endpoint checked independently)
    bridge_only: set[str] = set()
    for bc in schema.bridge_columns:
        if bc.table_name in directly_isolated:
            bridge_only.add(bc.table_name)
        if bc.bridge_table in directly_isolated and bc.bridge_table in all_tables:
            bridge_only.add(bc.bridge_table)

    orphans = directly_isolated - bridge_only
    return bridge_only, orphans, pass1_components


def _deprecated_table_names(schema: SchemaSpec) -> set[str]:
    """
    Return the set of table names where every non-synthetic column is deprecated.

    A table is included only when it has at least one non-synthetic column and
    all of them appear in ``schema.deprecated_ids``.  Tables that consist
    entirely of synthetic infrastructure columns are never included.
    """
    result: set[str] = set()
    for name, tbl in schema.tables.items():
        non_synthetic = [c for c in tbl.columns if not c.is_synthetic]
        if non_synthetic and all(c.definition_id in schema.deprecated_ids for c in non_synthetic):
            result.add(name)
    return result


def _collect_ghost_tables(schema: SchemaSpec) -> set[str]:
    referenced: set[str] = set()
    for tbl in schema.tables.values():
        for fk in tbl.foreign_keys:
            referenced.add(fk.target_table)
    for bc in schema.bridge_columns:
        referenced.add(bc.bridge_table)
    for parent in schema.category_parent.values():
        if parent:
            referenced.add(parent)
    return referenced - schema.tables.keys()


# ---------------------------------------------------------------------------
# Node / edge builders
# ---------------------------------------------------------------------------

_HEADER_BG: dict[str, str] = {
    'Set':  '#dce8f5',
    'Loop': '#d8f0dc',
}

_BADGE_BORDER_MAP: dict[str, str] = {
    'bridge_only': 'COLOR="#ccaa00" STYLE="dashed"',
    'orphan':      'COLOR="#cc0000" STYLE="dashed"',
}


def _escape(s: str) -> str:
    return html.escape(s, quote=True)


def _dot_id(name: str) -> str:
    """Wrap a table name in double-quotes for use as a DOT node ID."""
    return '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _column_rows(
    tbl: TableDef,
    schema: SchemaSpec,
    show_columns: Literal['all', 'sparse', 'none'],
    deprecated_ids: frozenset[str],
) -> list[str]:
    """Return a list of DOT HTML-like <TR> strings for the column rows."""
    if show_columns == 'none':
        return []

    if show_columns == 'sparse':
        fk_source_cols: set[str] = set()
        for fk in tbl.foreign_keys:
            fk_source_cols.update(fk.source_columns)
        bridge_cols: set[str] = set()
        for bc in schema.bridge_columns:
            if bc.table_name == tbl.name:
                bridge_cols.add(bc.via_column)
                bridge_cols.add(bc.column_name)

    rows: list[str] = []
    for col in tbl.columns:
        if col.name == '_cifflow_id':
            continue
        if show_columns == 'sparse':
            # Synthetic columns are excluded unless they qualify via bridge rules
            if not (col.is_primary_key or col.name in fk_source_cols or col.name in bridge_cols):
                continue
        if col.definition_id in deprecated_ids:
            continue

        parts: list[str] = []

        # PK prefix
        if col.is_primary_key:
            parts.append('<B>[PK]</B> ')

        # name text
        text = _escape(col.name)
        if col.is_synthetic:
            text = f'<I><FONT COLOR="#888888">{text}</FONT></I>'
        elif col.is_primary_key:
            text = f'<B>{text}</B>'

        parts.append(text)

        # type_contents annotation
        if col.type_contents is not None:
            parts.append(f' <FONT COLOR="#555555">({_escape(col.type_contents)})</FONT>')

        # badges
        if col.type_container is not None and col.type_container != 'Single':
            parts.append(' <FONT COLOR="#0055aa">[JSON]</FONT>')
        if col.linked_item_id is not None:
            parts.append(' <FONT COLOR="#888888">[SU]</FONT>')

        cell_text = ''.join(parts)
        tooltip = _escape(col.definition_id) if col.definition_id else _escape(col.name)
        rows.append(
            f'        <TR><TD ALIGN="LEFT" TOOLTIP="{tooltip}">{cell_text}</TD></TR>'
        )
    return rows


def _header_row(
    tbl: TableDef,
    connectivity: str,  # 'connected', 'bridge_only', 'orphan'
    highlight_orphans: bool,
) -> str:
    bg = _HEADER_BG.get(tbl.category_class, '#f0f0f0')
    class_badge = f'[{tbl.category_class}]'

    conn_badge = ''
    border_attr = ''
    if highlight_orphans:
        if connectivity == 'bridge_only':
            conn_badge = ' <FONT COLOR="#ccaa00">[BRIDGE ONLY]</FONT>'
            border_attr = ' COLOR="#ccaa00" STYLE="dashed"'
        elif connectivity == 'orphan':
            conn_badge = ' <FONT COLOR="#cc0000">[ORPHAN]</FONT>'
            border_attr = ' COLOR="#cc0000" STYLE="dashed"'

    name_html = _escape(tbl.name)
    header_td = (
        f'<TD BGCOLOR="{bg}"{border_attr}>'
        f'<B>{name_html}</B>'
        f' <FONT COLOR="#555555">{class_badge}</FONT>'
        f'{conn_badge}'
        f'</TD>'
    )
    return f'        <TR>{header_td}</TR>'


def _ghost_node_dot(ghost_name: str) -> list[str]:
    name_html = _escape(ghost_name)
    lines = [
        f'    {_dot_id(ghost_name)} [shape=none margin=0 label=<',
        '    <TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" COLOR="#cc0000" STYLE="dashed">',
        f'        <TR><TD BGCOLOR="#e8e8e8"><B>[MISSING] {name_html}</B></TD></TR>',
        '    </TABLE>',
        '    >]',
    ]
    return lines


def _table_node_dot(
    tbl: TableDef,
    connectivity: str,
    highlight_orphans: bool,
    show_columns: Literal['all', 'sparse', 'none'],
    schema: SchemaSpec,
    deprecated_ids: frozenset[str],
) -> list[str]:
    header = _header_row(tbl, connectivity, highlight_orphans)
    col_rows = _column_rows(tbl, schema, show_columns, deprecated_ids)

    border_attr = ''
    if highlight_orphans:
        if connectivity == 'bridge_only':
            border_attr = ' COLOR="#ccaa00" STYLE="dashed"'
        elif connectivity == 'orphan':
            border_attr = ' COLOR="#cc0000" STYLE="dashed"'

    lines = [
        f'    {_dot_id(tbl.name)} [shape=none margin=0 label=<',
        f'    <TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0"{border_attr}>',
        header,
    ]
    if col_rows:
        lines.append('        <TR><TD><TABLE BORDER="0" CELLBORDER="0" CELLSPACING="1">')
        lines.extend(col_rows)
        lines.append('        </TABLE></TD></TR>')
    lines += [
        '    </TABLE>',
        '    >]',
    ]
    return lines


# ---------------------------------------------------------------------------
# Edge builders
# ---------------------------------------------------------------------------

def _fk_label(fk, visible_columns: set[str], show_columns: str) -> str:
    """Compute the label for a FK edge (empty string = no label)."""
    if len(fk.source_columns) == 1:
        src_col = fk.source_columns[0]
        tgt_col = fk.target_columns[0]
        if show_columns == 'none' or src_col not in visible_columns:
            return f'{_escape(src_col)} → {_escape(tgt_col)}'
        return ''
    # multi-column: always label, per-pair parentheses
    pairs = ', '.join(
        f'({_escape(s)} → {_escape(t)})'
        for s, t in zip(fk.source_columns, fk.target_columns)
    )
    return pairs


def _visible_columns(
    tbl: TableDef,
    schema: SchemaSpec,
    show_columns: str,
    deprecated_ids: frozenset[str],
) -> set[str]:
    """Return the set of column names that would be rendered as rows."""
    if show_columns == 'none':
        return set()
    if show_columns == 'all':
        return {c.name for c in tbl.columns if c.definition_id not in deprecated_ids}
    # sparse
    fk_src: set[str] = set()
    for fk in tbl.foreign_keys:
        fk_src.update(fk.source_columns)
    bridge: set[str] = set()
    for bc in schema.bridge_columns:
        if bc.table_name == tbl.name:
            bridge.add(bc.via_column)
            bridge.add(bc.column_name)
    return {
        c.name for c in tbl.columns
        if (c.is_primary_key or c.name in fk_src or c.name in bridge)
        and c.definition_id not in deprecated_ids
    }


# ---------------------------------------------------------------------------
# Legend
# ---------------------------------------------------------------------------

def _legend_dot(
    highlight_orphans: bool,
    show_bridge: bool,
    show_parent_edges: bool,
    show_columns: Literal['all', 'sparse', 'none'],
) -> list[str]:
    """Return DOT lines for a ``__legend__`` node."""
    inner: list[str] = []

    # Header
    inner.append('        <TR><TD BGCOLOR="#cccccc"><B>Legend</B></TD></TR>')

    # --- Node types ---
    inner.append('        <TR><TD ALIGN="LEFT" BGCOLOR="#eeeeee"><B>Node types</B></TD></TR>')
    inner.append('        <TR><TD><TABLE BORDER="0" CELLBORDER="0" CELLSPACING="2">')
    inner.append(
        '            <TR>'
        '<TD BGCOLOR="#dce8f5" BORDER="1" WIDTH="14" HEIGHT="14"> </TD>'
        '<TD ALIGN="LEFT"> Set category</TD></TR>'
    )
    inner.append(
        '            <TR>'
        '<TD BGCOLOR="#d8f0dc" BORDER="1" WIDTH="14" HEIGHT="14"> </TD>'
        '<TD ALIGN="LEFT"> Loop category</TD></TR>'
    )
    inner.append(
        '            <TR>'
        '<TD BGCOLOR="#e8e8e8" BORDER="1" COLOR="#cc0000" STYLE="dashed" WIDTH="14" HEIGHT="14"> </TD>'
        '<TD ALIGN="LEFT"> <FONT COLOR="#cc0000">Missing</FONT>  (referenced, not defined)</TD></TR>'
    )
    inner.append('        </TABLE></TD></TR>')

    # --- Connectivity badges (shown only when highlight_orphans is on) ---
    if highlight_orphans:
        inner.append('        <TR><TD ALIGN="LEFT" BGCOLOR="#eeeeee"><B>Connectivity</B></TD></TR>')
        inner.append('        <TR><TD><TABLE BORDER="0" CELLBORDER="0" CELLSPACING="1">')
        inner.append(
            '            <TR><TD ALIGN="LEFT">'
            '<FONT COLOR="#ccaa00">[BRIDGE ONLY]</FONT>'
            '  reachable only via bridge columns</TD></TR>'
        )
        inner.append(
            '            <TR><TD ALIGN="LEFT">'
            '<FONT COLOR="#cc0000">[ORPHAN]</FONT>'
            '  no inter-table relationship</TD></TR>'
        )
        inner.append('        </TABLE></TD></TR>')

    # --- Edge styles ---
    inner.append('        <TR><TD ALIGN="LEFT" BGCOLOR="#eeeeee"><B>Edges</B></TD></TR>')
    inner.append('        <TR><TD><TABLE BORDER="0" CELLBORDER="0" CELLSPACING="1">')
    inner.append(
        '            <TR><TD ALIGN="LEFT">&#x2192; solid black: foreign key</TD></TR>'
    )
    if show_bridge:
        inner.append(
            '            <TR><TD ALIGN="LEFT">'
            '<FONT COLOR="#888888">&#x2192; grey dashed: bridge</FONT></TD></TR>'
        )
    if show_parent_edges:
        inner.append(
            '            <TR><TD ALIGN="LEFT">'
            '<FONT COLOR="#aaaaaa">&#x2192; grey dotted: category parent</FONT></TD></TR>'
        )
    inner.append('        </TABLE></TD></TR>')

    # --- Column badges (shown only when columns are visible) ---
    if show_columns != 'none':
        inner.append('        <TR><TD ALIGN="LEFT" BGCOLOR="#eeeeee"><B>Columns</B></TD></TR>')
        inner.append('        <TR><TD><TABLE BORDER="0" CELLBORDER="0" CELLSPACING="1">')
        inner.append('            <TR><TD ALIGN="LEFT"><B>[PK]</B>  primary key</TD></TR>')
        inner.append(
            '            <TR><TD ALIGN="LEFT">'
            '<FONT COLOR="#0055aa">[JSON]</FONT>  list/table container</TD></TR>'
        )
        inner.append(
            '            <TR><TD ALIGN="LEFT">'
            '<FONT COLOR="#888888">[SU]</FONT>  standard uncertainty link</TD></TR>'
        )
        inner.append(
            '            <TR><TD ALIGN="LEFT">'
            '<FONT COLOR="#888888"><I>italic grey</I></FONT>  synthetic column</TD></TR>'
        )
        inner.append('        </TABLE></TD></TR>')

    lines = [
        '    "__legend__" [shape=none margin=0 label=<',
        '    <TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0">',
    ]
    lines.extend(inner)
    lines += [
        '    </TABLE>',
        '    >]',
    ]
    return lines


# ---------------------------------------------------------------------------
# Cluster helpers
# ---------------------------------------------------------------------------

def _component_label(component: frozenset[str]) -> str:
    """Stable cluster label: smallest table name lexicographically."""
    return min(component)


# ---------------------------------------------------------------------------
# Public: visualise_schema
# ---------------------------------------------------------------------------

def visualise_schema(
    schema: SchemaSpec,
    *,
    show_columns: Literal['all', 'sparse', 'none'] = 'sparse',
    show_bridge: bool = True,
    show_parent_edges: bool = True,
    highlight_orphans: bool = True,
    highlight_components: bool = False,
    show_orphans: bool = True,
    show_legend: bool = True,
    concentrate: bool = False,
    hide_deprecated: bool = False,
    layout: str = 'dot',
    splines: str = 'curved',
    ranksep: float = 1.0,
    nodesep: float = 0.4,
) -> str:
    """
    Return a Graphviz DOT string visualising *schema*.

    Parameters
    ----------
    schema:
        The :class:`~cifflow.dictionary.schema.SchemaSpec` to visualise.
    show_columns:
        ``'all'`` — every column; ``'sparse'`` — only PK and key/bridge columns;
        ``'none'`` — header only.
    show_bridge:
        Include bridge column edges.  Always ``True`` for ``[BRIDGE ONLY]`` nodes.
    show_parent_edges:
        Include category-parent hierarchy edges.  Always ``True`` when the target
        is a ghost node.
    highlight_orphans:
        Apply ``[ORPHAN]`` / ``[BRIDGE ONLY]`` badges and border styles.
    highlight_components:
        Wrap each connected component in a ``subgraph cluster_`` box.
    show_orphans:
        When ``False``, ``[ORPHAN]`` and ``[BRIDGE ONLY]`` nodes (and their edges)
        are omitted entirely.
    show_legend:
        When ``True`` (default), emit a ``__legend__`` node summarising node
        colours, connectivity badges, edge styles, and column badges.  The
        content of the legend adapts to the active flags.
    concentrate:
        When ``True``, set ``concentrate=true`` in the graph attributes.
        Graphviz merges parallel edges that share a common endpoint into a
        shared spine, reducing visual clutter in dense schemas.
    hide_deprecated:
        When ``True``, deprecated columns (those whose ``definition_id``
        appears in ``schema.deprecated_ids``) are omitted from column rows.
        Any table where every non-synthetic column is deprecated is removed
        from the graph entirely — no node, no ghost, no edges.
    layout:
        Graphviz layout engine written into ``graph [layout=...]``.  viz.js
        reads this attribute automatically.
    splines:
        Graphviz ``splines`` attribute controlling edge routing.  ``'curved'``
        (default) draws smooth distinct arcs and handles edge labels correctly,
        including edges that run backwards in the layout.  ``'ortho'`` routes
        edges as right-angle lines but has known issues with label placement
        and backwards edges.  Other values: ``'polyline'``, ``'spline'``,
        ``'none'``.
    ranksep:
        Minimum separation in inches between ranks (layout rows/columns).
        Larger values spread the graph out vertically (or horizontally with
        ``rankdir=LR``) and give edge routing more room.  Default ``1.0``.
    nodesep:
        Minimum separation in inches between adjacent nodes in the same rank.
        Default ``0.4``.
    """
    ghost_tables = _collect_ghost_tables(schema)
    bridge_only, orphans, pass1_components = _classify_tables(schema)

    # Deprecated filtering
    deprecated_ids: frozenset[str] = (
        frozenset(schema.deprecated_ids) if hide_deprecated else frozenset()
    )
    hidden_deprecated: set[str] = (
        _deprecated_table_names(schema) if hide_deprecated else set()
    )

    # Determine which real tables to emit
    if show_orphans:
        real_tables = set(schema.tables) - hidden_deprecated
    else:
        real_tables = set(schema.tables) - bridge_only - orphans - hidden_deprecated

    # Ghost tables must not include tables we deliberately hid as deprecated
    ghost_tables -= hidden_deprecated

    concentrate_attr = ' concentrate=true' if concentrate else ''
    lines: list[str] = [
        'digraph schema {',
        f'    graph [rankdir=LR layout="{_escape(layout)}" splines="{_escape(splines)}"'
        f' ranksep={ranksep} nodesep={nodesep}'
        f' fontname="Helvetica" fontsize=11{concentrate_attr}]',
        '    node  [fontname="Helvetica" fontsize=10]',
        '    edge  [fontname="Helvetica" fontsize=9 decorate=true]',
        '',
    ]

    # --- Connectivity lookup ---
    def _connectivity(name: str) -> str:
        if name in bridge_only:
            return 'bridge_only'
        if name in orphans:
            return 'orphan'
        return 'connected'

    # --- Ghost nodes ---
    if ghost_tables:
        for ghost in sorted(ghost_tables):
            lines += _ghost_node_dot(ghost)
        lines.append('')

    # --- Real table nodes (possibly clustered) ---
    if highlight_components:
        # Sort components by their representative name for stability
        sorted_components = sorted(pass1_components, key=_component_label)

        # Collect component nodes (only real, non-orphan/bridge tables)
        real_structural_components = [
            c for c in sorted_components if len(c) >= 2 and c.issubset(real_tables)
        ]
        # Partial components (some members hidden by show_orphans=False)
        partial_structural_components = [
            c for c in sorted_components
            if len(c) >= 2 and not c.issubset(real_tables) and any(t in real_tables for t in c)
        ]
        # Include partially-visible components too
        all_structural = real_structural_components + partial_structural_components

        for i, component in enumerate(all_structural):
            visible_members = sorted(t for t in component if t in real_tables)
            if not visible_members:
                continue
            rep = _component_label(component)
            lines.append(f'    subgraph cluster_{i} {{')
            lines.append(f'        label="{_escape(rep)}" style=filled fillcolor="#f5f5f5"')
            for tbl_name in visible_members:
                tbl = schema.tables[tbl_name]
                for node_line in _table_node_dot(tbl, _connectivity(tbl_name), highlight_orphans, show_columns, schema, deprecated_ids):
                    lines.append('    ' + node_line)
            lines.append('    }')
            lines.append('')

        # Orphans cluster
        visible_orphans = sorted(orphans & real_tables)
        visible_bridge_only = sorted(bridge_only & real_tables)
        if visible_orphans or visible_bridge_only:
            lines.append('    subgraph cluster_orphans {')
            lines.append('        label="Isolated tables" style=filled fillcolor="#fff8f8"')
            for tbl_name in visible_orphans + visible_bridge_only:
                if tbl_name not in real_tables:
                    continue
                tbl = schema.tables[tbl_name]
                for node_line in _table_node_dot(tbl, _connectivity(tbl_name), highlight_orphans, show_columns, schema, deprecated_ids):
                    lines.append('    ' + node_line)
            lines.append('    }')
            lines.append('')

        # Ghost node cluster
        if ghost_tables:
            lines.append('    subgraph cluster_missing {')
            lines.append('        label="Missing tables" style=filled fillcolor="#ffe8e8"')
            for ghost in sorted(ghost_tables):
                for node_line in _ghost_node_dot(ghost):
                    lines.append('    ' + node_line)
            lines.append('    }')
            lines.append('')

        # Singleton real-table nodes not yet placed
        placed = set()
        for c in all_structural:
            placed.update(c)
        placed.update(orphans)
        placed.update(bridge_only)
        for tbl_name in sorted(real_tables - placed):
            tbl = schema.tables[tbl_name]
            lines += _table_node_dot(tbl, _connectivity(tbl_name), highlight_orphans, show_columns, schema, deprecated_ids)
            lines.append('')
    else:
        for tbl_name in sorted(real_tables):
            tbl = schema.tables[tbl_name]
            lines += _table_node_dot(tbl, _connectivity(tbl_name), highlight_orphans, show_columns, schema, deprecated_ids)
            lines.append('')

    # --- Legend node ---
    if show_legend:
        lines += _legend_dot(highlight_orphans, show_bridge, show_parent_edges, show_columns)
        lines.append('')

    # --- Edges ---
    lines.append('')

    # FK edges
    for tbl_name in sorted(real_tables):
        tbl = schema.tables[tbl_name]
        vis_cols = _visible_columns(tbl, schema, show_columns, deprecated_ids)
        for fk in tbl.foreign_keys:
            target = fk.target_table
            # Skip if target is a real table that's been hidden
            if target not in ghost_tables and target not in real_tables:
                continue
            label = _fk_label(fk, vis_cols, show_columns)
            attr = f' [label="{label}"]' if label else ''
            lines.append(f'    {_dot_id(fk.source_table)} -> {_dot_id(target)}{attr}')

    # Bridge edges
    bridge_col_by_table: dict[str, list[BridgeColumnDef]] = {}
    for bc in schema.bridge_columns:
        bridge_col_by_table.setdefault(bc.table_name, []).append(bc)

    for tbl_name in sorted(real_tables):
        if tbl_name not in bridge_col_by_table:
            continue
        is_bridge_only_node = tbl_name in bridge_only
        for bc in bridge_col_by_table[tbl_name]:
            bridge_target = bc.bridge_table
            target_is_ghost = bridge_target in ghost_tables
            target_in_real = bridge_target in real_tables
            if not target_is_ghost and not target_in_real:
                continue
            # Show bridge edge if: show_bridge is True, OR the node is bridge_only, OR target is ghost
            if show_bridge or is_bridge_only_node or target_is_ghost:
                label = _escape(f'{bc.column_name} via {bc.via_column}')
                lines.append(
                    f'    {_dot_id(tbl_name)} -> {_dot_id(bridge_target)}'
                    f' [label="{label}" style=dashed color="#888888"]'
                )

    # Parent-hierarchy edges
    for child, parent in sorted(schema.category_parent.items()):
        if not parent:
            continue
        child_in_real = child in real_tables
        parent_is_ghost = parent in ghost_tables
        parent_in_real = parent in real_tables
        if not child_in_real:
            continue
        if not parent_is_ghost and not parent_in_real:
            continue
        # Show parent edge if: show_parent_edges is True, OR target is ghost
        if show_parent_edges or parent_is_ghost:
            lines.append(
                f'    {_dot_id(child)} -> {_dot_id(parent)}'
                f' [style=dotted arrowhead=open color="#aaaaaa"]'
            )

    lines.append('}')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Public: visualise_schema_html
# ---------------------------------------------------------------------------

def visualise_schema_html(
    schema: SchemaSpec,
    *,
    title: str | None = None,
    show_columns: Literal['all', 'sparse', 'none'] = 'sparse',
    show_bridge: bool = True,
    show_parent_edges: bool = True,
    highlight_orphans: bool = True,
    highlight_components: bool = False,
    show_orphans: bool = True,
    show_legend: bool = True,
    concentrate: bool = False,
    hide_deprecated: bool = False,
    layout: str = 'dot',
    splines: str = 'curved',
    ranksep: float = 1.0,
    nodesep: float = 0.4,
) -> str:
    """
    Return a self-contained HTML string that renders *schema* interactively.

    All keyword arguments except *title* are forwarded to
    :func:`visualise_schema`.  The returned HTML embeds viz.js and svg-pan-zoom
    as inline ``<script>`` blocks — no network access is required.

    Parameters
    ----------
    title:
        ``<title>`` element text.  Defaults to ``schema.dictionary_name``
        or ``'Schema'`` when not given.
    show_legend:
        Forwarded to :func:`visualise_schema`.
    concentrate:
        Forwarded to :func:`visualise_schema`.
    hide_deprecated:
        Forwarded to :func:`visualise_schema`.
    """
    dot_string = visualise_schema(
        schema,
        show_columns=show_columns,
        show_bridge=show_bridge,
        show_parent_edges=show_parent_edges,
        highlight_orphans=highlight_orphans,
        highlight_components=highlight_components,
        show_orphans=show_orphans,
        show_legend=show_legend,
        concentrate=concentrate,
        hide_deprecated=hide_deprecated,
        layout=layout,
        splines=splines,
        ranksep=ranksep,
        nodesep=nodesep,
    )

    page_title = title or schema.dictionary_name or 'Schema'
    page_title_escaped = html.escape(page_title)

    viz_js = _read_js('viz.js')
    full_render_js = _read_js('full.render.js')
    svg_pan_zoom_js = _read_js('svg-pan-zoom.min.js')

    # Escape DOT string for embedding in a JS template literal
    dot_escaped = dot_string.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{page_title_escaped}</title>
  <script>{viz_js}</script>
  <script>{full_render_js}</script>
  <script>{svg_pan_zoom_js}</script>
  <style>
    body {{ margin: 0; background: #fafafa; }}
    #graph {{ width: 100vw; height: 100vh; overflow: hidden; }}
    #graph svg {{ width: 100%; height: 100%; }}
  </style>
</head>
<body>
  <div id="graph"></div>
  <script>
    const dot = `{dot_escaped}`;
    new Viz().renderSVGElement(dot).then(svg => {{
      document.getElementById('graph').appendChild(svg);
      svgPanZoom(svg, {{ zoomEnabled: true, controlIconsEnabled: true, fit: true, center: true }});
    }}).catch(err => {{
      document.getElementById('graph').textContent = 'Render error: ' + err;
    }});
  </script>
</body>
</html>"""
