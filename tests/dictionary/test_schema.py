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

    def test_row_id_absent_on_set_table(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        col_names = [c.name for c in schema.tables['cfg'].columns]
        assert '_row_id' not in col_names

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
        assert table.primary_keys == ['_block_id']
        block_col = next(c for c in table.columns if c.name == '_block_id')
        assert block_col.is_primary_key is True

    def test_set_without_category_key_emits_warning(self):
        cats = [_cat('series', 'series', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        assert any('series' in w and 'Set' in w for w in schema.warnings)

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

    def test_unsupported_class_skipped_with_warning(self):
        cats = [_cat('funcs', 'funcs', 'Functions')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        assert len(schema.tables) == 0
        assert any('funcs' in w and 'Functions' in w for w in schema.warnings)


# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------

class TestTypeMapping:
    @pytest.mark.parametrize('type_contents,expected', [
        ('Integer', 'INTEGER'),
        ('integer', 'INTEGER'),    # case-insensitive
        ('Real', 'REAL'),
        ('real', 'REAL'),
        ('Text', 'TEXT'),
        ('Word', 'TEXT'),
        ('Code', 'TEXT'),
        ('Imag', 'TEXT'),
        ('Complex', 'TEXT'),
        ('Implied', 'TEXT'),
        ('ByReference', 'TEXT'),
        ('Inherited', 'TEXT'),
        (None, 'TEXT'),
    ])
    def test_sql_type(self, type_contents, expected):
        cats = [_cat('t', 't', 'Set')]
        items = [_item('_t.col', 't', 'col', type_contents=type_contents)]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        col = next(c for c in schema.tables['t'].columns if c.name == 'col')
        assert col.sql_type == expected


# ---------------------------------------------------------------------------
# Column ordering
# ---------------------------------------------------------------------------

class TestColumnOrdering:
    def test_set_column_order(self):
        # Set table: _block_id, then PKs, then alpha non-PKs
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
        assert names[1] == 'id'          # PK
        assert names[2] == 'a_first'     # alpha first
        assert names[3] == 'z_last'

    def test_loop_column_order(self):
        # Loop table: _block_id, _row_id, PKs, alpha non-PKs
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
        assert names[3] == 'a_name'   # alpha
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
        assert fk.source_column == 'config_id'
        assert fk.target_table == 'config'
        assert fk.target_column == 'id'

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
        assert fk.target_column == 'id'

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

    def test_link_where_target_not_in_category_keys_warns_but_still_records(self):
        cats = [
            _cat('src', 'src', 'Loop', ['_src.id']),
            _cat('tgt', 'tgt', 'Loop', ['_tgt.id']),
        ]
        items = [
            _item('_src.id', 'src', 'id', type_purpose='Key', type_contents='Text'),
            _item('_tgt.id', 'tgt', 'id', type_purpose='Key', type_contents='Text'),
            _item('_tgt.extra', 'tgt', 'extra', type_contents='Text'),
            # Links to _tgt.extra which is NOT a category key
            _item('_src.ref', 'src', 'ref', type_purpose='Link',
                  linked_item_id='_tgt.extra', type_contents='Text'),
        ]
        d = _make_dict(cats, items)
        schema = generate_schema(d)
        # FK is still recorded
        assert len(schema.tables['src'].foreign_keys) == 1
        assert schema.tables['src'].foreign_keys[0].target_column == 'extra'
        # Warning emitted
        assert any('_tgt.extra' in w and 'category key' in w for w in schema.warnings)

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

    def test_row_id_unique_in_loop_stmt(self):
        cats = [_cat('meas', 'meas', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        stmt = emit_create_statements(schema)[0]
        assert 'UNIQUE' in stmt
        assert '"_row_id"  INTEGER  NOT NULL  UNIQUE' in stmt

    def test_row_id_absent_from_set_stmt(self):
        cats = [_cat('cfg', 'cfg', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        stmt = emit_create_statements(schema)[0]
        assert '_row_id' not in stmt

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

    def test_row_id_unique_via_pragma(self):
        cats = [_cat('meas', 'meas', 'Loop')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)
        conn = _execute_schema(schema)
        indexes = list(conn.execute("PRAGMA index_list('meas')"))
        unique_indexes = [row for row in indexes if row[2] == 1]  # col 2 is 'unique'
        index_names = [row[1] for row in unique_indexes]
        # Find indexes covering _row_id
        found = False
        for idx_name in index_names:
            cols = [r[2] for r in conn.execute(f"PRAGMA index_info('{idx_name}')")]
            if '_row_id' in cols:
                found = True
                break
        assert found, "_row_id should have a UNIQUE index"

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
