"""
Unit tests for ingest().
"""

import json
import sqlite3
import uuid

import pytest

from pycifparse import ingest, IngestionError
from pycifparse.cifmodel.model import CifBlock, CifFile
from pycifparse.cifmodel.scalar import CifScalar
from pycifparse.dictionary.ddlm_item import DdlmItem
from pycifparse.dictionary.ddlm_parser import DdlmDictionary
from pycifparse.dictionary.schema import generate_schema
from pycifparse.dictionary.schema_apply import apply_fallback_schema, apply_schema
from pycifparse.ingestion.ingest import (
    _apply_fk,
    _merge_into,
    build_su_map,
    build_tag_to_column,
    decode_container,
    encode_container,
    encode_value,
    split_su,
)
from pycifparse.types import ValueType


# ---------------------------------------------------------------------------
# Primitive CifScalar helpers
# ---------------------------------------------------------------------------

def _s(value, vtype=ValueType.STRING):
    return CifScalar(value, vtype)

def _ph(value):
    return CifScalar(value, ValueType.PLACEHOLDER)

def _dq(value):
    return CifScalar(value, ValueType.DOUBLE_QUOTED)


# ---------------------------------------------------------------------------
# DdlmItem / DdlmDictionary builders
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


def _make_dict(cats, items, alias=None, deprecated=None):
    categories = {c.definition_id: c for c in cats}
    item_map = {i.definition_id: i for i in items}
    tag_to_item = {**categories, **item_map}
    return DdlmDictionary(
        name='TEST', title=None, version=None,
        categories=categories, items=item_map, tag_to_item=tag_to_item,
        alias_to_definition_id=alias or {},
        deprecated_ids=set(deprecated or []),
    )


# ---------------------------------------------------------------------------
# CifFile builders
# ---------------------------------------------------------------------------

def _block(name, scalars=None, loops=None):
    """
    scalars: {tag: CifScalar}  or  {tag: [CifScalar, ...]}
    loops: [(loop_tags, [[v00, v01, ...], [v10, ...], ...])]
    """
    b = CifBlock(name)
    for tag, val in (scalars or {}).items():
        if isinstance(val, list):
            for v in val:
                b._append_value(tag, v)
        else:
            b._append_value(tag, val)
    for loop_tags, rows in (loops or []):
        buffers = {tag: [row[i] for row in rows] for i, tag in enumerate(loop_tags)}
        b._add_loop(loop_tags, buffers)
    return b


def _file(*blocks):
    f = CifFile()
    for b in blocks:
        f._add_block(b)
    return f


# ---------------------------------------------------------------------------
# DB connection builder
# ---------------------------------------------------------------------------

def _conn(schema=None):
    conn = sqlite3.connect(':memory:')
    if schema is not None:
        apply_schema(conn, schema)
    apply_fallback_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Schema A
#   structure  (Set,  PK=id)
#   cell       (Set,  PK=structure_id key-FK→structure.id, col=length_a)
#   atom_site  (Loop, PK=label, non-key FK=structure_id→structure.id,
#                     col=x_fract, col=x_fract_su SU linked to x_fract)
# ---------------------------------------------------------------------------

def _schema_a():
    cats = [
        _cat('_structure', 'Set', ['_structure.id']),
        _cat('_cell', 'Set', ['_cell.structure_id']),
        _cat('_atom_site', 'Loop', ['_atom_site.label']),
    ]
    items = [
        _item('_structure.id', '_structure', 'id',
              type_purpose='Key', type_contents='Text'),
        _item('_cell.structure_id', '_cell', 'structure_id',
              type_purpose='Link', linked_item_id='_structure.id',
              type_contents='Text'),
        _item('_cell.length_a', '_cell', 'length_a', type_contents='Real'),
        _item('_atom_site.label', '_atom_site', 'label',
              type_purpose='Key', type_contents='Text'),
        _item('_atom_site.structure_id', '_atom_site', 'structure_id',
              type_purpose='Link', linked_item_id='_structure.id',
              type_contents='Text'),
        _item('_atom_site.x_fract', '_atom_site', 'x_fract',
              type_contents='Real'),
        _item('_atom_site.x_fract_su', '_atom_site', 'x_fract_su',
              type_purpose='SU', linked_item_id='_atom_site.x_fract',
              type_contents='Real'),
    ]
    return generate_schema(_make_dict(cats, items))


# Schema for alias/deprecation tests (no FK constraints to complicate things)
def _schema_alias_deprecated():
    """
    atom_site (Loop, PK=label, col=x_fract)
    Alias: _atom_site.old_x → _atom_site.x_fract
    Deprecated (canonical): _atom_site.x_fract
    """
    cats = [_cat('_atom_site', 'Loop', ['_atom_site.label'])]
    items = [
        _item('_atom_site.label', '_atom_site', 'label',
              type_purpose='Key', type_contents='Text'),
        _item('_atom_site.x_fract', '_atom_site', 'x_fract',
              type_contents='Real'),
    ]
    return generate_schema(_make_dict(
        cats, items,
        # _atom_site.old_x is an alias for _atom_site.x_fract
        alias={'_atom_site.old_x': '_atom_site.x_fract'},
        # deprecated_ids holds canonical IDs
        deprecated={'_atom_site.x_fract'},
    ))


# Schema for compatible multi-category loop:
#   atom_site (Loop, PK=label)
#   atom_site_aniso (Loop, PK=label key-FK→atom_site.label)
def _schema_compat_multi():
    cats = [
        _cat('_atom_site', 'Loop', ['_atom_site.label']),
        _cat('_atom_site_aniso', 'Loop', ['_atom_site_aniso.label']),
    ]
    items = [
        _item('_atom_site.label', '_atom_site', 'label',
              type_purpose='Key', type_contents='Text'),
        _item('_atom_site.x_fract', '_atom_site', 'x_fract',
              type_contents='Real'),
        _item('_atom_site_aniso.label', '_atom_site_aniso', 'label',
              type_purpose='Link', linked_item_id='_atom_site.label',
              type_contents='Text'),
        _item('_atom_site_aniso.u_11', '_atom_site_aniso', 'u_11',
              type_contents='Real'),
    ]
    return generate_schema(_make_dict(cats, items))


# Schema for incompatible multi-category loop:
#   structure (Set, PK=id) and atom_site (Loop, PK=label) — PKs resolve differently
def _schema_incompat_multi():
    cats = [
        _cat('_structure', 'Set', ['_structure.id']),
        _cat('_atom_site', 'Loop', ['_atom_site.label']),
    ]
    items = [
        _item('_structure.id', '_structure', 'id',
              type_purpose='Key', type_contents='Text'),
        _item('_atom_site.label', '_atom_site', 'label',
              type_purpose='Key', type_contents='Text'),
    ]
    return generate_schema(_make_dict(cats, items))


# Keyless Set: no category_keys declared
def _schema_keyless_set():
    cats = [_cat('_props', 'Set', [])]
    items = [_item('_props.name', '_props', 'name', type_contents='Text')]
    return generate_schema(_make_dict(cats, items))


# ---------------------------------------------------------------------------
# Helpers to query the DB
# ---------------------------------------------------------------------------

def _rows(conn, table, cols=None):
    if cols:
        col_list = ', '.join(f'"{c}"' for c in cols)
        return conn.execute(f'SELECT {col_list} FROM "{table}"').fetchall()
    return conn.execute(f'SELECT * FROM "{table}"').fetchall()


def _fallback(conn, block_id=None):
    if block_id:
        return conn.execute(
            'SELECT "_block_id","_row_id","tag","value","value_type","loop_id","col_index"'
            ' FROM "_cif_fallback" WHERE "_block_id"=? ORDER BY "_row_id","col_index","tag"',
            (block_id,)
        ).fetchall()
    return conn.execute(
        'SELECT "_block_id","_row_id","tag","value","value_type","loop_id","col_index"'
        ' FROM "_cif_fallback" ORDER BY "_block_id","_row_id","col_index","tag"'
    ).fetchall()


def _membership(conn, block_id=None):
    if block_id:
        return conn.execute(
            'SELECT "_block_id","_audit_dataset_id","id_regime"'
            ' FROM "_block_dataset_membership" WHERE "_block_id"=?',
            (block_id,)
        ).fetchall()
    return conn.execute(
        'SELECT "_block_id","_audit_dataset_id","id_regime"'
        ' FROM "_block_dataset_membership"'
    ).fetchall()


def _validation(conn):
    return conn.execute(
        'SELECT "check_name","severity","block_id","detail","id_regime"'
        ' FROM "_validation_result"'
    ).fetchall()


# ===========================================================================
# TestImport
# ===========================================================================

class TestImport:
    def test_ingest_importable_from_top_level(self):
        from pycifparse import ingest as _ingest
        assert callable(_ingest)

    def test_ingest_importable_from_ingestion(self):
        from pycifparse.ingestion import ingest as _ingest
        assert callable(_ingest)


# ===========================================================================
# TestEncodeValue
# ===========================================================================

class TestEncodeValue:
    def test_string_value(self):
        stored, vtype = encode_value(_s('hello'))
        assert stored == 'hello'
        assert vtype == 'string'

    def test_placeholder_dot(self):
        stored, vtype = encode_value(_ph('.'))
        assert stored == '.'
        assert vtype == 'placeholder'

    def test_placeholder_question(self):
        stored, vtype = encode_value(_ph('?'))
        assert stored == '?'
        assert vtype == 'placeholder'

    def test_quoted_dot(self):
        stored, vtype = encode_value(_dq('.'))
        assert stored == '"."'
        assert vtype == ValueType.DOUBLE_QUOTED.value

    def test_quoted_question(self):
        stored, vtype = encode_value(_dq('?'))
        assert stored == '"?"'
        assert vtype == ValueType.DOUBLE_QUOTED.value

    def test_list_value(self):
        lst = [_s('a'), _s('b')]
        stored, vtype = encode_value(lst)
        assert vtype == 'list'
        assert json.loads(stored) == ['a', 'b']

    def test_dict_value(self):
        d = {'key': _s('val')}
        stored, vtype = encode_value(d)
        assert vtype == 'table'
        assert json.loads(stored) == {'key': 'val'}


# ===========================================================================
# TestEncodeContainer
# ===========================================================================

class TestEncodeContainer:
    def test_placeholder_in_list_stored_as_string(self):
        lst = [_ph('.'), _s('real')]
        stored, _ = encode_container(lst)
        data = json.loads(stored)
        assert data[0] == '.'     # PLACEHOLDER stored as plain '.'
        assert data[1] == 'real'

    def test_quoted_dot_in_list_stored_with_delimiters(self):
        lst = [_dq('.'), _s('x')]
        stored, _ = encode_container(lst)
        data = json.loads(stored)
        assert data[0] == '"."'

    def test_nested_container(self):
        lst = [{'a': _s('1')}, {'a': _s('2')}]
        stored, vtype = encode_container(lst)
        assert vtype == 'list'
        data = json.loads(stored)
        assert data == [{'a': '1'}, {'a': '2'}]

    def test_decode_container_roundtrips(self):
        lst = [_s('x'), _ph('?'), {'k': _s('v')}]
        stored, _ = encode_container(lst)
        decoded = decode_container(stored)
        assert decoded == ['x', '?', {'k': 'v'}]


# ===========================================================================
# TestSplitSu
# ===========================================================================

class TestSplitSu:
    def test_integer_with_su(self):
        assert split_su('1234(5)') == ('1234', '5')

    def test_decimal_with_su(self):
        assert split_su('1.234(5)') == ('1.234', '0.005')

    def test_negative_with_su(self):
        assert split_su('-1.234(5)') == ('-1.234', '0.005')

    def test_scientific_with_su(self):
        assert split_su('1.23e-4(5)') == ('1.23e-4', '0.000005')

    def test_no_su(self):
        assert split_su('1.234') is None

    def test_plain_text(self):
        assert split_su('hello') is None

    def test_multi_digit_su(self):
        assert split_su('12.34(56)') == ('12.34', '0.56')

    def test_su_larger_than_last_digit(self):
        # 456 units in the first decimal place: 45.6
        assert split_su('12.3(456)') == ('12.3', '45.6')

    def test_integer_multi_digit_su(self):
        # No decimal places: scale factor is 1
        assert split_su('100(12)') == ('100', '12')

    def test_scientific_positive_exp(self):
        assert split_su('1.5e2(3)') == ('1.5e2', '30')


# ===========================================================================
# TestBuildSuMap
# ===========================================================================

class TestBuildSuMap:
    def test_su_map_from_schema(self):
        schema = _schema_a()
        su_map = build_su_map(schema)
        assert su_map == {'_atom_site.x_fract': 'x_fract_su'}

    def test_no_su_columns(self):
        cats = [_cat('_simple', 'Loop', ['_simple.id'])]
        items = [_item('_simple.id', '_simple', 'id', type_purpose='Key')]
        schema = generate_schema(_make_dict(cats, items))
        assert build_su_map(schema) == {}


# ===========================================================================
# TestBuildTagToColumn
# ===========================================================================

class TestBuildTagToColumn:
    def test_maps_definition_id_to_table_col(self):
        schema = _schema_a()
        mapping = build_tag_to_column(schema)
        assert mapping['_structure.id'] == ('structure', 'id')
        assert mapping['_atom_site.label'] == ('atom_site', 'label')
        assert mapping['_atom_site.x_fract'] == ('atom_site', 'x_fract')

    def test_synthetic_columns_excluded(self):
        schema = _schema_a()
        mapping = build_tag_to_column(schema)
        # Synthetic columns (_block_id, _row_id) are not in column_to_tag
        for key in mapping:
            assert not key.startswith('_block') and '_row_id' not in key


# ===========================================================================
# TestNoSchema
# ===========================================================================

class TestNoSchema:
    def test_all_scalars_to_fallback(self):
        f = _file(_block('B', scalars={
            '_cell.length_a': _s('5.4'),
            '_cell.length_b': _s('5.4'),
        }))
        conn = _conn()
        ingest(f, conn)
        rows = _fallback(conn, 'B')
        tags = {r[2] for r in rows}
        assert '_cell.length_a' in tags
        assert '_cell.length_b' in tags

    def test_scalar_row_id_is_1(self):
        f = _file(_block('B', scalars={'_cell.length_a': _s('5.4')}))
        conn = _conn()
        ingest(f, conn)
        row = _fallback(conn, 'B')[0]
        assert row[1] == 1  # _row_id

    def test_loop_rows_to_fallback(self):
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.x_fract'],
             [[_s('C1'), _s('0.1')], [_s('C2'), _s('0.2')]]),
        ]))
        conn = _conn()
        ingest(f, conn)
        rows = _fallback(conn, 'B')
        assert len(rows) == 4  # 2 iterations × 2 tags

    def test_loop_row_id_increments_per_iteration(self):
        f = _file(_block('B', loops=[
            (['_atom_site.label'], [[_s('C1')], [_s('C2')]]),
        ]))
        conn = _conn()
        ingest(f, conn)
        rows = _fallback(conn, 'B')
        row_ids = [r[1] for r in rows]
        assert row_ids == [1, 2]  # iteration 0 → row_id=1, iteration 1 → row_id=2

    def test_loop_id_assigned(self):
        f = _file(_block('B', loops=[
            (['_a.x'], [[_s('1')], [_s('2')]]),
        ]))
        conn = _conn()
        ingest(f, conn)
        rows = _fallback(conn, 'B')
        assert all(r[5] == 1 for r in rows)  # loop_id = 1

    def test_col_index_assigned(self):
        f = _file(_block('B', loops=[
            (['_a.x', '_a.y'], [[_s('1'), _s('2')]]),
        ]))
        conn = _conn()
        ingest(f, conn)
        rows = sorted(_fallback(conn, 'B'), key=lambda r: r[6])  # sort by col_index
        assert rows[0][6] == 0  # _a.x col_index
        assert rows[1][6] == 1  # _a.y col_index

    def test_scalar_loop_id_is_null(self):
        f = _file(_block('B', scalars={'_cell.a': _s('5')}))
        conn = _conn()
        ingest(f, conn)
        row = _fallback(conn, 'B')[0]
        assert row[5] is None  # loop_id

    def test_membership_row_assumed(self):
        f = _file(_block('B', scalars={'_cell.a': _s('5')}))
        conn = _conn()
        ingest(f, conn)
        rows = _membership(conn, 'B')
        assert len(rows) == 1
        assert rows[0][2] == 'assumed'

    def test_two_loops_get_different_loop_ids(self):
        f = _file(_block('B', loops=[
            (['_a.x'], [[_s('1')]]),
            (['_b.y'], [[_s('2')]]),
        ]))
        conn = _conn()
        ingest(f, conn)
        rows = _fallback(conn, 'B')
        loop_ids = {r[2]: r[5] for r in rows}  # tag → loop_id
        assert loop_ids['_a.x'] == 1
        assert loop_ids['_b.y'] == 2


# ===========================================================================
# TestScalarRouting
# ===========================================================================

class TestScalarRouting:
    def test_scalar_to_set_table(self):
        """_structure.id scalar → structure table."""
        schema = _schema_a()
        f = _file(_block('B', scalars={'_structure.id': _s('S1')}))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'structure', ['id'])
        assert rows == [('S1',)]

    def test_unmapped_tag_to_fallback(self):
        """Tag not in schema → _cif_fallback."""
        schema = _schema_a()
        f = _file(_block('B', scalars={'_unknown.tag': _s('val')}))
        conn = _conn(schema)
        ingest(f, conn, schema)
        fb = _fallback(conn, 'B')
        assert any(r[2] == '_unknown.tag' for r in fb)

    def test_mapped_tag_not_in_fallback(self):
        schema = _schema_a()
        f = _file(_block('B', scalars={'_structure.id': _s('S1')}))
        conn = _conn(schema)
        ingest(f, conn, schema)
        fb = _fallback(conn, 'B')
        assert not any(r[2] == '_structure.id' for r in fb)


# ===========================================================================
# TestLoopRouting
# ===========================================================================

class TestLoopRouting:
    def test_loop_tags_to_structured_table(self):
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.x_fract'],
             [[_s('C1'), _s('0.1')], [_s('C2'), _s('0.2')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'atom_site', ['label', 'x_fract'])
        assert set(rows) == {('C1', '0.1'), ('C2', '0.2')}

    def test_loop_row_id_increments(self):
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label'],
             [[_s('C1')], [_s('C2')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'atom_site', ['_row_id', 'label'])
        row_ids = sorted(r[0] for r in rows)
        assert row_ids == [1, 2]


# ===========================================================================
# TestAliasDeprecation
# ===========================================================================

class TestAliasDeprecation:
    def test_alias_tag_routed_to_same_column(self):
        """_atom_site.old_x is an alias for _atom_site.x_fract → same column."""
        schema = _schema_alias_deprecated()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.old_x'],
             [[_s('C1'), _s('0.1')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'atom_site', ['label', 'x_fract'])
        assert rows == [('C1', '0.1')]

    def test_deprecated_tag_emits_warning_once_per_block(self):
        """Deprecated tag used in a multi-row loop: warned once, not per iteration."""
        schema = _schema_alias_deprecated()
        # _atom_site.old_x is alias → _atom_site.x_fract (deprecated)
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.old_x'],
             [[_s('C1'), _s('0.1')], [_s('C2'), _s('0.2')]]),
        ]))
        conn = _conn(schema)
        errors = ingest(f, conn, schema)
        warns = [e for e in errors if 'deprecated' in e]
        assert len(warns) == 1

    def test_deprecated_tag_in_next_block_warns_again(self):
        schema = _schema_alias_deprecated()
        b1 = _block('B1', loops=[
            (['_atom_site.label', '_atom_site.old_x'], [[_s('C1'), _s('0.1')]]),
        ])
        b2 = _block('B2', loops=[
            (['_atom_site.label', '_atom_site.old_x'], [[_s('C2'), _s('0.2')]]),
        ])
        conn = _conn(schema)
        errors = ingest(_file(b1, b2), conn, schema)
        warns = [e for e in errors if 'deprecated' in e]
        assert len(warns) == 2  # one per block

    def test_alias_and_deprecated_both_applied(self):
        """Alias resolved AND deprecation warning emitted for same tag."""
        schema = _schema_alias_deprecated()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.old_x'], [[_s('C1'), _s('0.5')]]),
        ]))
        conn = _conn(schema)
        errors = ingest(f, conn, schema)
        warns = [e for e in errors if 'deprecated' in e]
        assert len(warns) == 1
        rows = _rows(conn, 'atom_site', ['x_fract'])
        assert rows[0][0] == '0.5'


# ===========================================================================
# TestPlaceholderEncoding
# ===========================================================================

class TestPlaceholderEncoding:
    def test_placeholder_dot_in_fallback(self):
        f = _file(_block('B', scalars={'_unknown.x': _ph('.')}))
        conn = _conn()
        ingest(f, conn)
        row = _fallback(conn, 'B')[0]
        assert row[3] == '.'
        assert row[4] == 'placeholder'

    def test_placeholder_question_in_fallback(self):
        f = _file(_block('B', scalars={'_unknown.x': _ph('?')}))
        conn = _conn()
        ingest(f, conn)
        row = _fallback(conn, 'B')[0]
        assert row[3] == '?'
        assert row[4] == 'placeholder'

    def test_quoted_dot_in_fallback_distinguished(self):
        f = _file(_block('B', scalars={'_unknown.x': _dq('.')}))
        conn = _conn()
        ingest(f, conn)
        row = _fallback(conn, 'B')[0]
        assert row[3] == '"."'
        assert row[4] != 'placeholder'


# ===========================================================================
# TestSUIngestion
# ===========================================================================

class TestSUIngestion:
    def test_su_split_measurand_and_su_columns(self):
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.x_fract'],
             [[_s('C1'), _s('0.123(4)')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'atom_site', ['label', 'x_fract', 'x_fract_su'])
        assert rows[0] == ('C1', '0.123', '0.004')

    def test_su_absent_leaves_su_column_null(self):
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.x_fract'],
             [[_s('C1'), _s('0.123')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'atom_site', ['x_fract', 'x_fract_su'])
        assert rows[0] == ('0.123', None)

    def test_su_not_attempted_on_quoted_value(self):
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.x_fract'],
             [[_s('C1'), _dq('0.123(4)')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'atom_site', ['x_fract', 'x_fract_su'])
        # Quoted values are never SU candidates; stored as-is
        assert rows[0] == ('0.123(4)', None)

    def test_su_not_attempted_in_fallback(self):
        f = _file(_block('B', loops=[
            (['_atom_site.x_fract'], [[_s('0.123(4)')]]),
        ]))
        conn = _conn()
        ingest(f, conn)
        row = _fallback(conn, 'B')[0]
        assert row[3] == '0.123(4)'  # stored raw; no SU split


# ===========================================================================
# TestMerge
# ===========================================================================

class TestKeylessLoop:
    def test_uuid_generated_per_iteration(self):
        """Each loop iteration must receive a distinct UUID when the key column
        is absent from the loop.  Before the fix, Source-3 UUIDs were stored in
        fk_accumulator and reused by subsequent iterations, collapsing N rows
        into 1."""
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.x_fract'],
             [[_s('0.1')], [_s('0.2')], [_s('0.3')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'atom_site', ['label', 'x_fract'])
        assert len(rows) == 3
        labels = [r[0] for r in rows]
        assert len(set(labels)) == 3, "each row must have a distinct UUID label"
        assert sorted(r[1] for r in rows) == ['0.1', '0.2', '0.3']

    def test_shared_parent_fk_not_affected(self):
        """A real value in fk_accumulator (e.g. from a scalar) must still
        propagate to every loop iteration — only *generated* UUIDs are
        excluded from persistence."""
        schema = _schema_a()
        # _structure.id given as scalar → lands in fk_accumulator
        f = _file(_block('B',
            scalars={'_structure.id': _s('S1')},
            loops=[
                (['_atom_site.x_fract'],
                 [[_s('0.1')], [_s('0.2')]]),
            ],
        ))
        conn = _conn(schema)
        # propagate_fk=True so the non-key FK column structure_id is filled from
        # the accumulator — this verifies that real accumulator values propagate
        # to every iteration (unlike generated UUIDs which must not persist).
        ingest(f, conn, schema, propagate_fk=True)
        # Both rows must reference the same structure id 'S1'
        rows = conn.execute(
            "SELECT x_fract, structure_id FROM atom_site ORDER BY x_fract"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][1] == rows[1][1] == 'S1'


class TestMerge:
    def test_distinct_pks_from_two_blocks(self):
        schema = _schema_a()
        b1 = _block('B1', loops=[
            (['_atom_site.label'], [[_s('C1')]]),
        ])
        b2 = _block('B2', loops=[
            (['_atom_site.label'], [[_s('C2')]]),
        ])
        conn = _conn(schema)
        ingest(_file(b1, b2), conn, schema)
        rows = _rows(conn, 'atom_site', ['label'])
        assert set(rows) == {('C1',), ('C2',)}

    def test_same_pk_across_blocks_merged(self):
        schema = _schema_a()
        b1 = _block('B1', loops=[
            (['_atom_site.label', '_atom_site.x_fract'],
             [[_s('C1'), _s('0.1')]]),
        ])
        b2 = _block('B2', loops=[
            (['_atom_site.label', '_atom_site.x_fract'],
             [[_s('C1'), _s('0.1')]]),
        ])
        conn = _conn(schema)
        ingest(_file(b1, b2), conn, schema)
        rows = _rows(conn, 'atom_site', ['label', 'x_fract'])
        assert rows == [('C1', '0.1')]

    def test_merged_block_id_from_first_block(self):
        schema = _schema_a()
        b1 = _block('B1', loops=[
            (['_atom_site.label'], [[_s('C1')]]),
        ])
        b2 = _block('B2', loops=[
            (['_atom_site.label', '_atom_site.x_fract'],
             [[_s('C1'), _s('0.5')]]),
        ])
        conn = _conn(schema)
        ingest(_file(b1, b2), conn, schema)
        rows = _rows(conn, 'atom_site', ['_block_id', 'label', 'x_fract'])
        assert rows == [('B1', 'C1', '0.5')]

    def test_cross_block_merge_conflict_raises(self):
        schema = _schema_a()
        b1 = _block('B1', loops=[
            (['_atom_site.label', '_atom_site.x_fract'],
             [[_s('C1'), _s('0.1')]]),
        ])
        b2 = _block('B2', loops=[
            (['_atom_site.label', '_atom_site.x_fract'],
             [[_s('C1'), _s('0.9')]]),  # conflicting value
        ])
        conn = _conn(schema)
        with pytest.raises(IngestionError) as exc_info:
            ingest(_file(b1, b2), conn, schema)
        assert any('merge conflict' in e for e in exc_info.value.errors)
        # transaction rolled back — no rows committed
        rows = _rows(conn, 'atom_site', ['x_fract'])
        assert rows == []

    def test_row_id_does_not_reset_between_blocks(self):
        schema = _schema_a()
        b1 = _block('B1', loops=[
            (['_atom_site.label'], [[_s('C1')]]),
        ])
        b2 = _block('B2', loops=[
            (['_atom_site.label'], [[_s('C2')]]),
        ])
        conn = _conn(schema)
        ingest(_file(b1, b2), conn, schema)
        rows = _rows(conn, 'atom_site', ['_row_id', 'label'])
        row_id_by_label = {label: rid for rid, label in rows}
        assert row_id_by_label['C1'] == 1
        assert row_id_by_label['C2'] == 2  # not 1 again


# ===========================================================================
# TestFKPropagation
# ===========================================================================

class TestFKPropagation:
    def test_key_fk_propagated_from_scalar_set(self):
        """_cell.structure_id is a key-FK; should be filled from _structure.id."""
        schema = _schema_a()
        f = _file(_block('B', scalars={
            '_structure.id': _s('S1'),
            '_cell.length_a': _s('5.4'),
        }))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'cell', ['structure_id'])
        assert rows[0][0] == 'S1'

    def test_key_fk_propagated_from_loop(self):
        """atom_site.label is known from a same-loop value when in a multi-category loop."""
        schema = _schema_compat_multi()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site_aniso.u_11'],
             [[_s('C1'), _s('0.01')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'atom_site_aniso', ['label', 'u_11'])
        assert rows[0] == ('C1', '0.01')

    def test_key_fk_from_single_iteration_loop(self):
        """Single-iteration loop populates fk_accumulator for subsequent loops."""
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label'], [[_s('C1')]]),           # single-iter → fk_accumulator
            (['_atom_site.label', '_atom_site.x_fract'],    # second loop: label already known
             [[_s('C1'), _s('0.5')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'atom_site', ['label', 'x_fract'])
        # merged: same PK label=C1; x_fract filled from second loop
        assert ('C1', '0.5') in rows

    def test_non_key_fk_not_propagated_by_default(self):
        """_atom_site.structure_id is a non-key FK; should stay NULL if not in CIF."""
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label'], [[_s('C1')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema, propagate_fk=False)
        rows = _rows(conn, 'atom_site', ['label', 'structure_id'])
        assert rows[0] == ('C1', None)

    def test_non_key_fk_propagated_when_flag_set(self):
        schema = _schema_a()
        f = _file(_block('B', scalars={
            '_structure.id': _s('S1'),
        }, loops=[
            (['_atom_site.label'], [[_s('C1')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema, propagate_fk=True)
        rows = _rows(conn, 'atom_site', ['label', 'structure_id'])
        assert rows[0] == ('C1', 'S1')

    def test_key_fk_absent_generates_uuid(self):
        """If key-FK propagation source not found, _apply_fk generates UUID."""
        schema = _schema_a()
        # Build cell table row with no structure_id value
        table = schema.tables['cell']
        row = {'_block_id': 'B', 'length_a': '5.4'}
        errors = []
        fk_acc = {}
        _apply_fk(row, table, schema, None, fk_acc,
                  propagate_fk=False, emit=errors.append)
        sid = row.get('structure_id')
        assert sid is not None
        try:
            uuid.UUID(sid)
        except ValueError:
            pytest.fail(f'structure_id is not a UUID: {sid!r}')
        assert any(e for e in errors)
        # UUID stored in fk_accumulator for subsequent propagation
        assert fk_acc.get('_structure.id') == sid

    def test_explicit_value_overrides_propagation(self):
        """Explicit value in row is not overridden by fk_accumulator."""
        schema = _schema_a()
        table = schema.tables['cell']
        # row already has structure_id='EXPLICIT'; fk_acc has 'S1'
        row = {'_block_id': 'B', 'structure_id': 'EXPLICIT', 'length_a': '5.4'}
        fk_acc = {'_structure.id': 'S1'}
        errors = []
        _apply_fk(row, table, schema, None, fk_acc, propagate_fk=True, emit=errors.append)
        assert row['structure_id'] == 'EXPLICIT'
        assert not errors

    def test_key_fk_absent_creates_stub_in_parent(self):
        """UUID generated for key-FK also creates a stub row in the parent table."""
        schema = _schema_a()
        table = schema.tables['cell']
        row = {'_block_id': 'B', 'length_a': '5.4'}
        errors = []
        fk_acc = {}
        merged_rows: dict = {}
        row_id_counters: dict = {}
        _apply_fk(row, table, schema, None, fk_acc,
                  propagate_fk=False, emit=errors.append,
                  block_id='B', merged_rows=merged_rows,
                  row_id_counters=row_id_counters)
        sid = row.get('structure_id')
        assert sid is not None
        # stub row must exist in the parent (structure) table
        assert 'structure' in merged_rows
        stub_rows = list(merged_rows['structure'].values())
        assert len(stub_rows) == 1
        assert stub_rows[0]['id'] == sid
        assert stub_rows[0]['_block_id'] == 'B'

    def test_key_fk_absent_stub_does_not_overwrite_real_parent(self):
        """If the parent table already has a real row, the stub is merged in without
        overwriting existing non-NULL values."""
        schema = _schema_a()
        table = schema.tables['cell']
        errors = []
        fk_acc = {}
        # Pre-populate a real structure row with id='S1'
        real_structure_row = {'_block_id': 'B', 'id': 'S1', '_row_id': 1}
        merged_rows: dict = {'structure': {('S1',): real_structure_row}}
        row_id_counters: dict = {'structure': 2}
        # Now ingest a cell row that already knows structure_id='S1'
        row = {'_block_id': 'B', 'structure_id': 'S1', 'length_a': '5.4'}
        _apply_fk(row, table, schema, None, fk_acc,
                  propagate_fk=False, emit=errors.append,
                  block_id='B', merged_rows=merged_rows,
                  row_id_counters=row_id_counters)
        # No UUID generated; only one structure row
        assert not errors
        assert len(merged_rows['structure']) == 1


# ===========================================================================
# TestSetTable
# ===========================================================================

class TestSetTable:
    def test_set_table_one_row_per_block(self):
        schema = _schema_a()
        b1 = _block('B1', scalars={'_structure.id': _s('S1')})
        b2 = _block('B2', scalars={'_structure.id': _s('S2')})
        conn = _conn(schema)
        ingest(_file(b1, b2), conn, schema)
        rows = _rows(conn, 'structure', ['id'])
        assert set(rows) == {('S1',), ('S2',)}

    def test_set_table_block_id_populated(self):
        schema = _schema_a()
        f = _file(_block('MY_BLOCK', scalars={'_structure.id': _s('S1')}))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'structure', ['_block_id', 'id'])
        assert rows[0] == ('MY_BLOCK', 'S1')

    def test_keyless_set_table_pycifparse_id_is_uuid(self):
        schema = _schema_keyless_set()
        f = _file(_block('B', scalars={'_props.name': _s('val')}))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'props', ['_pycifparse_id', 'name'])
        assert len(rows) == 1
        pid = rows[0][0]
        assert pid is not None
        try:
            uuid.UUID(pid)
        except ValueError:
            pytest.fail(f'_pycifparse_id is not a UUID: {pid!r}')

    def test_set_table_row_id_reserved_at_first_tag(self):
        """_row_id for Set table should reflect encounter order, not be arbitrary."""
        schema = _schema_a()
        f = _file(_block('B', scalars={
            '_structure.id': _s('S1'),
        }))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'structure', ['_row_id'])
        assert rows[0][0] == 1


# ===========================================================================
# TestMixedLoop
# ===========================================================================

class TestMixedLoop:
    def test_mixed_loop_known_to_table_unknown_to_fallback(self):
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.x_fract', '_atom_site.custom_tag'],
             [[_s('C1'), _s('0.1'), _s('extra')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _rows(conn, 'atom_site', ['label', 'x_fract'])
        assert rows == [('C1', '0.1')]
        fb = _fallback(conn, 'B')
        assert any(r[2] == '_atom_site.custom_tag' for r in fb)

    def test_mixed_loop_fallback_row_id_matches_structured(self):
        """Fallback cells in mixed loop share _row_id with structured row."""
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.custom_tag'],
             [[_s('C1'), _s('extra')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        struct_row_id = _rows(conn, 'atom_site', ['_row_id'])[0][0]
        fb = _fallback(conn, 'B')
        fb_row_id = fb[0][1]  # _row_id
        assert fb_row_id == struct_row_id

    def test_mixed_loop_col_index_correct(self):
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_unknown.x'],
             [[_s('C1'), _s('val')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        fb = _fallback(conn, 'B')
        assert fb[0][6] == 1  # _unknown.x is col_index=1


# ===========================================================================
# TestCompatibleMultiCategory
# ===========================================================================

class TestCompatibleMultiCategory:
    def test_compatible_loop_routes_each_tag_to_own_table(self):
        schema = _schema_compat_multi()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.x_fract',
              '_atom_site_aniso.u_11'],
             [[_s('C1'), _s('0.1'), _s('0.01')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        as_rows = _rows(conn, 'atom_site', ['label', 'x_fract'])
        ana_rows = _rows(conn, 'atom_site_aniso', ['label', 'u_11'])
        assert as_rows == [('C1', '0.1')]
        assert ana_rows == [('C1', '0.01')]


# ===========================================================================
# TestIncompatibleMultiCategory
# ===========================================================================

class TestIncompatibleMultiCategory:
    def test_incompatible_loop_all_to_fallback(self):
        schema = _schema_incompat_multi()
        f = _file(_block('B', loops=[
            (['_structure.id', '_atom_site.label'],
             [[_s('S1'), _s('C1')]]),
        ]))
        conn = _conn(schema)
        errors = ingest(f, conn, schema)
        assert any('incompatible' in e for e in errors)
        fb = _fallback(conn, 'B')
        tags = {r[2] for r in fb}
        assert '_structure.id' in tags
        assert '_atom_site.label' in tags
        assert _rows(conn, 'structure', ['id']) == []
        assert _rows(conn, 'atom_site', ['label']) == []


# ===========================================================================
# TestLoopIdInFallback
# ===========================================================================

class TestLoopIdInFallback:
    def test_loop_id_increments_even_without_fallback_rows(self):
        """loop_id_counter increments after each loop, including structured-table loops."""
        # Use two mixed loops: each has one structured tag and one fallback tag.
        # Loop 1 gets loop_id=1, loop 2 gets loop_id=2 in _cif_fallback.
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_unknown.a'], [[_s('C1'), _s('extra1')]]),
            (['_atom_site.x_fract', '_unknown.b'], [[_s('0.5'), _s('extra2')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        fb = _fallback(conn, 'B')
        # _unknown.a is in loop 1 → loop_id=1; _unknown.b is in loop 2 → loop_id=2
        loop_ids = {r[2]: r[5] for r in fb}
        assert loop_ids['_unknown.a'] == 1
        assert loop_ids['_unknown.b'] == 2


# ===========================================================================
# TestDatasetNamespace
# ===========================================================================

class TestDatasetNamespace:
    def test_dataset_block_gets_dataset_id_regime(self):
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_audit_dataset.id'], [[_s('DS1')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _membership(conn, 'B')
        assert rows[0][1] == 'DS1'
        assert rows[0][2] == 'dataset'

    def test_dataset_block_multiple_ids_one_row_each(self):
        f = _file(_block('B', loops=[
            (['_audit_dataset.id'], [[_s('DS1')], [_s('DS2')]]),
        ]))
        conn = _conn()
        ingest(f, conn)
        rows = _membership(conn, 'B')
        dataset_ids = {r[1] for r in rows}
        assert dataset_ids == {'DS1', 'DS2'}
        assert all(r[2] == 'dataset' for r in rows)

    def test_general_block_uuid_pks_gets_uuid_regime(self):
        schema = _schema_a()
        uid = str(uuid.uuid4())
        f = _file(_block('B', loops=[
            (['_atom_site.label'], [[_s(uid)]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _membership(conn, 'B')
        assert rows[0][1] == ''
        assert rows[0][2] == 'uuid'

    def test_general_block_non_uuid_pks_gets_assumed(self):
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label'], [[_s('C1')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        rows = _membership(conn, 'B')
        assert rows[0][2] == 'assumed'

    def test_general_block_no_rows_gets_assumed(self):
        conn = _conn()
        f = _file(_block('B', scalars={'_unknown.x': _s('val')}))
        ingest(f, conn)
        rows = _membership(conn, 'B')
        assert rows[0][2] == 'assumed'

    def test_incompatible_datasets_raises_value_error(self):
        f = _file(
            _block('B1', loops=[(['_audit_dataset.id'], [[_s('DS1')]])]),
            _block('B2', loops=[(['_audit_dataset.id'], [[_s('DS2')]])]),
        )
        conn = _conn()
        with pytest.raises(ValueError, match='incompatible'):
            ingest(f, conn)

    def test_incompatible_datasets_no_rows_written(self):
        f = _file(
            _block('B1', loops=[(['_audit_dataset.id'], [[_s('DS1')]])]),
            _block('B2', loops=[(['_audit_dataset.id'], [[_s('DS2')]])]),
        )
        conn = _conn()
        try:
            ingest(f, conn)
        except ValueError:
            pass
        assert _fallback(conn) == []
        assert _membership(conn) == []

    def test_dataset_id_filter_selects_matching_blocks(self):
        f = _file(
            _block('B1', loops=[(['_audit_dataset.id'], [[_s('DS1')]])]),
            _block('B2', loops=[(['_audit_dataset.id'], [[_s('DS2')]])]),
        )
        conn = _conn()
        ingest(f, conn, dataset_id='DS1')
        rows = _membership(conn)
        block_ids = {r[0] for r in rows}
        assert 'B1' in block_ids
        assert 'B2' not in block_ids

    def test_dataset_id_not_found_raises_value_error(self):
        f = _file(_block('B1', loops=[(['_audit_dataset.id'], [[_s('DS1')]])]))
        conn = _conn()
        with pytest.raises(ValueError):
            ingest(f, conn, dataset_id='UNKNOWN')

    def test_dataset_id_missing_from_all_blocks_raises_value_error(self):
        """dataset_id provided but CifFile has no dataset blocks at all."""
        f = _file(_block('B', scalars={'_cell.a': _s('5')}))
        conn = _conn()
        with pytest.raises(ValueError):
            ingest(f, conn, dataset_id='DS1')

    def test_general_block_included_with_dataset_filter(self):
        """General blocks (no _audit_dataset.id) are always included."""
        f = _file(
            _block('DS_BLOCK', loops=[(['_audit_dataset.id'], [[_s('DS1')]])]),
            _block('GEN_BLOCK', scalars={'_unknown.x': _s('val')}),
        )
        conn = _conn()
        ingest(f, conn, dataset_id='DS1')
        rows = _membership(conn)
        block_ids = {r[0] for r in rows}
        assert 'DS_BLOCK' in block_ids
        assert 'GEN_BLOCK' in block_ids

    def test_skipped_block_tags_absent_from_fallback(self):
        f = _file(
            _block('B1', loops=[(['_audit_dataset.id'], [[_s('DS1')]])]),
            _block('B2', loops=[
                (['_audit_dataset.id'], [[_s('DS2')]]),
            ], scalars={'_unknown.x': _s('ONLY_IN_B2')}),
        )
        conn = _conn()
        ingest(f, conn, dataset_id='DS1')
        fb = _fallback(conn)
        assert not any(r[3] == 'ONLY_IN_B2' for r in fb)


# ===========================================================================
# TestValidationResult
# ===========================================================================

class TestValidationResult:
    def test_non_uuid_pks_trigger_uuid_regime_warning(self):
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label'], [[_s('C1')]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        vrows = _validation(conn)
        assert any(r[0] == 'uuid_regime' and r[1] == 'Warning' for r in vrows)

    def test_uuid_pks_no_uuid_regime_warning(self):
        schema = _schema_a()
        uid = str(uuid.uuid4())
        f = _file(_block('B', loops=[
            (['_atom_site.label'], [[_s(uid)]]),
        ]))
        conn = _conn(schema)
        ingest(f, conn, schema)
        vrows = _validation(conn)
        assert not any(r[0] == 'uuid_regime' for r in vrows)

    def test_uuid_reference_check_not_implemented(self):
        """Stage 4 stub: no uuid_reference_check rows written."""
        schema = _schema_a()
        f = _file(
            _block('DS', loops=[(['_audit_dataset.id'], [[_s('DS1')]])]),
            _block('GEN', loops=[(['_atom_site.label'], [[_s(str(uuid.uuid4()))]])]),
        )
        conn = _conn(schema)
        ingest(f, conn, schema)
        vrows = _validation(conn)
        assert not any(r[0] == 'uuid_reference_check' for r in vrows)


# ===========================================================================
# TestContainerValues
# ===========================================================================

class TestContainerValues:
    # CIF list/table values are stored as Python list/dict as a single element
    # in the block's tag values.  Wrap in an outer list to pass via _make_block:
    #   scalars={'tag': [cif_list_value]} means one value which is a CIF list.

    def test_list_value_in_fallback_is_json(self):
        cif_list = [_s('a'), _s('b')]  # CIF list value
        f = _file(_block('B', scalars={'_unknown.x': [cif_list]}))
        conn = _conn()
        ingest(f, conn)
        row = _fallback(conn, 'B')[0]
        assert row[4] == 'list'
        assert json.loads(row[3]) == ['a', 'b']

    def test_table_value_in_fallback_is_json(self):
        cif_table = {'k': _s('v')}  # CIF table value
        f = _file(_block('B', scalars={'_unknown.x': [cif_table]}))
        conn = _conn()
        ingest(f, conn)
        row = _fallback(conn, 'B')[0]
        assert row[4] == 'table'
        assert json.loads(row[3]) == {'k': 'v'}

    def test_placeholder_inside_list_stored_as_plain_string(self):
        cif_list = [_ph('.'), _s('real')]
        f = _file(_block('B', scalars={'_unknown.x': [cif_list]}))
        conn = _conn()
        ingest(f, conn)
        row = _fallback(conn, 'B')[0]
        data = json.loads(row[3])
        assert data[0] == '.'

    def test_quoted_dot_inside_list_stored_with_delimiters(self):
        cif_list = [_dq('.'), _s('real')]
        f = _file(_block('B', scalars={'_unknown.x': [cif_list]}))
        conn = _conn()
        ingest(f, conn)
        row = _fallback(conn, 'B')[0]
        data = json.loads(row[3])
        assert data[0] == '"."'

    def test_decode_container_roundtrips(self):
        lst = [_s('x'), _ph('?'), {'k': _s('v')}]
        stored, _ = encode_container(lst)
        decoded = decode_container(stored)
        assert decoded == ['x', '?', {'k': 'v'}]


# ===========================================================================
# TestOnErrorCallback
# ===========================================================================

class TestOnErrorCallback:
    def test_on_error_called_for_each_error(self):
        schema = _schema_a()
        # Incompatible multi-cat loop → error
        f = _file(_block('B', loops=[
            (['_structure.id', '_atom_site.label'], [[_s('S1'), _s('C1')]]),
        ]))
        conn = _conn(schema)
        received = []
        errors = ingest(f, conn, schema, on_error=received.append)
        assert len(received) == len(errors)
        assert received == errors


# ===========================================================================
# TestMergeIntoCoverage — direct unit tests for _merge_into edge cases
# ===========================================================================

class TestMergeIntoCoverage:
    """Cover _merge_into lines: None-value skip (283) and emit-only conflict (294)."""

    def _make_table(self, schema):
        """Return the cell TableSpec from schema_a."""
        return schema.tables['structure']

    def test_incoming_none_value_not_written(self):
        # Line 283: incoming row has None for a column → skip it, keep existing
        schema = _schema_a()
        table = schema.tables['structure']
        merged: dict = {}
        counters: dict = {}
        msgs = []
        # First insert a real row
        row1 = {'_block_id': 'B', 'id': 'S1'}
        _merge_into(merged, 'structure', row1, table, counters, msgs.append)
        # Second insert: same PK, None value for 'id' (PK skipped), plus a hypothetical col
        # Since structure only has 'id' (which is PK), use cell table for a non-PK col
        cell_table = schema.tables['cell']
        cell_merged: dict = {}
        cell_counters: dict = {}
        row2 = {'_block_id': 'B', 'structure_id': 'S1', 'length_a': '5.0'}
        _merge_into(cell_merged, 'cell', row2, cell_table, cell_counters, msgs.append)
        row3 = {'_block_id': 'B', 'structure_id': 'S1', 'length_a': None}
        _merge_into(cell_merged, 'cell', row3, cell_table, cell_counters, msgs.append)
        # length_a should still be '5.0' (None incoming was skipped)
        existing = list(cell_merged['cell'].values())[0]
        assert existing['length_a'] == '5.0'
        assert not msgs

    def test_conflict_without_emit_error_uses_emit(self):
        # Line 294: no emit_error → emit(msg) on conflict
        schema = _schema_a()
        cell_table = schema.tables['cell']
        merged: dict = {}
        counters: dict = {}
        msgs = []
        row1 = {'_block_id': 'B1', 'structure_id': 'S1', 'length_a': '5.0'}
        _merge_into(merged, 'cell', row1, cell_table, counters, msgs.append)
        row2 = {'_block_id': 'B2', 'structure_id': 'S1', 'length_a': '6.0'}
        # emit_error=None (default) → emit is used for conflict
        _merge_into(merged, 'cell', row2, cell_table, counters, msgs.append)
        assert any('merge conflict' in m for m in msgs)


# ===========================================================================
# TestSetMergeConflict — cross-block Set merge conflict (lines 726-730)
# ===========================================================================

class TestSetMergeConflict:
    def test_two_blocks_conflicting_set_values_raises_ingestion_error(self):
        # Two blocks share structure.id='S1' but cell.length_a differs
        # → Set buffer flush for block B conflicts with block A's merged row
        schema = _schema_a()
        f = _file(
            _block('A', scalars={
                '_structure.id': _s('S1'),
                '_cell.length_a': _s('5.0'),
            }),
            _block('B', scalars={
                '_structure.id': _s('S1'),
                '_cell.length_a': _s('6.0'),
            }),
        )
        conn = _conn(schema)
        with pytest.raises(IngestionError):
            ingest(f, conn, schema)


# ===========================================================================
# TestPreCommitHook — _Ingester.run(_pre_commit_hook=...) (line 609)
# ===========================================================================

class TestPreCommitHook:
    def test_pre_commit_hook_called_before_commit(self):
        from pycifparse.ingestion.ingest import _Ingester
        schema = _schema_a()
        f = _file(_block('B', scalars={'_structure.id': _s('S1')}))
        conn = _conn(schema)
        hook_called = []

        def hook(ingester):
            hook_called.append(True)

        _Ingester(f, conn, schema, False, None, None).run(_pre_commit_hook=hook)
        assert hook_called == [True]
        conn.close()

    def test_pre_commit_hook_exception_rolls_back(self):
        from pycifparse.ingestion.ingest import _Ingester
        schema = _schema_a()
        f = _file(_block('B', scalars={'_structure.id': _s('S1')}))
        conn = _conn(schema)

        def bad_hook(ingester):
            raise RuntimeError('hook failure')

        with pytest.raises(RuntimeError, match='hook failure'):
            _Ingester(f, conn, schema, False, None, None).run(_pre_commit_hook=bad_hook)
        # After rollback, no rows should be in structure
        rows = _rows(conn, 'structure')
        assert rows == []
        conn.close()


# ===========================================================================
# TestSqliteInsertError — sqlite3 error during _flush insert (lines 1198-1199)
# ===========================================================================

class TestSqliteInsertError:
    def test_sqlite_error_on_insert_emits_warning(self):
        """Drop the table after schema creation so that INSERT fails."""
        from pycifparse.ingestion.ingest import _Ingester
        schema = _schema_a()
        f = _file(_block('B', scalars={'_structure.id': _s('S1')}))
        conn = _conn(schema)
        # Drop the structured table so the INSERT fails
        conn.execute('DROP TABLE "structure"')
        errors = []
        _Ingester(f, conn, schema, False, None, errors.append).run()
        assert any('sqlite3 error' in e for e in errors)
        conn.close()


# ===========================================================================
# TestFKTargetNotInSchema — FK target not in structured schema (lines 344-349)
# ===========================================================================

class TestFKKeyAbsentTargetNotInSchema:
    def test_key_fk_target_missing_from_schema_emits_and_generates_uuid(self):
        """When the FK target column is not mapped in schema.column_to_tag,
        a UUID is still generated for a key-FK column (lines 344-349 branch)."""
        # Build a minimal SchemaSpec where column_to_tag doesn't map the FK target
        from pycifparse.dictionary.schema import SchemaSpec, TableDef, ColumnDef, ForeignKeyDef
        col_pk = ColumnDef(name='structure_id', definition_id='_cell.structure_id',
                           type_contents='Text', nullable=False,
                           is_primary_key=True, is_synthetic=False,
                           linked_item_id=None)
        col_len = ColumnDef(name='length_a', definition_id='_cell.length_a',
                            type_contents='Real', nullable=True,
                            is_primary_key=False, is_synthetic=False,
                            linked_item_id=None)
        fk = ForeignKeyDef(
            source_table='cell',
            source_columns=['structure_id'],
            target_table='structure',
            target_columns=['id'],
        )
        cell_table = TableDef(
            name='cell', definition_id='_cell',
            category_class='Set',
            columns=[col_pk, col_len],
            primary_keys=['structure_id'],
            foreign_keys=[fk],
        )
        # column_to_tag does NOT map ('structure', 'id') — simulates missing target
        schema = SchemaSpec(
            tables={'cell': cell_table},
            column_to_tag={
                ('cell', 'structure_id'): '_cell.structure_id',
                ('cell', 'length_a'): '_cell.length_a',
                # ('structure', 'id') intentionally absent
            },
        )
        row = {'_block_id': 'B', 'length_a': '5.0'}
        msgs = []
        _apply_fk(row, cell_table, schema, None, {}, propagate_fk=False, emit=msgs.append)
        # When target is not in schema.column_to_tag, emits a message and leaves NULL
        sid = row.get('structure_id')
        assert sid is None  # No UUID generated when target_def_id is missing
        assert any('NULL' in m or 'not in structured' in m for m in msgs)


# ===========================================================================
# TestFillBridgeColumns — fallback chain resolution
# ===========================================================================

class TestFillBridgeColumns:
    """Unit tests for _fill_bridge_columns with multiple resolution chains."""

    def test_primary_chain_used_when_populated(self):
        from pycifparse.dictionary.schema import BridgeColumnDef
        from pycifparse.ingestion.ingest import _fill_bridge_columns

        # Two chains: primary goes A→B (via col1→pk→val_primary),
        # fallback goes A→C (via col2→pk→val_fallback).
        # Primary chain is populated — it should be used.
        merged_rows = {
            'a': {(None, 1): {'_block_id': 'blk', 'col1': 'k1', 'col2': 'k2'}},
            'b': {(None, 1): {'_block_id': 'blk', 'pk': 'k1', 'val_primary': 'PRIMARY'}},
            'c': {(None, 1): {'_block_id': 'blk', 'pk': 'k2', 'val_fallback': 'FALLBACK'}},
        }
        bd = BridgeColumnDef(
            table_name='a',
            column_name='derived',
            hops=[('col1', 'b', 'pk')],
            bridge_value_column='val_primary',
            fallback_chains=[([('col2', 'c', 'pk')], 'val_fallback')],
        )
        _fill_bridge_columns(merged_rows, [bd])
        assert merged_rows['a'][(None, 1)]['derived'] == 'PRIMARY'

    def test_fallback_chain_used_when_primary_misses(self):
        from pycifparse.dictionary.schema import BridgeColumnDef
        from pycifparse.ingestion.ingest import _fill_bridge_columns

        # Primary chain lookup returns nothing (b has no matching row).
        # Fallback chain should be used instead.
        merged_rows = {
            'a': {(None, 1): {'_block_id': 'blk', 'col1': 'k1', 'col2': 'k2'}},
            'b': {},   # primary bridge table empty
            'c': {(None, 1): {'_block_id': 'blk', 'pk': 'k2', 'val_fallback': 'FALLBACK'}},
        }
        bd = BridgeColumnDef(
            table_name='a',
            column_name='derived',
            hops=[('col1', 'b', 'pk')],
            bridge_value_column='val_primary',
            fallback_chains=[([('col2', 'c', 'pk')], 'val_fallback')],
        )
        _fill_bridge_columns(merged_rows, [bd])
        assert merged_rows['a'][(None, 1)]['derived'] == 'FALLBACK'

    def test_null_when_all_chains_miss(self):
        from pycifparse.dictionary.schema import BridgeColumnDef
        from pycifparse.ingestion.ingest import _fill_bridge_columns

        merged_rows = {
            'a': {(None, 1): {'_block_id': 'blk', 'col1': 'k1', 'col2': 'k2'}},
            'b': {},
            'c': {},
        }
        bd = BridgeColumnDef(
            table_name='a',
            column_name='derived',
            hops=[('col1', 'b', 'pk')],
            bridge_value_column='val_primary',
            fallback_chains=[([('col2', 'c', 'pk')], 'val_fallback')],
        )
        _fill_bridge_columns(merged_rows, [bd])
        assert merged_rows['a'][(None, 1)].get('derived') is None

    def test_chains_agree_no_warning_emitted(self):
        from pycifparse.dictionary.schema import BridgeColumnDef
        from pycifparse.ingestion.ingest import _fill_bridge_columns

        # Both chains resolve to the same value — no warning should be emitted.
        merged_rows = {
            'a': {(None, 1): {'_block_id': 'blk', 'col1': 'k1', 'col2': 'k2'}},
            'b': {(None, 1): {'pk': 'k1', 'val': 'SAME'}},
            'c': {(None, 1): {'pk': 'k2', 'val': 'SAME'}},
        }
        bd = BridgeColumnDef(
            table_name='a',
            column_name='derived',
            hops=[('col1', 'b', 'pk')],
            bridge_value_column='val',
            fallback_chains=[([('col2', 'c', 'pk')], 'val')],
        )
        warnings = []
        _fill_bridge_columns(merged_rows, [bd], emit=warnings.append)
        assert merged_rows['a'][(None, 1)]['derived'] == 'SAME'
        assert warnings == []

    def test_chains_disagree_warning_emitted_first_value_used(self):
        from pycifparse.dictionary.schema import BridgeColumnDef
        from pycifparse.ingestion.ingest import _fill_bridge_columns

        # Chains resolve to different values — warning emitted, first value used.
        merged_rows = {
            'a': {(None, 1): {'_block_id': 'blk', 'col1': 'k1', 'col2': 'k2'}},
            'b': {(None, 1): {'pk': 'k1', 'val': 'FIRST'}},
            'c': {(None, 1): {'pk': 'k2', 'val': 'SECOND'}},
        }
        bd = BridgeColumnDef(
            table_name='a',
            column_name='derived',
            hops=[('col1', 'b', 'pk')],
            bridge_value_column='val',
            fallback_chains=[([('col2', 'c', 'pk')], 'val')],
        )
        warnings = []
        _fill_bridge_columns(merged_rows, [bd], emit=warnings.append)
        assert merged_rows['a'][(None, 1)]['derived'] == 'FIRST'
        assert len(warnings) == 1
        assert 'disagree' in warnings[0]
        assert 'FIRST' in warnings[0]
        assert 'SECOND' in warnings[0]


def _cif_pow_schema():
    """Load cif_pow.dic and return a generated SchemaSpec (cached per process)."""
    from pathlib import Path
    from pycifparse.dictionary.loader import DictionaryLoader, directory_resolver
    from pycifparse.dictionary.schema import generate_schema
    dic_path = Path('data/dictionaries/cif_pow.dic')
    loader = DictionaryLoader(resolver=directory_resolver(dic_path.parent))
    return generate_schema(loader.load(dic_path.read_text(encoding='utf-8')))


def _ingest_cif_file(cif_path_str, schema):
    """Parse *cif_path_str*, ingest into an in-memory DB, return (conn, errors)."""
    from pathlib import Path
    from pycifparse import build, ingest
    from pycifparse.dictionary.schema_apply import apply_schema, apply_fallback_schema
    cif, _ = build(Path(cif_path_str).read_text(encoding='utf-8'))
    conn = sqlite3.connect(':memory:')
    conn.execute('PRAGMA foreign_keys = OFF')
    apply_schema(conn, schema)
    apply_fallback_schema(conn)
    errors = ingest(cif, conn, schema)
    return conn, errors


class TestTransitiveBridgeFallback:
    """Integration tests: bridge column resolution across alternative paths."""

    def test_radiation_id_resolved_via_instr_path(self):
        # transitive_01.cif: diffrn.diffrn_radiation_id absent; pd_instr path used.
        schema = _cif_pow_schema()
        conn, _ = _ingest_cif_file('tests/cif_files/transitive_01.cif', schema)

        rows = conn.execute(
            'SELECT "radiation_id" FROM "pd_peak" WHERE "radiation_id" IS NOT NULL'
        ).fetchall()
        assert rows, "radiation_id was not resolved for any pd_peak row"
        assert all(r[0] == 'CR_RAD' for r in rows)

    def test_two_patterns_each_use_different_bridge_path(self):
        # transitive_02.cif: pattern_1 resolves via pd_instr (no diffrn_radiation_id),
        # pattern_2 resolves via diffrn (no pd_instr.radiation_id).
        # Both should resolve correctly with no disagreement warning.
        schema = _cif_pow_schema()
        conn, errors = _ingest_cif_file('tests/cif_files/transitive_02.cif', schema)

        disagree_warnings = [e for e in errors if 'disagree' in e]
        assert disagree_warnings == [], f"unexpected disagreement warnings: {disagree_warnings}"

        peak_radiation = dict(conn.execute(
            'SELECT "diffractogram_id", "radiation_id" FROM "pd_peak"'
            ' WHERE "radiation_id" IS NOT NULL'
        ).fetchall())

        assert peak_radiation.get('pattern_1') == 'CR_RAD', \
            "pattern_1 peaks should resolve radiation_id via pd_instr path"
        assert peak_radiation.get('pattern_2') == 'copper', \
            "pattern_2 peaks should resolve radiation_id via diffrn path"
