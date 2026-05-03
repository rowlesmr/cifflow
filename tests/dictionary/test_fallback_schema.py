"""
Tests for emit_fallback_create_statements and apply_fallback_schema.
"""

import sqlite3

import pytest

from cifflow.dictionary.schema import emit_fallback_create_statements
from cifflow.dictionary.schema_apply import apply_fallback_schema


# ---------------------------------------------------------------------------
# emit_fallback_create_statements
# ---------------------------------------------------------------------------

class TestEmitFallbackCreateStatements:
    def test_statement_count(self):
        stmts = emit_fallback_create_statements()
        assert len(stmts) == 6

    def test_first_is_create_table(self):
        stmts = emit_fallback_create_statements()
        assert stmts[0].startswith('CREATE TABLE IF NOT EXISTS')
        assert '_cif_fallback' in stmts[0]

    def test_second_is_create_index(self):
        stmts = emit_fallback_create_statements()
        assert stmts[1].startswith('CREATE INDEX IF NOT EXISTS')
        assert 'idx_cif_fallback_tag_block' in stmts[1]

    def test_index_covers_tag_and_cifflow_block_id(self):
        stmts = emit_fallback_create_statements()
        assert '"tag"' in stmts[1]
        assert '"_cifflow_block_id"' in stmts[1]

    def test_third_is_membership_table(self):
        stmts = emit_fallback_create_statements()
        assert 'CREATE TABLE IF NOT EXISTS' in stmts[2]
        assert '_block_dataset_membership' in stmts[2]

    def test_fourth_is_validation_result_table(self):
        stmts = emit_fallback_create_statements()
        assert 'CREATE TABLE IF NOT EXISTS' in stmts[3]
        assert '_validation_result' in stmts[3]

    def test_fifth_is_block_order_table(self):
        stmts = emit_fallback_create_statements()
        assert 'CREATE TABLE IF NOT EXISTS' in stmts[4]
        assert '_block_order' in stmts[4]

    def test_sixth_is_tag_presence_table(self):
        stmts = emit_fallback_create_statements()
        assert 'CREATE TABLE IF NOT EXISTS' in stmts[5]
        assert '_tag_presence' in stmts[5]


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
        assert '_block_dataset_membership' in names
        assert '_validation_result' in names

    def test_index_created(self, conn):
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert 'idx_cif_fallback_tag_block' in names

    def test_expected_columns_present(self, conn):
        pragma = {row[1]: row for row in conn.execute('PRAGMA table_info("_cif_fallback")')}
        for col in ('_cifflow_block_id', '_cifflow_row_id', 'tag', 'value', 'value_type', 'loop_id', 'col_index', 'ref_table'):
            assert col in pragma, f"column {col!r} missing from _cif_fallback"

    def test_cifflow_block_id_not_null(self, conn):
        pragma = {row[1]: row for row in conn.execute('PRAGMA table_info("_cif_fallback")')}
        assert pragma['_cifflow_block_id'][3] == 1  # notnull

    def test_cifflow_row_id_not_null(self, conn):
        pragma = {row[1]: row for row in conn.execute('PRAGMA table_info("_cif_fallback")')}
        assert pragma['_cifflow_row_id'][3] == 1  # notnull

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

    def test_ref_table_nullable(self, conn):
        pragma = {row[1]: row for row in conn.execute('PRAGMA table_info("_cif_fallback")')}
        assert pragma['ref_table'][3] == 0  # nullable

    def test_primary_key_is_cifflow_block_id_cifflow_row_id_tag(self, conn):
        pk_cols = {
            row[1]
            for row in conn.execute('PRAGMA table_info("_cif_fallback")')
            if row[5] > 0  # pk flag
        }
        assert pk_cols == {'_cifflow_block_id', '_cifflow_row_id', 'tag'}


class TestBlockDatasetMembershipTableStructure:
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
        assert '_block_dataset_membership' in names

    def test_expected_columns_present(self, conn):
        pragma = {
            row[1]: row
            for row in conn.execute('PRAGMA table_info("_block_dataset_membership")')
        }
        for col in ('_cifflow_block_id', '_audit_dataset_id', 'id_regime'):
            assert col in pragma, f"column {col!r} missing from _block_dataset_membership"

    def test_all_columns_not_null(self, conn):
        pragma = {
            row[1]: row
            for row in conn.execute('PRAGMA table_info("_block_dataset_membership")')
        }
        for col in ('_cifflow_block_id', '_audit_dataset_id', 'id_regime'):
            assert pragma[col][3] == 1, f"column {col!r} should be NOT NULL"

    def test_primary_key_is_cifflow_block_id_and_dataset_id(self, conn):
        pk_cols = {
            row[1]
            for row in conn.execute('PRAGMA table_info("_block_dataset_membership")')
            if row[5] > 0
        }
        assert pk_cols == {'_cifflow_block_id', '_audit_dataset_id'}


class TestValidationResultTableStructure:
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
        assert '_validation_result' in names

    def test_expected_columns_present(self, conn):
        pragma = {
            row[1]: row
            for row in conn.execute('PRAGMA table_info("_validation_result")')
        }
        for col in ('check_name', 'severity', 'block_id', 'detail', 'id_regime'):
            assert col in pragma, f"column {col!r} missing from _validation_result"

    def test_check_name_not_null(self, conn):
        pragma = {
            row[1]: row
            for row in conn.execute('PRAGMA table_info("_validation_result")')
        }
        assert pragma['check_name'][3] == 1

    def test_severity_not_null(self, conn):
        pragma = {
            row[1]: row
            for row in conn.execute('PRAGMA table_info("_validation_result")')
        }
        assert pragma['severity'][3] == 1

    def test_cifflow_block_id_nullable(self, conn):
        pragma = {
            row[1]: row
            for row in conn.execute('PRAGMA table_info("_validation_result")')
        }
        assert pragma['block_id'][3] == 0

    def test_detail_nullable(self, conn):
        pragma = {
            row[1]: row
            for row in conn.execute('PRAGMA table_info("_validation_result")')
        }
        assert pragma['detail'][3] == 0

    def test_id_regime_nullable(self, conn):
        pragma = {
            row[1]: row
            for row in conn.execute('PRAGMA table_info("_validation_result")')
        }
        assert pragma['id_regime'][3] == 0

    def test_no_domain_primary_key(self, conn):
        # _validation_result is a rowid table — no column-level PK
        pk_cols = {
            row[1]
            for row in conn.execute('PRAGMA table_info("_validation_result")')
            if row[5] > 0
        }
        assert pk_cols == set()


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
            'INSERT INTO "_cif_fallback" (_cifflow_block_id, _cifflow_row_id, tag, value, value_type) '
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
            '(_cifflow_block_id, _cifflow_row_id, tag, value, value_type, loop_id, col_index) '
            "VALUES ('blk1', 1, '_some.tag', 'hello', 'string', NULL, NULL)"
        )
        conn.commit()
        row = conn.execute('SELECT loop_id, col_index FROM "_cif_fallback"').fetchone()
        assert row == (None, None)

    def test_accepts_null_ref_table(self):
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute(
            'INSERT INTO "_cif_fallback" '
            '(_cifflow_block_id, _cifflow_row_id, tag, value, value_type, loop_id, col_index, ref_table) '
            "VALUES ('blk1', 1, '_some.tag', 'hello', 'string', NULL, NULL, NULL)"
        )
        conn.commit()
        row = conn.execute('SELECT ref_table FROM "_cif_fallback"').fetchone()
        assert row == (None,)

    def test_accepts_non_null_ref_table(self):
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute(
            'INSERT INTO "_cif_fallback" '
            '(_cifflow_block_id, _cifflow_row_id, tag, value, value_type, loop_id, col_index, ref_table) '
            "VALUES ('blk1', 1, '_some.tag', 'hello', 'string', 0, 0, 'pd_data')"
        )
        conn.commit()
        row = conn.execute('SELECT ref_table FROM "_cif_fallback"').fetchone()
        assert row == ('pd_data',)

    def test_same_tag_different_blocks_allowed(self):
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute(
            'INSERT INTO "_cif_fallback" (_cifflow_block_id, _cifflow_row_id, tag, value, value_type) '
            "VALUES ('blk1', 1, '_some.tag', 'a', 'string')"
        )
        conn.execute(
            'INSERT INTO "_cif_fallback" (_cifflow_block_id, _cifflow_row_id, tag, value, value_type) '
            "VALUES ('blk2', 1, '_some.tag', 'b', 'string')"
        )
        conn.commit()
        rows = list(conn.execute('SELECT * FROM "_cif_fallback"'))
        assert len(rows) == 2

    def test_drop_existing_clears_data(self):
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute(
            'INSERT INTO "_cif_fallback" (_cifflow_block_id, _cifflow_row_id, tag, value, value_type) '
            "VALUES ('blk1', 1, '_some.tag', 'hello', 'string')"
        )
        conn.execute(
            'INSERT INTO "_block_dataset_membership" (_cifflow_block_id, _audit_dataset_id, id_regime) '
            "VALUES ('blk1', 'ds1', 'dataset')"
        )
        conn.commit()
        apply_fallback_schema(conn, drop_existing=True)
        assert list(conn.execute('SELECT * FROM "_cif_fallback"')) == []
        assert list(conn.execute('SELECT * FROM "_block_dataset_membership"')) == []

    def test_drop_existing_false_preserves_data(self):
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute(
            'INSERT INTO "_cif_fallback" (_cifflow_block_id, _cifflow_row_id, tag, value, value_type) '
            "VALUES ('blk1', 1, '_some.tag', 'hello', 'string')"
        )
        conn.commit()
        apply_fallback_schema(conn, drop_existing=False)
        rows = list(conn.execute('SELECT * FROM "_cif_fallback"'))
        assert len(rows) == 1

    def test_rollback_when_view_conflicts_with_table_name(self):
        """apply_fallback_schema rolls back and re-raises when a VIEW named
        _cif_fallback already exists (IF NOT EXISTS only suppresses for tables,
        not views). Exercises the except sqlite3.Error path (lines 138-140)."""
        conn = sqlite3.connect(':memory:')
        conn.execute('CREATE VIEW "_cif_fallback" AS SELECT 1')
        with pytest.raises(sqlite3.Error):
            apply_fallback_schema(conn)
        # Verify no _cif_fallback TABLE was created (the view still exists)
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert '_cif_fallback' not in names
        conn.close()

    def test_coexists_with_structured_schema(self):
        from cifflow.dictionary.ddlm_item import DdlmItem
        from cifflow.dictionary.ddlm_parser import DdlmDictionary
        from cifflow.dictionary.schema import generate_schema
        from cifflow.dictionary.schema_apply import apply_schema

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
        assert '_block_dataset_membership' in table_names
        assert '_validation_result' in table_names
