"""Tests for schema.py — generate_schema and emit_create_statements."""

import sqlite3

import pytest

from pycifparse.dictionary.ddlm_item import DdlmItem
from pycifparse.dictionary.ddlm_parser import DdlmDictionary
from pycifparse.dictionary.schema import (
    ColumnDef,
    ForeignKeyDef,
    SchemaSpec,
    TableDef,
    emit_create_statements,
    generate_schema,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(
    definition_id: str,
    category_id: str | None,
    object_id: str | None,
    *,
    type_purpose: str | None = None,
    type_contents: str | None = None,
    linked_item_id: str | None = None,
    **kwargs,
) -> DdlmItem:
    return DdlmItem(
        definition_id=definition_id,
        scope='Item',
        definition_class='Datum',
        category_id=category_id,
        object_id=object_id,
        type_purpose=type_purpose,
        type_source=None,
        type_container='Single',
        type_contents=type_contents,
        linked_item_id=linked_item_id,
        units_code=None,
        description=None,
        **kwargs,
    )


def _cat(
    definition_id: str,
    category_id: str,
    cat_class: str,
    category_keys: list[str] | None = None,
) -> DdlmItem:
    return DdlmItem(
        definition_id=definition_id,
        scope='Category',
        definition_class=cat_class,
        category_id=category_id,
        object_id=None,
        type_purpose=None,
        type_source=None,
        type_container='Single',
        type_contents=None,
        linked_item_id=None,
        units_code=None,
        description=None,
        category_keys=category_keys or [],
    )


def _make_dict(
    cats: list[DdlmItem],
    items: list[DdlmItem],
) -> DdlmDictionary:
    categories = {c.definition_id: c for c in cats}
    item_map = {i.definition_id: i for i in items}
    tag_to_item: dict[str, DdlmItem] = {}
    for entry in list(categories.values()) + list(item_map.values()):
        tag_to_item[entry.definition_id] = entry
        for alias in entry.aliases:
            tag_to_item[alias] = entry
    return DdlmDictionary(
        name='TEST',
        title=None,
        version=None,
        categories=categories,
        items=item_map,
        tag_to_item=tag_to_item,
        alias_to_definition_id={},
        deprecated_ids=set(),
    )


def _execute_schema(schema: SchemaSpec) -> sqlite3.Connection:
    """Return an in-memory SQLite connection with the schema applied."""
    conn = sqlite3.connect(':memory:')
    for stmt in emit_create_statements(schema):
        conn.execute(stmt)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Table naming
# ---------------------------------------------------------------------------

class TestTableNaming:
    def test_table_name_from_category_id(self):
        cats = [_cat('config', 'config', 'Set', ['_config.id'])]
        items = [_item('_config.id', 'config', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert 'config' in schema.tables

    def test_category_id_with_leading_underscore_stripped(self):
        cats = [_cat('_thing', '_thing', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        assert 'thing' in schema.tables

    def test_category_id_dot_replaced(self):
        cats = [_cat('a.b', 'a.b', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        assert 'a_b' in schema.tables

    def test_column_name_from_object_id(self):
        cats = [_cat('config', 'config', 'Set', ['_config.id'])]
        items = [_item('_config.id', 'config', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        col_names = [c.name for c in schema.tables['config'].columns]
        assert 'id' in col_names

    def test_mismatched_category_id_uses_name_category_id(self):
        # _weird.item has category_id='realcat', not 'weird'
        cats = [_cat('realcat', 'realcat', 'Set')]
        items = [_item('_weird.item', 'realcat', 'item', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert 'realcat' in schema.tables
        col_names = [c.name for c in schema.tables['realcat'].columns]
        assert 'item' in col_names


# ---------------------------------------------------------------------------
# Synthetic columns
# ---------------------------------------------------------------------------

class TestSyntheticColumns:
    def test_block_id_present_on_set_table(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        col_names = [c.name for c in schema.tables['cfg'].columns]
        assert '_block_id' in col_names

    def test_block_id_present_on_loop_table(self):
        cats = [_cat('meas', 'meas', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        col_names = [c.name for c in schema.tables['meas'].columns]
        assert '_block_id' in col_names

    def test_row_id_present_on_set_table(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        col_names = [c.name for c in schema.tables['cfg'].columns]
        assert '_row_id' in col_names

    def test_row_id_present_on_loop_table(self):
        cats = [_cat('meas', 'meas', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        col_names = [c.name for c in schema.tables['meas'].columns]
        assert '_row_id' in col_names

    def test_block_id_not_null(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        block_id = next(c for c in schema.tables['cfg'].columns if c.name == '_block_id')
        assert block_id.nullable is False

    def test_row_id_not_null(self):
        cats = [_cat('meas', 'meas', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        row_id = next(c for c in schema.tables['meas'].columns if c.name == '_row_id')
        assert row_id.nullable is False

    def test_synthetics_absent_from_column_to_tag(self):
        cats = [_cat('meas', 'meas', 'Loop', ['_meas.id'])]
        items = [_item('_meas.id', 'meas', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert ('meas', '_block_id') not in schema.column_to_tag
        assert ('meas', '_row_id') not in schema.column_to_tag

    def test_block_id_is_synthetic(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        col = next(c for c in schema.tables['cfg'].columns if c.name == '_block_id')
        assert col.is_synthetic is True

    def test_row_id_is_synthetic(self):
        cats = [_cat('meas', 'meas', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        col = next(c for c in schema.tables['meas'].columns if c.name == '_row_id')
        assert col.is_synthetic is True


# ---------------------------------------------------------------------------
# Primary key cases
# ---------------------------------------------------------------------------

class TestPrimaryKeys:
    def test_set_with_category_key(self):
        cats = [_cat('config', 'config', 'Set', ['_config.id'])]
        items = [_item('_config.id', 'config', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        table = schema.tables['config']
        assert table.primary_keys == ['id']
        pk_col = next(c for c in table.columns if c.name == 'id')
        assert pk_col.is_primary_key is True
        assert pk_col.nullable is False
        # _block_id not PK when key is present
        block_col = next(c for c in table.columns if c.name == '_block_id')
        assert block_col.is_primary_key is False

    def test_set_without_category_key_fallback(self):
        cats = [_cat('series', 'series', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        table = schema.tables['series']
        assert table.primary_keys == ['_pycifparse_id']
        pycifparse_id_col = next(c for c in table.columns if c.name == '_pycifparse_id')
        assert pycifparse_id_col.is_primary_key is True
        assert pycifparse_id_col.is_synthetic is True
        # _block_id is present but informational only
        block_col = next(c for c in table.columns if c.name == '_block_id')
        assert block_col.is_primary_key is False

    def test_set_without_category_key_emits_warning(self):
        cats = [_cat('series', 'series', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        assert any('series' in w and 'Set' in w and '_pycifparse_id' in w for w in schema.warnings)

    def test_loop_with_single_key(self):
        cats = [_cat('meas', 'meas', 'Loop', ['_meas.id'])]
        items = [_item('_meas.id', 'meas', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        table = schema.tables['meas']
        assert table.primary_keys == ['id']
        pk_col = next(c for c in table.columns if c.name == 'id')
        assert pk_col.is_primary_key is True

    def test_loop_with_composite_key(self):
        cats = [_cat('point', 'point', 'Loop', ['_point.x', '_point.y'])]
        items = [
            _item('_point.x', 'point', 'x', type_contents='Integer'),
            _item('_point.y', 'point', 'y', type_contents='Integer'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        table = schema.tables['point']
        assert table.primary_keys == ['x', 'y']
        for name in ('x', 'y'):
            col = next(c for c in table.columns if c.name == name)
            assert col.is_primary_key is True

    def test_loop_without_category_key_fallback(self):
        cats = [_cat('orphan', 'orphan', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        table = schema.tables['orphan']
        assert table.primary_keys == ['_block_id', '_row_id']
        for name in ('_block_id', '_row_id'):
            col = next(c for c in table.columns if c.name == name)
            assert col.is_primary_key is True

    def test_loop_without_category_key_emits_warning(self):
        cats = [_cat('orphan', 'orphan', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        assert any('orphan' in w and 'Loop' in w for w in schema.warnings)


# ---------------------------------------------------------------------------
# Head and unsupported categories
# ---------------------------------------------------------------------------

class TestCategorySkipping:
    def test_head_category_not_in_schema(self):
        cats = [_cat('cifcore', 'cifcore', 'Head')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        assert len(schema.tables) == 0

    def test_head_category_no_warning(self):
        cats = [_cat('cifcore', 'cifcore', 'Head')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        assert schema.warnings == []

    def test_functions_class_skipped_silently(self):
        cats = [_cat('funcs', 'funcs', 'Functions')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        assert len(schema.tables) == 0
        assert schema.warnings == []

    def test_truly_unsupported_class_skipped_with_warning(self):
        cats = [_cat('weird', 'weird', 'Bizarre')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        assert len(schema.tables) == 0
        assert any('weird' in w and 'Bizarre' in w for w in schema.warnings)


# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------

class TestTypeMapping:
    @pytest.mark.parametrize('type_contents', [
        'Integer', 'Real', 'Text', 'Word', 'Code', 'List', 'Table',
    ])
    def test_type_contents_stored_as_is(self, type_contents):
        """type_contents is stored verbatim from the DDLm dictionary."""
        cats = [_cat('t', 't', 'Set')]
        items = [_item('_t.col', 't', 'col', type_contents=type_contents)]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        col = next(c for c in schema.tables['t'].columns if c.name == 'col')
        assert col.type_contents == type_contents

    def test_type_contents_none_defaults_to_text(self):
        """Missing type_contents in a domain item defaults to 'Text'."""
        cats = [_cat('t', 't', 'Set')]
        items = [_item('_t.col', 't', 'col', type_contents=None)]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        col = next(c for c in schema.tables['t'].columns if c.name == 'col')
        assert col.type_contents == 'Text'

    @pytest.mark.parametrize('type_contents', [
        'Integer', 'Real', 'Text', 'Word', None,
    ])
    def test_ddl_always_emits_text_for_domain_columns(self, type_contents):
        """DDL always emits TEXT for domain columns regardless of type_contents."""
        cats = [_cat('t', 't', 'Set')]
        items = [_item('_t.col', 't', 'col', type_contents=type_contents)]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        stmt = emit_create_statements(schema)[0]
        assert '"col"  TEXT' in stmt


# ---------------------------------------------------------------------------
# Column ordering
# ---------------------------------------------------------------------------

class TestColumnOrdering:
    def test_set_column_order(self):
        # Set table: _block_id, _row_id, PK cols, then alpha non-PK cols
        cats = [_cat('cfg', 'cfg', 'Set', ['_cfg.id'])]
        items = [
            _item('_cfg.id', 'cfg', 'id', type_contents='Text'),
            _item('_cfg.z_last', 'cfg', 'z_last', type_contents='Text'),
            _item('_cfg.a_first', 'cfg', 'a_first', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        names = [c.name for c in schema.tables['cfg'].columns]
        assert names[0] == '_block_id'
        assert names[1] == '_row_id'
        assert names[2] == 'id'       # PK
        assert names[3] == 'a_first'  # alpha first non-PK
        assert names[4] == 'z_last'

    def test_loop_column_order(self):
        # Loop table: _block_id, _row_id, PK cols, then alpha non-PK cols
        cats = [_cat('meas', 'meas', 'Loop', ['_meas.id'])]
        items = [
            _item('_meas.id', 'meas', 'id', type_contents='Text'),
            _item('_meas.z_val', 'meas', 'z_val', type_contents='Real'),
            _item('_meas.a_name', 'meas', 'a_name', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        names = [c.name for c in schema.tables['meas'].columns]
        assert names[0] == '_block_id'
        assert names[1] == '_row_id'
        assert names[2] == 'id'       # PK
        assert names[3] == 'a_name'   # alpha non-PK
        assert names[4] == 'z_val'

    def test_composite_pk_order_follows_category_keys(self):
        cats = [_cat('point', 'point', 'Loop', ['_point.y', '_point.x'])]
        items = [
            _item('_point.x', 'point', 'x', type_contents='Integer'),
            _item('_point.y', 'point', 'y', type_contents='Integer'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        names = [c.name for c in schema.tables['point'].columns]
        # y comes before x because that's category_keys order
        assert names[2] == 'y'
        assert names[3] == 'x'


# ---------------------------------------------------------------------------
# column_to_tag reverse mapping
# ---------------------------------------------------------------------------

class TestColumnToTag:
    def test_domain_column_present(self):
        cats = [_cat('cfg', 'cfg', 'Set', ['_cfg.id'])]
        items = [
            _item('_cfg.id', 'cfg', 'id', type_contents='Text'),
            _item('_cfg.name', 'cfg', 'name', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert schema.column_to_tag[('cfg', 'id')] == '_cfg.id'
        assert schema.column_to_tag[('cfg', 'name')] == '_cfg.name'

    def test_synthetic_columns_excluded(self):
        cats = [_cat('meas', 'meas', 'Loop', ['_meas.id'])]
        items = [_item('_meas.id', 'meas', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert ('meas', '_block_id') not in schema.column_to_tag
        assert ('meas', '_row_id') not in schema.column_to_tag

    def test_column_to_tag_round_trip(self):
        cats = [_cat('cfg', 'cfg', 'Set', ['_cfg.id'])]
        items = [_item('_cfg.id', 'cfg', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        tag = schema.column_to_tag[('cfg', 'id')]
        resolved = d.tag_to_item[tag]
        assert resolved.object_id == 'id'


# ---------------------------------------------------------------------------
# Foreign keys
# ---------------------------------------------------------------------------

class TestForeignKeys:
    def _base_dict(self):
        cats = [
            _cat('config', 'config', 'Set', ['_config.id']),
            _cat('meas', 'meas', 'Loop', ['_meas.id']),
        ]
        items = [
            _item('_config.id', 'config', 'id', type_contents='Text', type_purpose='Key'),
            _item('_meas.id', 'meas', 'id', type_contents='Text', type_purpose='Key'),
            _item('_meas.time', 'meas', 'time', type_contents='Real'),
        ]
        return _make_dict(cats, items)

    def test_link_item_produces_foreign_key_def(self):
        cats = [
            _cat('config', 'config', 'Set', ['_config.id']),
            _cat('meas', 'meas', 'Loop', ['_meas.id']),
        ]
        items = [
            _item('_config.id', 'config', 'id', type_purpose='Key', type_contents='Text'),
            _item('_meas.id', 'meas', 'id', type_purpose='Key', type_contents='Text'),
            _item('_meas.config_id', 'meas', 'config_id', type_purpose='Link',
                  linked_item_id='_config.id', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        fks = schema.tables['meas'].foreign_keys
        assert len(fks) == 1
        fk = fks[0]
        assert fk.source_table == 'meas'
        assert fk.source_columns == ['config_id']
        assert fk.target_table == 'config'
        assert fk.target_columns == ['id']

    def test_self_referential_link(self):
        cats = [_cat('node', 'node', 'Loop', ['_node.id'])]
        items = [
            _item('_node.id', 'node', 'id', type_purpose='Key', type_contents='Text'),
            _item('_node.parent_id', 'node', 'parent_id', type_purpose='Link',
                  linked_item_id='_node.id', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        fks = schema.tables['node'].foreign_keys
        assert len(fks) == 1
        fk = fks[0]
        assert fk.source_table == 'node'
        assert fk.target_table == 'node'
        assert fk.target_columns == ['id']

    def test_link_with_unknown_target_skipped_with_warning(self):
        cats = [_cat('meas', 'meas', 'Loop', ['_meas.id'])]
        items = [
            _item('_meas.id', 'meas', 'id', type_purpose='Key', type_contents='Text'),
            _item('_meas.ref', 'meas', 'ref', type_purpose='Link',
                  linked_item_id='_unknown.id', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert schema.tables['meas'].foreign_keys == []
        assert any('_unknown.id' in w for w in schema.warnings)

    def test_link_where_target_not_pk_skipped_with_warning(self):
        # If the target column is not a PK of its table, SQLite raises
        # "foreign key mismatch" at INSERT time.  generate_schema must skip
        # such FKs and emit a warning instead.
        cats = [
            _cat('src', 'src', 'Loop', ['_src.id']),
            _cat('tgt', 'tgt', 'Loop', ['_tgt.id']),
        ]
        items = [
            _item('_src.id', 'src', 'id', type_purpose='Key', type_contents='Text'),
            _item('_tgt.id', 'tgt', 'id', type_purpose='Key', type_contents='Text'),
            _item('_tgt.extra', 'tgt', 'extra', type_contents='Text'),
            # Links to _tgt.extra which is NOT a category key and NOT a PK
            _item('_src.ref', 'src', 'ref', type_purpose='Link',
                  linked_item_id='_tgt.extra', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        # FK must be skipped — target column is not a PK
        assert schema.tables['src'].foreign_keys == []
        # Warning emitted for the non-PK target
        assert any('_src.ref' in w and 'not a PK' in w for w in schema.warnings)

    def test_composite_fk_when_all_pks_covered(self):
        # When two source columns each link to one column of a composite PK,
        # generate_schema must emit one composite FOREIGN KEY constraint.
        cats = [
            _cat('parent', 'parent', 'Loop', ['_parent.a', '_parent.b']),
            _cat('child',  'child',  'Loop', ['_child.a',  '_child.b']),
        ]
        items = [
            _item('_parent.a', 'parent', 'a', type_purpose='Key', type_contents='Text'),
            _item('_parent.b', 'parent', 'b', type_purpose='Key', type_contents='Text'),
            _item('_child.a',  'child',  'a', type_purpose='Link',
                  linked_item_id='_parent.a', type_contents='Text'),
            _item('_child.b',  'child',  'b', type_purpose='Link',
                  linked_item_id='_parent.b', type_contents='Text'),
            _item('_child.val', 'child', 'val', type_contents='Real'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        fks = schema.tables['child'].foreign_keys
        assert len(fks) == 1
        fk = fks[0]
        assert fk.source_table == 'child'
        assert fk.target_table == 'parent'
        # Columns ordered by target PK order (a, b)
        assert fk.source_columns == ['a', 'b']
        assert fk.target_columns == ['a', 'b']
        # No warnings about this FK
        assert not any('child' in w and 'skipping' in w for w in schema.warnings)

    def test_partial_composite_fk_skipped_with_warning(self):
        # Only one of two composite PK columns is linked — can't form a complete FK.
        cats = [
            _cat('parent', 'parent', 'Loop', ['_parent.a', '_parent.b']),
            _cat('child',  'child',  'Loop', ['_child.x']),
        ]
        items = [
            _item('_parent.a', 'parent', 'a', type_purpose='Key', type_contents='Text'),
            _item('_parent.b', 'parent', 'b', type_purpose='Key', type_contents='Text'),
            # Links to only _parent.a, missing _parent.b
            _item('_child.x', 'child', 'x', type_purpose='Link',
                  linked_item_id='_parent.a', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert schema.tables['child'].foreign_keys == []
        assert any('_child.x' in w and 'skipping' in w for w in schema.warnings)

    def test_su_item_populates_linked_item_id_no_fk(self):
        cats = [_cat('meas', 'meas', 'Loop', ['_meas.id'])]
        items = [
            _item('_meas.id', 'meas', 'id', type_purpose='Key', type_contents='Text'),
            _item('_meas.val', 'meas', 'val', type_contents='Real'),
            _item('_meas.val_su', 'meas', 'val_su', type_purpose='SU',
                  linked_item_id='_meas.val', type_contents='Real'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        # No FK produced
        assert schema.tables['meas'].foreign_keys == []
        # linked_item_id populated on the SU column
        su_col = next(c for c in schema.tables['meas'].columns if c.name == 'val_su')
        assert su_col.linked_item_id == '_meas.val'
        # Not populated on the non-SU column
        val_col = next(c for c in schema.tables['meas'].columns if c.name == 'val')
        assert val_col.linked_item_id is None


# ---------------------------------------------------------------------------
# emit_create_statements
# ---------------------------------------------------------------------------

class TestEmitCreateStatements:
    def test_returns_one_stmt_per_table(self):
        cats = [
            _cat('a', 'a', 'Set'),
            _cat('b', 'b', 'Loop'),
        ]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        stmts = emit_create_statements(schema)
        assert len(stmts) == 2

    def test_stmt_starts_with_create_table_if_not_exists(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        stmt = emit_create_statements(schema)[0]
        assert stmt.startswith('CREATE TABLE IF NOT EXISTS "cfg" (')

    def test_not_null_on_block_id(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        stmt = emit_create_statements(schema)[0]
        assert '"_block_id"  TEXT  NOT NULL' in stmt

    def test_row_id_composite_unique_in_keyed_loop_stmt(self):
        # Keyed Loop: _row_id is not PK, so composite UNIQUE (_block_id, _row_id) added
        cats = [_cat('meas', 'meas', 'Loop', ['_meas.id'])]
        items = [_item('_meas.id', 'meas', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        stmt = emit_create_statements(schema)[0]
        assert 'UNIQUE ("_block_id", "_row_id")' in stmt

    def test_row_id_no_extra_unique_in_keyless_loop_stmt(self):
        # Keyless Loop: PK is (_block_id, _row_id) — no extra UNIQUE constraint
        cats = [_cat('meas', 'meas', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        stmt = emit_create_statements(schema)[0]
        assert 'PRIMARY KEY ("_block_id", "_row_id")' in stmt
        assert 'UNIQUE' not in stmt

    def test_row_id_present_in_set_stmt(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        stmt = emit_create_statements(schema)[0]
        assert '"_row_id"  INTEGER  NOT NULL' in stmt
        assert 'UNIQUE ("_block_id", "_row_id")' in stmt

    def test_fk_clause_with_deferrable(self):
        cats = [
            _cat('config', 'config', 'Set', ['_config.id']),
            _cat('meas', 'meas', 'Loop', ['_meas.id']),
        ]
        items = [
            _item('_config.id', 'config', 'id', type_purpose='Key', type_contents='Text'),
            _item('_meas.id', 'meas', 'id', type_purpose='Key', type_contents='Text'),
            _item('_meas.config_id', 'meas', 'config_id', type_purpose='Link',
                  linked_item_id='_config.id', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        stmts = emit_create_statements(schema)
        meas_stmt = next(s for s in stmts if 'meas' in s.split('(')[0])
        assert 'FOREIGN KEY ("config_id")' in meas_stmt
        assert 'REFERENCES "config"("id")' in meas_stmt
        assert 'DEFERRABLE INITIALLY DEFERRED' in meas_stmt

    def test_no_fk_clause_for_su(self):
        cats = [_cat('meas', 'meas', 'Loop', ['_meas.id'])]
        items = [
            _item('_meas.id', 'meas', 'id', type_purpose='Key', type_contents='Text'),
            _item('_meas.val_su', 'meas', 'val_su', type_purpose='SU',
                  linked_item_id='_meas.val', type_contents='Real'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        stmt = emit_create_statements(schema)[0]
        assert 'FOREIGN KEY' not in stmt

    def test_stmts_execute_against_sqlite(self):
        cats = [
            _cat('config', 'config', 'Set', ['_config.id']),
            _cat('meas', 'meas', 'Loop', ['_meas.id']),
        ]
        items = [
            _item('_config.id', 'config', 'id', type_purpose='Key', type_contents='Text'),
            _item('_config.name', 'config', 'name', type_contents='Text'),
            _item('_meas.id', 'meas', 'id', type_purpose='Key', type_contents='Text'),
            _item('_meas.val', 'meas', 'val', type_contents='Real'),
            _item('_meas.config_id', 'meas', 'config_id', type_purpose='Link',
                  linked_item_id='_config.id', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        conn = _execute_schema(schema)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert 'config' in tables
        assert 'meas' in tables

    def test_block_id_row_id_composite_unique_via_pragma(self):
        # Keyed Loop: composite UNIQUE (_block_id, _row_id) should exist
        cats = [_cat('meas', 'meas', 'Loop', ['_meas.id'])]
        items = [_item('_meas.id', 'meas', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        conn = _execute_schema(schema)
        indexes = list(conn.execute("PRAGMA index_list('meas')"))
        unique_indexes = [row for row in indexes if row[2] == 1]
        found = False
        for row in unique_indexes:
            cols = [r[2] for r in conn.execute(f"PRAGMA index_info('{row[1]}')")]
            if '_block_id' in cols and '_row_id' in cols:
                found = True
                break
        assert found, "composite UNIQUE (_block_id, _row_id) should exist on keyed Loop table"

    def test_fk_via_pragma(self):
        cats = [
            _cat('config', 'config', 'Set', ['_config.id']),
            _cat('meas', 'meas', 'Loop', ['_meas.id']),
        ]
        items = [
            _item('_config.id', 'config', 'id', type_purpose='Key', type_contents='Text'),
            _item('_meas.id', 'meas', 'id', type_purpose='Key', type_contents='Text'),
            _item('_meas.config_id', 'meas', 'config_id', type_purpose='Link',
                  linked_item_id='_config.id', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        conn = _execute_schema(schema)
        fk_list = list(conn.execute("PRAGMA foreign_key_list('meas')"))
        assert len(fk_list) >= 1
        fk = fk_list[0]
        assert fk[2] == 'config'     # referenced table
        assert fk[3] == 'config_id'  # from column
        assert fk[4] == 'id'         # to column


# ---------------------------------------------------------------------------
# Gap-coverage tests — category key warnings and FK edge cases
# ---------------------------------------------------------------------------

class TestCategoryKeyWarnings:
    def test_category_key_not_in_dictionary_warns(self):
        """category_keys contains a tag absent from tag_to_item → warning (lines 373-377)."""
        cats = [_cat('atom', 'atom', 'Loop', ['_atom.missing_key'])]
        items = [_item('_atom.x', 'atom', 'x', type_contents='Real')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert any('not found in dictionary' in w for w in schema.warnings)

    def test_category_key_no_object_id_warns(self):
        """Key item has object_id=None → warning (lines 379-383)."""
        cats = [_cat('atom', 'atom', 'Loop', ['_atom.noobj'])]
        # Create the key item with object_id=None via DdlmItem directly
        key_item = DdlmItem(
            definition_id='_atom.noobj', scope='Item', definition_class='Datum',
            category_id='atom', object_id=None,
            type_purpose='Key', type_source=None, type_container='Single',
            type_contents='Text', linked_item_id=None, units_code=None, description=None,
        )
        items = [key_item]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert any('has no object_id' in w for w in schema.warnings)

    def test_pk_column_not_in_domain_items_warns(self):
        """Key object_id does not appear in category items → warning (lines 446-450)."""
        # Create a category whose key tag has object_id='ghost_col'
        # but no item in the category has object_id='ghost_col'.
        ghost_key = DdlmItem(
            definition_id='_atom.ghost', scope='Item', definition_class='Datum',
            category_id='atom', object_id='ghost_col',
            type_purpose='Key', type_source=None, type_container='Single',
            type_contents='Text', linked_item_id=None, units_code=None, description=None,
        )
        cats = [_cat('atom', 'atom', 'Loop', ['_atom.ghost'])]
        # No item with object_id='ghost_col' in domain_items
        items = [ghost_key, _item('_atom.x', 'atom', 'x')]
        # Remove 'ghost' from the items list but keep it in tag_to_item
        cat_obj = cats[0]
        item_map = {'_atom.x': items[1]}  # ghost not in items
        tag_to_item = {
            'atom': cat_obj,
            '_atom.ghost': ghost_key,
            '_atom.x': items[1],
        }
        d = DdlmDictionary(
            name='TEST', title=None, version=None,
            categories={'atom': cat_obj},
            items=item_map,
            tag_to_item=tag_to_item,
            alias_to_definition_id={}, deprecated_ids=set(),
        )
        schema = generate_schema(d)
        assert any('not found in category items' in w for w in schema.warnings)


class TestFKEdgeCases:
    def test_link_item_with_no_category_id_skipped(self):
        """Link item with category_id=None doesn't produce FK (lines 532-533)."""
        cats = [_cat('tgt', 'tgt', 'Loop', ['_tgt.id'])]
        items = [
            _item('_tgt.id', 'tgt', 'id', type_purpose='Key', type_contents='Text'),
        ]
        link_item = DdlmItem(
            definition_id='_orphan.ref', scope='Item', definition_class='Datum',
            category_id=None,  # no category
            object_id='ref', type_purpose='Link', type_source=None,
            type_container='Single', type_contents='Text',
            linked_item_id='_tgt.id', units_code=None, description=None,
        )
        cat_obj = cats[0]
        item_map = {i.definition_id: i for i in items + [link_item]}
        tag_to_item = {**{c.definition_id: c for c in cats}, **item_map}
        d = DdlmDictionary(
            name='T', title=None, version=None,
            categories={c.definition_id: c for c in cats},
            items=item_map, tag_to_item=tag_to_item,
            alias_to_definition_id={}, deprecated_ids=set(),
        )
        schema = generate_schema(d)
        assert schema.tables['tgt'].foreign_keys == []

    def test_link_target_not_in_schema_warns(self):
        """FK target table not in schema → warning (lines 543-547)."""
        # Head category → no table generated
        head_cat = DdlmItem(
            definition_id='head', scope='Category', definition_class='Head',
            category_id='head', object_id=None, type_purpose=None, type_source=None,
            type_container='Single', type_contents=None, linked_item_id=None,
            units_code=None, description=None, category_keys=[],
        )
        head_item = DdlmItem(
            definition_id='_head.id', scope='Item', definition_class='Datum',
            category_id='head', object_id='id',
            type_purpose='Key', type_source=None, type_container='Single',
            type_contents='Text', linked_item_id=None, units_code=None, description=None,
        )
        cats = [_cat('src', 'src', 'Loop', ['_src.id'])]
        items = [
            _item('_src.id', 'src', 'id', type_purpose='Key', type_contents='Text'),
            _item('_src.ref', 'src', 'ref', type_purpose='Link',
                  linked_item_id='_head.id', type_contents='Text'),
        ]
        all_cats = {c.definition_id: c for c in cats}
        all_cats['head'] = head_cat
        item_map = {i.definition_id: i for i in items + [head_item]}
        tag_to_item = {**all_cats, **item_map}
        d = DdlmDictionary(
            name='T', title=None, version=None,
            categories=all_cats, items=item_map, tag_to_item=tag_to_item,
            alias_to_definition_id={}, deprecated_ids=set(),
        )
        schema = generate_schema(d)
        assert any('not in schema' in w for w in schema.warnings)

    def test_link_target_item_no_category_id_skipped(self):
        """Target item has category_id=None → silently skipped (line 535)."""
        orphan_target = DdlmItem(
            definition_id='_orphan.id', scope='Item', definition_class='Datum',
            category_id=None, object_id='id',
            type_purpose='Key', type_source=None, type_container='Single',
            type_contents='Text', linked_item_id=None, units_code=None, description=None,
        )
        cats = [_cat('src', 'src', 'Loop', ['_src.id'])]
        items = [
            _item('_src.id', 'src', 'id', type_purpose='Key', type_contents='Text'),
            _item('_src.ref', 'src', 'ref', type_purpose='Link',
                  linked_item_id='_orphan.id', type_contents='Text'),
        ]
        item_map = {i.definition_id: i for i in items + [orphan_target]}
        tag_to_item = {**{c.definition_id: c for c in cats}, **item_map}
        d = DdlmDictionary(
            name='T', title=None, version=None,
            categories={c.definition_id: c for c in cats},
            items=item_map, tag_to_item=tag_to_item,
            alias_to_definition_id={}, deprecated_ids=set(),
        )
        schema = generate_schema(d)
        assert schema.tables['src'].foreign_keys == []


class TestPropagationLinks:
    def test_propagation_links_populated_for_pk_link_item(self):
        """Loop category with a PK column that is a Link item → propagation_links
        is non-empty and the PK column is made nullable (lines 709, 712, 721)."""
        # parent Set category
        cats = [
            _cat('parent', 'parent', 'Set', ['_parent.id']),
            _cat('child', 'child', 'Loop', ['_child.parent_id']),
        ]
        items = [
            _item('_parent.id', 'parent', 'id', type_purpose='Key', type_contents='Text'),
            # child PK is a Link to parent.id
            _item('_child.parent_id', 'child', 'parent_id', type_purpose='Link',
                  linked_item_id='_parent.id', type_contents='Text'),
            _item('_child.val', 'child', 'val', type_contents='Real'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert 'child' in schema.propagation_links
        entries = schema.propagation_links['child']
        assert any(col == 'parent_id' for col, _, _ in entries)
        # The PK column must be made nullable
        col_def = next(
            c for c in schema.tables['child'].columns if c.name == 'parent_id'
        )
        assert col_def.nullable is True



