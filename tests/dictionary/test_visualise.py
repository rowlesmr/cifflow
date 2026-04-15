"""Tests for dictionary/visualise.py — visualise_schema and visualise_schema_html."""

import pytest

from pycifparse.dictionary.schema import (
    BridgeColumnDef,
    ColumnDef,
    ForeignKeyDef,
    SchemaSpec,
    TableDef,
)
from pycifparse.dictionary.visualise import visualise_schema, visualise_schema_html


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _col(
    name: str,
    *,
    definition_id: str = '',
    type_contents: str | None = None,
    type_container: str | None = 'Single',
    is_primary_key: bool = False,
    is_synthetic: bool = False,
    linked_item_id: str | None = None,
    nullable: bool = True,
) -> ColumnDef:
    return ColumnDef(
        name=name,
        definition_id=definition_id or f'_test.{name}',
        type_contents=type_contents,
        type_container=type_container,
        is_primary_key=is_primary_key,
        is_synthetic=is_synthetic,
        linked_item_id=linked_item_id,
        nullable=nullable,
    )


def _table(
    name: str,
    *,
    category_class: str = 'Loop',
    columns: list[ColumnDef] | None = None,
    primary_keys: list[str] | None = None,
    foreign_keys: list[ForeignKeyDef] | None = None,
    definition_id: str = '',
) -> TableDef:
    cols = columns or [_col('id', is_primary_key=True)]
    pks = primary_keys or ['id']
    return TableDef(
        name=name,
        definition_id=definition_id or f'_{name}',
        category_class=category_class,
        columns=cols,
        primary_keys=pks,
        foreign_keys=foreign_keys or [],
    )


def _schema(
    tables: list[TableDef],
    *,
    bridge_columns: list[BridgeColumnDef] | None = None,
    category_parent: dict[str, str | None] | None = None,
    dictionary_name: str | None = None,
) -> SchemaSpec:
    return SchemaSpec(
        tables={t.name: t for t in tables},
        column_to_tag={},
        bridge_columns=bridge_columns or [],
        category_parent=category_parent or {},
        dictionary_name=dictionary_name,
    )


# ---------------------------------------------------------------------------
# DOT output — basic structure
# ---------------------------------------------------------------------------

class TestDotBasic:
    def test_starts_with_digraph(self):
        dot = visualise_schema(_schema([_table('atom')]))
        assert dot.startswith('digraph schema {')

    def test_table_name_in_output(self):
        dot = visualise_schema(_schema([_table('atom_site'), _table('cell')]))
        assert 'atom_site' in dot
        assert 'cell' in dot

    def test_set_header_marker(self):
        tbl = _table('cell', category_class='Set')
        dot = visualise_schema(_schema([tbl]))
        assert '[Set]' in dot

    def test_loop_header_marker(self):
        tbl = _table('atom_site', category_class='Loop')
        dot = visualise_schema(_schema([tbl]))
        assert '[Loop]' in dot

    def test_no_fks_no_edges(self):
        dot = visualise_schema(_schema([_table('a'), _table('b')]))
        assert '->' not in dot

    def test_layout_parameter_in_graph(self):
        dot = visualise_schema(_schema([_table('a')]), layout='fdp')
        assert 'layout="fdp"' in dot

    def test_default_layout_is_dot(self):
        dot = visualise_schema(_schema([_table('a')]))
        assert 'layout="dot"' in dot


# ---------------------------------------------------------------------------
# DOT output — FK edges
# ---------------------------------------------------------------------------

class TestFkEdges:
    def _fk_schema(self) -> SchemaSpec:
        fk = ForeignKeyDef(
            source_table='atom_site',
            source_columns=['structure_id'],
            target_table='structure',
            target_columns=['id'],
        )
        atom_cols = [
            _col('structure_id', is_primary_key=True),
            _col('label', is_primary_key=True),
        ]
        atom = _table('atom_site', columns=atom_cols, primary_keys=['structure_id', 'label'], foreign_keys=[fk])
        struct = _table('structure', columns=[_col('id', is_primary_key=True)], primary_keys=['id'])
        return _schema([atom, struct])

    def test_fk_edge_present(self):
        dot = visualise_schema(self._fk_schema())
        assert '"atom_site" -> "structure"' in dot

    def test_single_col_fk_label_when_show_columns_none(self):
        dot = visualise_schema(self._fk_schema(), show_columns='none')
        assert 'structure_id' in dot   # label should appear

    def test_single_col_fk_no_label_when_column_visible(self):
        # In sparse mode structure_id is a PK, so it appears in the node —
        # therefore the edge label should be omitted.
        dot = visualise_schema(self._fk_schema(), show_columns='sparse')
        # Edge must be present but the label text (source → target) should not
        # appear as a standalone label (column is visible in node)
        assert '"atom_site" -> "structure"' in dot
        # The label "structure_id → id" should NOT appear as an edge label
        lines = [l for l in dot.splitlines() if '"atom_site" -> "structure"' in l]
        assert lines
        assert 'structure_id → id' not in lines[0]

    def test_multi_col_fk_label_always_shown(self):
        fk = ForeignKeyDef(
            source_table='child',
            source_columns=['a', 'b'],
            target_table='parent',
            target_columns=['x', 'y'],
        )
        child = _table('child', columns=[_col('a', is_primary_key=True), _col('b', is_primary_key=True)],
                       primary_keys=['a', 'b'], foreign_keys=[fk])
        parent = _table('parent', columns=[_col('x', is_primary_key=True), _col('y', is_primary_key=True)],
                        primary_keys=['x', 'y'])
        dot = visualise_schema(_schema([child, parent]), show_columns='all')
        assert '(a → x)' in dot
        assert '(b → y)' in dot

    def test_edges_present_regardless_of_show_columns(self):
        s = self._fk_schema()
        for mode in ('all', 'sparse', 'none'):
            dot = visualise_schema(s, show_columns=mode)
            assert '"atom_site" -> "structure"' in dot, f'Edge missing for show_columns={mode!r}'


# ---------------------------------------------------------------------------
# DOT output — column display
# ---------------------------------------------------------------------------

class TestColumnDisplay:
    def _schema_with_cols(self) -> SchemaSpec:
        cols = [
            _col('id', definition_id='_atom.id', is_primary_key=True),
            _col('fract_x', definition_id='_atom.fract_x', type_contents='Real'),
            _col('fract_y', definition_id='_atom.fract_y', type_contents='Real'),
        ]
        tbl = _table('atom', columns=cols, primary_keys=['id'])
        return _schema([tbl])

    def test_show_columns_none_no_column_rows(self):
        dot = visualise_schema(self._schema_with_cols(), show_columns='none')
        assert 'fract_x' not in dot
        assert 'fract_y' not in dot

    def test_show_columns_all_every_column_present(self):
        dot = visualise_schema(self._schema_with_cols(), show_columns='all')
        assert 'fract_x' in dot
        assert 'fract_y' in dot
        assert 'id' in dot

    def test_show_columns_sparse_pk_present(self):
        dot = visualise_schema(self._schema_with_cols(), show_columns='sparse')
        assert 'id' in dot

    def test_show_columns_sparse_non_key_absent(self):
        dot = visualise_schema(self._schema_with_cols(), show_columns='sparse')
        assert 'fract_x' not in dot
        assert 'fract_y' not in dot

    def test_column_display_is_object_id_not_definition_id(self):
        cols = [_col('fract_x', definition_id='_atom_site.fract_x', is_primary_key=True)]
        tbl = _table('atom_site', columns=cols, primary_keys=['fract_x'])
        dot = visualise_schema(_schema([tbl]), show_columns='all')
        # object_id 'fract_x' should appear; full definition_id not as plain text
        assert 'fract_x' in dot
        # definition_id should appear only in TOOLTIP, not as a display label
        assert 'TOOLTIP' in dot

    def test_tooltip_contains_definition_id(self):
        cols = [_col('fract_x', definition_id='_atom_site.fract_x', is_primary_key=True)]
        tbl = _table('atom_site', columns=cols, primary_keys=['fract_x'])
        dot = visualise_schema(_schema([tbl]), show_columns='all')
        assert 'TOOLTIP="_atom_site.fract_x"' in dot

    def test_pk_marker_present(self):
        cols = [_col('id', is_primary_key=True)]
        dot = visualise_schema(_schema([_table('t', columns=cols, primary_keys=['id'])]), show_columns='all')
        assert '[PK]' in dot

    def test_type_contents_annotation_present_when_set(self):
        cols = [_col('val', type_contents='Real', is_primary_key=True)]
        dot = visualise_schema(_schema([_table('t', columns=cols, primary_keys=['val'])]), show_columns='all')
        assert '(Real)' in dot

    def test_type_contents_absent_when_none(self):
        cols = [_col('val', type_contents=None, is_primary_key=True)]
        dot = visualise_schema(_schema([_table('t', columns=cols, primary_keys=['val'])]), show_columns='all')
        assert '(None)' not in dot

    def test_json_badge_for_non_single_container(self):
        cols = [_col('vals', type_container='List', is_primary_key=True)]
        dot = visualise_schema(_schema([_table('t', columns=cols, primary_keys=['vals'])]), show_columns='all')
        assert '[JSON]' in dot

    def test_no_json_badge_for_single_container(self):
        cols = [_col('val', type_container='Single', is_primary_key=True)]
        dot = visualise_schema(_schema([_table('t', columns=cols, primary_keys=['val'])]), show_columns='all')
        assert '[JSON]' not in dot

    def test_su_badge_for_linked_item_id(self):
        cols = [_col('val_su', linked_item_id='_test.val', is_primary_key=True)]
        dot = visualise_schema(_schema([_table('t', columns=cols, primary_keys=['val_su'])]), show_columns='all')
        assert '[SU]' in dot

    def test_sparse_includes_bridge_columns(self):
        bc = BridgeColumnDef(
            table_name='geom', column_name='structure_id',
            via_column='model_id', bridge_table='model',
            bridge_pk_column='id', bridge_value_column='structure_id',
        )
        cols = [
            _col('id', is_primary_key=True),
            _col('model_id'),
            _col('structure_id', is_synthetic=True),
            _col('angle', type_contents='Real'),
        ]
        tbl = _table('geom', columns=cols, primary_keys=['id'])
        model = _table('model', columns=[_col('id', is_primary_key=True)], primary_keys=['id'])
        s = _schema([tbl, model], bridge_columns=[bc])
        dot = visualise_schema(s, show_columns='sparse')
        assert 'model_id' in dot          # via_column
        assert 'structure_id' in dot      # derived column_name
        assert 'angle' not in dot         # plain data column not in sparse set


# ---------------------------------------------------------------------------
# DOT output — bridge edges
# ---------------------------------------------------------------------------

class TestBridgeEdges:
    def _bridge_schema(self) -> tuple[SchemaSpec, BridgeColumnDef]:
        bc = BridgeColumnDef(
            table_name='geom', column_name='structure_id',
            via_column='model_id', bridge_table='model',
            bridge_pk_column='id', bridge_value_column='structure_id',
        )
        geom = _table('geom')
        model = _table('model')
        s = _schema([geom, model], bridge_columns=[bc])
        return s, bc

    def test_bridge_edge_present_when_show_bridge_true(self):
        s, _ = self._bridge_schema()
        dot = visualise_schema(s, show_bridge=True)
        assert '"geom" -> "model"' in dot

    def test_bridge_edge_absent_when_show_bridge_false_for_connected_table(self):
        # geom is connected via FK to something else — bridge edge suppressed
        fk = ForeignKeyDef('geom', ['ref_id'], 'other', ['id'])
        bc = BridgeColumnDef('geom', 'structure_id', 'model_id', 'model', 'id', 'structure_id')
        geom = _table('geom', columns=[_col('id', is_primary_key=True), _col('ref_id')],
                      primary_keys=['id'], foreign_keys=[fk])
        other = _table('other')
        model = _table('model')
        s = _schema([geom, other, model], bridge_columns=[bc])
        dot = visualise_schema(s, show_bridge=False)
        assert '"geom" -> "model"' not in dot

    def test_bridge_edge_label_contains_via_column(self):
        s, bc = self._bridge_schema()
        dot = visualise_schema(s, show_bridge=True)
        assert f'via {bc.via_column}' in dot

    def test_bridge_edge_is_dashed(self):
        s, _ = self._bridge_schema()
        dot = visualise_schema(s, show_bridge=True)
        lines = [l for l in dot.splitlines() if '"geom" -> "model"' in l]
        assert lines
        assert 'dashed' in lines[0]

    def test_show_parent_edges_false_suppresses_parent_edge(self):
        s = _schema([_table('child'), _table('parent')], category_parent={'child': 'parent'})
        dot = visualise_schema(s, show_parent_edges=False)
        assert '"child" -> "parent"' not in dot

    def test_show_parent_edges_true_includes_parent_edge(self):
        s = _schema([_table('child'), _table('parent')], category_parent={'child': 'parent'})
        dot = visualise_schema(s, show_parent_edges=True)
        assert '"child" -> "parent"' in dot

    def test_parent_edge_is_dotted(self):
        s = _schema([_table('child'), _table('parent')], category_parent={'child': 'parent'})
        dot = visualise_schema(s, show_parent_edges=True)
        lines = [l for l in dot.splitlines() if '"child" -> "parent"' in l]
        assert lines
        assert 'dotted' in lines[0]


# ---------------------------------------------------------------------------
# Ghost nodes
# ---------------------------------------------------------------------------

class TestGhostNodes:
    def test_fk_target_missing_creates_ghost(self):
        fk = ForeignKeyDef('child', ['ref_id'], 'missing_table', ['id'])
        child = _table('child', columns=[_col('id', is_primary_key=True), _col('ref_id')],
                       primary_keys=['id'], foreign_keys=[fk])
        dot = visualise_schema(_schema([child]))
        assert 'missing_table' in dot
        assert '[MISSING]' in dot

    def test_bridge_target_missing_creates_ghost(self):
        bc = BridgeColumnDef('t', 'x', 'y', 'ghost_bridge', 'id', 'x')
        tbl = _table('t')
        dot = visualise_schema(_schema([tbl], bridge_columns=[bc]))
        assert 'ghost_bridge' in dot
        assert '[MISSING]' in dot

    def test_parent_missing_creates_ghost(self):
        tbl = _table('child')
        dot = visualise_schema(_schema([tbl], category_parent={'child': 'ghost_parent'}))
        assert 'ghost_parent' in dot
        assert '[MISSING]' in dot

    def test_ghost_node_has_no_column_rows(self):
        fk = ForeignKeyDef('child', ['ref_id'], 'ghost_tbl', ['id'])
        child = _table('child', columns=[_col('id', is_primary_key=True), _col('ref_id')],
                       primary_keys=['id'], foreign_keys=[fk])
        dot = visualise_schema(_schema([child]), show_columns='all')
        # Ghost node appears but has no PK or column content beyond [MISSING] label
        # It should NOT show [PK] (which comes from real columns)
        ghost_section = dot[dot.find('ghost_tbl'):]
        # The ghost node block ends at >] — find that
        end = ghost_section.find('>]')
        ghost_block = ghost_section[:end]
        assert '[PK]' not in ghost_block

    def test_edge_to_ghost_present_even_when_show_bridge_false(self):
        bc = BridgeColumnDef('t', 'x', 'y', 'ghost_bridge', 'id', 'x')
        tbl = _table('t')
        dot = visualise_schema(_schema([tbl], bridge_columns=[bc]), show_bridge=False)
        assert '"t" -> "ghost_bridge"' in dot

    def test_edge_to_ghost_present_even_when_show_parent_edges_false(self):
        tbl = _table('child')
        dot = visualise_schema(_schema([tbl], category_parent={'child': 'ghost_parent'}),
                               show_parent_edges=False)
        assert '"child" -> "ghost_parent"' in dot

    def test_ghost_present_even_when_show_orphans_false(self):
        fk = ForeignKeyDef('child', ['ref_id'], 'ghost_tbl', ['id'])
        child = _table('child', columns=[_col('id', is_primary_key=True), _col('ref_id')],
                       primary_keys=['id'], foreign_keys=[fk])
        dot = visualise_schema(_schema([child]), show_orphans=False)
        assert 'ghost_tbl' in dot

    def test_ghost_present_even_when_highlight_orphans_false(self):
        fk = ForeignKeyDef('child', ['ref_id'], 'ghost_tbl', ['id'])
        child = _table('child', columns=[_col('id', is_primary_key=True), _col('ref_id')],
                       primary_keys=['id'], foreign_keys=[fk])
        dot = visualise_schema(_schema([child]), highlight_orphans=False)
        assert 'ghost_tbl' in dot
        assert '[MISSING]' in dot

    def test_no_ghost_nodes_when_all_references_exist(self):
        fk = ForeignKeyDef('child', ['parent_id'], 'parent', ['id'])
        child = _table('child', columns=[_col('id', is_primary_key=True), _col('parent_id')],
                       primary_keys=['id'], foreign_keys=[fk])
        parent = _table('parent')
        dot = visualise_schema(_schema([child, parent]))
        assert '[MISSING]' not in dot


# ---------------------------------------------------------------------------
# Connectivity / orphan classification
# ---------------------------------------------------------------------------

class TestConnectivity:
    def test_connected_table_has_no_badge(self):
        fk = ForeignKeyDef('child', ['parent_id'], 'parent', ['id'])
        child = _table('child', columns=[_col('id', is_primary_key=True), _col('parent_id')],
                       primary_keys=['id'], foreign_keys=[fk])
        parent = _table('parent')
        dot = visualise_schema(_schema([child, parent]))
        assert '[ORPHAN]' not in dot
        assert '[BRIDGE ONLY]' not in dot

    def test_parent_hierarchy_only_is_connected(self):
        child = _table('child')
        parent = _table('parent')
        s = _schema([child, parent], category_parent={'child': 'parent'})
        dot = visualise_schema(s)
        assert '[ORPHAN]' not in dot
        assert '[BRIDGE ONLY]' not in dot

    def test_bridge_only_table_gets_badge(self):
        bc = BridgeColumnDef('isolated', 'x', 'y', 'model', 'id', 'x')
        isolated = _table('isolated')
        model = _table('model')
        s = _schema([isolated, model], bridge_columns=[bc])
        dot = visualise_schema(s)
        assert '[BRIDGE ONLY]' in dot

    def test_orphan_table_gets_badge(self):
        s = _schema([_table('alone')])
        dot = visualise_schema(s)
        assert '[ORPHAN]' in dot

    def test_linked_item_id_does_not_prevent_orphan_badge(self):
        # SU association does not count for connectivity
        col_with_su = _col('val_su', linked_item_id='_test.val', is_primary_key=True)
        tbl = _table('t', columns=[col_with_su], primary_keys=['val_su'])
        dot = visualise_schema(_schema([tbl]))
        assert '[ORPHAN]' in dot

    def test_bridge_only_edge_shown_even_when_show_bridge_false(self):
        bc = BridgeColumnDef('isolated', 'x', 'y', 'model', 'id', 'x')
        isolated = _table('isolated')
        model = _table('model')
        s = _schema([isolated, model], bridge_columns=[bc])
        dot = visualise_schema(s, show_bridge=False)
        assert '"isolated" -> "model"' in dot

    def test_show_orphans_false_removes_orphan_nodes(self):
        connected_fk = ForeignKeyDef('a', ['b_id'], 'b', ['id'])
        a = _table('a', columns=[_col('id', is_primary_key=True), _col('b_id')],
                   primary_keys=['id'], foreign_keys=[connected_fk])
        b = _table('b')
        lone = _table('lone_orphan')
        s = _schema([a, b, lone])
        dot = visualise_schema(s, show_orphans=False)
        assert 'lone_orphan' not in dot

    def test_show_orphans_false_removes_orphan_edges(self):
        bc = BridgeColumnDef('bridge_only_tbl', 'x', 'y', 'model', 'id', 'x')
        bridge_tbl = _table('bridge_only_tbl')
        model = _table('model')
        s = _schema([bridge_tbl, model], bridge_columns=[bc])
        dot = visualise_schema(s, show_orphans=False)
        assert 'bridge_only_tbl' not in dot
        assert '"bridge_only_tbl" -> "model"' not in dot

    def test_highlight_orphans_false_suppresses_badges(self):
        s = _schema([_table('lone')])
        dot = visualise_schema(s, highlight_orphans=False)
        assert '[ORPHAN]' not in dot
        assert '[BRIDGE ONLY]' not in dot

    def test_highlight_orphans_false_still_shows_table(self):
        s = _schema([_table('lone')])
        dot = visualise_schema(s, highlight_orphans=False)
        assert 'lone' in dot

    def test_highlight_components_produces_subgraph_clusters(self):
        fk = ForeignKeyDef('a', ['b_id'], 'b', ['id'])
        a = _table('a', columns=[_col('id', is_primary_key=True), _col('b_id')],
                   primary_keys=['id'], foreign_keys=[fk])
        b = _table('b')
        fk2 = ForeignKeyDef('c', ['d_id'], 'd', ['id'])
        c = _table('c', columns=[_col('id', is_primary_key=True), _col('d_id')],
                   primary_keys=['id'], foreign_keys=[fk2])
        d = _table('d')
        s = _schema([a, b, c, d])
        dot = visualise_schema(s, highlight_components=True)
        assert dot.count('subgraph cluster_') >= 2

    def test_highlight_components_orphans_in_cluster_orphans(self):
        s = _schema([_table('alone')])
        dot = visualise_schema(s, highlight_components=True)
        assert 'cluster_orphans' in dot

    def test_highlight_components_ghosts_in_cluster_missing(self):
        fk = ForeignKeyDef('child', ['ref_id'], 'ghost_tbl', ['id'])
        child = _table('child', columns=[_col('id', is_primary_key=True), _col('ref_id')],
                       primary_keys=['id'], foreign_keys=[fk])
        dot = visualise_schema(_schema([child]), highlight_components=True)
        assert 'cluster_missing' in dot


# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------

class TestHtml:
    def _simple_schema(self) -> SchemaSpec:
        return _schema([_table('atom'), _table('cell', category_class='Set')],
                       dictionary_name='test_dict')

    def test_starts_with_doctype(self):
        html = visualise_schema_html(self._simple_schema())
        assert html.startswith('<!DOCTYPE html>')

    def test_contains_table_name_in_script(self):
        html = visualise_schema_html(self._simple_schema())
        assert 'atom' in html

    def test_contains_render_svg_element(self):
        html = visualise_schema_html(self._simple_schema())
        assert 'renderSVGElement' in html

    def test_contains_svg_pan_zoom(self):
        html = visualise_schema_html(self._simple_schema())
        assert 'svgPanZoom' in html

    def test_title_parameter_in_title_tag(self):
        html = visualise_schema_html(self._simple_schema(), title='My Schema')
        assert '<title>My Schema</title>' in html

    def test_dictionary_name_in_title_when_no_title_given(self):
        html = visualise_schema_html(self._simple_schema())
        assert '<title>test_dict</title>' in html

    def test_default_title_when_no_dictionary_name(self):
        s = _schema([_table('x')])
        html = visualise_schema_html(s)
        assert '<title>Schema</title>' in html

    def test_no_external_urls(self):
        html = visualise_schema_html(self._simple_schema())
        assert 'cdn.jsdelivr.net' not in html
        assert 'unpkg.com' not in html
        assert 'cdnjs' not in html
