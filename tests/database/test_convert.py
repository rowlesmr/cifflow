"""
Unit tests for convert_database().
"""

from __future__ import annotations

import sqlite3

import pytest

from cifflow import convert_database
from cifflow.dictionary.ddlm_item import DdlmItem
from cifflow.dictionary.ddlm_parser import DdlmDictionary
from cifflow.dictionary.schema import generate_schema
from cifflow.dictionary.schema_apply import apply_fallback_schema, apply_schema


# ---------------------------------------------------------------------------
# Helpers — mirrors test_compact.py
# ---------------------------------------------------------------------------

def _item(definition_id, category_id, object_id, *,
          type_purpose=None, type_contents=None, type_container='Single',
          linked_item_id=None):
    return DdlmItem(
        definition_id=definition_id,
        scope='Item',
        definition_class='Datum',
        category_id=category_id,
        object_id=object_id,
        type_purpose=type_purpose,
        type_source=None,
        type_container=type_container,
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


def _make_dict(cats, items):
    return DdlmDictionary(
        name='TEST',
        title=None,
        version=None,
        categories={c.definition_id: c for c in cats},
        items={i.definition_id: i for i in items},
        tag_to_item={i.definition_id: i for i in items},
        alias_to_definition_id={},
        deprecated_ids=set(),
        warnings=[],
    )


def _schema_typed():
    """
    One Loop table 'vals' with columns:
      label (Key / Code / TEXT)
      count (Integer / Single)
      length (Real / Single)
      name (Text / Single)
    """
    cats = [_cat('vals', 'Loop', ['_vals.label'])]
    items = [
        _item('_vals.label',  'vals', 'label',  type_purpose='Key',
              type_contents='Code'),
        _item('_vals.count',  'vals', 'count',  type_contents='Integer'),
        _item('_vals.length', 'vals', 'length', type_contents='Real'),
        _item('_vals.name',   'vals', 'name',   type_contents='Text'),
    ]
    return generate_schema(_make_dict(cats, items))


def _schema_matrix():
    """
    One Loop table 'mat' with columns:
      label (Key / Code / TEXT)
      hkl   (Integer / Matrix — stored as JSON list)
      xyz   (Real / List — stored as JSON list)
    """
    cats = [_cat('mat', 'Loop', ['_mat.label'])]
    items = [
        _item('_mat.label', 'mat', 'label', type_purpose='Key',
              type_contents='Code'),
        _item('_mat.hkl',   'mat', 'hkl',   type_contents='Integer',
              type_container='Matrix'),
        _item('_mat.xyz',   'mat', 'xyz',   type_contents='Real',
              type_container='List'),
    ]
    return generate_schema(_make_dict(cats, items))


def _src(schema, rows):
    """Return a populated :memory: src connection with given TEXT rows."""
    c = sqlite3.connect(':memory:')
    c.isolation_level = None
    apply_schema(c, schema)
    apply_fallback_schema(c)
    c.execute('PRAGMA foreign_keys = OFF')
    c.execute('BEGIN')
    for blk, row_id, label, count, length, name in rows:
        c.execute(
            'INSERT INTO "vals" ("_cifflow_block_id","_cifflow_row_id","label","count","length","name") '
            'VALUES (?,?,?,?,?,?)',
            (blk, row_id, label, count, length, name),
        )
    c.execute('COMMIT')
    return c


def _dst():
    c = sqlite3.connect(':memory:')
    c.isolation_level = None
    return c


def _fetch(conn, tbl, cols):
    col_sql = ', '.join(f'"{c}"' for c in cols)
    return conn.execute(f'SELECT {col_sql} FROM "{tbl}" ORDER BY "_cifflow_row_id"').fetchall()


def _col_type(conn, tbl, col):
    """Return the declared type of *col* in *tbl* from sqlite_master DDL."""
    rows = conn.execute(f'PRAGMA table_info("{tbl}")').fetchall()
    for row in rows:
        if row[1] == col:
            return row[2]
    return None


# ===========================================================================
# TestColumnTypes — DDL uses correct affinities
# ===========================================================================

class TestColumnTypes:
    def test_integer_column_declared_integer(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', '5', '1.0', 'hello')])
        dst = _dst()
        convert_database(src, dst, schema)
        assert _col_type(dst, 'vals', 'count') == 'INTEGER'

    def test_real_column_declared_real(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', '5', '1.0', 'hello')])
        dst = _dst()
        convert_database(src, dst, schema)
        assert _col_type(dst, 'vals', 'length') == 'REAL'

    def test_text_column_declared_text(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', '5', '1.0', 'hello')])
        dst = _dst()
        convert_database(src, dst, schema)
        assert _col_type(dst, 'vals', 'name') == 'TEXT'


# ===========================================================================
# TestCasting — values arrive as the right Python type
# ===========================================================================

class TestCasting:
    def test_integer_value_is_python_int(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', '42', '1.0', 'n')])
        dst = _dst()
        convert_database(src, dst, schema)
        rows = _fetch(dst, 'vals', ['count'])
        assert rows == [(42,)]
        assert isinstance(rows[0][0], int)

    def test_real_value_is_python_float(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', '1', '3.14', 'n')])
        dst = _dst()
        convert_database(src, dst, schema)
        rows = _fetch(dst, 'vals', ['length'])
        assert rows == [(3.14,)]
        assert isinstance(rows[0][0], float)

    def test_text_value_unchanged(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', '1', '1.0', 'hello world')])
        dst = _dst()
        convert_database(src, dst, schema)
        rows = _fetch(dst, 'vals', ['name'])
        assert rows == [('hello world',)]

    def test_empty_table_still_created(self):
        schema = _schema_typed()
        src = _src(schema, [])
        dst = _dst()
        convert_database(src, dst, schema)
        tables = {r[0] for r in dst.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert 'vals' in tables


# ===========================================================================
# TestSentinels — '.' and '?' become NULL regardless of column type
# ===========================================================================

class TestSentinels:
    def test_dot_sentinel_integer_col_becomes_null(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', '.', '1.0', 'n')])
        dst = _dst()
        convert_database(src, dst, schema)
        rows = _fetch(dst, 'vals', ['count'])
        assert rows == [(None,)]

    def test_question_sentinel_real_col_becomes_null(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', '1', '?', 'n')])
        dst = _dst()
        convert_database(src, dst, schema)
        rows = _fetch(dst, 'vals', ['length'])
        assert rows == [(None,)]

    def test_sentinel_text_col_becomes_null(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', '1', '1.0', '.')])
        dst = _dst()
        convert_database(src, dst, schema)
        rows = _fetch(dst, 'vals', ['name'])
        assert rows == [(None,)]

    def test_sentinel_produces_no_warning(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', '.', '?', 'n')])
        dst = _dst()
        msgs = convert_database(src, dst, schema)
        assert msgs == []


# ===========================================================================
# TestSU — SU suffixes stripped before cast, always with warning
# ===========================================================================

class TestSU:
    def test_su_stripped_real_col(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', '1', '1.23(4)', 'n')])
        dst = _dst()
        convert_database(src, dst, schema)
        rows = _fetch(dst, 'vals', ['length'])
        assert rows == [(1.23,)]

    def test_su_stripped_integer_col(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', '100(3)', '1.0', 'n')])
        dst = _dst()
        convert_database(src, dst, schema)
        rows = _fetch(dst, 'vals', ['count'])
        assert rows == [(100,)]

    def test_su_always_produces_warning(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', '1', '1.23(4)', 'n')])
        dst = _dst()
        msgs = convert_database(src, dst, schema)
        assert len(msgs) == 1
        assert 'SU dropped' in msgs[0]
        assert '1.23(4)' in msgs[0]
        assert '1.23' in msgs[0]


# ===========================================================================
# TestCoercionFailure — on_coercion_failure policies
# ===========================================================================

class TestCoercionFailure:
    def test_null_policy_bad_value_becomes_null(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', 'not_a_number', '1.0', 'n')])
        dst = _dst()
        msgs = convert_database(src, dst, schema, on_coercion_failure='null')
        rows = _fetch(dst, 'vals', ['count'])
        assert rows == [(None,)]
        assert any('coercion failed' in m for m in msgs)

    def test_keep_policy_bad_value_preserved(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', 'not_a_number', '1.0', 'n')])
        dst = _dst()
        msgs = convert_database(src, dst, schema, on_coercion_failure='keep')
        rows = _fetch(dst, 'vals', ['count'])
        assert rows == [('not_a_number',)]
        assert any('coercion failed' in m for m in msgs)

    def test_error_policy_raises(self):
        schema = _schema_typed()
        src = _src(schema, [('B', 1, 'x', 'not_a_number', '1.0', 'n')])
        dst = _dst()
        with pytest.raises(ValueError, match='coercion failed'):
            convert_database(src, dst, schema, on_coercion_failure='error')


# ===========================================================================
# TestFallbackTables — _cif_fallback copied verbatim
# ===========================================================================

class TestFallbackTables:
    def test_fallback_table_created_in_dst(self):
        schema = _schema_typed()
        src = _src(schema, [])
        apply_fallback_schema(src)  # already applied in _src, idempotent
        src.execute(
            'INSERT INTO "_cif_fallback" '
            '("_cifflow_block_id","_cifflow_row_id","tag","value","value_type") '
            'VALUES (?,?,?,?,?)',
            ('B', 1, '_some.tag', 'hello', 'STRING'),
        )
        src.commit()
        dst = _dst()
        convert_database(src, dst, schema)
        tables = {r[0] for r in dst.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert '_cif_fallback' in tables

    def test_fallback_row_copied_as_text(self):
        schema = _schema_typed()
        src = _src(schema, [])
        src.execute(
            'INSERT INTO "_cif_fallback" '
            '("_cifflow_block_id","_cifflow_row_id","tag","value","value_type") '
            'VALUES (?,?,?,?,?)',
            ('B', 1, '_some.tag', '42', 'STRING'),
        )
        src.commit()
        dst = _dst()
        convert_database(src, dst, schema)
        rows = dst.execute(
            'SELECT "value" FROM "_cif_fallback"'
        ).fetchall()
        assert rows == [('42',)]
        # Must still be TEXT (no casting)
        assert isinstance(rows[0][0], str)


# ===========================================================================
# TestContainerColumns — non-Single containers: JSON preserved, leaves cast
# ===========================================================================

def _src_matrix(schema, rows):
    """Populate the 'mat' table with (blk, row_id, label, hkl, xyz) rows."""
    c = sqlite3.connect(':memory:')
    c.isolation_level = None
    apply_schema(c, schema)
    apply_fallback_schema(c)
    c.execute('PRAGMA foreign_keys = OFF')
    c.execute('BEGIN')
    for blk, row_id, label, hkl, xyz in rows:
        c.execute(
            'INSERT INTO "mat" ("_cifflow_block_id","_cifflow_row_id","label","hkl","xyz") '
            'VALUES (?,?,?,?,?)',
            (blk, row_id, label, hkl, xyz),
        )
    c.execute('COMMIT')
    return c


class TestContainerColumns:
    def test_matrix_column_stays_text(self):
        """A Matrix/Integer column has TEXT affinity in the destination."""
        schema = _schema_matrix()
        src = _src_matrix(schema, [('B', 1, 'x', '["1","2","3"]', '[1.0,2.0]')])
        dst = _dst()
        convert_database(src, dst, schema)
        assert _col_type(dst, 'mat', 'hkl') == 'TEXT'

    def test_matrix_integer_leaves_cast(self):
        """Integer leaves inside a JSON list are cast to Python int."""
        import json
        schema = _schema_matrix()
        src = _src_matrix(schema, [('B', 1, 'x', '["1","2","3"]', '[1.0]')])
        dst = _dst()
        convert_database(src, dst, schema)
        rows = _fetch(dst, 'mat', ['hkl'])
        decoded = json.loads(rows[0][0])
        assert decoded == [1, 2, 3]
        assert all(isinstance(v, int) for v in decoded)

    def test_list_real_leaves_cast(self):
        """Real leaves inside a JSON list are cast to Python float."""
        import json
        schema = _schema_matrix()
        src = _src_matrix(schema, [('B', 1, 'x', '["1"]', '["1.5","2.25"]')])
        dst = _dst()
        convert_database(src, dst, schema)
        rows = _fetch(dst, 'mat', ['xyz'])
        decoded = json.loads(rows[0][0])
        assert decoded == [1.5, 2.25]
        assert all(isinstance(v, float) for v in decoded)

    def test_matrix_su_leaves_stripped(self):
        """SU suffixes on integer leaves inside JSON are stripped with warning."""
        import json
        schema = _schema_matrix()
        src = _src_matrix(schema, [('B', 1, 'x', '["1(1)","2(1)"]', '[1.0]')])
        dst = _dst()
        msgs = convert_database(src, dst, schema)
        rows = _fetch(dst, 'mat', ['hkl'])
        decoded = json.loads(rows[0][0])
        assert decoded == [1, 2]
        assert sum(1 for m in msgs if 'SU dropped' in m) == 2

    def test_matrix_no_spurious_coercion_warning(self):
        """A JSON value in an Integer/Matrix column does not produce a
        'coercion failed' warning — it is handled by JSON parsing, not
        by the single-scalar cast path."""
        schema = _schema_matrix()
        src = _src_matrix(schema, [('B', 1, 'x', '["1","2","3"]', '[1.0]')])
        dst = _dst()
        msgs = convert_database(src, dst, schema)
        assert not any('coercion failed' in m for m in msgs)
