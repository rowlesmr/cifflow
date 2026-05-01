"""
Unit tests for ingest().
"""

import json
import uuid

import duckdb
import pytest

from pycifparse import ingest, IngestionError
from pycifparse.cifmodel.model import CifBlock, CifFile
from pycifparse.dictionary.ddlm_item import DdlmItem
from pycifparse.dictionary.ddlm_parser import DdlmDictionary
from pycifparse.dictionary.schema import generate_schema
from pycifparse.ingestion.ingest import (
    build_su_map,
    build_tag_to_column,
    decode_container,
    encode_container,
    encode_value,
    split_su,
)
from pycifparse.types import ValueType


# ---------------------------------------------------------------------------
# Value helpers (plain strings in the new encoding)
# ---------------------------------------------------------------------------

def _s(value):
    return value

def _ph(value):
    return value

def _dq(value):
    # Only '.' and '?' need sentinel encoding; other double-quoted values are
    # indistinguishable from bare strings in the new model.
    if value in ('.', '?'):
        return f'"{value}"'
    return value


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
    scalars: {tag: str}  or  {tag: [str, ...]}
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
# DB helpers
# ---------------------------------------------------------------------------

def _do_ingest(f, schema=None, **kw):
    """Ingest CifFile f into a fresh DuckDB; return (conn, errors)."""
    return ingest(f, None, schema, **kw)


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
            [block_id]
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
            [block_id]
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
        assert decode_container(stored) == ['a', 'b']

    def test_dict_value(self):
        d = {'key': _s('val')}
        stored, vtype = encode_value(d)
        assert vtype == 'table'
        assert decode_container(stored) == {'key': 'val'}


# ===========================================================================
# TestEncodeContainer
# ===========================================================================

class TestEncodeContainer:
    def test_placeholder_in_list_stored_as_string(self):
        lst = [_ph('.'), _s('real')]
        stored, _ = encode_container(lst)
        data = decode_container(stored)
        assert data[0] == '.'     # PLACEHOLDER stored as plain '.'
        assert data[1] == 'real'

    def test_quoted_dot_in_list_stored_with_delimiters(self):
        lst = [_dq('.'), _s('x')]
        stored, _ = encode_container(lst)
        data = decode_container(stored)
        assert data[0] == '"."'

    def test_nested_container(self):
        lst = [{'a': _s('1')}, {'a': _s('2')}]
        stored, vtype = encode_container(lst)
        assert vtype == 'list'
        data = decode_container(stored)
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
        conn, _ = _do_ingest(f)
        rows = _fallback(conn, 'B')
        tags = {r[2] for r in rows}
        assert '_cell.length_a' in tags
        assert '_cell.length_b' in tags

    def test_scalar_row_id_is_1(self):
        f = _file(_block('B', scalars={'_cell.length_a': _s('5.4')}))
        conn, _ = _do_ingest(f)
        row = _fallback(conn, 'B')[0]
        assert row[1] == 1  # _row_id

    def test_loop_rows_to_fallback(self):
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.x_fract'],
             [[_s('C1'), _s('0.1')], [_s('C2'), _s('0.2')]]),
        ]))
        conn, _ = _do_ingest(f)
        rows = _fallback(conn, 'B')
        assert len(rows) == 4  # 2 iterations × 2 tags

    def test_loop_row_id_increments_per_iteration(self):
        f = _file(_block('B', loops=[
            (['_atom_site.label'], [[_s('C1')], [_s('C2')]]),
        ]))
        conn, _ = _do_ingest(f)
        rows = _fallback(conn, 'B')
        row_ids = [r[1] for r in rows]
        assert row_ids == [1, 2]  # iteration 0 → row_id=1, iteration 1 → row_id=2

    def test_loop_id_assigned(self):
        f = _file(_block('B', loops=[
            (['_a.x'], [[_s('1')], [_s('2')]]),
        ]))
        conn, _ = _do_ingest(f)
        rows = _fallback(conn, 'B')
        assert all(r[5] == 1 for r in rows)  # loop_id = 1

    def test_col_index_assigned(self):
        f = _file(_block('B', loops=[
            (['_a.x', '_a.y'], [[_s('1'), _s('2')]]),
        ]))
        conn, _ = _do_ingest(f)
        rows = sorted(_fallback(conn, 'B'), key=lambda r: r[6])  # sort by col_index
        assert rows[0][6] == 0  # _a.x col_index
        assert rows[1][6] == 1  # _a.y col_index

    def test_scalar_loop_id_is_null(self):
        f = _file(_block('B', scalars={'_cell.a': _s('5')}))
        conn, _ = _do_ingest(f)
        row = _fallback(conn, 'B')[0]
        assert row[5] is None  # loop_id

    def test_membership_row_assumed(self):
        f = _file(_block('B', scalars={'_cell.a': _s('5')}))
        conn, _ = _do_ingest(f)
        rows = _membership(conn, 'B')
        assert len(rows) == 1
        assert rows[0][2] == 'assumed'

    def test_two_loops_get_different_loop_ids(self):
        f = _file(_block('B', loops=[
            (['_a.x'], [[_s('1')]]),
            (['_b.y'], [[_s('2')]]),
        ]))
        conn, _ = _do_ingest(f)
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
        conn, _ = _do_ingest(f, schema)
        rows = _rows(conn, 'structure', ['id'])
        assert rows == [('S1',)]

    def test_unmapped_tag_to_fallback(self):
        """Tag not in schema → _cif_fallback."""
        schema = _schema_a()
        f = _file(_block('B', scalars={'_unknown.tag': _s('val')}))
        conn, _ = _do_ingest(f, schema)
        fb = _fallback(conn, 'B')
        assert any(r[2] == '_unknown.tag' for r in fb)

    def test_mapped_tag_not_in_fallback(self):
        schema = _schema_a()
        f = _file(_block('B', scalars={'_structure.id': _s('S1')}))
        conn, _ = _do_ingest(f, schema)
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
        conn, _ = _do_ingest(f, schema)
        rows = _rows(conn, 'atom_site', ['label', 'x_fract'])
        assert set(rows) == {('C1', '0.1'), ('C2', '0.2')}

    def test_loop_row_id_increments(self):
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label'],
             [[_s('C1')], [_s('C2')]]),
        ]))
        conn, _ = _do_ingest(f, schema)
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
        conn, _ = _do_ingest(f, schema)
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
        conn, errors = _do_ingest(f, schema)
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
        conn, errors = _do_ingest(_file(b1, b2), schema)
        warns = [e for e in errors if 'deprecated' in e]
        assert len(warns) == 2  # one per block

    def test_alias_and_deprecated_both_applied(self):
        """Alias resolved AND deprecation warning emitted for same tag."""
        schema = _schema_alias_deprecated()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.old_x'], [[_s('C1'), _s('0.5')]]),
        ]))
        conn, errors = _do_ingest(f, schema)
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
        conn, _ = _do_ingest(f)
        row = _fallback(conn, 'B')[0]
        assert row[3] == '.'
        assert row[4] == 'placeholder'

    def test_placeholder_question_in_fallback(self):
        f = _file(_block('B', scalars={'_unknown.x': _ph('?')}))
        conn, _ = _do_ingest(f)
        row = _fallback(conn, 'B')[0]
        assert row[3] == '?'
        assert row[4] == 'placeholder'

    def test_quoted_dot_in_fallback_distinguished(self):
        f = _file(_block('B', scalars={'_unknown.x': _dq('.')}))
        conn, _ = _do_ingest(f)
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
        conn, _ = _do_ingest(f, schema)
        rows = _rows(conn, 'atom_site', ['label', 'x_fract', 'x_fract_su'])
        assert rows[0] == ('C1', '0.123', '0.004')

    def test_su_absent_leaves_su_column_null(self):
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.x_fract'],
             [[_s('C1'), _s('0.123')]]),
        ]))
        conn, _ = _do_ingest(f, schema)
        rows = _rows(conn, 'atom_site', ['x_fract', 'x_fract_su'])
        assert rows[0] == ('0.123', None)

    def test_su_attempted_on_all_string_values(self):
        # In the new model, quoting is not preserved for non-sentinel values,
        # so SU splitting is attempted on all string values matching the pattern.
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site.x_fract'],
             [[_s('C1'), _s('0.123(4)')]]),
        ]))
        conn, _ = _do_ingest(f, schema)
        rows = _rows(conn, 'atom_site', ['x_fract', 'x_fract_su'])
        assert rows[0] == ('0.123', '0.004')

    def test_su_not_attempted_in_fallback(self):
        f = _file(_block('B', loops=[
            (['_atom_site.x_fract'], [[_s('0.123(4)')]]),
        ]))
        conn, _ = _do_ingest(f)
        row = _fallback(conn, 'B')[0]
        assert row[3] == '0.123(4)'  # stored raw; no SU split


# ===========================================================================
# TestKeylessLoop
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
        conn, _ = _do_ingest(f, schema)
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
        # propagate_fk=True so the non-key FK column structure_id is filled from
        # the accumulator — this verifies that real accumulator values propagate
        # to every iteration (unlike generated UUIDs which must not persist).
        conn, _ = _do_ingest(f, schema, propagate_fk=True)
        # Both rows must reference the same structure id 'S1'
        rows = conn.execute(
            "SELECT x_fract, structure_id FROM atom_site ORDER BY x_fract"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][1] == rows[1][1] == 'S1'


# ===========================================================================
# TestMerge
# ===========================================================================

class TestMerge:
    def test_distinct_pks_from_two_blocks(self):
        schema = _schema_a()
        b1 = _block('B1', loops=[
            (['_atom_site.label'], [[_s('C1')]]),
        ])
        b2 = _block('B2', loops=[
            (['_atom_site.label'], [[_s('C2')]]),
        ])
        conn, _ = _do_ingest(_file(b1, b2), schema)
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
        conn, _ = _do_ingest(_file(b1, b2), schema)
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
        conn, _ = _do_ingest(_file(b1, b2), schema)
        rows = _rows(conn, 'atom_site', ['_block_id', 'label', 'x_fract'])
        assert rows == [('B1', 'C1', '0.5')]

    def test_row_id_does_not_reset_between_blocks(self):
        schema = _schema_a()
        b1 = _block('B1', loops=[
            (['_atom_site.label'], [[_s('C1')]]),
        ])
        b2 = _block('B2', loops=[
            (['_atom_site.label'], [[_s('C2')]]),
        ])
        conn, _ = _do_ingest(_file(b1, b2), schema)
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
        conn, _ = _do_ingest(f, schema)
        rows = _rows(conn, 'cell', ['structure_id'])
        assert rows[0][0] == 'S1'

    def test_key_fk_propagated_from_loop(self):
        """atom_site.label is known from a same-loop value when in a multi-category loop."""
        schema = _schema_compat_multi()
        f = _file(_block('B', loops=[
            (['_atom_site.label', '_atom_site_aniso.u_11'],
             [[_s('C1'), _s('0.01')]]),
        ]))
        conn, _ = _do_ingest(f, schema)
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
        conn, _ = _do_ingest(f, schema)
        rows = _rows(conn, 'atom_site', ['label', 'x_fract'])
        # merged: same PK label=C1; x_fract filled from second loop
        assert ('C1', '0.5') in rows

    def test_non_key_fk_not_propagated_by_default(self):
        """_atom_site.structure_id is a non-key FK; should stay NULL if not in CIF."""
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label'], [[_s('C1')]]),
        ]))
        conn, _ = _do_ingest(f, schema, propagate_fk=False)
        rows = _rows(conn, 'atom_site', ['label', 'structure_id'])
        assert rows[0] == ('C1', None)

    def test_non_key_fk_propagated_when_flag_set(self):
        schema = _schema_a()
        f = _file(_block('B', scalars={
            '_structure.id': _s('S1'),
        }, loops=[
            (['_atom_site.label'], [[_s('C1')]]),
        ]))
        conn, _ = _do_ingest(f, schema, propagate_fk=True)
        rows = _rows(conn, 'atom_site', ['label', 'structure_id'])
        assert rows[0] == ('C1', 'S1')


# ===========================================================================
# TestSetTable
# ===========================================================================

class TestSetTable:
    def test_set_table_one_row_per_block(self):
        schema = _schema_a()
        b1 = _block('B1', scalars={'_structure.id': _s('S1')})
        b2 = _block('B2', scalars={'_structure.id': _s('S2')})
        conn, _ = _do_ingest(_file(b1, b2), schema)
        rows = _rows(conn, 'structure', ['id'])
        assert set(rows) == {('S1',), ('S2',)}

    def test_set_table_block_id_populated(self):
        schema = _schema_a()
        f = _file(_block('MY_BLOCK', scalars={'_structure.id': _s('S1')}))
        conn, _ = _do_ingest(f, schema)
        rows = _rows(conn, 'structure', ['_block_id', 'id'])
        assert rows[0] == ('MY_BLOCK', 'S1')

    def test_keyless_set_table_pycifparse_id_is_uuid(self):
        schema = _schema_keyless_set()
        f = _file(_block('B', scalars={'_props.name': _s('val')}))
        conn, _ = _do_ingest(f, schema)
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
        conn, _ = _do_ingest(f, schema)
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
        conn, _ = _do_ingest(f, schema)
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
        conn, _ = _do_ingest(f, schema)
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
        conn, _ = _do_ingest(f, schema)
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
        conn, _ = _do_ingest(f, schema)
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
        conn, errors = _do_ingest(f, schema)
        assert any('incompatible' in e for e in errors)
        fb = _fallback(conn, 'B')
        tags = {r[2] for r in fb}
        assert '_structure.id' in tags
        assert '_atom_site.label' in tags
        # DuckDB only creates final tables when rows are present;
        # incompatible tags go to fallback so no structured rows exist.
        all_tables = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()}
        assert 'structure' not in all_tables or _rows(conn, 'structure', ['id']) == []
        assert 'atom_site' not in all_tables or _rows(conn, 'atom_site', ['label']) == []


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
        conn, _ = _do_ingest(f, schema)
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
        conn, _ = _do_ingest(f, schema)
        rows = _membership(conn, 'B')
        assert rows[0][1] == 'DS1'
        assert rows[0][2] == 'dataset'

    def test_dataset_block_multiple_ids_one_row_each(self):
        f = _file(_block('B', loops=[
            (['_audit_dataset.id'], [[_s('DS1')], [_s('DS2')]]),
        ]))
        conn, _ = _do_ingest(f)
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
        conn, _ = _do_ingest(f, schema)
        rows = _membership(conn, 'B')
        assert rows[0][1] == ''
        assert rows[0][2] == 'uuid'

    def test_general_block_non_uuid_pks_gets_assumed(self):
        schema = _schema_a()
        f = _file(_block('B', loops=[
            (['_atom_site.label'], [[_s('C1')]]),
        ]))
        conn, _ = _do_ingest(f, schema)
        rows = _membership(conn, 'B')
        assert rows[0][2] == 'assumed'

    def test_general_block_no_rows_gets_assumed(self):
        f = _file(_block('B', scalars={'_unknown.x': _s('val')}))
        conn, _ = _do_ingest(f)
        rows = _membership(conn, 'B')
        assert rows[0][2] == 'assumed'

    def test_incompatible_datasets_raises_value_error(self):
        f = _file(
            _block('B1', loops=[(['_audit_dataset.id'], [[_s('DS1')]])]),
            _block('B2', loops=[(['_audit_dataset.id'], [[_s('DS2')]])]),
        )
        with pytest.raises(ValueError, match='incompatible'):
            _do_ingest(f)

    def test_incompatible_datasets_no_rows_written(self):
        f = _file(
            _block('B1', loops=[(['_audit_dataset.id'], [[_s('DS1')]])]),
            _block('B2', loops=[(['_audit_dataset.id'], [[_s('DS2')]])]),
        )
        with pytest.raises(ValueError):
            _do_ingest(f)

    def test_dataset_id_filter_selects_matching_blocks(self):
        f = _file(
            _block('B1', loops=[(['_audit_dataset.id'], [[_s('DS1')]])]),
            _block('B2', loops=[(['_audit_dataset.id'], [[_s('DS2')]])]),
        )
        conn, _ = _do_ingest(f, dataset_id='DS1')
        rows = _membership(conn)
        block_ids = {r[0] for r in rows}
        assert 'B1' in block_ids
        assert 'B2' not in block_ids

    def test_dataset_id_not_found_raises_value_error(self):
        f = _file(_block('B1', loops=[(['_audit_dataset.id'], [[_s('DS1')]])]))
        with pytest.raises(ValueError):
            _do_ingest(f, dataset_id='UNKNOWN')

    def test_dataset_id_missing_from_all_blocks_raises_value_error(self):
        """dataset_id provided but CifFile has no dataset blocks at all."""
        f = _file(_block('B', scalars={'_cell.a': _s('5')}))
        with pytest.raises(ValueError):
            _do_ingest(f, dataset_id='DS1')

    def test_general_block_included_with_dataset_filter(self):
        """General blocks (no _audit_dataset.id) are always included."""
        f = _file(
            _block('DS_BLOCK', loops=[(['_audit_dataset.id'], [[_s('DS1')]])]),
            _block('GEN_BLOCK', scalars={'_unknown.x': _s('val')}),
        )
        conn, _ = _do_ingest(f, dataset_id='DS1')
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
        conn, _ = _do_ingest(f, dataset_id='DS1')
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
        conn, _ = _do_ingest(f, schema)
        vrows = _validation(conn)
        assert any(r[0] == 'uuid_regime' and r[1] == 'Warning' for r in vrows)

    def test_uuid_pks_no_uuid_regime_warning(self):
        schema = _schema_a()
        uid = str(uuid.uuid4())
        f = _file(_block('B', loops=[
            (['_atom_site.label'], [[_s(uid)]]),
        ]))
        conn, _ = _do_ingest(f, schema)
        vrows = _validation(conn)
        assert not any(r[0] == 'uuid_regime' for r in vrows)

    def test_uuid_reference_check_not_implemented(self):
        """Stage 4 stub: no uuid_reference_check rows written."""
        schema = _schema_a()
        f = _file(
            _block('DS', loops=[(['_audit_dataset.id'], [[_s('DS1')]])]),
            _block('GEN', loops=[(['_atom_site.label'], [[_s(str(uuid.uuid4()))]])]),
        )
        conn, _ = _do_ingest(f, schema)
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
        conn, _ = _do_ingest(f)
        row = _fallback(conn, 'B')[0]
        assert row[4] == 'list'
        assert decode_container(row[3]) == ['a', 'b']

    def test_table_value_in_fallback_is_json(self):
        cif_table = {'k': _s('v')}  # CIF table value
        f = _file(_block('B', scalars={'_unknown.x': [cif_table]}))
        conn, _ = _do_ingest(f)
        row = _fallback(conn, 'B')[0]
        assert row[4] == 'table'
        assert decode_container(row[3]) == {'k': 'v'}

    def test_placeholder_inside_list_stored_as_plain_string(self):
        cif_list = [_ph('.'), _s('real')]
        f = _file(_block('B', scalars={'_unknown.x': [cif_list]}))
        conn, _ = _do_ingest(f)
        row = _fallback(conn, 'B')[0]
        data = decode_container(row[3])
        assert data[0] == '.'

    def test_quoted_dot_inside_list_stored_with_delimiters(self):
        cif_list = [_dq('.'), _s('real')]
        f = _file(_block('B', scalars={'_unknown.x': [cif_list]}))
        conn, _ = _do_ingest(f)
        row = _fallback(conn, 'B')[0]
        data = decode_container(row[3])
        assert data[0] == '"."'

    def test_decode_container_roundtrips(self):
        lst = [_s('x'), _ph('?'), {'k': _s('v')}]
        stored, _ = encode_container(lst)
        decoded = decode_container(stored)
        assert decoded == ['x', '?', {'k': 'v'}]
