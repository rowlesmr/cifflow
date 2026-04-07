"""Tests for schema_apply.py — apply_schema."""

import sqlite3

import pytest

from pycifparse.dictionary.ddlm_item import DdlmItem
from pycifparse.dictionary.ddlm_parser import DdlmDictionary
from pycifparse.dictionary.schema import generate_schema
from pycifparse.dictionary.schema_apply import apply_schema


# ---------------------------------------------------------------------------
# Helpers (duplicated from test_schema for isolation)
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


def _cat(definition_id, category_id, cat_class, category_keys=None):
    return DdlmItem(
        definition_id=definition_id, scope='Category',
        definition_class=cat_class, category_id=category_id, object_id=None,
        type_purpose=None, type_source=None, type_container='Single',
        type_contents=None, linked_item_id=None, units_code=None,
        description=None, category_keys=category_keys or [],
    )


def _make_dict(cats, items):
    categories = {c.definition_id: c for c in cats}
    item_map = {i.definition_id: i for i in items}
    tag_to_item = {**categories, **item_map}
    return DdlmDictionary(
        name='TEST', title=None, version=None,
        categories=categories, items=item_map, tag_to_item=tag_to_item,
        alias_to_definition_id={}, deprecated_ids=set(),
    )


def _simple_schema():
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
    return generate_schema(_make_dict(cats, items))


# ---------------------------------------------------------------------------
# Pragma verification
# ---------------------------------------------------------------------------

class TestPragmas:
    def test_foreign_keys_enabled(self):
        conn = sqlite3.connect(':memory:')
        schema = _simple_schema()
        apply_schema(conn, schema)
        row = conn.execute('PRAGMA foreign_keys').fetchone()
        assert row[0] == 1

    def test_wal_mode_enabled(self):
        # WAL mode is not supported for in-memory databases; SQLite silently
        # keeps memory mode.  We verify the pragma is accepted without error.
        conn = sqlite3.connect(':memory:')
        schema = _simple_schema()
        apply_schema(conn, schema)  # must not raise

    def test_wal_on_file_db(self, tmp_path):
        db_path = tmp_path / 'test.db'
        conn = sqlite3.connect(str(db_path))
        schema = _simple_schema()
        apply_schema(conn, schema)
        row = conn.execute('PRAGMA journal_mode').fetchone()
        assert row[0] == 'wal'
        conn.close()


# ---------------------------------------------------------------------------
# Tables created
# ---------------------------------------------------------------------------

class TestTablesCreated:
    def test_all_tables_present(self):
        conn = sqlite3.connect(':memory:')
        schema = _simple_schema()
        apply_schema(conn, schema)
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert 'config' in names
        assert 'meas' in names

    def test_idempotent_via_if_not_exists(self):
        conn = sqlite3.connect(':memory:')
        schema = _simple_schema()
        apply_schema(conn, schema)
        apply_schema(conn, schema)   # second call must not raise


# ---------------------------------------------------------------------------
# FK constraints registered
# ---------------------------------------------------------------------------

class TestForeignKeys:
    def test_fk_registered_via_pragma(self):
        conn = sqlite3.connect(':memory:')
        schema = _simple_schema()
        apply_schema(conn, schema)
        fk_list = list(conn.execute("PRAGMA foreign_key_list('meas')"))
        assert len(fk_list) >= 1
        fk = fk_list[0]
        assert fk[2] == 'config'
        assert fk[3] == 'config_id'
        assert fk[4] == 'id'


# ---------------------------------------------------------------------------
# drop_existing
# ---------------------------------------------------------------------------

class TestDropExisting:
    def test_drop_existing_recreates_tables(self):
        conn = sqlite3.connect(':memory:')
        schema = _simple_schema()
        apply_schema(conn, schema)
        # Insert a row so we can confirm the table is dropped and recreated.
        conn.execute(
            "INSERT INTO config (_block_id, id) VALUES ('b1', 'x1')"
        )
        conn.commit()
        apply_schema(conn, schema, drop_existing=True)
        rows = list(conn.execute("SELECT * FROM config"))
        assert rows == []

    def test_drop_existing_false_preserves_data(self):
        conn = sqlite3.connect(':memory:')
        schema = _simple_schema()
        apply_schema(conn, schema)
        conn.execute(
            "INSERT INTO config (_block_id, id) VALUES ('b1', 'x1')"
        )
        conn.commit()
        apply_schema(conn, schema, drop_existing=False)
        rows = list(conn.execute("SELECT * FROM config"))
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Rollback on failure
# ---------------------------------------------------------------------------

class TestRollback:
    def test_rollback_on_bad_sql(self):
        conn = sqlite3.connect(':memory:')
        cats = [_cat('good', 'good', 'Set')]
        d = _make_dict(cats, [])
        schema = generate_schema(d)

        # Monkey-patch emit_create_statements to inject a bad statement.
        import pycifparse.dictionary.schema_apply as sa_mod
        original = sa_mod.emit_create_statements

        def _bad_emit(s):
            return ['CREATE TABLE good (_block_id TEXT NOT NULL, PRIMARY KEY (_block_id))',
                    'THIS IS NOT VALID SQL']

        sa_mod.emit_create_statements = _bad_emit
        try:
            with pytest.raises(sqlite3.Error):
                apply_schema(conn, schema)
        finally:
            sa_mod.emit_create_statements = original

        # 'good' table must not exist after rollback
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert 'good' not in names
