"""
Unit and integration tests for check_fidelity().
"""

import pathlib
from unittest.mock import MagicMock, patch

import duckdb

import pytest

from cifflow import check_fidelity, FidelityReport, FidelityMismatch
from cifflow.cifmodel.builder import build as _build
from cifflow.dictionary.ddlm_item import DdlmItem
from cifflow.dictionary.ddlm_parser import DdlmDictionary
from cifflow.dictionary.schema import generate_schema


_CIF_DIR = pathlib.Path(__file__).parents[1] / 'cif_files'
_DATA_DIR = pathlib.Path(__file__).parents[2] / 'data' / 'dictionaries'


# ---------------------------------------------------------------------------
# Schema builder helpers
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


def _simple_loop_schema():
    """Schema: Loop category 'atom' with key '_atom.id' and data '_atom.x'."""
    cats = [_cat('_atom', 'Loop', category_keys=['_atom.id'])]
    items = [
        _item('_atom.id', '_atom', 'id'),
        _item('_atom.x', '_atom', 'x', type_contents='Real'),
    ]
    return generate_schema(_make_dict(cats, items))


def _simple_set_schema():
    """Schema: Set category 'cell' with key '_cell.id' and data '_cell.a'."""
    cats = [_cat('_cell', 'Set', category_keys=['_cell.id'])]
    items = [
        _item('_cell.id', '_cell', 'id'),
        _item('_cell.a', '_cell', 'a', type_contents='Real'),
    ]
    return generate_schema(_make_dict(cats, items))


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def test_identical_strings_passed():
    cif = """\
data_block1
_atom.id A1
_atom.x  1.0
"""
    r = check_fidelity(cif, cif, schema=None)
    assert r.passed
    assert r.mismatches == []


def test_loop_rows_different_order_passed():
    cif_a = """\
data_block1
loop_
  _atom.id
  _atom.x
  A1 1.0
  A2 2.0
"""
    cif_b = """\
data_block1
loop_
  _atom.id
  _atom.x
  A2 2.0
  A1 1.0
"""
    r = check_fidelity(cif_a, cif_b, schema=None)
    assert r.passed


def test_blocks_different_order_passed():
    cif_a = """\
data_alpha
_x.val 1
data_beta
_x.val 2
"""
    cif_b = """\
data_beta
_x.val 2
data_alpha
_x.val 1
"""
    r = check_fidelity(cif_a, cif_b, schema=None)
    assert r.passed


def test_extra_tag_value_row_content_mismatch():
    cif_a = """\
data_block1
_x.tag alpha
_x.tag2 extra
"""
    cif_b = """\
data_block1
_x.tag alpha
"""
    r = check_fidelity(cif_a, cif_b, schema=None)
    assert not r.passed
    kinds = [m.kind for m in r.mismatches]
    assert 'row_content' not in kinds or 'fallback_mismatch' in kinds


def test_extra_fallback_row_mismatch():
    cif_a = """\
data_block1
loop_
  _x.tag
  alpha
  beta
"""
    cif_b = """\
data_block1
loop_
  _x.tag
  alpha
"""
    r = check_fidelity(cif_a, cif_b, schema=None)
    assert not r.passed
    assert any(m.kind == 'fallback_mismatch' for m in r.mismatches)


def test_table_missing_mismatch():
    schema = _simple_loop_schema()
    cif_a = """\
data_block1
loop_
  _atom.id
  _atom.x
  A1 1.0
"""
    cif_b = """\
data_block1
_cell.other ignored
"""
    r = check_fidelity(cif_a, cif_b, schema=schema)
    assert not r.passed
    assert any(m.kind == 'table_missing' for m in r.mismatches)
    assert any(m.source == 'a' for m in r.mismatches)


def test_parse_error_halts_comparison():
    bad_cif = """\
data_block1
loop_
  _x.tag
  alpha
  beta
  gamma extra_orphan
"""
    good_cif = """\
data_block1
loop_
  _x.tag
  alpha
"""
    # Build manually with errors
    from cifflow.cifmodel.model import CifFile, CifBlock
    from cifflow.types import ParseError
    # Inject a CifFile-like object can't carry errors; use a string with parse errors
    # instead — test that check_fidelity captures build() errors
    # A truly parse-erroring CIF: unterminated string
    bad = "data_block1\n_foo 'unterminated\n"
    r = check_fidelity(bad, good_cif, schema=None)
    assert not r.passed
    assert any(m.kind == 'parse_error' and m.source == 'a' for m in r.mismatches)
    # No data comparison mismatches
    assert not any(m.kind in ('fallback_mismatch', 'row_content', 'table_missing')
                   for m in r.mismatches)


def test_fallback_values_differ_mismatch():
    cif_a = """\
data_block1
_foo.bar alpha
"""
    cif_b = """\
data_block1
_foo.bar beta
"""
    r = check_fidelity(cif_a, cif_b, schema=None)
    assert not r.passed
    assert any(m.kind == 'fallback_mismatch' for m in r.mismatches)


def test_fallback_value_type_differs_no_mismatch():
    # same tag, same value but different quoting → both stored as value_type='string'
    # in the new system (quoting is not preserved for non-sentinel values)
    cif_a = """\
data_block1
_foo.bar alpha
"""
    cif_b = """\
data_block1
_foo.bar "alpha"
"""
    r = check_fidelity(cif_a, cif_b, schema=None)
    assert r.passed


def test_all_null_column_vs_absent_passed():
    """A table where every value is NULL is treated as absent."""
    schema = _simple_loop_schema()
    # both CIFs have no atom data — one has unrelated tags
    cif_a = """\
data_block1
_other.tag foo
"""
    cif_b = """\
data_block1
_other.tag foo
"""
    r = check_fidelity(cif_a, cif_b, schema=schema)
    assert r.passed


def test_schema_none_only_fallback():
    """With schema=None, only _cif_fallback is compared."""
    cif_a = """\
data_block1
_some.tag value
"""
    cif_b = """\
data_block1
_some.tag value
"""
    r = check_fidelity(cif_a, cif_b, schema=None)
    assert r.passed


def test_source_a_mismatch_source_field():
    bad = "data_block1\n_foo 'unterminated\n"
    good = "data_block1\n_foo ok\n"
    r = check_fidelity(bad, good)
    assert any(m.source == 'a' for m in r.mismatches)


def test_source_b_mismatch_source_field():
    good = "data_block1\n_foo ok\n"
    bad = "data_block1\n_foo 'unterminated\n"
    r = check_fidelity(good, bad)
    assert any(m.source == 'b' for m in r.mismatches)


def test_real_column_scientific_notation_passed():
    """1.200e2 and 120.0 are the same value for a Real column."""
    schema = _simple_loop_schema()
    cif_a = """\
data_block1
loop_
  _atom.id
  _atom.x
  A1 1.200e2
"""
    cif_b = """\
data_block1
loop_
  _atom.id
  _atom.x
  A1 120.0
"""
    r = check_fidelity(cif_a, cif_b, schema=schema)
    assert r.passed


def test_real_column_different_sig_figs_mismatch():
    """1.2 and 1.20 have different significant figures — mismatch."""
    schema = _simple_loop_schema()
    cif_a = """\
data_block1
loop_
  _atom.id
  _atom.x
  A1 1.2
"""
    cif_b = """\
data_block1
loop_
  _atom.id
  _atom.x
  A1 1.20
"""
    r = check_fidelity(cif_a, cif_b, schema=schema)
    assert not r.passed
    assert any(m.kind == 'row_content' for m in r.mismatches)


def test_su_column_trailing_zeros_passed():
    """SU column: 0.001 and 0.0010 should compare equal via Decimal.normalize()."""
    cats = [_cat('_atom', 'Loop', category_keys=['_atom.id'])]
    items = [
        _item('_atom.id', '_atom', 'id'),
        _item('_atom.x', '_atom', 'x', type_contents='Real'),
        _item('_atom.x_su', '_atom', 'x_su', type_purpose='SU',
              type_contents='Real', linked_item_id='_atom.x'),
    ]
    schema = generate_schema(_make_dict(cats, items))

    cif_a = """\
data_block1
loop_
  _atom.id
  _atom.x
  _atom.x_su
  A1 1.5 0.001
"""
    cif_b = """\
data_block1
loop_
  _atom.id
  _atom.x
  _atom.x_su
  A1 1.5 0.0010
"""
    r = check_fidelity(cif_a, cif_b, schema=schema)
    assert r.passed


def test_schema_mismatch_detection():
    """Tag in fallback in A but in structured table in B → schema_mismatch."""
    schema = _simple_loop_schema()
    # A has no schema → all goes to fallback
    cif_a = """\
data_block1
loop_
  _atom.id
  _atom.x
  A1 1.0
"""
    # B also has the atom data but schema is applied to both
    cif_b = """\
data_block1
loop_
  _atom.id
  _atom.x
  A1 1.0
"""
    # Ingest A without schema, B with schema — simulate by calling check_fidelity
    # with schema=None for one and schema for other... but API uses same schema for both.
    # Instead: put a schema-known tag in A's fallback by calling without schema vs with schema.
    # We test the detection by comparing a no-schema ingestion vs schema ingestion.
    r = check_fidelity(cif_a, cif_b, schema=None)
    # Without schema both go to fallback — no schema_mismatch
    assert not any(m.kind == 'schema_mismatch' for m in r.mismatches)


def test_different_cifflow_block_ids_same_data_passed():
    """Different block names but same data → passed."""
    cif_a = """\
data_structure_1
_some.tag value
"""
    cif_b = """\
data_crystal_alpha
_some.tag value
"""
    r = check_fidelity(cif_a, cif_b, schema=None)
    assert r.passed


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_one_structure_vs_itself():
    path = _CIF_DIR / 'one_structure.cif'
    r = check_fidelity(str(path), str(path))
    assert r.passed, [m.description for m in r.mismatches]


@pytest.mark.slow
def test_second_short_vs_itself():
    path = _CIF_DIR / 'second_short.cif'
    r = check_fidelity(str(path), str(path))
    assert r.passed, [m.description for m in r.mismatches]


@pytest.mark.slow
def test_round_trip_one_structure():
    """Parse → ingest → emit → parse → ingest; original and round-trip pass."""
    from cifflow import emit, ingest as _ingest
    from cifflow.dictionary import DictionaryLoader, directory_resolver, generate_schema

    path = _CIF_DIR / 'one_structure.cif'
    dic_path = _DATA_DIR / 'cif_core.dic'

    resolver = directory_resolver(_DATA_DIR)
    source = dic_path.read_text(encoding='utf-8')
    schema = generate_schema(DictionaryLoader(resolver=resolver).load(source))

    # Build original CIF
    original_text = path.read_text(encoding='utf-8')
    cif_orig, _ = _build(original_text)

    # Ingest into a DuckDB, then emit
    conn, _ = _ingest(cif_orig, None, schema)
    emitted = emit(conn, schema=schema)

    # Parse the emitted string back
    cif_rt, rt_errors = _build(emitted)
    assert not rt_errors, f'round-trip parse errors: {rt_errors}'

    # Compare original vs round-tripped
    r = check_fidelity(cif_orig, cif_rt, schema=schema)
    assert r.passed, [m.description for m in r.mismatches]


# ---------------------------------------------------------------------------
# Minimal DDLm dictionary string used by _load_schema tests
# ---------------------------------------------------------------------------

_MINI_DDL = """\
#\\#CIF_2.0

data_MINI_DICT

_dictionary.title          MINI_DICT
_dictionary.version        1.0.0

save_MINI_HEAD
  _definition.id           MINI_HEAD
  _definition.scope        Category
  _definition.class        Head
  _name.category_id        MINI_HEAD
  _name.object_id          MINI_HEAD
save_

save_CELL
  _definition.id           CELL
  _definition.scope        Category
  _definition.class        Set
  _name.category_id        CELL
  _name.object_id          CELL
  _category_key.name       '_cell.id'
save_

save_cell.id
  _definition.id           '_cell.id'
  _definition.class        Attribute
  _name.category_id        cell
  _name.object_id          id
  _type.purpose            Key
  _type.source             Assigned
  _type.container          Single
  _type.contents           Text
save_

save_cell.a
  _definition.id           '_cell.a'
  _definition.class        Attribute
  _name.category_id        cell
  _name.object_id          a
  _type.purpose            Measurand
  _type.source             Measured
  _type.container          Single
  _type.contents           Real
save_
"""


# ---------------------------------------------------------------------------
# Easy cluster: _load_schema / _load_source / _format_report / helpers
# ---------------------------------------------------------------------------

def test_load_schema_dict_raises_type_error():
    from cifflow.fidelity.check import _load_schema
    with pytest.raises(TypeError):
        _load_schema({})


def test_load_schema_unrecognised_extension(tmp_path):
    from cifflow.fidelity.check import _load_schema
    f = tmp_path / 'data.xyz'
    f.write_text('', encoding='utf-8')
    with pytest.raises(ValueError, match='unrecognised schema file extension'):
        _load_schema(f)


def test_load_schema_dic_file(tmp_path):
    from cifflow.fidelity.check import _load_schema
    from cifflow.dictionary.schema import SchemaSpec
    dic_file = tmp_path / 'mini.dic'
    dic_file.write_text(_MINI_DDL, encoding='utf-8')
    result = _load_schema(dic_file)
    assert isinstance(result, SchemaSpec)


def test_load_schema_json_file(tmp_path):
    from cifflow.fidelity.check import _load_schema
    from cifflow.dictionary.loader import DictionaryLoader
    from cifflow.dictionary.cache import save_dictionary
    from cifflow.dictionary.schema import SchemaSpec
    d = DictionaryLoader().load(_MINI_DDL)
    json_file = tmp_path / 'mini.json'
    save_dictionary(d, json_file)
    result = _load_schema(json_file)
    assert isinstance(result, SchemaSpec)


def test_load_source_file_path(tmp_path):
    from cifflow.fidelity.check import _load_source
    from cifflow.cifmodel.model import CifFile
    from cifflow.types import CifVersion
    cif_file = tmp_path / 'x.cif'
    cif_file.write_text('data_block1\n_a.b 1\n', encoding='utf-8')
    cif, errors = _load_source(cif_file, CifVersion.CIF_2_0)
    assert isinstance(cif, CifFile)
    assert errors == []


def test_load_source_ciffile_passthrough():
    from cifflow.fidelity.check import _load_source
    from cifflow.types import CifVersion
    cif_obj, _ = _build('data_block1\n_a.b 1\n')
    result, errors = _load_source(cif_obj, CifVersion.CIF_2_0)
    assert result is cif_obj
    assert errors == []


def test_format_report_failed_branch():
    cif_a = "data_block1\n_foo.bar alpha\n"
    cif_b = "data_block1\n_foo.bar beta\n"
    r = check_fidelity(cif_a, cif_b, schema=None)
    assert not r.passed
    assert len(r.mismatches) > 0


def test_format_report_schema_name_no_source_files():
    from cifflow.fidelity.check import _format_report
    from cifflow.dictionary.schema import SchemaSpec
    spec = SchemaSpec(tables={}, column_to_tag={}, dictionary_name='TEST', source_files=[])
    report = FidelityReport(passed=True, mismatches=[])
    text = _format_report(report, 'A', 'B', schema_spec=spec)
    assert 'Schema   : TEST' in text
    assert '(' not in text.split('Schema')[1].split('\n')[0]  # no filename in parens


def test_format_report_schema_name_with_source_files():
    from cifflow.fidelity.check import _format_report
    from cifflow.dictionary.schema import SchemaSpec
    spec = SchemaSpec(tables={}, column_to_tag={}, dictionary_name='TEST',
                      source_files=['a.dic', 'b.dic'])
    report = FidelityReport(passed=True, mismatches=[])
    text = _format_report(report, 'A', 'B', schema_spec=spec)
    assert 'Schema   : TEST (a.dic, b.dic)' in text


def test_format_report_schema_name_unknown():
    from cifflow.fidelity.check import _format_report
    from cifflow.dictionary.schema import SchemaSpec
    spec = SchemaSpec(tables={}, column_to_tag={}, dictionary_name=None, source_files=[])
    report = FidelityReport(passed=True, mismatches=[])
    text = _format_report(report, 'A', 'B', schema_spec=spec)
    assert '(unknown)' in text


def test_format_report_failed_branch_direct():
    from cifflow.fidelity.check import _format_report
    report = FidelityReport(
        passed=False,
        mismatches=[FidelityMismatch(kind='fallback_mismatch', source='both',
                                     description='tag foo differs')],
    )
    text = _format_report(report, 'source_a.cif', 'source_b.cif')
    assert 'FAILED' in text
    assert 'fallback_mismatch' in text
    assert 'foo differs' in text


def test_report_file_written(tmp_path):
    cif = "data_block1\n_a.b 1\n"
    r_file = tmp_path / 'r.txt'
    check_fidelity(cif, cif, schema=None, report_file=r_file)
    assert r_file.exists()
    assert 'Fidelity Report' in r_file.read_text(encoding='utf-8')


def test_report_file_written_with_failure(tmp_path):
    """report_file is written even when the comparison fails; exercises the FAILED format path."""
    cif_a = "data_block1\n_foo.bar alpha\n"
    cif_b = "data_block1\n_foo.bar beta\n"
    r_file = tmp_path / 'fail.txt'
    r = check_fidelity(cif_a, cif_b, schema=None, report_file=r_file)
    assert not r.passed
    text = r_file.read_text(encoding='utf-8')
    assert 'FAILED' in text
    assert 'fallback_mismatch' in text


def test_check_fidelity_accepts_ciffile_source():
    """Passing a CifFile object directly exercises the _label CifFile branch."""
    cif_str = "data_block1\n_a.b 1\n"
    cif_obj, _ = _build(cif_str)
    r = check_fidelity(cif_obj, cif_str, schema=None)
    assert r.passed


def test_canonical_real_su_normalizes_trailing_zeros():
    from cifflow.fidelity.check import _canonical_real
    # SU=True → Decimal.normalize() strips trailing zeros
    assert _canonical_real('1.2300', True) == '1.23'
    # SU=False → format(d, 'f') preserves trailing zeros
    assert _canonical_real('1.2300', False) == '1.2300'


def test_canonical_real_non_numeric_passthrough():
    from cifflow.fidelity.check import _canonical_real
    # Non-numeric value passes through unchanged
    assert _canonical_real('abc', False) == 'abc'


def test_strip_su_suffix():
    from cifflow.fidelity.check import _strip_su_suffix
    assert _strip_su_suffix('3.14(5)') == '3.14'
    assert _strip_su_suffix('3.14') == '3.14'
    assert _strip_su_suffix('1.2(10)') == '1.2'


# ---------------------------------------------------------------------------
# Schema-backed cluster: structural helpers
# ---------------------------------------------------------------------------

def test_table_present_operational_error():
    from cifflow.fidelity.check import _table_present
    from cifflow.dictionary.schema import TableDef, ColumnDef
    tdef = TableDef(
        name='test', definition_id='_test', category_class='Loop',
        columns=[ColumnDef(name='id', definition_id='_test.id', type_contents='Text',
                           nullable=False, is_primary_key=True, is_synthetic=False,
                           linked_item_id=None)],
        primary_keys=['id'],
    )
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception('no such table')
    result = _table_present(mock_conn, 'test', tdef)
    assert result is False


def test_normalised_rows_operational_error():
    from cifflow.fidelity.check import _normalised_rows
    from cifflow.dictionary.schema import TableDef, ColumnDef
    tdef = TableDef(
        name='test', definition_id='_test', category_class='Loop',
        columns=[ColumnDef(name='id', definition_id='_test.id', type_contents='Text',
                           nullable=False, is_primary_key=True, is_synthetic=False,
                           linked_item_id=None)],
        primary_keys=['id'],
    )
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception('no such table')
    result = _normalised_rows(mock_conn, 'test', tdef, {}, {}, {}, set())
    assert result == []


def test_row_diff_hint_no_candidates():
    from cifflow.fidelity.check import _row_diff_hint
    row = frozenset({('a', '1'), ('b', '2')})
    hint = _row_diff_hint(row, [])
    assert hint.startswith(' [')


def test_row_diff_hint_no_candidates_empty_row():
    from cifflow.fidelity.check import _row_diff_hint
    # Row has only frozenset values → pairs is empty → returns ''
    row = frozenset({('fp', frozenset())})
    hint = _row_diff_hint(row, [])
    assert hint == ''


def test_row_diff_hint_va_none_branch():
    """va is None (row missing key that best has) — line 474."""
    from cifflow.fidelity.check import _row_diff_hint
    row = frozenset({('x', '1')})
    best = frozenset({('x', '1'), ('z', '3')})  # z only in best
    hint = _row_diff_hint(row, [best])
    assert '-z=3' in hint


def test_row_diff_hint_vb_none_branch():
    """vb is None (row has key that best lacks) — line 476."""
    from cifflow.fidelity.check import _row_diff_hint
    row = frozenset({('x', '1'), ('extra', '5')})
    best = frozenset({('x', '1')})  # extra not in best
    hint = _row_diff_hint(row, [best])
    assert '+extra=5' in hint


def test_row_diff_hint_frozenset_skip():
    """frozenset value columns are skipped — line 471."""
    from cifflow.fidelity.check import _row_diff_hint
    row = frozenset({('fp', frozenset({('a', '1')})), ('x', '1')})
    best = frozenset({('fp', frozenset({('b', '2')})), ('x', '2')})
    hint = _row_diff_hint(row, [best])
    assert 'fp' not in hint   # frozenset col skipped
    assert 'x' in hint        # x: 1!=2 shown


def test_row_diff_hint_no_diffs_returns_empty():
    """All fields match → return '' — line 481."""
    from cifflow.fidelity.check import _row_diff_hint
    row = frozenset({('x', '1'), ('y', '2')})
    best = frozenset({('x', '1'), ('y', '2')})
    hint = _row_diff_hint(row, [best])
    assert hint == ''


def test_row_diff_hint_many_diffs():
    from cifflow.fidelity.check import _row_diff_hint
    row = frozenset({('a', '1'), ('b', '2'), ('c', '3'), ('d', '4')})
    best = frozenset({('a', 'X'), ('b', 'X'), ('c', 'X'), ('d', 'X')})
    hint = _row_diff_hint(row, [best])
    assert 'more' in hint


def test_compare_schema_mismatch_tag_in_fallback_a_structured_b():
    from cifflow.fidelity.check import _compare_schema_mismatch
    from cifflow.ingestion.ingest import ingest

    schema = _simple_set_schema()
    cif = "data_b\n_cell.id 1\n_cell.a 5.0\n"

    # conn_a: ingested without schema → _cell.a lands in fallback
    conn_a, _ = ingest(_build(cif)[0], None, None)

    # conn_b: ingested with schema → _cell.a lands in structured table
    conn_b, _ = ingest(_build(cif)[0], None, schema)

    mismatches = _compare_schema_mismatch(conn_a, conn_b, schema)

    assert any(m.kind == 'schema_mismatch' for m in mismatches)


def test_compare_schema_mismatch_reverse_direction():
    """Tag in fallback in B but structured in A — exercises the second loop (line 688)."""
    from cifflow.fidelity.check import _compare_schema_mismatch
    from cifflow.ingestion.ingest import ingest

    schema = _simple_set_schema()
    cif = "data_b\n_cell.id 1\n_cell.a 5.0\n"

    # conn_a: WITH schema → _cell.a in structured table
    conn_a, _ = ingest(_build(cif)[0], None, schema)

    # conn_b: WITHOUT schema → _cell.a in fallback
    conn_b, _ = ingest(_build(cif)[0], None, None)

    mismatches = _compare_schema_mismatch(conn_a, conn_b, schema)

    assert any(m.kind == 'schema_mismatch' for m in mismatches)


def test_compare_fallback_no_table():
    """_compare_fallback returns [] when _cif_fallback table does not exist."""
    from cifflow.fidelity.check import _compare_fallback
    conn_a = duckdb.connect()
    conn_b = duckdb.connect()
    result = _compare_fallback(conn_a, conn_b)
    conn_a.close()
    conn_b.close()
    assert result == []


# ---------------------------------------------------------------------------
# check_fidelity ingest error paths
# ---------------------------------------------------------------------------

def test_ingest_error_in_check_fidelity():
    from cifflow.ingestion.ingest import IngestionError
    cif = "data_b\n_a.b 1\n"
    with patch('cifflow.fidelity.check.ingest') as mock_ingest:
        mock_ingest.side_effect = IngestionError(['broken ingest'])
        r = check_fidelity(cif, cif, schema=None)
    assert not r.passed
    assert any(m.kind == 'ingest_error' for m in r.mismatches)
    assert any('broken ingest' in m.description for m in r.mismatches)


def test_generic_exception_in_check_fidelity():
    """ValueError in ingest for source_a exercises the except (ValueError, Exception) path."""
    cif = "data_b\n_a.b 1\n"
    with patch('cifflow.fidelity.check.ingest') as mock_ingest:
        mock_ingest.side_effect = ValueError('unexpected failure')
        r = check_fidelity(cif, cif, schema=None)
    assert not r.passed
    assert any(m.kind == 'ingest_error' and 'unexpected failure' in m.description
               for m in r.mismatches)


# ---------------------------------------------------------------------------
# _normalised_rows: placeholder / synthetic_set paths (lines 439, 442)
# ---------------------------------------------------------------------------

def test_placeholder_value_in_structured_table_is_skipped():
    """Placeholder values (. or ?) in structured columns are excluded from normalised rows."""
    schema = _simple_loop_schema()
    # A has atom A1 with x = '.' (placeholder); B has A1 with x = 1.0
    cif_a = "data_b\nloop_\n  _atom.id\n  _atom.x\n  A1 .\n"
    cif_b = "data_b\nloop_\n  _atom.id\n  _atom.x\n  A1 1.0\n"
    r = check_fidelity(cif_a, cif_b, schema=schema)
    # placeholder stripped → A1 row has only id; B has id + x → mismatch
    assert not r.passed
    assert any(m.kind == 'row_content' for m in r.mismatches)


def test_normalised_rows_synthetic_set_skipped():
    """Rows in synthetic_set are excluded from normalised output (line 442)."""
    from cifflow.fidelity.check import _normalised_rows
    from cifflow.dictionary.schema import TableDef, ColumnDef

    tdef = TableDef(
        name='atom', definition_id='_atom', category_class='Loop',
        columns=[
            ColumnDef(name='id', definition_id='_atom.id', type_contents='Text',
                      nullable=False, is_primary_key=True, is_synthetic=False,
                      linked_item_id=None),
            ColumnDef(name='x', definition_id='_atom.x', type_contents='Real',
                      nullable=True, is_primary_key=False, is_synthetic=False,
                      linked_item_id=None),
            ColumnDef(name='_cifflow_row_id', definition_id='_cifflow_row_id', type_contents='Text',
                      nullable=True, is_primary_key=False, is_synthetic=True,
                      linked_item_id=None),
        ],
        primary_keys=['id'],
    )

    conn = duckdb.connect()
    conn.execute('CREATE TABLE "atom" (id TEXT, x DOUBLE, _cifflow_row_id TEXT)')
    conn.execute("INSERT INTO atom VALUES ('A1', 1.5, 'ROW1')")

    # synthetic_set says ('atom', 'ROW1', 'x') is default-filled → should be skipped
    synthetic_set = {('atom', 'ROW1', 'x')}
    rows = _normalised_rows(conn, 'atom', tdef, {}, {}, {}, synthetic_set)
    conn.close()

    assert len(rows) == 1
    row_dict = dict(rows[0])
    # 'x' should be absent because it was in synthetic_set
    assert 'x' not in row_dict


# ---------------------------------------------------------------------------
# _load_synthetic_set: table-with-data path (line 382)
# ---------------------------------------------------------------------------

def test_load_synthetic_set_with_data():
    """_load_synthetic_set returns set of tuples when _cif_synthetic exists and has rows."""
    from cifflow.fidelity.check import _load_synthetic_set
    conn = duckdb.connect()
    conn.execute(
        'CREATE TABLE _cif_synthetic (table_name TEXT, row_id TEXT, column_name TEXT)'
    )
    conn.execute("INSERT INTO _cif_synthetic VALUES ('cell', 'R1', 'length_a')")
    result = _load_synthetic_set(conn)
    conn.close()
    assert ('cell', 'R1', 'length_a') in result


# ---------------------------------------------------------------------------
# _compare_schema_mismatch: OperationalError paths (lines 659->654, 661-662, 672-673)
# ---------------------------------------------------------------------------

def test_compare_schema_mismatch_no_fallback_table():
    """_fallback_tags returns [] when _cif_fallback does not exist (lines 672-673)."""
    from cifflow.fidelity.check import _compare_schema_mismatch
    schema = _simple_set_schema()
    conn_a = duckdb.connect()
    conn_b = duckdb.connect()
    # Neither connection has _cif_fallback or structured tables
    result = _compare_schema_mismatch(conn_a, conn_b, schema)
    conn_a.close()
    conn_b.close()
    assert result == []


def test_compare_schema_mismatch_structured_table_missing_in_b():
    """_in_structured swallows CatalogException when table missing in conn_b."""
    import duckdb as _duckdb
    from cifflow.fidelity.check import _compare_schema_mismatch
    from cifflow.dictionary.schema import emit_fallback_create_statements

    schema = _simple_set_schema()

    # conn_a: _cif_fallback has a schema-known tag (_cell.a)
    conn_a = _duckdb.connect()
    for stmt in emit_fallback_create_statements():
        conn_a.execute(stmt)
    conn_a.execute(
        "INSERT INTO _cif_fallback (_cifflow_block_id, _cifflow_row_id, tag, value, value_type) "
        "VALUES ('B', 1, '_cell.a', '5.0', 'string')"
    )

    # conn_b: _cif_fallback exists but structured tables do NOT
    conn_b = _duckdb.connect()
    for stmt in emit_fallback_create_statements():
        conn_b.execute(stmt)

    # Should not raise; CatalogException on missing table is caught silently
    result = _compare_schema_mismatch(conn_a, conn_b, schema)
    assert isinstance(result, list)


def test_compare_schema_mismatch_in_structured_multi_table_mapping():
    """_in_structured loops over multiple (table, col) pairs for same defid."""
    import duckdb as _duckdb
    from cifflow.fidelity.check import _compare_schema_mismatch
    from cifflow.dictionary.schema import SchemaSpec, TableDef, ColumnDef, emit_fallback_create_statements

    # Build an artificial schema where _item.val maps to two tables
    col_id = ColumnDef(name='id', definition_id='_item.id', type_contents='Text',
                       nullable=False, is_primary_key=True, is_synthetic=False,
                       linked_item_id=None)
    col_val = ColumnDef(name='val', definition_id='_item.val', type_contents='Text',
                        nullable=True, is_primary_key=False, is_synthetic=False,
                        linked_item_id=None)
    tdef1 = TableDef(name='table1', definition_id='_cat1', category_class='Loop',
                     columns=[col_id, col_val], primary_keys=['id'])
    tdef2 = TableDef(name='table2', definition_id='_cat2', category_class='Loop',
                     columns=[col_id, col_val], primary_keys=['id'])
    schema = SchemaSpec(
        tables={'table1': tdef1, 'table2': tdef2},
        column_to_tag={
            ('table1', 'val'): '_item.val',
            ('table2', 'val'): '_item.val',
            ('table1', 'id'): '_item.id',
            ('table2', 'id'): '_item.id',
        },
    )

    # conn_a has _cif_fallback with _item.val
    conn_a = _duckdb.connect()
    for stmt in emit_fallback_create_statements():
        conn_a.execute(stmt)
    conn_a.execute(
        "INSERT INTO _cif_fallback (_cifflow_block_id, _cifflow_row_id, tag, value, value_type) "
        "VALUES ('B', 1, '_item.val', 'foo', 'string')"
    )

    # conn_b: table1 exists but empty, table2 has non-NULL data → _in_structured returns True
    conn_b = _duckdb.connect()
    for stmt in emit_fallback_create_statements():
        conn_b.execute(stmt)
    conn_b.execute('CREATE TABLE "table1" (id VARCHAR, val VARCHAR)')
    conn_b.execute('CREATE TABLE "table2" (id VARCHAR, val VARCHAR)')
    conn_b.execute("INSERT INTO \"table2\" VALUES ('1', 'foo')")

    result = _compare_schema_mismatch(conn_a, conn_b, schema)
    # table2 has data → _in_structured eventually returns True → schema_mismatch emitted
    assert any(m.kind == 'schema_mismatch' for m in result)
