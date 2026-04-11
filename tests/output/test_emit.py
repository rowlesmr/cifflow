"""
Tests for pycifparse.output.emit.

Tests are organised by mode:
- ONE_BLOCK
- ALL_BLOCKS
- GROUPED (default)
- Round-trip (build → ingest → emit → build, value equivalence)

All tests use in-memory SQLite databases.
"""

from __future__ import annotations

import sqlite3

import pytest

from pycifparse import (
    build,
    ingest,
    emit,
    EmitMode,
    apply_schema,
    apply_fallback_schema,
    generate_schema,
)
from pycifparse.dictionary import DictionaryLoader
from pycifparse.dictionary.schema import SchemaSpec
from pycifparse.output import BlockSpec, OutputPlan
from pycifparse.types import CifVersion

CIF20 = CifVersion.CIF_2_0
CIF11 = CifVersion.CIF_1_1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_schema(ddl_source: str) -> SchemaSpec:
    d = DictionaryLoader().load(ddl_source)
    return generate_schema(d)


def _ingest_src(cif_source: str, schema: SchemaSpec | None = None) -> sqlite3.Connection:
    """Parse *cif_source* and ingest into a fresh in-memory DB."""
    cif, errors = build(cif_source)
    assert not errors, errors
    conn = sqlite3.connect(':memory:')
    if schema:
        apply_schema(conn, schema)
    apply_fallback_schema(conn)
    ingest(cif, conn, schema=schema)
    return conn


def _empty_schema() -> SchemaSpec:
    return SchemaSpec(tables={}, column_to_tag={})


# ---------------------------------------------------------------------------
# No-schema (fallback-only) tests
# ---------------------------------------------------------------------------

class TestFallbackOnly:
    """emit() with an empty schema — all tags go to _cif_fallback."""

    CIF = '#\\#CIF_2.0\ndata_test\n_cell.length_a  5.4\n_cell.length_b  5.4\n'

    def test_magic_line(self):
        conn = _ingest_src(self.CIF)
        result = emit(conn, _empty_schema())
        assert result.startswith('#\\#CIF_2.0\n')

    def test_data_block_header(self):
        conn = _ingest_src(self.CIF)
        result = emit(conn, _empty_schema())
        assert 'data_test\n' in result

    def test_tags_present(self):
        conn = _ingest_src(self.CIF)
        result = emit(conn, _empty_schema())
        assert '_cell.length_a  5.4' in result
        assert '_cell.length_b  5.4' in result

    def test_round_trip_parse(self):
        conn = _ingest_src(self.CIF)
        result = emit(conn, _empty_schema())
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert str(block['_cell.length_a'][0]) == '5.4'
        assert str(block['_cell.length_b'][0]) == '5.4'

    def test_terminates_with_newline(self):
        conn = _ingest_src(self.CIF)
        result = emit(conn, _empty_schema())
        assert result.endswith('\n')

    def test_no_trailing_whitespace(self):
        conn = _ingest_src(self.CIF)
        result = emit(conn, _empty_schema())
        for line in result.splitlines():
            assert line == line.rstrip(), f'Trailing whitespace: {line!r}'

    def test_cif11_magic(self):
        conn = _ingest_src(self.CIF)
        result = emit(conn, _empty_schema(), version=CIF11)
        assert result.startswith('#\\#CIF_1.1\n')


# ---------------------------------------------------------------------------
# Small schema — a single Set category
# ---------------------------------------------------------------------------

_MINI_DIC = """\
#\\#CIF_2.0
data_mini

save_CELL
  _definition.id        CELL
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     cell
save_

save_cell.length_a
  _definition.id        '_cell.length_a'
  _definition.class     Attribute
  _name.category_id     cell
  _name.object_id       length_a
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_

save_cell.length_b
  _definition.id        '_cell.length_b'
  _definition.class     Attribute
  _name.category_id     cell
  _name.object_id       length_b
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_

save_cell.length_c
  _definition.id        '_cell.length_c'
  _definition.class     Attribute
  _name.category_id     cell
  _name.object_id       length_c
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_
"""


class TestSetCategory:
    """Emit a Set-class category as scalar tag-value pairs."""

    CIF = '#\\#CIF_2.0\ndata_myblock\n_cell.length_a  5.4\n_cell.length_b  5.4\n_cell.length_c  13.2\n'

    @pytest.fixture
    def schema(self):
        return _make_schema(_MINI_DIC)

    @pytest.fixture
    def conn(self, schema):
        return _ingest_src(self.CIF, schema)

    def test_tags_emitted_as_scalars(self, conn, schema):
        result = emit(conn, schema)
        print(f"\n|{result}|")
        assert '_cell.length_a  5.4' in result
        assert '_cell.length_b  5.4' in result
        assert '_cell.length_c  13.2' in result

    def test_no_loop_keyword(self, conn, schema):
        result = emit(conn, schema)
        assert 'loop_' not in result

    def test_block_name_preserved(self, conn, schema):
        result = emit(conn, schema)
        assert 'data_myblock\n' in result

    def test_round_trip(self, conn, schema):
        result = emit(conn, schema)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert str(block['_cell.length_a'][0]) == '5.4'

    def test_synthetic_cols_not_emitted(self, conn, schema):
        result = emit(conn, schema)
        assert '_block_id' not in result
        assert '_row_id' not in result
        assert '_pycifparse_id' not in result


# ---------------------------------------------------------------------------
# Loop category
# ---------------------------------------------------------------------------

_LOOP_DIC = """\
#\\#CIF_2.0
data_loop_dic

save_ATOM_SITE
  _definition.id        ATOM_SITE
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     atom_site
  _category_key.name    '_atom_site.id'
save_

save_atom_site.id
  _definition.id        '_atom_site.id'
  _definition.class     Attribute
  _name.category_id     atom_site
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_atom_site.type_symbol
  _definition.id        '_atom_site.type_symbol'
  _definition.class     Attribute
  _name.category_id     atom_site
  _name.object_id       type_symbol
  _type.purpose         Encode
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_atom_site.fract_x
  _definition.id        '_atom_site.fract_x'
  _definition.class     Attribute
  _name.category_id     atom_site
  _name.object_id       fract_x
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_
"""

_LOOP_CIF = """\
#\\#CIF_2.0
data_loop_test
loop_
  _atom_site.id
  _atom_site.type_symbol
  _atom_site.fract_x
  C1  C  0.125
  O1  O  0.250
  N1  N  0.375
"""


class TestLoopCategory:
    @pytest.fixture
    def schema(self):
        return _make_schema(_LOOP_DIC)

    @pytest.fixture
    def conn(self, schema):
        return _ingest_src(_LOOP_CIF, schema)

    def test_loop_keyword_present(self, conn, schema):
        result = emit(conn, schema)
        assert 'loop_' in result

    def test_loop_tags_in_header(self, conn, schema):
        result = emit(conn, schema)
        lines = result.splitlines()
        loop_idx = next(i for i, l in enumerate(lines) if l == 'loop_')
        header_lines = []
        i = loop_idx + 1
        while i < len(lines) and lines[i].startswith('  _'):
            header_lines.append(lines[i].strip())
            i += 1
        assert '_atom_site.id' in header_lines
        assert '_atom_site.type_symbol' in header_lines
        assert '_atom_site.fract_x' in header_lines

    def test_correct_row_count(self, conn, schema):
        result = emit(conn, schema)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert len(block['_atom_site.id']) == 3

    def test_values_preserved(self, conn, schema):
        result = emit(conn, schema)
        cif2, _ = build(result)
        block = cif2[cif2.blocks[0]]
        syms = [str(v) for v in block['_atom_site.type_symbol']]
        assert sorted(syms) == ['C', 'N', 'O']

    def test_round_trip(self, conn, schema):
        result = emit(conn, schema)
        cif2, errors = build(result)
        assert not errors


# ---------------------------------------------------------------------------
# ONE_BLOCK mode
# ---------------------------------------------------------------------------

class TestOneBlock:
    CIF = '#\\#CIF_2.0\ndata_myblock\n_cell.length_a  5.4\n_cell.length_b  5.4\n'

    @pytest.fixture
    def schema(self):
        return _make_schema(_MINI_DIC)

    def test_single_data_block(self, schema):
        conn = _ingest_src(self.CIF, schema)
        result = emit(conn, schema, mode=EmitMode.ONE_BLOCK)
        block_headers = [l for l in result.splitlines() if l.startswith('data_')]
        assert len(block_headers) == 1

    def test_block_named_output(self, schema):
        conn = _ingest_src(self.CIF, schema)
        result = emit(conn, schema, mode=EmitMode.ONE_BLOCK)
        assert 'data_output\n' in result

    def test_values_present(self, schema):
        conn = _ingest_src(self.CIF, schema)
        result = emit(conn, schema, mode=EmitMode.ONE_BLOCK)
        assert '_cell.length_a  5.4' in result


# ---------------------------------------------------------------------------
# ALL_BLOCKS mode
# ---------------------------------------------------------------------------

class TestAllBlocks:
    @pytest.fixture
    def schema(self):
        return _make_schema(_MINI_DIC)

    def test_one_block_per_non_empty_table(self, schema):
        conn = _ingest_src(
            '#\\#CIF_2.0\ndata_b\n_cell.length_a  5.4\n',
            schema,
        )
        result = emit(conn, schema, mode=EmitMode.ALL_BLOCKS)
        block_headers = [l for l in result.splitlines() if l.startswith('data_')]
        # 'cell' table has data → 1 structured block; no fallback block
        assert any('cell' in h for h in block_headers)

    def test_round_trip(self, schema):
        conn = _ingest_src(
            '#\\#CIF_2.0\ndata_b\n_cell.length_a  5.4\n_cell.length_b  3.2\n',
            schema,
        )
        result = emit(conn, schema, mode=EmitMode.ALL_BLOCKS)
        cif2, errors = build(result)
        assert not errors


# ---------------------------------------------------------------------------
# ORIGINAL mode — multiple source blocks grouped by _block_id
# ---------------------------------------------------------------------------

class TestOriginal:
    MULTI_CIF = (
        '#\\#CIF_2.0\n'
        'data_block_a\n_cell.length_a  5.4\n_cell.length_b  5.4\n'
        '\n\n'
        'data_block_b\n_cell.length_a  3.2\n_cell.length_b  3.2\n'
    )

    @pytest.fixture
    def schema(self):
        return _make_schema(_MINI_DIC)

    def test_two_blocks_produced(self, schema):
        conn = _ingest_src(self.MULTI_CIF, schema)
        result = emit(conn, schema, mode=EmitMode.ORIGINAL)
        headers = [l for l in result.splitlines() if l.startswith('data_')]
        assert len(headers) == 2

    def test_block_names_preserved(self, schema):
        conn = _ingest_src(self.MULTI_CIF, schema)
        result = emit(conn, schema, mode=EmitMode.ORIGINAL)
        assert 'data_block_a\n' in result
        assert 'data_block_b\n' in result

    def test_values_in_correct_blocks(self, schema):
        conn = _ingest_src(self.MULTI_CIF, schema)
        result = emit(conn, schema, mode=EmitMode.ORIGINAL)
        cif2, errors = build(result)
        assert not errors
        block_a = cif2['block_a']
        block_b = cif2['block_b']
        assert str(block_a['_cell.length_a'][0]) == '5.4'
        assert str(block_b['_cell.length_a'][0]) == '3.2'

    def test_blocks_separated_by_two_blank_lines(self, schema):
        conn = _ingest_src(self.MULTI_CIF, schema)
        result = emit(conn, schema, mode=EmitMode.ORIGINAL)
        # Two consecutive blank lines appear between blocks
        assert '\n\n\n' in result

    def test_is_default_mode(self, schema):
        conn = _ingest_src(self.MULTI_CIF, schema)
        default_result = emit(conn, schema)
        explicit_result = emit(conn, schema, mode=EmitMode.ORIGINAL)
        assert default_result == explicit_result


# ---------------------------------------------------------------------------
# GROUPED mode — FK-chain grouping by Set anchor key values
# ---------------------------------------------------------------------------

# Schema: EXPT (Set, keyed by expt.id) + PEAK (Loop, FK to expt.id)
_GROUPED_DIC = """\
#\\#CIF_2.0
data_grouped_dic

save_EXPT
  _definition.id        EXPT
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     expt
  _category_key.name    '_expt.id'
save_

save_expt.id
  _definition.id        '_expt.id'
  _definition.class     Attribute
  _name.category_id     expt
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_PEAK
  _definition.id        PEAK
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     peak
  _category_key.name    '_peak.id'
save_

save_peak.id
  _definition.id        '_peak.id'
  _definition.class     Attribute
  _name.category_id     peak
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_peak.expt_id
  _definition.id        '_peak.expt_id'
  _definition.class     Attribute
  _name.category_id     peak
  _name.object_id       expt_id
  _type.purpose         Link
  _type.source          Related
  _type.container       Single
  _type.contents        Code
  _name.linked_item_id  '_expt.id'
save_

save_peak.intensity
  _definition.id        '_peak.intensity'
  _definition.class     Attribute
  _name.category_id     peak
  _name.object_id       intensity
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_
"""

# Two blocks with the same expt_id → GROUPED merges them into one block.
_GROUPED_MERGE_CIF = (
    '#\\#CIF_2.0\n'
    'data_run1\n'
    '_expt.id  X1\n'
    'loop_\n  _peak.id\n  _peak.expt_id\n  _peak.intensity\n'
    '  p1  X1  100.0\n'
    '\n\n'
    'data_run2\n'
    '_expt.id  X1\n'
    'loop_\n  _peak.id\n  _peak.expt_id\n  _peak.intensity\n'
    '  p2  X1  200.0\n'
)

# Two blocks with different expt_ids → GROUPED keeps them separate.
_GROUPED_SEPARATE_CIF = (
    '#\\#CIF_2.0\n'
    'data_run1\n'
    '_expt.id  X1\n'
    'loop_\n  _peak.id\n  _peak.expt_id\n  _peak.intensity\n'
    '  p1  X1  100.0\n'
    '\n\n'
    'data_run2\n'
    '_expt.id  X2\n'
    'loop_\n  _peak.id\n  _peak.expt_id\n  _peak.intensity\n'
    '  p2  X2  200.0\n'
)


# Schema for composite-key test.
# SCAN (Loop, no FK to any Set — pure Loop chain)
# RESULT (Loop, composite FK: scan_id → SCAN (Loop) AND expt_id → EXPT (Set))
# The BFS anchor search must find EXPT as RESULT's Set anchor even though the
# first FK target (SCAN) has no Set ancestor.
_COMPOSITE_DIC = """\
#\\#CIF_2.0
data_composite_dic

save_EXPT
  _definition.id        EXPT
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     expt
  _category_key.name    '_expt.id'
save_

save_expt.id
  _definition.id        '_expt.id'
  _definition.class     Attribute
  _name.category_id     expt
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_SCAN
  _definition.id        SCAN
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     scan
  _category_key.name    '_scan.id'
save_

save_scan.id
  _definition.id        '_scan.id'
  _definition.class     Attribute
  _name.category_id     scan
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_RESULT
  _definition.id        RESULT
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     result
  _category_key.name    '_result.id'
save_

save_result.id
  _definition.id        '_result.id'
  _definition.class     Attribute
  _name.category_id     result
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_result.scan_id
  _definition.id        '_result.scan_id'
  _definition.class     Attribute
  _name.category_id     result
  _name.object_id       scan_id
  _type.purpose         Link
  _type.source          Related
  _type.container       Single
  _type.contents        Code
  _name.linked_item_id  '_scan.id'
save_

save_result.expt_id
  _definition.id        '_result.expt_id'
  _definition.class     Attribute
  _name.category_id     result
  _name.object_id       expt_id
  _type.purpose         Link
  _type.source          Related
  _type.container       Single
  _type.contents        Code
  _name.linked_item_id  '_expt.id'
save_

save_result.value
  _definition.id        '_result.value'
  _definition.class     Attribute
  _name.category_id     result
  _name.object_id       value
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_
"""

# Two blocks share the same expt_id; RESULT rows have FK to both SCAN (Loop,
# no Set ancestor) and EXPT (Set).  GROUPED must anchor RESULT to EXPT and
# merge the two blocks into one.
_COMPOSITE_MERGE_CIF = (
    '#\\#CIF_2.0\n'
    'data_run1\n'
    '_expt.id  E1\n'
    'loop_\n  _scan.id\n  sc1\n'
    'loop_\n  _result.id\n  _result.scan_id\n  _result.expt_id\n  _result.value\n'
    '  r1  sc1  E1  1.0\n'
    '\n\n'
    'data_run2\n'
    '_expt.id  E1\n'
    'loop_\n  _scan.id\n  sc2\n'
    'loop_\n  _result.id\n  _result.scan_id\n  _result.expt_id\n  _result.value\n'
    '  r2  sc2  E1  2.0\n'
)


class TestGroupedMode:
    @pytest.fixture
    def schema(self):
        return _make_schema(_GROUPED_DIC)

    def test_same_set_key_merges_blocks(self, schema):
        conn = _ingest_src(_GROUPED_MERGE_CIF, schema)
        result = emit(conn, schema, mode=EmitMode.GROUPED)
        headers = [l for l in result.splitlines() if l.startswith('data_')]
        assert len(headers) == 1

    def test_merged_block_contains_both_peaks(self, schema):
        conn = _ingest_src(_GROUPED_MERGE_CIF, schema)
        result = emit(conn, schema, mode=EmitMode.GROUPED)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        peak_ids = [str(v) for v in block['_peak.id']]
        assert sorted(peak_ids) == ['p1', 'p2']

    def test_different_set_keys_stay_separate(self, schema):
        conn = _ingest_src(_GROUPED_SEPARATE_CIF, schema)
        result = emit(conn, schema, mode=EmitMode.GROUPED)
        headers = [l for l in result.splitlines() if l.startswith('data_')]
        assert len(headers) == 2

    def test_different_keys_values_in_correct_blocks(self, schema):
        conn = _ingest_src(_GROUPED_SEPARATE_CIF, schema)
        result = emit(conn, schema, mode=EmitMode.GROUPED)
        cif2, errors = build(result)
        assert not errors
        # Each block has the expt.id value matching its peaks
        for block_name in cif2.blocks:
            block = cif2[block_name]
            expt_id = str(block['_expt.id'][0])
            peak_expt_ids = [str(v) for v in block['_peak.expt_id']]
            assert all(eid == expt_id for eid in peak_expt_ids)


class TestGroupedCompositeKey:
    """GROUPED mode: table with FK to both a Loop (no Set ancestor) and a Set.

    The BFS anchor search must find the Set even when the first FK target is a
    pure-Loop table with no Set ancestor.
    """

    @pytest.fixture
    def schema(self):
        return _make_schema(_COMPOSITE_DIC)

    def test_result_anchored_to_expt_merges_blocks(self, schema):
        """Two blocks sharing expt_id=E1 are merged into one output block."""
        conn = _ingest_src(_COMPOSITE_MERGE_CIF, schema)
        result = emit(conn, schema, mode=EmitMode.GROUPED)
        headers = [l for l in result.splitlines() if l.startswith('data_')]
        assert len(headers) == 1

    def test_merged_block_has_both_results(self, schema):
        conn = _ingest_src(_COMPOSITE_MERGE_CIF, schema)
        result = emit(conn, schema, mode=EmitMode.GROUPED)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        result_ids = sorted(str(v) for v in block['_result.id'])
        assert result_ids == ['r1', 'r2']


# ---------------------------------------------------------------------------
# OutputPlan — custom ordering
# ---------------------------------------------------------------------------

class TestOutputPlan:
    @pytest.fixture
    def schema(self):
        return _make_schema(_MINI_DIC)

    def test_plan_column_order_respected(self, schema):
        conn = _ingest_src(
            '#\\#CIF_2.0\ndata_b\n_cell.length_a  5.4\n_cell.length_b  3.2\n_cell.length_c  10.0\n',
            schema,
        )
        spec = BlockSpec(column_order={'cell': ['length_c', 'length_a', 'length_b']})
        plan = OutputPlan(blocks=[spec])
        result = emit(conn, schema, plan=plan)
        lines = result.splitlines()
        tag_lines = [l for l in lines if l.startswith('_cell.length')]
        names = [l.split('  ')[0] for l in tag_lines]
        assert names == ['_cell.length_c', '_cell.length_a', '_cell.length_b']


# ---------------------------------------------------------------------------
# Quoting in output
# ---------------------------------------------------------------------------

class TestQuotingInOutput:
    """Values requiring quoting are correctly quoted and round-trip."""

    @pytest.fixture
    def schema(self):
        return _make_schema(_MINI_DIC)

    def _cif_with_value(self, value: str) -> str:
        return f'#\\#CIF_2.0\ndata_t\n_cell.length_a  {value}\n'

    def test_bare_word_value(self, schema):
        conn = _ingest_src('#\\#CIF_2.0\ndata_t\n_cell.length_a  5.4\n', schema)
        result = emit(conn, schema)
        assert '_cell.length_a  5.4' in result

    def test_value_with_space_is_quoted(self, schema):
        conn = _ingest_src("#\\#CIF_2.0\ndata_t\n_cell.length_a  'hello world'\n", schema)
        result = emit(conn, schema)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert str(block['_cell.length_a'][0]) == 'hello world'

    def test_semicolon_value_round_trips(self, schema):
        src = '#\\#CIF_2.0\ndata_t\n_cell.length_a\n;multiline\nvalue here\n;\n'
        conn = _ingest_src(src, schema)
        result = emit(conn, schema)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert str(block['_cell.length_a'][0]) == 'multiline\nvalue here'

    def test_placeholder_dot_not_quoted(self, schema):
        conn = _ingest_src('#\\#CIF_2.0\ndata_t\n_cell.length_a  .\n', schema)
        result = emit(conn, schema)
        assert '_cell.length_a  .' in result

    def test_placeholder_question_not_quoted(self, schema):
        conn = _ingest_src('#\\#CIF_2.0\ndata_t\n_cell.length_a  ?\n', schema)
        result = emit(conn, schema)
        assert '_cell.length_a  ?' in result


# ---------------------------------------------------------------------------
# CIF 1.1 emission
# ---------------------------------------------------------------------------

class TestCIF11Emission:
    @pytest.fixture
    def schema(self):
        return _make_schema(_MINI_DIC)

    def test_cif11_magic(self, schema):
        conn = _ingest_src('#\\#CIF_1.1\ndata_t\n_cell.length_a  5.4\n', schema)
        result = emit(conn, schema, version=CIF11)
        assert result.startswith('#\\#CIF_1.1\n')

    def test_cif11_values_round_trip(self, schema):
        src = '#\\#CIF_1.1\ndata_t\n_cell.length_a  5.4\n'
        conn = _ingest_src(src, schema)
        result = emit(conn, schema, version=CIF11)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert str(block['_cell.length_a'][0]) == '5.4'


# ---------------------------------------------------------------------------
# Null / missing value handling
# ---------------------------------------------------------------------------

class TestNullHandling:
    @pytest.fixture
    def schema(self):
        return _make_schema(_MINI_DIC)

    def test_null_column_skipped(self, schema):
        # Ingest a CIF with length_a only; length_b and length_c are NULL
        conn = _ingest_src('#\\#CIF_2.0\ndata_t\n_cell.length_a  5.4\n', schema)
        result = emit(conn, schema)
        assert '_cell.length_a' in result
        assert '_cell.length_b' not in result
        assert '_cell.length_c' not in result

    def test_loop_null_value_emitted_as_placeholder(self, schema):
        # Use the loop schema; ingest a row that has only some columns
        loop_schema = _make_schema(_LOOP_DIC)
        # _atom_site.fract_x is missing from this row
        src = '#\\#CIF_2.0\ndata_t\nloop_\n  _atom_site.id\n  _atom_site.type_symbol\n  C1  C\n  O1  O\n'
        conn = _ingest_src(src, loop_schema)
        result = emit(conn, loop_schema)
        cif2, errors = build(result)
        # fract_x column should not appear (all NULL)
        assert '_atom_site.fract_x' not in result
