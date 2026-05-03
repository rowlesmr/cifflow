"""
Unit tests for compactify_database().
"""

import sqlite3

import pytest

from cifflow import compactify_database
from cifflow.dictionary.ddlm_item import DdlmItem
from cifflow.dictionary.ddlm_parser import DdlmDictionary
from cifflow.dictionary.schema import generate_schema
from cifflow.dictionary.schema_apply import apply_fallback_schema, apply_schema


# ---------------------------------------------------------------------------
# Helpers — mirrors the pattern from test_ingest.py
# ---------------------------------------------------------------------------

def _item(definition_id, category_id, object_id, *,
          type_purpose=None, type_contents=None, linked_item_id=None):
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
        enumeration_states=[],
        category_keys=[],
        aliases=[],
        replaced_by=[],
        is_deprecated=False,
    )


def _cat(definition_id, cat_class, category_keys=None):
    return DdlmItem(
        definition_id=definition_id,
        scope='Category',
        definition_class=cat_class,
        category_id=None,
        object_id=None,
        type_purpose=None,
        type_source=None,
        type_container='Single',
        type_contents=None,
        linked_item_id=None,
        units_code=None,
        description=None,
        enumeration_states=[],
        category_keys=category_keys or [],
        aliases=[],
        replaced_by=[],
        is_deprecated=False,
    )


def _make_dict(cats, items, alias=None, deprecated=None):
    all_items = {c.definition_id: c for c in cats}
    all_items.update({i.definition_id: i for i in items})
    tag_to_item = {i.definition_id: i for i in items}
    return DdlmDictionary(
        name='TEST',
        title=None,
        version=None,
        categories={c.definition_id: c for c in cats},
        items={i.definition_id: i for i in items},
        tag_to_item=tag_to_item,
        alias_to_definition_id=alias or {},
        deprecated_ids=set(deprecated or []),
        warnings=[],
    )


def _schema_ab():
    """
    Two tables:
      a (Set, PK=id)
      b (Loop, PK=label, non-key FK=a_id -> a.id, col=x)
    """
    cats = [
        _cat('a', 'Set', ['_a.id']),
        _cat('b', 'Loop', ['_b.label']),
    ]
    items = [
        _item('_a.id',    'a', 'id',    type_purpose='Key'),
        _item('_b.label', 'b', 'label', type_purpose='Key'),
        _item('_b.a_id',  'b', 'a_id',  type_purpose='Link', linked_item_id='_a.id'),
        _item('_b.x',     'b', 'x'),
    ]
    return generate_schema(_make_dict(cats, items))


def _populated_src(schema, *, with_b=True):
    """Return a populated :memory: src connection."""
    c = sqlite3.connect(':memory:')
    c.isolation_level = None
    apply_schema(c, schema)
    apply_fallback_schema(c)
    # Disable FK for easy direct insertion
    c.execute('PRAGMA foreign_keys = OFF')
    c.execute('BEGIN')
    c.execute('INSERT INTO "a" ("_cifflow_block_id", "_cifflow_row_id", "id") VALUES (?, ?, ?)',
              ('BLK', 1, 'A1'))
    if with_b:
        c.execute('INSERT INTO "b" ("_cifflow_block_id", "_cifflow_row_id", "label", "a_id", "x") '
                  'VALUES (?, ?, ?, ?, ?)', ('BLK', 1, 'L1', 'A1', '1.0'))
    c.execute('COMMIT')
    return c


def _dst():
    c = sqlite3.connect(':memory:')
    c.isolation_level = None
    return c


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def _cols(conn, tbl):
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{tbl}")').fetchall()]


def _rows(conn, tbl, cols):
    col_list = ', '.join(f'"{c}"' for c in cols)
    return conn.execute(f'SELECT {col_list} FROM "{tbl}"').fetchall()


# ===========================================================================
# TestDropEmptyTable
# ===========================================================================

class TestDropEmptyTable:
    def test_empty_table_not_in_dst(self):
        schema = _schema_ab()
        src = _populated_src(schema, with_b=False)  # 'b' has no rows
        dst = _dst()
        compactify_database(src, dst, schema)
        assert 'b' not in _tables(dst)

    def test_empty_table_reported_in_messages(self):
        schema = _schema_ab()
        src = _populated_src(schema, with_b=False)
        dst = _dst()
        msgs = compactify_database(src, dst, schema)
        assert any("dropped table: 'b'" in m for m in msgs)

    def test_non_empty_table_kept(self):
        schema = _schema_ab()
        src = _populated_src(schema, with_b=True)
        dst = _dst()
        compactify_database(src, dst, schema)
        assert 'b' in _tables(dst)
        assert 'a' in _tables(dst)

    def test_data_copied_correctly(self):
        schema = _schema_ab()
        src = _populated_src(schema, with_b=True)
        dst = _dst()
        compactify_database(src, dst, schema)
        rows = _rows(dst, 'a', ['id'])
        assert rows == [('A1',)]


# ===========================================================================
# TestDropEmptyColumn
# ===========================================================================

class TestDropEmptyColumn:
    def test_all_null_column_dropped(self):
        """Column 'x' in b is all NULL -> dropped."""
        schema = _schema_ab()
        src = _populated_src(schema, with_b=False)
        # Add a b row with x=NULL
        src.execute('PRAGMA foreign_keys = OFF')
        src.execute('BEGIN')
        src.execute('INSERT INTO "b" ("_cifflow_block_id", "_cifflow_row_id", "label", "a_id") '
                    'VALUES (?, ?, ?, ?)', ('BLK', 1, 'L1', 'A1'))
        src.execute('COMMIT')
        dst = _dst()
        compactify_database(src, dst, schema)
        assert 'x' not in _cols(dst, 'b')

    def test_all_null_column_reported_in_messages(self):
        schema = _schema_ab()
        src = _populated_src(schema, with_b=False)
        src.execute('PRAGMA foreign_keys = OFF')
        src.execute('BEGIN')
        src.execute('INSERT INTO "b" ("_cifflow_block_id", "_cifflow_row_id", "label") '
                    'VALUES (?, ?, ?)', ('BLK', 1, 'L1'))
        src.execute('COMMIT')
        dst = _dst()
        msgs = compactify_database(src, dst, schema)
        assert any("dropped column: 'b'.'x'" in m for m in msgs)

    def test_non_null_column_kept(self):
        schema = _schema_ab()
        src = _populated_src(schema, with_b=True)
        dst = _dst()
        compactify_database(src, dst, schema)
        assert 'x' in _cols(dst, 'b')

    def test_pk_column_never_dropped(self):
        """PK column kept even when... well it can't be all-NULL by definition,
        but the code must mark it as undropable."""
        schema = _schema_ab()
        src = _populated_src(schema, with_b=True)
        dst = _dst()
        compactify_database(src, dst, schema)
        assert 'label' in _cols(dst, 'b')

    def test_synthetic_column_never_dropped(self):
        schema = _schema_ab()
        src = _populated_src(schema, with_b=True)
        dst = _dst()
        compactify_database(src, dst, schema)
        assert '_cifflow_block_id' in _cols(dst, 'b')
        assert '_cifflow_row_id' in _cols(dst, 'b')


# ===========================================================================
# TestFKConstraints
# ===========================================================================

class TestFKConstraints:
    def test_fk_preserved_when_both_tables_kept(self):
        schema = _schema_ab()
        src = _populated_src(schema, with_b=True)
        dst = _dst()
        compactify_database(src, dst, schema)
        fks = dst.execute('PRAGMA foreign_key_list("b")').fetchall()
        # Should have at least one FK (b.a_id -> a.id)
        fk_targets = [(r[2], r[3]) for r in fks]  # (target_table, from_col)
        assert ('a', 'a_id') in fk_targets

    def test_fk_omitted_when_target_table_dropped(self):
        """If 'a' has no rows, it's dropped; b's FK to a must be omitted."""
        schema = _schema_ab()
        src = sqlite3.connect(':memory:')
        src.isolation_level = None
        apply_schema(src, schema)
        apply_fallback_schema(src)
        src.execute('PRAGMA foreign_keys = OFF')
        src.execute('BEGIN')
        # Only insert into b, leave a empty
        src.execute('INSERT INTO "b" ("_cifflow_block_id", "_cifflow_row_id", "label") '
                    'VALUES (?, ?, ?)', ('BLK', 1, 'L1'))
        src.execute('COMMIT')

        dst = _dst()
        compactify_database(src, dst, schema)

        assert 'a' not in _tables(dst)
        fks = dst.execute('PRAGMA foreign_key_list("b")').fetchall()
        fk_targets = [r[2] for r in fks]  # target table names
        assert 'a' not in fk_targets

    def test_dst_fk_enforcement_works(self):
        """FK enforcement should be ON in dst and not violated after compact."""
        schema = _schema_ab()
        src = _populated_src(schema, with_b=True)
        dst = _dst()
        compactify_database(src, dst, schema)

        fk_status = dst.execute('PRAGMA foreign_keys').fetchone()[0]
        assert fk_status == 1

        # Attempt to insert a b row with non-existent a_id — must fail
        with pytest.raises(sqlite3.IntegrityError):
            dst.execute(
                'INSERT INTO "b" ("_cifflow_block_id", "_cifflow_row_id", "label", "a_id") '
                'VALUES (?, ?, ?, ?)', ('X', 99, 'BAD', 'NONEXISTENT')
            )


# ===========================================================================
# TestFallbackTables
# ===========================================================================

class TestFallbackTables:
    def test_fallback_tables_always_present(self):
        schema = _schema_ab()
        # src with nothing in structured tables
        src = sqlite3.connect(':memory:')
        src.isolation_level = None
        apply_schema(src, schema)
        apply_fallback_schema(src)
        dst = _dst()
        compactify_database(src, dst, schema)

        tables = _tables(dst)
        assert '_cif_fallback' in tables
        assert '_block_dataset_membership' in tables
        assert '_validation_result' in tables

    def test_fallback_data_copied(self):
        schema = _schema_ab()
        src = sqlite3.connect(':memory:')
        src.isolation_level = None
        apply_schema(src, schema)
        apply_fallback_schema(src)
        src.execute(
            'INSERT INTO "_cif_fallback" '
            '("_cifflow_block_id","_cifflow_row_id","tag","value","value_type","loop_id","col_index") '
            'VALUES (?,?,?,?,?,?,?)',
            ('BLK', 1, '_unknown.x', '42', 'string', None, None)
        )

        dst = _dst()
        compactify_database(src, dst, schema)

        rows = dst.execute(
            'SELECT tag, value FROM "_cif_fallback"'
        ).fetchall()
        assert rows == [('_unknown.x', '42')]

    def test_fallback_tables_present_even_when_structured_tables_empty(self):
        """Fallback tables survive even when all structured tables are dropped."""
        schema = _schema_ab()
        src = sqlite3.connect(':memory:')
        src.isolation_level = None
        apply_schema(src, schema)
        apply_fallback_schema(src)
        dst = _dst()
        msgs = compactify_database(src, dst, schema)

        assert '_cif_fallback' in _tables(dst)
        # Both structured tables empty
        assert any("dropped table: 'a'" in m for m in msgs)
        assert any("dropped table: 'b'" in m for m in msgs)


# ===========================================================================
# TestReturnMessages
# ===========================================================================

class TestReturnMessages:
    def test_returns_list(self):
        schema = _schema_ab()
        src = _populated_src(schema, with_b=True)
        dst = _dst()
        result = compactify_database(src, dst, schema)
        assert isinstance(result, list)

    def test_no_messages_when_fully_populated(self):
        schema = _schema_ab()
        src = _populated_src(schema, with_b=True)
        dst = _dst()
        msgs = compactify_database(src, dst, schema)
        assert msgs == []

    def test_messages_include_dropped_items(self):
        schema = _schema_ab()
        src = _populated_src(schema, with_b=False)
        dst = _dst()
        msgs = compactify_database(src, dst, schema)
        assert any('dropped table' in m for m in msgs)
