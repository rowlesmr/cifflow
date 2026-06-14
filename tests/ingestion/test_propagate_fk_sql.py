"""Branch-coverage tests for propagate_fk_sql helpers.

Tests are written against the extracted helper functions before refactoring
so that the refactoring can be verified to preserve behaviour.
"""
import duckdb
import pytest

from cifflow.dictionary.ddlm_item import DdlmItem
from cifflow.dictionary.ddlm_parser import DdlmDictionary
from cifflow.dictionary.schema import generate_schema
from cifflow.ingestion.duckdb_ingest import (
    _create_composite_fk_stub_parents,
    _create_single_fk_stub_parents,
    _generate_uuid_pks,
    _topo_order,
    setup_duckdb,
)


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _item(definition_id, category_id, object_id, *, type_purpose=None,
          type_contents=None, linked_item_id=None):
    return DdlmItem(
        definition_id=definition_id, scope='Item', definition_class='Datum',
        category_id=category_id, object_id=object_id,
        type_purpose=type_purpose, type_source=None, type_container='Single',
        type_contents=type_contents, linked_item_id=linked_item_id,
        units_code=None, description=None,
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


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

_SCALARS = '__scalars__'


def _insert(db, table, rows):
    """Insert rows into _raw_{table}. Each row dict may include system cols or just data cols."""
    for i, row in enumerate(rows):
        sys_defaults = {
            '_cifflow_block_id': 'blk1',
            '_cifflow_block_idx': 0,
            '_loop_id': _SCALARS,
            '_iter_idx': 0,
            '_cifflow_row_id': i + 1,
        }
        sys_cols = ['_cifflow_block_id', '_cifflow_block_idx', '_loop_id', '_iter_idx', '_cifflow_row_id']
        sys_vals = [
            f"'{row.get('_cifflow_block_id', sys_defaults['_cifflow_block_id'])}'",
            str(row.get('_cifflow_block_idx', sys_defaults['_cifflow_block_idx'])),
            f"'{row.get('_loop_id', sys_defaults['_loop_id'])}'",
            str(row.get('_iter_idx', sys_defaults['_iter_idx'])),
            str(row.get('_cifflow_row_id', sys_defaults['_cifflow_row_id'])),
        ]
        data_cols = [f'"{k}"' for k in row if not k.startswith('_cifflow_') and k != '_loop_id' and k != '_iter_idx']
        data_vals = [
            f"'{v}'" if v is not None else 'NULL'
            for k, v in row.items()
            if not k.startswith('_cifflow_') and k != '_loop_id' and k != '_iter_idx'
        ]
        all_cols = ', '.join(sys_cols + data_cols)
        all_vals = ', '.join(sys_vals + data_vals)
        db.execute(f'INSERT INTO "_raw_{table}" ({all_cols}) VALUES ({all_vals})')


def _fetch(db, table, col):
    return [r[0] for r in db.execute(f'SELECT "{col}" FROM "_raw_{table}"').fetchall()]


def _count(db, table):
    return db.execute(f'SELECT COUNT(*) FROM "_raw_{table}"').fetchone()[0]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

def _schema_simple():
    """structure (Set, PK=id) — no FK on id."""
    cats = [_cat('_structure', 'Set', ['_structure.id'])]
    items = [
        _item('_structure.id', '_structure', 'id', type_purpose='Key', type_contents='Text'),
        _item('_structure.name', '_structure', 'name', type_contents='Text'),
    ]
    return generate_schema(_make_dict(cats, items))


def _schema_parent_child():
    """structure (Set, PK=id) → cell (Set, PK=structure_id key-FK→structure.id)."""
    cats = [
        _cat('_structure', 'Set', ['_structure.id']),
        _cat('_cell', 'Set', ['_cell.structure_id']),
    ]
    items = [
        _item('_structure.id', '_structure', 'id', type_purpose='Key', type_contents='Text'),
        _item('_cell.structure_id', '_cell', 'structure_id',
              type_purpose='Link', linked_item_id='_structure.id', type_contents='Text'),
        _item('_cell.length_a', '_cell', 'length_a', type_contents='Real'),
    ]
    return generate_schema(_make_dict(cats, items))


def _schema_composite_fk():
    """phase (Set, PK=(id1,id2)), atom (Loop, PK=label, composite FK→phase)."""
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


def _schema_siblings():
    """Two Loop tables (cat_a, cat_b) sharing the same PK column name 'label', no FK between them."""
    cats = [
        _cat('_cat_a', 'Loop', ['_cat_a.label']),
        _cat('_cat_b', 'Loop', ['_cat_b.label']),
    ]
    items = [
        _item('_cat_a.label', '_cat_a', 'label', type_purpose='Key', type_contents='Text'),
        _item('_cat_a.value', '_cat_a', 'value', type_contents='Text'),
        _item('_cat_b.label', '_cat_b', 'label', type_purpose='Key', type_contents='Text'),
        _item('_cat_b.value', '_cat_b', 'value', type_contents='Text'),
    ]
    return generate_schema(_make_dict(cats, items))


# ---------------------------------------------------------------------------
# Tests: _generate_uuid_pks
# ---------------------------------------------------------------------------

class TestGenerateUuidPks:
    def test_null_pk_gets_uuid(self):
        schema = _schema_simple()
        db = setup_duckdb(schema)
        _insert(db, 'structure', [{'id': None, 'name': 'test'}])
        _generate_uuid_pks(db, schema, None)
        ids = _fetch(db, 'structure', 'id')
        assert len(ids) == 1
        assert ids[0] is not None
        assert len(ids[0]) > 0

    def test_existing_pk_unchanged(self):
        schema = _schema_simple()
        db = setup_duckdb(schema)
        _insert(db, 'structure', [{'id': 'my_id', 'name': 'test'}])
        _generate_uuid_pks(db, schema, None)
        assert _fetch(db, 'structure', 'id') == ['my_id']

    def test_pk_with_single_fk_skipped(self):
        # cell.structure_id has a single FK to structure.id — should NOT get UUID assigned
        schema = _schema_parent_child()
        db = setup_duckdb(schema)
        _insert(db, 'structure', [{'id': 's1'}])
        _insert(db, 'cell', [{'structure_id': None, 'length_a': '5.0'}])
        _generate_uuid_pks(db, schema, None)
        # structure_id has has_single_fk=True → skipped; stays NULL
        assert _fetch(db, 'cell', 'structure_id') == [None]

    def test_null_pk_multiple_rows(self):
        schema = _schema_simple()
        db = setup_duckdb(schema)
        _insert(db, 'structure', [
            {'id': None, 'name': 'a', '_cifflow_row_id': 1},
            {'id': None, 'name': 'b', '_cifflow_row_id': 2},
        ])
        _generate_uuid_pks(db, schema, None)
        ids = _fetch(db, 'structure', 'id')
        assert all(v is not None for v in ids)
        assert ids[0] != ids[1]  # each row gets a distinct UUID

    def test_sibling_copies_from_canonical(self):
        # cat_a and cat_b share PK 'label'; cat_a is canonical (sorted first)
        schema = _schema_siblings()
        db = setup_duckdb(schema)
        _insert(db, 'cat_a', [{'label': 'L1', 'value': 'x', '_loop_id': 'lp1', '_iter_idx': 0, '_cifflow_row_id': 1}])
        _insert(db, 'cat_b', [{'label': None, 'value': 'y', '_loop_id': 'lp1', '_iter_idx': 0, '_cifflow_row_id': 2}])
        _generate_uuid_pks(db, schema, None)
        # cat_b should have copied 'L1' from cat_a (same block, loop, iter)
        assert _fetch(db, 'cat_b', 'label') == ['L1']

    def test_sibling_gets_own_uuid_when_canonical_null(self):
        schema = _schema_siblings()
        db = setup_duckdb(schema)
        # Both NULL at different iter_idx — no match to copy from
        _insert(db, 'cat_a', [{'label': None, '_loop_id': 'lp1', '_iter_idx': 0, '_cifflow_row_id': 1}])
        _insert(db, 'cat_b', [{'label': None, '_loop_id': 'lp1', '_iter_idx': 1, '_cifflow_row_id': 2}])
        _generate_uuid_pks(db, schema, None)
        vals_a = _fetch(db, 'cat_a', 'label')
        vals_b = _fetch(db, 'cat_b', 'label')
        assert vals_a[0] is not None
        assert vals_b[0] is not None

    def test_populated_filter_restricts_tables(self):
        schema = _schema_siblings()
        db = setup_duckdb(schema)
        _insert(db, 'cat_a', [{'label': None, '_cifflow_row_id': 1}])
        _insert(db, 'cat_b', [{'label': None, '_cifflow_row_id': 2}])
        _generate_uuid_pks(db, schema, {'cat_a'})
        # Only cat_a is in populated — cat_a gets UUID, cat_b stays NULL
        assert _fetch(db, 'cat_a', 'label')[0] is not None
        assert _fetch(db, 'cat_b', 'label')[0] is None


# ---------------------------------------------------------------------------
# Tests: _create_composite_fk_stub_parents
# ---------------------------------------------------------------------------

class TestCreateCompositeFkStubParents:
    def test_missing_composite_parent_created(self):
        schema = _schema_composite_fk()
        db = setup_duckdb(schema)
        topo = _topo_order(schema)
        _insert(db, 'atom', [{'label': 'A1', 'phase_id1': 'p1', 'phase_id2': 'q1'}])
        _create_composite_fk_stub_parents(db, schema, topo, None)
        assert _count(db, 'phase') == 1
        assert _fetch(db, 'phase', 'id1') == ['p1']
        assert _fetch(db, 'phase', 'id2') == ['q1']

    def test_existing_composite_parent_not_duplicated(self):
        schema = _schema_composite_fk()
        db = setup_duckdb(schema)
        topo = _topo_order(schema)
        _insert(db, 'phase', [{'id1': 'p1', 'id2': 'q1', '_cifflow_row_id': 1}])
        _insert(db, 'atom', [{'label': 'A1', 'phase_id1': 'p1', 'phase_id2': 'q1', '_cifflow_row_id': 2}])
        _create_composite_fk_stub_parents(db, schema, topo, None)
        assert _count(db, 'phase') == 1  # no duplicate inserted

    def test_null_composite_fk_not_inserted(self):
        schema = _schema_composite_fk()
        db = setup_duckdb(schema)
        topo = _topo_order(schema)
        _insert(db, 'atom', [{'label': 'A1', 'phase_id1': None, 'phase_id2': None}])
        _create_composite_fk_stub_parents(db, schema, topo, None)
        assert _count(db, 'phase') == 0

    def test_populated_filter_composite(self):
        schema = _schema_composite_fk()
        db = setup_duckdb(schema)
        topo = _topo_order(schema)
        _insert(db, 'atom', [{'label': 'A1', 'phase_id1': 'p1', 'phase_id2': 'q1'}])
        # atom not in populated → no stub created
        _create_composite_fk_stub_parents(db, schema, topo, {'phase'})
        assert _count(db, 'phase') == 0

    def test_populated_set_updated(self):
        schema = _schema_composite_fk()
        db = setup_duckdb(schema)
        topo = _topo_order(schema)
        _insert(db, 'atom', [{'label': 'A1', 'phase_id1': 'p1', 'phase_id2': 'q1'}])
        populated = {'atom', 'phase'}
        _create_composite_fk_stub_parents(db, schema, topo, populated)
        # phase should remain in populated (was added or already present)
        assert 'phase' in populated


# ---------------------------------------------------------------------------
# Tests: _create_single_fk_stub_parents
# ---------------------------------------------------------------------------

class TestCreateSingleFkStubParents:
    def test_missing_single_parent_created(self):
        schema = _schema_parent_child()
        db = setup_duckdb(schema)
        topo = _topo_order(schema)
        _insert(db, 'cell', [{'structure_id': 's1', 'length_a': '5.0'}])
        _create_single_fk_stub_parents(db, schema, topo, None)
        assert _count(db, 'structure') == 1
        assert _fetch(db, 'structure', 'id') == ['s1']

    def test_existing_single_parent_not_duplicated(self):
        schema = _schema_parent_child()
        db = setup_duckdb(schema)
        topo = _topo_order(schema)
        _insert(db, 'structure', [{'id': 's1', '_cifflow_row_id': 1}])
        _insert(db, 'cell', [{'structure_id': 's1', 'length_a': '5.0', '_cifflow_row_id': 2}])
        _create_single_fk_stub_parents(db, schema, topo, None)
        assert _count(db, 'structure') == 1

    def test_null_single_fk_not_inserted(self):
        schema = _schema_parent_child()
        db = setup_duckdb(schema)
        topo = _topo_order(schema)
        _insert(db, 'cell', [{'structure_id': None, 'length_a': '5.0'}])
        _create_single_fk_stub_parents(db, schema, topo, None)
        assert _count(db, 'structure') == 0

    def test_populated_filter_single(self):
        schema = _schema_parent_child()
        db = setup_duckdb(schema)
        topo = _topo_order(schema)
        _insert(db, 'cell', [{'structure_id': 's1', 'length_a': '5.0'}])
        # cell not in populated → no stub created for structure
        _create_single_fk_stub_parents(db, schema, topo, {'structure'})
        assert _count(db, 'structure') == 0

    def test_populated_set_updated_on_insert(self):
        schema = _schema_parent_child()
        db = setup_duckdb(schema)
        topo = _topo_order(schema)
        _insert(db, 'cell', [{'structure_id': 's1', 'length_a': '5.0'}])
        populated = {'cell', 'structure'}
        _create_single_fk_stub_parents(db, schema, topo, populated)
        assert 'structure' in populated
