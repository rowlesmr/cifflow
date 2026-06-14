"""Tests for schema.py — generate_schema and emit_create_statements."""

import sqlite3

import pytest

from cifflow.dictionary.ddlm_item import DdlmItem
from cifflow.dictionary.ddlm_parser import DdlmDictionary
from cifflow.dictionary.schema import (
    ColumnDef,
    ForeignKeyDef,
    PartialLinkDef,
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
    def test_cifflow_block_id_present_on_set_table(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        col_names = [c.name for c in schema.tables['cfg'].columns]
        assert '_cifflow_block_id' in col_names

    def test_cifflow_block_id_present_on_loop_table(self):
        cats = [_cat('meas', 'meas', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        col_names = [c.name for c in schema.tables['meas'].columns]
        assert '_cifflow_block_id' in col_names

    def test_cifflow_row_id_present_on_set_table(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        col_names = [c.name for c in schema.tables['cfg'].columns]
        assert '_cifflow_row_id' in col_names

    def test_cifflow_row_id_present_on_loop_table(self):
        cats = [_cat('meas', 'meas', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        col_names = [c.name for c in schema.tables['meas'].columns]
        assert '_cifflow_row_id' in col_names

    def test_cifflow_block_id_not_null(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        block_id = next(c for c in schema.tables['cfg'].columns if c.name == '_cifflow_block_id')
        assert block_id.nullable is False

    def test_cifflow_row_id_not_null(self):
        cats = [_cat('meas', 'meas', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        row_id = next(c for c in schema.tables['meas'].columns if c.name == '_cifflow_row_id')
        assert row_id.nullable is False

    def test_synthetics_absent_from_column_to_tag(self):
        cats = [_cat('meas', 'meas', 'Loop', ['_meas.id'])]
        items = [_item('_meas.id', 'meas', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert ('meas', '_cifflow_block_id') not in schema.column_to_tag
        assert ('meas', '_cifflow_row_id') not in schema.column_to_tag

    def test_cifflow_block_id_is_synthetic(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        col = next(c for c in schema.tables['cfg'].columns if c.name == '_cifflow_block_id')
        assert col.is_synthetic is True

    def test_cifflow_row_id_is_synthetic(self):
        cats = [_cat('meas', 'meas', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        col = next(c for c in schema.tables['meas'].columns if c.name == '_cifflow_row_id')
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
        # _cifflow_block_id not PK when key is present
        block_col = next(c for c in table.columns if c.name == '_cifflow_block_id')
        assert block_col.is_primary_key is False

    def test_set_without_category_key_fallback(self):
        cats = [_cat('series', 'series', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        table = schema.tables['series']
        assert table.primary_keys == ['_cifflow_id']
        cifflow_id_col = next(c for c in table.columns if c.name == '_cifflow_id')
        assert cifflow_id_col.is_primary_key is True
        assert cifflow_id_col.is_synthetic is True
        # _cifflow_block_id is present but informational only
        block_col = next(c for c in table.columns if c.name == '_cifflow_block_id')
        assert block_col.is_primary_key is False

    def test_set_without_category_key_emits_warning(self):
        cats = [_cat('series', 'series', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        assert any('series' in w and 'Set' in w and '_cifflow_id' in w for w in schema.warnings)

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
        assert table.primary_keys == ['_cifflow_block_id', '_cifflow_row_id']
        for name in ('_cifflow_block_id', '_cifflow_row_id'):
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
        # Set table: _cifflow_block_id, _cifflow_row_id, PK cols, then alpha non-PK cols
        cats = [_cat('cfg', 'cfg', 'Set', ['_cfg.id'])]
        items = [
            _item('_cfg.id', 'cfg', 'id', type_contents='Text'),
            _item('_cfg.z_last', 'cfg', 'z_last', type_contents='Text'),
            _item('_cfg.a_first', 'cfg', 'a_first', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        names = [c.name for c in schema.tables['cfg'].columns]
        assert names[0] == '_cifflow_block_id'
        assert names[1] == '_cifflow_row_id'
        assert names[2] == 'id'       # PK
        assert names[3] == 'a_first'  # alpha first non-PK
        assert names[4] == 'z_last'

    def test_loop_column_order(self):
        # Loop table: _cifflow_block_id, _cifflow_row_id, PK cols, then alpha non-PK cols
        cats = [_cat('meas', 'meas', 'Loop', ['_meas.id'])]
        items = [
            _item('_meas.id', 'meas', 'id', type_contents='Text'),
            _item('_meas.z_val', 'meas', 'z_val', type_contents='Real'),
            _item('_meas.a_name', 'meas', 'a_name', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        names = [c.name for c in schema.tables['meas'].columns]
        assert names[0] == '_cifflow_block_id'
        assert names[1] == '_cifflow_row_id'
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
        assert ('meas', '_cifflow_block_id') not in schema.column_to_tag
        assert ('meas', '_cifflow_row_id') not in schema.column_to_tag

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

    def test_not_null_on_cifflow_block_id(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        stmt = emit_create_statements(schema)[0]
        assert '"_cifflow_block_id"  TEXT  NOT NULL' in stmt

    def test_cifflow_row_id_composite_unique_in_keyed_loop_stmt(self):
        # Keyed Loop: _cifflow_row_id is not PK, so composite UNIQUE (_cifflow_block_id, _cifflow_row_id) added
        cats = [_cat('meas', 'meas', 'Loop', ['_meas.id'])]
        items = [_item('_meas.id', 'meas', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        stmt = emit_create_statements(schema)[0]
        assert 'UNIQUE ("_cifflow_block_id", "_cifflow_row_id")' in stmt

    def test_cifflow_row_id_no_extra_unique_in_keyless_loop_stmt(self):
        # Keyless Loop: PK is (_cifflow_block_id, _cifflow_row_id) — no extra UNIQUE constraint
        cats = [_cat('meas', 'meas', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        stmt = emit_create_statements(schema)[0]
        assert 'PRIMARY KEY ("_cifflow_block_id", "_cifflow_row_id")' in stmt
        assert 'UNIQUE' not in stmt

    def test_cifflow_row_id_present_in_set_stmt(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        stmt = emit_create_statements(schema)[0]
        assert '"_cifflow_row_id"  INTEGER  NOT NULL' in stmt
        assert 'UNIQUE ("_cifflow_block_id", "_cifflow_row_id")' in stmt

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

    def test_cifflow_block_id_cifflow_row_id_composite_unique_via_pragma(self):
        # Keyed Loop: composite UNIQUE (_cifflow_block_id, _cifflow_row_id) should exist
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
            if '_cifflow_block_id' in cols and '_cifflow_row_id' in cols:
                found = True
                break
        assert found, "composite UNIQUE (_cifflow_block_id, _cifflow_row_id) should exist on keyed Loop table"

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


# ---------------------------------------------------------------------------
# SchemaSpec.descendants()
# ---------------------------------------------------------------------------

class TestDescendants:
    """Tests for SchemaSpec.descendants(root)."""

    @pytest.fixture
    def schema(self):
        # Category hierarchy:
        #   root (Set)
        #     child_a (Loop)  — child_a.category_id == 'root'
        #       grandchild (Loop) — grandchild.category_id == 'child_a'
        #     child_b (Loop)  — child_b.category_id == 'root'
        #   unrelated (Set)
        cats = [
            _cat('root',       'root',    'Set',  ['_root.id']),
            _cat('child_a',    'root',    'Loop', ['_child_a.id']),
            _cat('grandchild', 'child_a', 'Loop', ['_grandchild.id']),
            _cat('child_b',    'root',    'Loop', ['_child_b.id']),
            _cat('unrelated',  'unrelated', 'Set', ['_unrelated.id']),
        ]
        items = [
            _item('_root.id',       'root',       'id',  type_purpose='Key', type_contents='Text'),
            _item('_child_a.id',    'child_a',    'id',  type_purpose='Key', type_contents='Text'),
            _item('_grandchild.id', 'grandchild', 'id',  type_purpose='Key', type_contents='Text'),
            _item('_child_b.id',    'child_b',    'id',  type_purpose='Key', type_contents='Text'),
            _item('_unrelated.id',  'unrelated',  'id',  type_purpose='Key', type_contents='Text'),
        ]
        return generate_schema(_make_dict(cats, items))

    def test_root_includes_itself(self, schema):
        result = schema.descendants('root')
        assert 'root' in result

    def test_root_includes_direct_children(self, schema):
        result = schema.descendants('root')
        assert 'child_a' in result
        assert 'child_b' in result

    def test_root_includes_grandchildren(self, schema):
        result = schema.descendants('root')
        assert 'grandchild' in result

    def test_root_excludes_unrelated(self, schema):
        result = schema.descendants('root')
        assert 'unrelated' not in result

    def test_child_excludes_parent(self, schema):
        result = schema.descendants('child_a')
        assert 'root' not in result
        assert 'child_a' in result
        assert 'grandchild' in result
        assert 'child_b' not in result

    def test_leaf_returns_singleton(self, schema):
        result = schema.descendants('grandchild')
        assert result == frozenset({'grandchild'})

    def test_unknown_root_returns_empty(self, schema):
        assert schema.descendants('does_not_exist') == frozenset()


# ---------------------------------------------------------------------------
# Dual links to the same PK (bond-endpoint pattern)
# ---------------------------------------------------------------------------

class TestDualLinkToSamePK:
    """has_conflicts=True, missing_pk_cols={}: two source columns reference the same sole PK.
    Expect one independent FK per source column (not a composite FK)."""

    @pytest.fixture
    def schema(self):
        cats = [
            _cat('atom', 'atom', 'Loop', ['_atom.number']),
            _cat('bond', 'bond', 'Loop', ['_bond.id']),
        ]
        items = [
            _item('_atom.number', 'atom', 'number', type_purpose='Key', type_contents='Text'),
            _item('_bond.id', 'bond', 'id', type_purpose='Key', type_contents='Text'),
            _item('_bond.atom_1', 'bond', 'atom_1', type_purpose='Link',
                  linked_item_id='_atom.number', type_contents='Text'),
            _item('_bond.atom_2', 'bond', 'atom_2', type_purpose='Link',
                  linked_item_id='_atom.number', type_contents='Text'),
        ]
        return generate_schema(_make_dict(cats, items))

    def test_two_fks_produced(self, schema):
        assert len(schema.tables['bond'].foreign_keys) == 2

    def test_each_fk_targets_atom_number(self, schema):
        for fk in schema.tables['bond'].foreign_keys:
            assert fk.target_table == 'atom'
            assert fk.target_columns == ['number']

    def test_both_endpoint_columns_present_as_sources(self, schema):
        src_cols = {fk.source_columns[0] for fk in schema.tables['bond'].foreign_keys}
        assert src_cols == {'atom_1', 'atom_2'}


# ---------------------------------------------------------------------------
# Missing one PK column already present in source (no bridge lookup needed)
# ---------------------------------------------------------------------------

class TestMissingOnePKAlreadyInSrc:
    """missing_pk_cols==1 and the missing column already exists in src.
    Expect a composite FK formed without a transitive bridge lookup."""

    def test_composite_fk_formed_from_existing_column(self):
        # parent PKs: (a, b); child has 'b' in its own PK; child.c → parent.a
        # missing_pk_col='b' is found directly in child's columns → FK formed.
        cats = [
            _cat('parent', 'parent', 'Loop', ['_parent.a', '_parent.b']),
            _cat('child', 'child', 'Loop', ['_child.b', '_child.x']),
        ]
        items = [
            _item('_parent.a', 'parent', 'a', type_purpose='Key', type_contents='Text'),
            _item('_parent.b', 'parent', 'b', type_purpose='Key', type_contents='Text'),
            _item('_child.b', 'child', 'b', type_purpose='Key', type_contents='Text'),
            _item('_child.x', 'child', 'x', type_purpose='Key', type_contents='Text'),
            _item('_child.c', 'child', 'c', type_purpose='Link',
                  linked_item_id='_parent.a', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        fks = schema.tables['child'].foreign_keys
        assert len(fks) == 1
        fk = fks[0]
        assert fk.target_table == 'parent'
        assert fk.target_columns == ['a', 'b']
        assert fk.source_columns == ['c', 'b']


# ---------------------------------------------------------------------------
# More than one PK column missing (bridge search skipped)
# ---------------------------------------------------------------------------

class TestMoreThanOneMissingPK:
    """len(missing_pk_cols) > 1 — falls into the final elif arm (no bridge attempted)."""

    @pytest.fixture
    def schema(self):
        cats = [
            _cat('parent', 'parent', 'Loop', ['_parent.a', '_parent.b', '_parent.c']),
            _cat('child', 'child', 'Loop', ['_child.id']),
        ]
        items = [
            _item('_parent.a', 'parent', 'a', type_purpose='Key', type_contents='Text'),
            _item('_parent.b', 'parent', 'b', type_purpose='Key', type_contents='Text'),
            _item('_parent.c', 'parent', 'c', type_purpose='Key', type_contents='Text'),
            _item('_child.id', 'child', 'id', type_purpose='Key', type_contents='Text'),
            _item('_child.a', 'child', 'a', type_purpose='Link',
                  linked_item_id='_parent.a', type_contents='Text'),
        ]
        return generate_schema(_make_dict(cats, items))

    def test_no_fk_produced(self, schema):
        assert schema.tables['child'].foreign_keys == []

    def test_warning_emitted(self, schema):
        assert any('_child.a' in w and 'skipping' in w for w in schema.warnings)

    def test_partial_link_recorded(self, schema):
        assert any(
            pl.source_table == 'child' and pl.source_column == 'a'
            for pl in schema.partial_links
        )


# ---------------------------------------------------------------------------
# partial_links field
# ---------------------------------------------------------------------------

class TestPartialLinks:
    """PartialLinkDef entries are recorded for unresolvable Link items."""

    @pytest.fixture
    def schema(self):
        # src.ref → tgt.extra where 'extra' is not a PK → partial link
        cats = [
            _cat('src', 'src', 'Loop', ['_src.id']),
            _cat('tgt', 'tgt', 'Loop', ['_tgt.id']),
        ]
        items = [
            _item('_src.id', 'src', 'id', type_purpose='Key', type_contents='Text'),
            _item('_tgt.id', 'tgt', 'id', type_purpose='Key', type_contents='Text'),
            _item('_tgt.extra', 'tgt', 'extra', type_contents='Text'),
            _item('_src.ref', 'src', 'ref', type_purpose='Link',
                  linked_item_id='_tgt.extra', type_contents='Text'),
        ]
        return generate_schema(_make_dict(cats, items))

    def test_partial_link_in_list(self, schema):
        assert any(
            pl.source_table == 'src' and pl.source_column == 'ref'
            for pl in schema.partial_links
        )

    def test_partial_link_target_fields(self, schema):
        pl = next(p for p in schema.partial_links if p.source_column == 'ref')
        assert pl.target_table == 'tgt'
        assert pl.target_column == 'extra'

    def test_partial_link_missing_pks(self, schema):
        pl = next(p for p in schema.partial_links if p.source_column == 'ref')
        assert 'id' in pl.missing_pk_cols


# ---------------------------------------------------------------------------
# Non-PK Link items in propagation_links
# ---------------------------------------------------------------------------

class TestPropagationLinksNonPK:
    """Non-PK Link items enter propagation_links only when enumeration_default is set."""

    def test_non_pk_link_with_default_in_propagation_links(self):
        cats = [
            _cat('parent', 'parent', 'Set', ['_parent.id']),
            _cat('child', 'child', 'Loop', ['_child.id']),
        ]
        items = [
            _item('_parent.id', 'parent', 'id', type_purpose='Key', type_contents='Text'),
            _item('_child.id', 'child', 'id', type_purpose='Key', type_contents='Text'),
            _item('_child.ref', 'child', 'ref', type_purpose='Link',
                  linked_item_id='_parent.id', type_contents='Text',
                  enumeration_default='DEFAULT_VAL'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert 'child' in schema.propagation_links
        assert any(col == 'ref' for col, _, _ in schema.propagation_links['child'])

    def test_non_pk_link_default_value_stored(self):
        cats = [
            _cat('parent', 'parent', 'Set', ['_parent.id']),
            _cat('child', 'child', 'Loop', ['_child.id']),
        ]
        items = [
            _item('_parent.id', 'parent', 'id', type_purpose='Key', type_contents='Text'),
            _item('_child.id', 'child', 'id', type_purpose='Key', type_contents='Text'),
            _item('_child.ref', 'child', 'ref', type_purpose='Link',
                  linked_item_id='_parent.id', type_contents='Text',
                  enumeration_default='MY_DEFAULT'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        entry = next(e for e in schema.propagation_links['child'] if e[0] == 'ref')
        assert entry[2] == 'MY_DEFAULT'

    def test_non_pk_link_without_default_excluded(self):
        cats = [
            _cat('parent', 'parent', 'Set', ['_parent.id']),
            _cat('child', 'child', 'Loop', ['_child.id']),
        ]
        items = [
            _item('_parent.id', 'parent', 'id', type_purpose='Key', type_contents='Text'),
            _item('_child.id', 'child', 'id', type_purpose='Key', type_contents='Text'),
            _item('_child.ref', 'child', 'ref', type_purpose='Link',
                  linked_item_id='_parent.id', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        entries = schema.propagation_links.get('child', [])
        assert not any(col == 'ref' for col, _, _ in entries)

    def test_non_pk_link_column_remains_nullable(self):
        # Non-PK column should already be nullable — propagation_links does not change it
        cats = [
            _cat('parent', 'parent', 'Set', ['_parent.id']),
            _cat('child', 'child', 'Loop', ['_child.id']),
        ]
        items = [
            _item('_parent.id', 'parent', 'id', type_purpose='Key', type_contents='Text'),
            _item('_child.id', 'child', 'id', type_purpose='Key', type_contents='Text'),
            _item('_child.ref', 'child', 'ref', type_purpose='Link',
                  linked_item_id='_parent.id', type_contents='Text',
                  enumeration_default='X'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        ref_col = next(c for c in schema.tables['child'].columns if c.name == 'ref')
        assert ref_col.nullable is True


# ---------------------------------------------------------------------------
# Category parent map edge cases
# ---------------------------------------------------------------------------

class TestCategoryParentMapEdgeCases:
    """Self-reference and parent-not-in-schema produce None in category_parent."""

    def test_self_referential_category_has_none_parent(self):
        # _cat('x', 'x', 'Set') → category_id == definition_id == 'x' → parent_tbl == tbl_name → None
        cats = [_cat('x', 'x', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        assert schema.category_parent.get('x') is None

    def test_parent_not_in_schema_gives_none(self):
        # child's category_id points to a Head category (not in tables) → None
        head_cat = DdlmItem(
            definition_id='headcat', scope='Category', definition_class='Head',
            category_id='headcat', object_id=None, type_purpose=None, type_source=None,
            type_container='Single', type_contents=None, linked_item_id=None,
            units_code=None, description=None, category_keys=[],
        )
        child_cat = DdlmItem(
            definition_id='child', scope='Category', definition_class='Loop',
            category_id='headcat',  # parent is the Head category
            object_id=None, type_purpose=None, type_source=None,
            type_container='Single', type_contents=None, linked_item_id=None,
            units_code=None, description=None, category_keys=[],
        )
        cats_map = {'headcat': head_cat, 'child': child_cat}
        d = DdlmDictionary(
            name='T', title=None, version=None,
            categories=cats_map, items={}, tag_to_item=cats_map,
            alias_to_definition_id={}, deprecated_ids=set(),
        )
        schema = generate_schema(d)
        assert schema.category_parent.get('child') is None


# ---------------------------------------------------------------------------
# tag_to_category_class
# ---------------------------------------------------------------------------

class TestTagToCategoryClass:
    def test_set_item_tagged_as_set(self):
        cats = [_cat('cfg', 'cfg', 'Set', ['_cfg.id'])]
        items = [_item('_cfg.id', 'cfg', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert schema.tag_to_category_class.get('_cfg.id') == 'Set'

    def test_loop_item_tagged_as_loop(self):
        cats = [_cat('meas', 'meas', 'Loop', ['_meas.id'])]
        items = [_item('_meas.id', 'meas', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert schema.tag_to_category_class.get('_meas.id') == 'Loop'

    def test_item_with_head_category_excluded(self):
        # item whose category is Head → not in tag_to_category_class
        head_cat = DdlmItem(
            definition_id='hd', scope='Category', definition_class='Head',
            category_id='hd', object_id=None, type_purpose=None, type_source=None,
            type_container='Single', type_contents=None, linked_item_id=None,
            units_code=None, description=None, category_keys=[],
        )
        head_item = DdlmItem(
            definition_id='_hd.val', scope='Item', definition_class='Datum',
            category_id='hd', object_id='val', type_purpose=None, type_source=None,
            type_container='Single', type_contents='Text', linked_item_id=None,
            units_code=None, description=None,
        )
        cats_map = {'hd': head_cat}
        item_map = {'_hd.val': head_item}
        tag_to_item = {**cats_map, **item_map}
        d = DdlmDictionary(
            name='T', title=None, version=None,
            categories=cats_map, items=item_map, tag_to_item=tag_to_item,
            alias_to_definition_id={}, deprecated_ids=set(),
        )
        schema = generate_schema(d)
        assert '_hd.val' not in schema.tag_to_category_class


# ---------------------------------------------------------------------------
# deprecated_replacements
# ---------------------------------------------------------------------------

class TestDeprecatedReplacements:
    def test_deprecated_item_in_deprecated_replacements(self):
        cats = [_cat('cfg', 'cfg', 'Set', ['_cfg.id'])]
        old_item = DdlmItem(
            definition_id='_cfg.old_name', scope='Item', definition_class='Datum',
            category_id='cfg', object_id='old_name', type_purpose=None, type_source=None,
            type_container='Single', type_contents='Text', linked_item_id=None,
            units_code=None, description=None,
            is_deprecated=True, replaced_by=['_cfg.new_name'],
        )
        cats_d = {c.definition_id: c for c in cats}
        item_map = {'_cfg.old_name': old_item}
        tag_to_item = {**cats_d, **item_map}
        d = DdlmDictionary(
            name='T', title=None, version=None,
            categories=cats_d, items=item_map, tag_to_item=tag_to_item,
            alias_to_definition_id={}, deprecated_ids={'_cfg.old_name'},
        )
        schema = generate_schema(d)
        assert '_cfg.old_name' in schema.deprecated_replacements
        assert schema.deprecated_replacements['_cfg.old_name'] == ['_cfg.new_name']

    def test_non_deprecated_item_absent_from_deprecated_replacements(self):
        cats = [_cat('cfg', 'cfg', 'Set', ['_cfg.id'])]
        items = [_item('_cfg.id', 'cfg', 'id', type_contents='Text')]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        assert '_cfg.id' not in schema.deprecated_replacements


# ---------------------------------------------------------------------------
# SchemaSpec passthrough fields
# ---------------------------------------------------------------------------

class TestSchemaSpecPassthrough:
    def test_alias_to_definition_id_passthrough(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        cats_d = {c.definition_id: c for c in cats}
        d = DdlmDictionary(
            name='T', title=None, version=None,
            categories=cats_d, items={}, tag_to_item=cats_d,
            alias_to_definition_id={'_cfg.old': '_cfg.new'},
            deprecated_ids=set(),
        )
        schema = generate_schema(d)
        assert schema.alias_to_definition_id == {'_cfg.old': '_cfg.new'}

    def test_deprecated_ids_passthrough(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        cats_d = {c.definition_id: c for c in cats}
        d = DdlmDictionary(
            name='T', title=None, version=None,
            categories=cats_d, items={}, tag_to_item=cats_d,
            alias_to_definition_id={},
            deprecated_ids={'_cfg.old_tag'},
        )
        schema = generate_schema(d)
        assert '_cfg.old_tag' in schema.deprecated_ids

    def test_dictionary_metadata_passthrough(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        cats_d = {c.definition_id: c for c in cats}
        d = DdlmDictionary(
            name='MY_DICT', title='My Dictionary', version='2.0',
            categories=cats_d, items={}, tag_to_item=cats_d,
            alias_to_definition_id={}, deprecated_ids=set(),
            uri='https://example.com/mydict.dic',
        )
        schema = generate_schema(d)
        assert schema.dictionary_name == 'MY_DICT'
        assert schema.dictionary_title == 'My Dictionary'
        assert schema.dictionary_version == '2.0'
        assert schema.dictionary_uri == 'https://example.com/mydict.dic'

