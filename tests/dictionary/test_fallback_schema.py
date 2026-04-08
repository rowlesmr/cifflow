"""
Tests for emit_fallback_create_statements and apply_fallback_schema.
"""

import sqlite3

import pytest

from pycifparse.dictionary.schema import emit_fallback_create_statements
from pycifparse.dictionary.schema_apply import apply_fallback_schema


# ---------------------------------------------------------------------------
# emit_fallback_create_statements
# ---------------------------------------------------------------------------

class TestEmitFallbackCreateStatements:
    def test_returns_two_statements(self):
        stmts = emit_fallback_create_statements()
        assert len(stmts) == 2

    def test_first_is_create_table(self):
        stmts = emit_fallback_create_statements()
        assert stmts[0].startswith('CREATE TABLE IF NOT EXISTS')
        assert '_cif_fallback' in stmts[0]

    def test_second_is_create_index(self):
        stmts = emit_fallback_create_statements()
        assert stmts[1].startswith('CREATE INDEX IF NOT EXISTS')
        assert 'idx_cif_fallback_tag_block' in stmts[1]

    def test_index_covers_tag_and_block_id(self):
        stmts = emit_fallback_create_statements()
        assert '"tag"' in stmts[1]
        assert '"_block_id"' in stmts[1]


# ---------------------------------------------------------------------------
# apply_fallback_schema — table structure
# ---------------------------------------------------------------------------

class TestFallbackTableStructure:
    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(':memory:')
        apply_fallback_schema(c)
        return c

    def test_table_created(self, conn):
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert '_cif_fallback' in names

    def test_index_created(self, conn):
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert 'idx_cif_fallback_tag_block' in names

    def test_expected_columns_present(self, conn):
        pragma = {row[1]: row for row in conn.execute('PRAGMA table_info("_cif_fallback")')}
        for col in ('_block_id', '_row_id', 'tag', 'value', 'value_type', 'loop_id', 'col_index'):
            assert col in pragma, f"column {col!r} missing from _cif_fallback"

    def test_block_id_not_null(self, conn):
        pragma = {row[1]: row for row in conn.execute('PRAGMA table_info("_cif_fallback")')}
        assert pragma['_block_id'][3] == 1  # notnull

    def test_row_id_not_null(self, conn):
        pragma = {row[1]: row for row in conn.execute('PRAGMA table_info("_cif_fallback")')}
        assert pragma['_row_id'][3] == 1  # notnull

    def test_tag_not_null(self, conn):
        pragma = {row[1]: row for row in conn.execute('PRAGMA table_info("_cif_fallback")')}
        assert pragma['tag'][3] == 1  # notnull

    def test_value_type_not_null(self, conn):
        pragma = {row[1]: row for row in conn.execute('PRAGMA table_info("_cif_fallback")')}
        assert pragma['value_type'][3] == 1  # notnull

    def test_value_nullable(self, conn):
        pragma = {row[1]: row for row in conn.execute('PRAGMA table_info("_cif_fallback")')}
        assert pragma['value'][3] == 0  # nullable

    def test_loop_id_nullable(self, conn):
        pragma = {row[1]: row for row in conn.execute('PRAGMA table_info("_cif_fallback")')}
        assert pragma['loop_id'][3] == 0  # nullable

    def test_col_index_nullable(self, conn):
        pragma = {row[1]: row for row in conn.execute('PRAGMA table_info("_cif_fallback")')}
        assert pragma['col_index'][3] == 0  # nullable

    def test_primary_key_is_block_id_row_id_tag(self, conn):
        pk_cols = {
            row[1]
            for row in conn.execute('PRAGMA table_info("_cif_fallback")')
            if row[5] > 0  # pk flag
        }
        assert pk_cols == {'_block_id', '_row_id', 'tag'}


# ---------------------------------------------------------------------------
# apply_fallback_schema — behaviour
# ---------------------------------------------------------------------------

class TestApplyFallbackSchemaBehaviour:
    def test_idempotent_via_if_not_exists(self):
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        apply_fallback_schema(conn)  # must not raise

    def test_accepts_scalar_row(self):
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute(
            'INSERT INTO "_cif_fallback" (_block_id, _row_id, tag, value, value_type) '
            "VALUES ('blk1', 1, '_some.tag', 'hello', 'string')"
        )
        conn.commit()
        rows = list(conn.execute('SELECT * FROM "_cif_fallback"'))
        assert len(rows) == 1

    def test_accepts_null_loop_id_and_col_index(self):
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute(
            'INSERT INTO "_cif_fallback" '
            '(_block_id, _row_id, tag, value, value_type, loop_id, col_index) '
            "VALUES ('blk1', 1, '_some.tag', 'hello', 'string', NULL, NULL)"
        )
        conn.commit()
        row = conn.execute('SELECT loop_id, col_index FROM "_cif_fallback"').fetchone()
        assert row == (None, None)

    def test_same_tag_different_blocks_allowed(self):
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute(
            'INSERT INTO "_cif_fallback" (_block_id, _row_id, tag, value, value_type) '
            "VALUES ('blk1', 1, '_some.tag', 'a', 'string')"
        )
        conn.execute(
            'INSERT INTO "_cif_fallback" (_block_id, _row_id, tag, value, value_type) '
            "VALUES ('blk2', 1, '_some.tag', 'b', 'string')"
        )
        conn.commit()
        rows = list(conn.execute('SELECT * FROM "_cif_fallback"'))
        assert len(rows) == 2

    def test_drop_existing_clears_data(self):
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute(
            'INSERT INTO "_cif_fallback" (_block_id, _row_id, tag, value, value_type) '
            "VALUES ('blk1', 1, '_some.tag', 'hello', 'string')"
        )
        conn.commit()
        apply_fallback_schema(conn, drop_existing=True)
        rows = list(conn.execute('SELECT * FROM "_cif_fallback"'))
        assert rows == []

    def test_drop_existing_false_preserves_data(self):
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute(
            'INSERT INTO "_cif_fallback" (_block_id, _row_id, tag, value, value_type) '
            "VALUES ('blk1', 1, '_some.tag', 'hello', 'string')"
        )
        conn.commit()
        apply_fallback_schema(conn, drop_existing=False)
        rows = list(conn.execute('SELECT * FROM "_cif_fallback"'))
        assert len(rows) == 1

    def test_coexists_with_structured_schema(self):
        from pycifparse.dictionary.ddlm_item import DdlmItem
        from pycifparse.dictionary.ddlm_parser import DdlmDictionary
        from pycifparse.dictionary.schema import generate_schema
        from pycifparse.dictionary.schema_apply import apply_schema

        cat = DdlmItem(
            definition_id='atom_site', scope='Category', definition_class='Loop',
            category_id=None, object_id=None, type_purpose=None, type_source=None,
            type_container='Single', type_contents=None, linked_item_id=None,
            units_code=None, description=None,
            category_keys=['_atom_site.id'],
        )
        item = DdlmItem(
            definition_id='_atom_site.id', scope='Item', definition_class='Datum',
            category_id='atom_site', object_id='id', type_purpose='Key',
            type_source=None, type_container='Single', type_contents='Text',
            linked_item_id=None, units_code=None, description=None,
        )
        d = DdlmDictionary(
            name='TEST', title=None, version=None,
            categories={'atom_site': cat},
            items={'_atom_site.id': item},
            tag_to_item={'atom_site': cat, '_atom_site.id': item},
            alias_to_definition_id={}, deprecated_ids=set(),
        )
        schema = generate_schema(d)
        conn = sqlite3.connect(':memory:')
        apply_schema(conn, schema)
        apply_fallback_schema(conn)

        table_names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert 'atom_site' in table_names
        assert '_cif_fallback' in table_names
