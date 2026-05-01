"""
Tests for emit_fallback_create_statements and fallback schema structure.
"""

import duckdb
import pytest

from pycifparse.dictionary.schema import emit_fallback_create_statements


def _fallback_conn():
    """Create a DuckDB connection with the fallback schema applied."""
    c = duckdb.connect()
    for stmt in emit_fallback_create_statements():
        c.execute(stmt)
    return c


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

    def test_index_covers_tag_and_block_id(self):
        stmts = emit_fallback_create_statements()
        assert '"tag"' in stmts[1]
        assert '"_block_id"' in stmts[1]

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
# Fallback schema structure — verified via DuckDB information_schema
# ---------------------------------------------------------------------------

class TestFallbackTableStructure:
    @pytest.fixture
    def conn(self):
        c = _fallback_conn()
        yield c
        c.close()

    def _tables(self, conn):
        return {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()
        }

    def _columns(self, conn, table):
        return {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name=?",
                [table],
            ).fetchall()
        }

    def test_table_created(self, conn):
        names = self._tables(conn)
        assert '_cif_fallback' in names
        assert '_block_dataset_membership' in names
        assert '_validation_result' in names

    def test_expected_columns_present(self, conn):
        cols = self._columns(conn, '_cif_fallback')
        for col in ('_block_id', '_row_id', 'tag', 'value', 'value_type', 'loop_id', 'col_index', 'ref_table'):
            assert col in cols, f"column {col!r} missing from _cif_fallback"

    def test_membership_columns_present(self, conn):
        cols = self._columns(conn, '_block_dataset_membership')
        for col in ('_block_id', '_audit_dataset_id', 'id_regime'):
            assert col in cols, f"column {col!r} missing from _block_dataset_membership"

    def test_validation_columns_present(self, conn):
        cols = self._columns(conn, '_validation_result')
        for col in ('check_name', 'severity', 'block_id', 'detail', 'id_regime'):
            assert col in cols, f"column {col!r} missing from _validation_result"

    def test_idempotent_via_if_not_exists(self, conn):
        for stmt in emit_fallback_create_statements():
            conn.execute(stmt)  # second application must not raise

    def test_accepts_scalar_row(self, conn):
        conn.execute(
            'INSERT INTO "_cif_fallback" (_block_id, _row_id, tag, value, value_type) '
            "VALUES ('blk1', 1, '_some.tag', 'hello', 'string')"
        )
        rows = conn.execute('SELECT * FROM "_cif_fallback"').fetchall()
        assert len(rows) == 1

    def test_accepts_null_loop_id_and_col_index(self, conn):
        conn.execute(
            'INSERT INTO "_cif_fallback" '
            '(_block_id, _row_id, tag, value, value_type, loop_id, col_index) '
            "VALUES ('blk1', 1, '_some.tag', 'hello', 'string', NULL, NULL)"
        )
        row = conn.execute('SELECT loop_id, col_index FROM "_cif_fallback"').fetchone()
        assert row == (None, None)

    def test_accepts_null_ref_table(self, conn):
        conn.execute(
            'INSERT INTO "_cif_fallback" '
            '(_block_id, _row_id, tag, value, value_type, loop_id, col_index, ref_table) '
            "VALUES ('blk1', 1, '_some.tag', 'hello', 'string', NULL, NULL, NULL)"
        )
        row = conn.execute('SELECT ref_table FROM "_cif_fallback"').fetchone()
        assert row == (None,)

    def test_same_tag_different_blocks_allowed(self, conn):
        conn.execute(
            'INSERT INTO "_cif_fallback" (_block_id, _row_id, tag, value, value_type) '
            "VALUES ('blk1', 1, '_some.tag', 'a', 'string')"
        )
        conn.execute(
            'INSERT INTO "_cif_fallback" (_block_id, _row_id, tag, value, value_type) '
            "VALUES ('blk2', 1, '_some.tag', 'b', 'string')"
        )
        rows = conn.execute('SELECT * FROM "_cif_fallback"').fetchall()
        assert len(rows) == 2

    def test_coexists_with_structured_table(self, conn):
        conn.execute('CREATE TABLE "atom_site" ("id" VARCHAR PRIMARY KEY, "_block_id" VARCHAR)')
        tables = self._tables(conn)
        assert 'atom_site' in tables
        assert '_cif_fallback' in tables
        assert '_block_dataset_membership' in tables
        assert '_validation_result' in tables
