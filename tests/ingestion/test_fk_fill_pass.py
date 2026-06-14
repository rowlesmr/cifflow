"""Branch-coverage tests for _run_fk_fill_pass helper functions.

Tests target _fill_single_fk, _fill_composite_fk, and _fill_propagation_links
in isolation before the refactoring.
"""
import duckdb
import pytest

from cifflow.dictionary.ddlm_item import DdlmItem
from cifflow.dictionary.ddlm_parser import DdlmDictionary
from cifflow.dictionary.schema import generate_schema
from cifflow.ingestion.duckdb_ingest import (
    _fill_composite_fk,
    _fill_propagation_links,
    _fill_single_fk,
    setup_duckdb,
)
from cifflow.ingestion.ingest import build_tag_to_column


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _item(definition_id, category_id, object_id, *, type_purpose=None,
          type_contents=None, linked_item_id=None, enumeration_default=None):
    return DdlmItem(
        definition_id=definition_id, scope='Item', definition_class='Datum',
        category_id=category_id, object_id=object_id,
        type_purpose=type_purpose, type_source=None, type_container='Single',
        type_contents=type_contents, linked_item_id=linked_item_id,
        units_code=None, description=None,
        enumeration_default=enumeration_default,
    )


def _cat(definition_id, cat_class, category_keys=None):
    return DdlmItem(
        definition_id=definition_id, scope='Category',
        definition_class=cat_class, category_id=None, object_id=None,
        type_purpose=None, type_source=None, type_container='Single',
        type_contents=None, linked_item_id=None, units_code=None,
        description=None, category_keys=category_keys or [],
    )


def _make_dict(cats, items):
    categories = {c.definition_id: c for c in cats}
    item_map = {i.definition_id: i for i in items}
    return DdlmDictionary(
        name='TEST', title=None, version=None,
        categories=categories, items=item_map,
        tag_to_item={**categories, **item_map},
        alias_to_definition_id={}, deprecated_ids=set(),
    )


_SCALARS = '__scalars__'


def _insert(db, table, rows):
    for i, row in enumerate(rows):
        sys_cols = ['_cifflow_block_id', '_cifflow_block_idx', '_loop_id', '_iter_idx', '_cifflow_row_id']
        sys_vals = [
            f"'{row.get('_cifflow_block_id', 'blk1')}'",
            str(row.get('_cifflow_block_idx', 0)),
            f"'{row.get('_loop_id', _SCALARS)}'",
            str(row.get('_iter_idx', 0)),
            str(row.get('_cifflow_row_id', i + 1)),
        ]
        data_cols = [f'"{k}"' for k in row
                     if k not in ('_cifflow_block_id', '_cifflow_block_idx', '_loop_id', '_iter_idx', '_cifflow_row_id')]
        data_vals = [
            f"'{v}'" if v is not None else 'NULL'
            for k, v in row.items()
            if k not in ('_cifflow_block_id', '_cifflow_block_idx', '_loop_id', '_iter_idx', '_cifflow_row_id')
        ]
        all_cols = ', '.join(sys_cols + data_cols)
        all_vals = ', '.join(sys_vals + data_vals)
        db.execute(f'INSERT INTO "_raw_{table}" ({all_cols}) VALUES ({all_vals})')


def _fetch(db, table, col):
    return [r[0] for r in db.execute(f'SELECT "{col}" FROM "_raw_{table}"').fetchall()]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

def _schema_parent_child():
    """structure (Set PK=id) → cell (Set PK=structure_id key-FK→structure.id)."""
    cats = [
        _cat('_structure', 'Set', ['_structure.id']),
        _cat('_cell', 'Set', ['_cell.structure_id']),
    ]
    items = [
        _item('_structure.id', '_structure', 'id', type_purpose='Key', type_contents='Text'),
        _item('_cell.structure_id', '_cell', 'structure_id',
              type_purpose='Link', linked_item_id='_structure.id', type_contents='Text'),
        _item('_cell.length_a', '_cell', 'length_a',
              type_purpose='Measurand', type_contents='Real'),
    ]
    return generate_schema(_make_dict(cats, items))


def _schema_non_key_fk():
    """structure (Set PK=id) and atom_site (Loop PK=label non-key FK structure_id→structure.id)."""
    cats = [
        _cat('_structure', 'Set', ['_structure.id']),
        _cat('_atom_site', 'Loop', ['_atom_site.label']),
    ]
    items = [
        _item('_structure.id', '_structure', 'id', type_purpose='Key', type_contents='Text'),
        _item('_atom_site.label', '_atom_site', 'label', type_purpose='Key', type_contents='Text'),
        _item('_atom_site.structure_id', '_atom_site', 'structure_id',
              type_purpose='Link', linked_item_id='_structure.id', type_contents='Text'),
    ]
    return generate_schema(_make_dict(cats, items))


def _schema_composite_fk():
    """phase (Set PK=(id1,id2)), atom (Loop PK=label composite FK→phase)."""
    cats = [
        _cat('_phase', 'Set', ['_phase.id1', '_phase.id2']),
        _cat('_atom', 'Loop', ['_atom.label']),
    ]
    items = [
        _item('_phase.id1', '_phase', 'id1', type_purpose='Key', type_contents='Text'),
        _item('_phase.id2', '_phase', 'id2', type_purpose='Key', type_contents='Text'),
        _item('_atom.label', '_atom', 'label', type_purpose='Key', type_contents='Text'),
        _item('_atom.phase_id1', '_atom', 'phase_id1',
              type_purpose='Link', linked_item_id='_phase.id1', type_contents='Text'),
        _item('_atom.phase_id2', '_atom', 'phase_id2',
              type_purpose='Link', linked_item_id='_phase.id2', type_contents='Text'),
    ]
    return generate_schema(_make_dict(cats, items))


def _schema_with_default():
    """structure (Set PK=id) and atom_site (Loop, PK=label, non-PK Link col with default)."""
    cats = [
        _cat('_structure', 'Set', ['_structure.id']),
        _cat('_atom_site', 'Loop', ['_atom_site.label']),
    ]
    items = [
        _item('_structure.id', '_structure', 'id', type_purpose='Key', type_contents='Text'),
        _item('_atom_site.label', '_atom_site', 'label', type_purpose='Key', type_contents='Text'),
        _item('_atom_site.structure_id', '_atom_site', 'structure_id',
              type_purpose='Link', linked_item_id='_structure.id',
              type_contents='Text', enumeration_default='default_struct'),
    ]
    return generate_schema(_make_dict(cats, items))


def _col_by_name(schema, tbl_name):
    table = schema.tables[tbl_name]
    return {c.name: c for c in table.columns if not c.is_synthetic}


# ---------------------------------------------------------------------------
# Tests: _fill_single_fk
# ---------------------------------------------------------------------------

class TestFillSingleFk:
    def test_key_fk_null_filled_from_loop_match(self):
        """Key-FK NULL col is filled from parent row with same block+loop+iter."""
        schema = _schema_parent_child()
        db = setup_duckdb(schema)
        _insert(db, 'structure', [{'id': 's1', '_loop_id': _SCALARS, '_iter_idx': 0, '_cifflow_row_id': 1}])
        _insert(db, 'cell', [{'structure_id': None, 'length_a': '5.0', '_loop_id': _SCALARS, '_iter_idx': 0, '_cifflow_row_id': 2}])
        table = schema.tables['cell']
        col_by_name = _col_by_name(schema, 'cell')
        _fill_single_fk(db, 'cell', table, col_by_name, schema, False, lambda *a: None)
        assert _fetch(db, 'cell', 'structure_id') == ['s1']

    def test_key_fk_already_set_unchanged(self):
        schema = _schema_parent_child()
        db = setup_duckdb(schema)
        _insert(db, 'structure', [{'id': 's1', '_cifflow_row_id': 1}])
        _insert(db, 'cell', [{'structure_id': 's1', 'length_a': '5.0', '_cifflow_row_id': 2}])
        table = schema.tables['cell']
        col_by_name = _col_by_name(schema, 'cell')
        _fill_single_fk(db, 'cell', table, col_by_name, schema, False, lambda *a: None)
        assert _fetch(db, 'cell', 'structure_id') == ['s1']

    def test_non_key_fk_propagate_false_stays_null(self):
        schema = _schema_non_key_fk()
        db = setup_duckdb(schema)
        _insert(db, 'structure', [{'id': 's1', '_cifflow_row_id': 1}])
        _insert(db, 'atom_site', [{'label': 'C1', 'structure_id': None, '_cifflow_row_id': 2}])
        table = schema.tables['atom_site']
        col_by_name = _col_by_name(schema, 'atom_site')
        _fill_single_fk(db, 'atom_site', table, col_by_name, schema, False, lambda *a: None)
        assert _fetch(db, 'atom_site', 'structure_id') == [None]

    def test_non_key_fk_propagate_true_filled(self):
        schema = _schema_non_key_fk()
        db = setup_duckdb(schema)
        _insert(db, 'structure', [{'id': 's1', '_cifflow_row_id': 1}])
        _insert(db, 'atom_site', [{'label': 'C1', 'structure_id': None, '_cifflow_row_id': 2}])
        table = schema.tables['atom_site']
        col_by_name = _col_by_name(schema, 'atom_site')
        _fill_single_fk(db, 'atom_site', table, col_by_name, schema, True, lambda *a: None)
        assert _fetch(db, 'atom_site', 'structure_id') == ['s1']

    def test_scalars_fallback_used_when_no_loop_match(self):
        """Parent row is in scalars loop; child is in a named loop."""
        schema = _schema_non_key_fk()
        db = setup_duckdb(schema)
        _insert(db, 'structure', [{'id': 's1', '_loop_id': _SCALARS, '_cifflow_row_id': 1}])
        _insert(db, 'atom_site', [{'label': 'C1', 'structure_id': None, '_loop_id': 'lp1', '_cifflow_row_id': 2}])
        table = schema.tables['atom_site']
        col_by_name = _col_by_name(schema, 'atom_site')
        _fill_single_fk(db, 'atom_site', table, col_by_name, schema, True, lambda *a: None)
        assert _fetch(db, 'atom_site', 'structure_id') == ['s1']


# ---------------------------------------------------------------------------
# Tests: _fill_composite_fk
# ---------------------------------------------------------------------------

class TestFillCompositeFk:
    def test_composite_fk_nulls_filled_from_parent_when_propagate_true(self):
        # phase_id1/phase_id2 are non-PK in atom → only filled when propagate_fk=True
        schema = _schema_composite_fk()
        db = setup_duckdb(schema)
        _insert(db, 'phase', [{'id1': 'p1', 'id2': 'q1', '_loop_id': _SCALARS, '_cifflow_row_id': 1}])
        _insert(db, 'atom', [{'label': 'A1', 'phase_id1': None, 'phase_id2': None,
                               '_loop_id': _SCALARS, '_cifflow_row_id': 2}])
        table = schema.tables['atom']
        col_by_name = _col_by_name(schema, 'atom')
        _fill_composite_fk(db, 'atom', table, col_by_name, schema, True)
        assert _fetch(db, 'atom', 'phase_id1') == ['p1']
        assert _fetch(db, 'atom', 'phase_id2') == ['q1']

    def test_composite_non_key_propagate_false_stays_null(self):
        # phase_id1/phase_id2 are non-PK in atom (PK is label), propagate_fk=False
        schema = _schema_composite_fk()
        db = setup_duckdb(schema)
        _insert(db, 'phase', [{'id1': 'p1', 'id2': 'q1', '_cifflow_row_id': 1}])
        _insert(db, 'atom', [{'label': 'A1', 'phase_id1': None, 'phase_id2': None, '_cifflow_row_id': 2}])
        table = schema.tables['atom']
        col_by_name = _col_by_name(schema, 'atom')
        _fill_composite_fk(db, 'atom', table, col_by_name, schema, False)
        assert _fetch(db, 'atom', 'phase_id1') == [None]

    def test_composite_non_key_propagate_true_filled(self):
        schema = _schema_composite_fk()
        db = setup_duckdb(schema)
        _insert(db, 'phase', [{'id1': 'p1', 'id2': 'q1', '_loop_id': _SCALARS, '_cifflow_row_id': 1}])
        _insert(db, 'atom', [{'label': 'A1', 'phase_id1': None, 'phase_id2': None,
                               '_loop_id': _SCALARS, '_cifflow_row_id': 2}])
        table = schema.tables['atom']
        col_by_name = _col_by_name(schema, 'atom')
        _fill_composite_fk(db, 'atom', table, col_by_name, schema, True)
        assert _fetch(db, 'atom', 'phase_id1') == ['p1']
        assert _fetch(db, 'atom', 'phase_id2') == ['q1']


# ---------------------------------------------------------------------------
# Tests: _fill_propagation_links
# ---------------------------------------------------------------------------

class TestFillPropagationLinks:
    def test_propagation_link_fills_from_linked_table(self):
        """Key-FK with propagation link: cell.structure_id filled from structure.id."""
        schema = _schema_parent_child()
        db = setup_duckdb(schema)
        _insert(db, 'structure', [{'id': 's1', '_loop_id': _SCALARS, '_cifflow_row_id': 1}])
        _insert(db, 'cell', [{'structure_id': None, 'length_a': '5.0',
                               '_loop_id': _SCALARS, '_cifflow_row_id': 2}])
        table = schema.tables['cell']
        col_by_name = _col_by_name(schema, 'cell')
        tag_to_column = build_tag_to_column(schema)
        _fill_propagation_links(db, 'cell', table, col_by_name, schema, True, tag_to_column)
        assert _fetch(db, 'cell', 'structure_id') == ['s1']

    def test_default_value_applied_when_no_match(self):
        """Non-PK Link col with enumeration_default gets default when still NULL."""
        schema = _schema_with_default()
        db = setup_duckdb(schema)
        # No structure rows → propagation chain finds nothing; default should apply
        _insert(db, 'atom_site', [{'label': 'C1', 'structure_id': None, '_cifflow_row_id': 1}])
        table = schema.tables['atom_site']
        col_by_name = _col_by_name(schema, 'atom_site')
        tag_to_column = build_tag_to_column(schema)
        _fill_propagation_links(db, 'atom_site', table, col_by_name, schema, False, tag_to_column)
        assert _fetch(db, 'atom_site', 'structure_id') == ['default_struct']

    def test_already_set_col_not_overwritten_by_default(self):
        schema = _schema_with_default()
        db = setup_duckdb(schema)
        _insert(db, 'atom_site', [{'label': 'C1', 'structure_id': 'existing', '_cifflow_row_id': 1}])
        table = schema.tables['atom_site']
        col_by_name = _col_by_name(schema, 'atom_site')
        tag_to_column = build_tag_to_column(schema)
        _fill_propagation_links(db, 'atom_site', table, col_by_name, schema, False, tag_to_column)
        assert _fetch(db, 'atom_site', 'structure_id') == ['existing']
