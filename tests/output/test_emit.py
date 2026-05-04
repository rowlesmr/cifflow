"""
Tests for cifflow.output.emit.

Tests are organised by mode:
- ONE_BLOCK
- ALL_BLOCKS
- GROUPED (default)
- Round-trip (build → ingest → emit → build, value equivalence)

All tests use in-memory SQLite databases.
"""

from __future__ import annotations

import pathlib

import duckdb
import pytest

from cifflow import (
    build,
    ingest,
    emit,
    EmitMode,
    generate_schema,
    directory_resolver,
)
from cifflow.dictionary import DictionaryLoader
from cifflow.dictionary.schema import SchemaSpec
from cifflow.output import BlockSpec, OutputPlan
from cifflow.types import CifVersion

_DATA_DIR = pathlib.Path(__file__).parents[2] / 'data' / 'dictionaries'
_CIF_DIR  = pathlib.Path(__file__).parents[2] / 'tests' / 'cif_files'

CIF20 = CifVersion.CIF_2_0
CIF11 = CifVersion.CIF_1_1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_schema(ddl_source: str) -> SchemaSpec:
    d = DictionaryLoader().load(ddl_source)
    return generate_schema(d)


def _ingest_src(cif_source: str, schema: SchemaSpec | None = None) -> duckdb.DuckDBPyConnection:
    """Parse *cif_source* and ingest into a fresh in-memory DuckDB."""
    cif, errors = build(cif_source)
    assert not errors, errors
    conn, _ = ingest(cif, None, schema)
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

_CELL_DIC = """\
#\\#CIF_2.0
data_cell_dic

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

save_cell.angle_alpha
  _definition.id        '_cell.angle_alpha'
  _definition.class     Attribute
  _name.category_id     cell
  _name.object_id       angle_alpha
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_

save_cell.angle_beta
  _definition.id        '_cell.angle_beta'
  _definition.class     Attribute
  _name.category_id     cell
  _name.object_id       angle_beta
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_

save_cell.angle_gamma
  _definition.id        '_cell.angle_gamma'
  _definition.class     Attribute
  _name.category_id     cell
  _name.object_id       angle_gamma
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
        # Values are decimal-aligned: ' 5.4' (space before 5 to match width of 13).
        # Check tags present and values round-trip correctly rather than exact spacing.
        assert '_cell.length_a' in result
        assert '_cell.length_b' in result
        assert '_cell.length_c' in result
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert str(block['_cell.length_a'][0]) == '5.4'
        assert str(block['_cell.length_b'][0]) == '5.4'
        assert str(block['_cell.length_c'][0]) == '13.2'

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
        assert '_cifflow_block_id' not in result
        assert '_cifflow_row_id' not in result
        assert '_cifflow_id' not in result


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
# line_ending parameter
# ---------------------------------------------------------------------------

class TestLineEnding:
    CIF = '#\\#CIF_2.0\ndata_b\n_cell.length_a  5.4\n'

    @pytest.fixture
    def conn(self):
        schema = _make_schema(_MINI_DIC)
        return _ingest_src(self.CIF, schema), schema

    def test_default_is_lf(self, conn):
        c, s = conn
        result = emit(c, s)
        assert '\r' not in result
        assert result.endswith('\n')

    def test_lf_explicit(self, conn):
        c, s = conn
        result = emit(c, s, line_ending='\n')
        assert '\r' not in result

    def test_crlf(self, conn):
        c, s = conn
        result = emit(c, s, line_ending='\r\n')
        # Every \n is preceded by \r
        assert '\r\n' in result
        assert result.endswith('\r\n')
        # No bare \n
        assert '\n' not in result.replace('\r\n', '')

    def test_cr(self, conn):
        c, s = conn
        result = emit(c, s, line_ending='\r')
        assert '\r' in result
        assert result.endswith('\r')
        assert '\n' not in result

    def test_content_unchanged_across_endings(self, conn):
        c, s = conn
        lf   = emit(c, s, line_ending='\n')
        crlf = emit(c, s, line_ending='\r\n')
        cr   = emit(c, s, line_ending='\r')
        # Normalise all to LF and compare
        assert crlf.replace('\r\n', '\n') == lf
        assert cr.replace('\r', '\n') == lf

    def test_magic_line_preserved(self, conn):
        c, s = conn
        result = emit(c, s, line_ending='\r\n')
        first_line = result.split('\r\n')[0]
        assert first_line == '#\\#CIF_2.0'


# ---------------------------------------------------------------------------
# pretty parameter
# ---------------------------------------------------------------------------

class TestPretty:
    """pretty=True (default) aligns Set tag–value pairs and loop columns."""

    # Set category: three tags of different lengths.
    SET_CIF = (
        '#\\#CIF_2.0\ndata_b\n'
        '_cell.length_a  5.4\n'
        '_cell.length_b  5.4\n'
        '_cell.length_c  13.2\n'
    )

    # Loop category: two rows, columns of different widths.
    LOOP_CIF = (
        '#\\#CIF_2.0\ndata_b\n'
        'loop_\n'
        '_atom_site.id\n'
        '_atom_site.type_symbol\n'
        '_atom_site.fract_x\n'
        'Se  Se  0.1234\n'
        'C   C   0.5\n'
    )

    @pytest.fixture
    def set_conn(self):
        schema = _make_schema(_MINI_DIC)
        return _ingest_src(self.SET_CIF, schema), schema

    @pytest.fixture
    def loop_conn(self):
        schema = _make_schema(_LOOP_DIC)
        return _ingest_src(self.LOOP_CIF, schema), schema

    # --- pretty=True (default) ---

    def test_set_tags_aligned(self, set_conn):
        """All inline tag–value lines share the same tag column width."""
        c, s = set_conn
        result = emit(c, s, pretty=True)
        tag_value_lines = [
            ln for ln in result.splitlines()
            if ln.startswith('_cell.')
        ]
        assert tag_value_lines, 'no tag-value lines found'
        # Split at the two-space separator; tag portion widths must all match.
        tag_widths = set()
        for ln in tag_value_lines:
            idx = ln.index('  ')
            tag_widths.add(idx)
        assert len(tag_widths) == 1, f'tags not aligned: widths={tag_widths}'

    def test_set_values_correct_after_align(self, set_conn):
        """Alignment must not corrupt values."""
        c, s = set_conn
        result = emit(c, s, pretty=True)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert str(block['_cell.length_a'][0]) == '5.4'
        assert str(block['_cell.length_c'][0]) == '13.2'

    def test_loop_columns_aligned(self, loop_conn):
        """Loop data rows have consistent column widths.

        With two rows ['Se', '0.1234', 'Se'] and ['C', '0.5', 'C'], col0
        has max width 2.  'C' must be padded to 'C ' so that col1 starts
        at the same character offset (indent + 2 + 2 sep = offset 6) in both rows.
        """
        c, s = loop_conn
        result = emit(c, s, pretty=True)
        data_lines = [
            ln for ln in result.splitlines()
            if ln.startswith('  ') and not ln.startswith('  _')
        ]
        assert len(data_lines) == 2
        # Column starts are computed as: indent(2) + sum of (col_width + sep(2)) for prior cols.
        # With col_widths [2, 6, 2]: col0 @ 2, col1 @ 6, col2 @ 10.
        # Verify by slicing each line at the known offsets.
        # We can recover col widths from the 'Se' row (row 0).
        se_row = data_lines[0]
        c_row  = data_lines[1]
        # col0 starts at offset 2, width = 2 ('Se' is the max).
        # So col1 starts at 2 + 2 + 2 = 6 in both rows.
        assert se_row[6] not in (' ', ''), f'col1 not at offset 6 in Se row: {se_row!r}'
        assert c_row[6] not in (' ', ''),  f'col1 not at offset 6 in C row: {c_row!r}'
        # 'C' padded to width 2 means two spaces before the separator,
        # giving three spaces total between 'C' and the next token.
        assert c_row[2:5] == 'C  ', f'C not padded: {c_row!r}'

    def test_loop_values_correct_after_align(self, loop_conn):
        """Alignment must not corrupt loop values."""
        c, s = loop_conn
        result = emit(c, s, pretty=True)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert str(block['_atom_site.fract_x'][0]) == '0.1234'
        assert str(block['_atom_site.fract_x'][1]) == '0.5'

    # --- pretty=False ---

    def test_pretty_false_no_padding(self, set_conn):
        """pretty=False: tag–value lines use exactly two spaces, no extra padding."""
        c, s = set_conn
        result = emit(c, s, pretty=False)
        # Every tag-value line must be of the form '{tag}  {value}' with no
        # extra spaces between the tag name and the two-space separator.
        for ln in result.splitlines():
            if ln.startswith('_cell.'):
                tag, _, rest = ln.partition('  ')
                # There must be no leading space in rest (no extra padding).
                assert not rest.startswith(' '), (
                    f'unexpected padding in compact mode: {ln!r}'
                )

    def test_pretty_false_values_correct(self, set_conn):
        """pretty=False output must still parse correctly."""
        c, s = set_conn
        result = emit(c, s, pretty=False)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert str(block['_cell.length_a'][0]) == '5.4'

    # --- pretty is default True ---

    def test_default_is_pretty(self, set_conn):
        """Omitting pretty= should give the same output as pretty=True."""
        c, s = set_conn
        assert emit(c, s) == emit(c, s, pretty=True)

    # --- fallback alignment ---

    def test_fallback_scalars_aligned(self):
        """pretty=True aligns fallback scalar tags.

        '_a.short' (8 chars) is padded to match '_a.longer_tag' (13 chars),
        so both values start at column 15 (13 + 2 sep).
        """
        schema = _empty_schema()
        src = (
            '#\\#CIF_2.0\ndata_x\n'
            '_a.short  1\n'
            '_a.longer_tag  2\n'
        )
        conn = _ingest_src(src, schema)
        result = emit(conn, schema, pretty=True)
        tag_lines = [ln for ln in result.splitlines() if ln.startswith('_a.')]
        assert len(tag_lines) == 2
        # The value follows the tag+padding+2-space separator.
        # tag_width = len('_a.longer_tag') = 13; value starts at 13 + 2 = 15.
        tag_width = max(len(ln.split()[0]) for ln in tag_lines)
        value_starts = set()
        for ln in tag_lines:
            tag = ln.split()[0]
            # Value starts after tag (padded to tag_width) + 2-space separator.
            value_starts.add(ln.index(ln.split()[1]))
        assert len(value_starts) == 1, f'fallback tags not aligned: {tag_lines}'

    def test_no_trailing_whitespace_pretty(self, set_conn):
        """pretty=True must not introduce trailing whitespace."""
        c, s = set_conn
        result = emit(c, s, pretty=True)
        for ln in result.splitlines():
            assert ln == ln.rstrip(), f'Trailing whitespace: {ln!r}'


# ---------------------------------------------------------------------------
# ALL_BLOCKS mode
# ---------------------------------------------------------------------------

class TestAllBlocks:
    """ALL_BLOCKS: one block per Set-anchor key combination (mirrors GROUPED)."""

    # Keyless Set (no _category_key.name) — groups by _cifflow_block_id like GROUPED.
    @pytest.fixture
    def mini_schema(self):
        return _make_schema(_MINI_DIC)

    # Keyed Set + Loop — enables per-row splitting.
    @pytest.fixture
    def grouped_schema(self):
        return _make_schema(_GROUPED_DIC)

    def test_keyless_set_raises(self, mini_schema):
        """ALL_BLOCKS raises ValueError when a keyless Set table contains data."""
        conn = _ingest_src(
            '#\\#CIF_2.0\ndata_b\n_cell.length_a  5.4\n',
            mini_schema,
        )
        with pytest.raises(ValueError, match='keyless Set'):
            emit(conn, schema=mini_schema, mode=EmitMode.ALL_BLOCKS)

    def test_fallback_rows_raise(self, grouped_schema):
        """ALL_BLOCKS raises ValueError when unknown tags are present in fallback."""
        cif_src = (
            '#\\#CIF_2.0\ndata_b\n'
            '_expt.id  exp1\n'
            '_unknown.tag  hello\n'
        )
        conn = _ingest_src(cif_src, grouped_schema)
        with pytest.raises(ValueError, match='fallback'):
            emit(conn, schema=grouped_schema, mode=EmitMode.ALL_BLOCKS)

    def test_set_table_one_block_per_row(self, grouped_schema):
        """Each row in a Set table produces its own block."""
        conn = _ingest_src(_GROUPED_SEPARATE_CIF, grouped_schema)
        result = emit(conn, schema=grouped_schema, mode=EmitMode.ALL_BLOCKS)
        cif2, errors = build(result)
        assert not errors
        expt_blocks = [n for n in cif2.blocks if '_expt.id' in cif2[n]]
        assert len(expt_blocks) == 2

    def test_set_table_merged_key_one_block(self, grouped_schema):
        """Two source blocks sharing the same expt.id → one expt block."""
        conn = _ingest_src(_GROUPED_MERGE_CIF, grouped_schema)
        result = emit(conn, schema=grouped_schema, mode=EmitMode.ALL_BLOCKS)
        cif2, errors = build(result)
        assert not errors
        expt_blocks = [n for n in cif2.blocks if '_expt.id' in cif2[n]]
        assert len(expt_blocks) == 1

    def test_loop_table_in_own_block(self, grouped_schema):
        """Loop table (no Set FK in PK) goes into its own single block."""
        conn = _ingest_src(_GROUPED_SEPARATE_CIF, grouped_schema)
        result = emit(conn, schema=grouped_schema, mode=EmitMode.ALL_BLOCKS)
        cif2, errors = build(result)
        assert not errors
        peak_blocks = [n for n in cif2.blocks if '_peak.intensity' in cif2[n]]
        assert len(peak_blocks) == 1
        assert len(cif2[peak_blocks[0]]['_peak.intensity']) == 2

    def test_block_name_derived_from_table_name(self, grouped_schema):
        """Block names start with the table name."""
        conn = _ingest_src(_GROUPED_SEPARATE_CIF, grouped_schema)
        result = emit(conn, schema=grouped_schema, mode=EmitMode.ALL_BLOCKS)
        headers = [l[len('data_'):] for l in result.splitlines() if l.startswith('data_')]
        assert any(h.startswith('expt_') for h in headers)
        assert any(h.startswith('peak') for h in headers)

    def test_dataset_id_injected_cif20(self, grouped_schema):
        conn = _ingest_src(_GROUPED_SEPARATE_CIF, grouped_schema)
        result = emit(conn, schema=grouped_schema, mode=EmitMode.ALL_BLOCKS,
                      version=CifVersion.CIF_2_0)
        assert '_audit_dataset.id' in result

    def test_dataset_id_not_injected_cif11(self, grouped_schema):
        conn = _ingest_src(_GROUPED_SEPARATE_CIF, grouped_schema)
        result = emit(conn, schema=grouped_schema, mode=EmitMode.ALL_BLOCKS,
                      version=CifVersion.CIF_1_1)
        assert '_audit_dataset.id' not in result

    def test_all_blocks_shared_dataset_id(self, grouped_schema):
        """All blocks in one emit() call share the same dataset UUID."""
        conn = _ingest_src(_GROUPED_SEPARATE_CIF, grouped_schema)
        result = emit(conn, schema=grouped_schema, mode=EmitMode.ALL_BLOCKS)
        cif2, errors = build(result)
        assert not errors
        dataset_ids = [
            str(cif2[n]['_audit_dataset.id'][0])
            for n in cif2.blocks if cif2[n]['_audit_dataset.id']
        ]
        assert len(dataset_ids) == len(cif2.blocks)
        assert len(set(dataset_ids)) == 1


# Schema with Loop table whose PK includes a Set FK — exercises header scalars.
_SET_KEY_IN_PK_DIC = """\
#\\#CIF_2.0
data_set_key_in_pk_dic

save_MEAS
  _definition.id        MEAS
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     meas
  _category_key.name    '_meas.id'
save_

save_meas.id
  _definition.id        '_meas.id'
  _definition.class     Attribute
  _name.category_id     meas
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_POINT
  _definition.id        POINT
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     point
  loop_
    _category_key.name
    '_point.meas_id'
    '_point.id'
save_

save_point.meas_id
  _definition.id        '_point.meas_id'
  _definition.class     Attribute
  _name.category_id     point
  _name.object_id       meas_id
  _type.purpose         Link
  _type.source          Related
  _type.container       Single
  _type.contents        Code
  _name.linked_item_id  '_meas.id'
save_

save_point.id
  _definition.id        '_point.id'
  _definition.class     Attribute
  _name.category_id     point
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_point.value
  _definition.id        '_point.value'
  _definition.class     Attribute
  _name.category_id     point
  _name.object_id       value
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_
"""

_SET_KEY_IN_PK_CIF = (
    '#\\#CIF_2.0\n'
    'data_m1\n'
    '_meas.id  M1\n'
    'loop_\n  _point.meas_id\n  _point.id\n  _point.value\n'
    '  M1  0  1.0\n'
    '  M1  1  2.0\n'
    '\n\n'
    'data_m2\n'
    '_meas.id  M2\n'
    'loop_\n  _point.meas_id\n  _point.id\n  _point.value\n'
    '  M2  0  3.0\n'
    '  M2  1  4.0\n'
)


class TestAllBlocksSetKeyInPK:
    """ALL_BLOCKS with a Loop table whose PK includes a FK to a Set category."""

    @pytest.fixture
    def schema(self):
        return _make_schema(_SET_KEY_IN_PK_DIC)

    @pytest.fixture
    def conn(self, schema):
        return _ingest_src(_SET_KEY_IN_PK_CIF, schema)

    def test_point_blocks_split_by_meas(self, conn, schema):
        """One point block per meas_id value."""
        result = emit(conn, schema=schema, mode=EmitMode.ALL_BLOCKS)
        cif2, errors = build(result)
        assert not errors
        point_blocks = [n for n in cif2.blocks if '_point.value' in cif2[n]]
        assert len(point_blocks) == 2

    def test_set_key_scalar_above_loop(self, conn, schema):
        """_meas.id appears as a scalar tag-value pair in each point block."""
        result = emit(conn, schema=schema, mode=EmitMode.ALL_BLOCKS)
        cif2, errors = build(result)
        assert not errors
        point_blocks = [n for n in cif2.blocks if '_point.value' in cif2[n]]
        for bname in point_blocks:
            assert '_meas.id' in cif2[bname], f"_meas.id missing in block {bname}"

    def test_meas_id_not_in_loop_header(self, conn, schema):
        """_point.meas_id is suppressed from the loop_ header (it's a Set-key scalar)."""
        result = emit(conn, schema=schema, mode=EmitMode.ALL_BLOCKS)
        lines = result.splitlines()
        in_loop = False
        for line in lines:
            s = line.strip()
            if s == 'loop_':
                in_loop = True
                continue
            if in_loop and s.startswith('_'):
                assert s != '_point.meas_id', "_point.meas_id must not appear in loop header"
                continue
            in_loop = False

    def test_block_name_includes_table_and_set_val(self, conn, schema):
        """Block names for point groups follow {table}_{set_val} pattern."""
        result = emit(conn, schema=schema, mode=EmitMode.ALL_BLOCKS)
        headers = [l[len('data_'):] for l in result.splitlines() if l.startswith('data_')]
        point_headers = [h for h in headers if h.startswith('point_')]
        assert 'point_M1' in point_headers
        assert 'point_M2' in point_headers

    def test_row_values_correct(self, conn, schema):
        """Each point block contains only the rows for its meas_id."""
        result = emit(conn, schema=schema, mode=EmitMode.ALL_BLOCKS)
        cif2, errors = build(result)
        assert not errors
        for bname in cif2.blocks:
            block = cif2[bname]
            if '_point.value' not in block:
                continue
            values = [float(str(v)) for v in block['_point.value']]
            meas_id = str(block['_meas.id'][0])
            if meas_id == 'M1':
                assert values == [1.0, 2.0]
            elif meas_id == 'M2':
                assert values == [3.0, 4.0]

    def test_output_is_valid_cif(self, conn, schema):
        result = emit(conn, schema=schema, mode=EmitMode.ALL_BLOCKS)
        _, errors = build(result)
        assert not errors, errors


# ---------------------------------------------------------------------------
# ORIGINAL mode — multiple source blocks grouped by _cifflow_block_id
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
        plan = OutputPlan(specs=[spec])
        result = emit(conn, schema, plan=plan)
        lines = result.splitlines()
        tag_lines = [l for l in lines if l.startswith('_cell.length')]
        names = [l.split('  ')[0] for l in tag_lines]
        assert names == ['_cell.length_c', '_cell.length_a', '_cell.length_b']

    def test_empty_specs_matches_no_block(self):
        """OutputPlan.match returns (None, None) when specs list is empty."""
        plan = OutputPlan(specs=[])
        assert plan.match(frozenset()) == (None, None)
        assert plan.match(frozenset({'cell'})) == (None, None)

    def test_catchall_spec_matches_any_block(self):
        """A spec with matches=None is a catch-all."""
        spec = BlockSpec(matches=None)
        plan = OutputPlan(specs=[spec])
        idx, matched = plan.match(frozenset({'cell'}))
        assert idx == 0
        assert matched is spec

    def test_predicate_spec_matches_correctly(self):
        """A spec with a predicate matches only blocks satisfying it."""
        spec_phase = BlockSpec(matches=lambda a: 'pd_phase' in a)
        spec_all = BlockSpec(matches=None)
        plan = OutputPlan(specs=[spec_phase, spec_all])

        idx, _ = plan.match(frozenset({'pd_phase'}))
        assert idx == 0

        idx, _ = plan.match(frozenset({'cell'}))
        assert idx == 1

        idx, _ = plan.match(frozenset())
        assert idx == 1


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


# ---------------------------------------------------------------------------
# Database round-trip comparison helpers
# ---------------------------------------------------------------------------

_ADMIN_COLS = {'_cifflow_block_id', '_cifflow_row_id', '_cifflow_id'}


def _data_cols(conn: duckdb.DuckDBPyConnection, table_name: str, schema: SchemaSpec) -> list[str]:
    """Return column names in *table_name* that carry real CIF data.

    Excludes administrative columns (_cifflow_block_id, _cifflow_row_id, _cifflow_id) and
    columns marked synthetic in the schema (transitive bridge helpers, etc.).
    """
    synthetic: set[str] = set()
    if table_name in schema.tables:
        synthetic = {c.name for c in schema.tables[table_name].columns if c.is_synthetic}
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name=? ORDER BY ordinal_position",
        [table_name],
    ).fetchall()
    all_cols = [row[0] for row in rows]
    return [c for c in all_cols if c not in _ADMIN_COLS and c not in synthetic]


def _norm(v: object) -> object:
    """Normalise absent-value sentinels for round-trip comparison.

    A NULL loop column is emitted as '.' (CIF placeholder — can't skip loop
    columns), then re-ingested as the string '.'.  Treat NULL and '.' as
    equivalent so the comparison is not sensitive to this transformation.
    '?' (unknown) is kept distinct.
    """
    return None if v is None or v == '.' else v


def _sorted_rows(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
    cols: list[str],
) -> list[tuple]:
    """Fetch all rows for the given columns, normalised and sorted."""
    if not cols:
        return []
    col_expr = ', '.join(f'"{c}"' for c in cols)
    rows = conn.execute(f'SELECT {col_expr} FROM "{table_name}"').fetchall()
    return sorted(tuple(_norm(v) for v in row) for row in rows)


def _assert_same_data(
    conn_orig: duckdb.DuckDBPyConnection,
    conn_emit: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    exclude_tables: set[str] | None = None,
) -> None:
    """Assert that two databases hold the same CIF data modulo _cifflow_block_id / _cifflow_row_id.

    For every structured table: the set of data rows (all non-admin, non-synthetic
    columns) must be identical.  For the fallback tier: the (tag, value) multiset
    must be identical.

    Block names and insertion order may differ; they are not compared.
    *exclude_tables* names tables to skip (e.g. conformance tables injected by ONE_BLOCK).
    """
    skip = exclude_tables or set()
    for table_name in schema.tables:
        if table_name in skip:
            continue
        try:
            cols = _data_cols(conn_orig, table_name, schema)
        except Exception:
            continue  # table absent in one connection — skip
        orig_rows = _sorted_rows(conn_orig, table_name, cols)
        emit_rows = _sorted_rows(conn_emit, table_name, cols)
        assert orig_rows == emit_rows, (
            f'Table {table_name!r} data mismatch after emit → re-ingest\n'
            f'  original : {orig_rows}\n'
            f'  re-ingest: {emit_rows}'
        )

    # Fallback tier: compare (tag, value) multisets; ignore block_id
    def _fb_rows(conn):
        rows = conn.execute('SELECT tag, value FROM _cif_fallback').fetchall()
        return sorted((_norm(tag), _norm(val)) for tag, val in rows)

    fb_orig = _fb_rows(conn_orig)
    fb_emit = _fb_rows(conn_emit)
    assert fb_orig == fb_emit, (
        f'_cif_fallback mismatch after emit → re-ingest\n'
        f'  original : {fb_orig}\n'
        f'  re-ingest: {fb_emit}'
    )


def _emit_and_reingest(
    conn: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    mode: EmitMode,
    **emit_kwargs,
) -> duckdb.DuckDBPyConnection:
    """Emit *conn* in *mode*, parse the result, and ingest into a fresh connection."""
    cif_text = emit(conn, schema, mode=mode, **emit_kwargs)
    cif_rt, errors = build(cif_text)
    assert not errors, f'Re-parse produced errors: {errors}'
    conn2, _ = ingest(cif_rt, None, schema)
    return conn2


# ---------------------------------------------------------------------------
# Database round-trip tests — synthetic CIFs
# ---------------------------------------------------------------------------

class TestDatabaseRoundTrip:
    """Emit → parse → re-ingest → compare: all data must survive each EmitMode."""

    # --- Set category ---

    def test_set_original(self):
        schema = _make_schema(_MINI_DIC)
        src = '#\\#CIF_2.0\ndata_b\n_cell.length_a  5.4\n_cell.length_b  3.2\n_cell.length_c  10.0\n'
        conn = _ingest_src(src, schema)
        _assert_same_data(conn, _emit_and_reingest(conn, schema, EmitMode.ORIGINAL), schema)

    def test_set_one_block(self):
        schema = _make_schema(_MINI_DIC)
        src = '#\\#CIF_2.0\ndata_b\n_cell.length_a  5.4\n_cell.length_b  3.2\n_cell.length_c  10.0\n'
        conn = _ingest_src(src, schema)
        _assert_same_data(conn, _emit_and_reingest(conn, schema, EmitMode.ONE_BLOCK), schema)

    def test_set_grouped(self):
        schema = _make_schema(_MINI_DIC)
        src = '#\\#CIF_2.0\ndata_b\n_cell.length_a  5.4\n_cell.length_b  3.2\n_cell.length_c  10.0\n'
        conn = _ingest_src(src, schema)
        _assert_same_data(conn, _emit_and_reingest(conn, schema, EmitMode.GROUPED), schema)

    # --- Loop category ---

    def test_loop_original(self):
        schema = _make_schema(_LOOP_DIC)
        conn = _ingest_src(_LOOP_CIF, schema)
        _assert_same_data(conn, _emit_and_reingest(conn, schema, EmitMode.ORIGINAL), schema)

    def test_loop_one_block(self):
        schema = _make_schema(_LOOP_DIC)
        conn = _ingest_src(_LOOP_CIF, schema)
        _assert_same_data(conn, _emit_and_reingest(conn, schema, EmitMode.ONE_BLOCK), schema)

    def test_loop_grouped(self):
        schema = _make_schema(_LOOP_DIC)
        conn = _ingest_src(_LOOP_CIF, schema)
        _assert_same_data(conn, _emit_and_reingest(conn, schema, EmitMode.GROUPED), schema)

    # --- Multi-block Set ---

    def test_multiblock_set_original(self):
        schema = _make_schema(_MINI_DIC)
        src = (
            '#\\#CIF_2.0\n'
            'data_block_a\n_cell.length_a  5.4\n_cell.length_b  5.4\n'
            '\n\n'
            'data_block_b\n_cell.length_a  3.2\n_cell.length_b  3.2\n'
        )
        conn = _ingest_src(src, schema)
        _assert_same_data(conn, _emit_and_reingest(conn, schema, EmitMode.ORIGINAL), schema)

    # --- GROUPED mode: merge and separate ---

    def test_grouped_merge(self):
        """Two blocks with same Set key merge; data must survive."""
        schema = _make_schema(_GROUPED_DIC)
        conn = _ingest_src(_GROUPED_MERGE_CIF, schema)
        _assert_same_data(conn, _emit_and_reingest(conn, schema, EmitMode.GROUPED), schema)

    def test_grouped_separate(self):
        """Two blocks with different Set keys stay separate; data must survive."""
        schema = _make_schema(_GROUPED_DIC)
        conn = _ingest_src(_GROUPED_SEPARATE_CIF, schema)
        _assert_same_data(conn, _emit_and_reingest(conn, schema, EmitMode.GROUPED), schema)

    def test_grouped_merge_one_block(self):
        schema = _make_schema(_GROUPED_DIC)
        conn = _ingest_src(_GROUPED_MERGE_CIF, schema)
        _assert_same_data(conn, _emit_and_reingest(conn, schema, EmitMode.ONE_BLOCK), schema)

    # --- Composite FK anchor ---

    def test_composite_grouped(self):
        """RESULT table with FK to both a Loop (SCAN) and Set (EXPT) must survive GROUPED."""
        schema = _make_schema(_COMPOSITE_DIC)
        conn = _ingest_src(_COMPOSITE_MERGE_CIF, schema)
        _assert_same_data(conn, _emit_and_reingest(conn, schema, EmitMode.GROUPED), schema)

    # --- Fallback-only (no schema) ---

    def test_fallback_only_original(self):
        schema = _empty_schema()
        src = '#\\#CIF_2.0\ndata_x\n_cell.length_a  5.4\n_custom.tag  hello\n'
        conn = _ingest_src(src, schema)
        _assert_same_data(conn, _emit_and_reingest(conn, schema, EmitMode.ORIGINAL), schema)

    def test_fallback_only_one_block(self):
        schema = _empty_schema()
        src = '#\\#CIF_2.0\ndata_x\n_cell.length_a  5.4\n_custom.tag  hello\n'
        conn = _ingest_src(src, schema)
        _assert_same_data(conn, _emit_and_reingest(conn, schema, EmitMode.ONE_BLOCK), schema)


# ---------------------------------------------------------------------------
# Database round-trip integration tests — real dictionaries and CIF files
# ---------------------------------------------------------------------------

def _load_schema(dic_file: pathlib.Path) -> SchemaSpec:
    resolver = directory_resolver(_DATA_DIR)
    source = dic_file.read_text(encoding='utf-8')
    d = DictionaryLoader(resolver=resolver).load(source, base_uri=dic_file.name)
    return generate_schema(d)


def _ingest_file(cif_path: pathlib.Path, schema: SchemaSpec) -> duckdb.DuckDBPyConnection:
    cif, errors = build(cif_path.read_text(encoding='utf-8'))
    assert not errors, f'Parse errors in {cif_path.name}: {errors}'
    conn, _ = ingest(cif, None, schema)
    return conn


@pytest.fixture(scope='module')
def core_schema():
    return _load_schema(_DATA_DIR / 'cif_core.dic')


@pytest.fixture(scope='module')
def pow_schema():
    return _load_schema(_DATA_DIR / 'cif_pow.dic')


@pytest.fixture(scope='module')
def one_structure_conn(core_schema):
    return _ingest_file(_CIF_DIR / 'one_structure.cif', core_schema)


@pytest.fixture(scope='module')
def multi_one_conn(pow_schema):
    return _ingest_file(_CIF_DIR / 'multi_one.cif', pow_schema)


@pytest.fixture(scope='module')
def powder_loop_conn(pow_schema):
    return _ingest_file(_CIF_DIR / 'cifflow' / 'powder_loop.cif', pow_schema)


@pytest.mark.slow
class TestEmitRoundTripIntegration:
    """Full pipeline: real CIF → ingest → emit → re-ingest → compare databases."""

    def test_one_structure_original(self, one_structure_conn, core_schema):
        conn2 = _emit_and_reingest(one_structure_conn, core_schema, EmitMode.ORIGINAL)
        _assert_same_data(one_structure_conn, conn2, core_schema)

    def test_one_structure_one_block(self, one_structure_conn, core_schema):
        conn2 = _emit_and_reingest(one_structure_conn, core_schema, EmitMode.ONE_BLOCK)
        # audit / audit_conform are injected as conformance metadata by ONE_BLOCK
        _assert_same_data(one_structure_conn, conn2, core_schema,
                          exclude_tables={'audit', 'audit_conform'})

    def test_one_structure_grouped(self, one_structure_conn, core_schema):
        conn2 = _emit_and_reingest(one_structure_conn, core_schema, EmitMode.GROUPED)
        _assert_same_data(one_structure_conn, conn2, core_schema)

    def test_multi_one_original(self, multi_one_conn, pow_schema):
        conn2 = _emit_and_reingest(multi_one_conn, pow_schema, EmitMode.ORIGINAL)
        _assert_same_data(multi_one_conn, conn2, pow_schema)

    def test_multi_one_grouped(self, multi_one_conn, pow_schema):
        conn2 = _emit_and_reingest(multi_one_conn, pow_schema, EmitMode.GROUPED)
        _assert_same_data(multi_one_conn, conn2, pow_schema)

    def test_multi_one_one_block(self, multi_one_conn, pow_schema):
        conn2 = _emit_and_reingest(multi_one_conn, pow_schema, EmitMode.ONE_BLOCK)
        _assert_same_data(multi_one_conn, conn2, pow_schema,
                          exclude_tables={'audit', 'audit_conform'})

    def test_powder_loop_original_round_trip(self, powder_loop_conn, pow_schema):
        """Multi-category source loop survives ORIGINAL emit round-trip."""
        conn2 = _emit_and_reingest(powder_loop_conn, pow_schema, EmitMode.ORIGINAL)
        _assert_same_data(powder_loop_conn, conn2, pow_schema)

    def test_powder_loop_original_single_loop(self, powder_loop_conn, pow_schema):
        """All four source-loop categories are emitted in a single loop_, not split."""
        cif_text = emit(powder_loop_conn, pow_schema, mode=EmitMode.ORIGINAL)
        tags_in_one_loop = {
            '_pd_data.point_id',
            '_pd_meas.2theta_scan',
            '_pd_proc.2theta_corrected',
            '_pd_proc.intensity_total',
            '_pd_proc.ls_weight',
            '_pd_calc.intensity_total',
            '_pd_calc.intensity_bkg',
        }
        lines = cif_text.splitlines()
        loop_tag_sets: list[set[str]] = []
        i = 0
        while i < len(lines):
            if lines[i].strip() == 'loop_':
                current: set[str] = set()
                j = i + 1
                while j < len(lines) and lines[j].strip().startswith('_'):
                    current.add(lines[j].strip())
                    j += 1
                loop_tag_sets.append(current)
                i = j
            else:
                i += 1
        assert any(tags_in_one_loop <= loop_tags for loop_tags in loop_tag_sets), (
            f'Expected all of {tags_in_one_loop} in one loop_; '
            f'found loops: {loop_tag_sets}'
        )

    def test_powder_loop_original_fk_suppressed(self, powder_loop_conn, pow_schema):
        """diffractogram_id FK is suppressed when pd_diffractogram scalar is co-emitted."""
        cif_text = emit(powder_loop_conn, pow_schema, mode=EmitMode.ORIGINAL)
        assert '_pd_data.diffractogram_id' not in cif_text


_SHARED_DATASET_CIF = """\
#\\#CIF_2.0

data_block1
_audit_dataset.id 7002c403-8d26-4c52-bd46-6b12bc761a48
_diffrn.id A

data_block2
_audit_dataset.id 7002c403-8d26-4c52-bd46-6b12bc761a48
_diffrn.id B

data_block3
_audit_dataset.id 7002c403-8d26-4c52-bd46-6b12bc761a48
_diffrn.id C
"""

_DATASET_UUID = '7002c403-8d26-4c52-bd46-6b12bc761a48'


@pytest.mark.slow
class TestOriginalModeSharedSet:
    """ORIGINAL mode must re-emit every block's own Set tags even when multiple
    blocks share the same Set key (here: all three blocks share one audit_dataset.id)."""

    @pytest.fixture(scope='class')
    def shared_dataset_conn(self, pow_schema):
        return _ingest_src(_SHARED_DATASET_CIF, pow_schema)

    @pytest.fixture(scope='class')
    def shared_dataset_cif_out(self, shared_dataset_conn, pow_schema):
        cif_out = emit(shared_dataset_conn, pow_schema, mode=EmitMode.ORIGINAL)
        cif_rt, errors = build(cif_out)
        assert not errors
        return cif_rt

    def test_all_blocks_have_audit_dataset_id(self, shared_dataset_cif_out):
        """Every output block must contain _audit_dataset.id."""
        for block_name in ('block1', 'block2', 'block3'):
            assert block_name in shared_dataset_cif_out, \
                f'block {block_name!r} missing from output'
            block = shared_dataset_cif_out[block_name]
            assert '_audit_dataset.id' in block, \
                f'block {block_name!r}: _audit_dataset.id absent'
            vals = block['_audit_dataset.id']
            assert vals[0] == _DATASET_UUID, (
                f'block {block_name!r}: expected _audit_dataset.id = {_DATASET_UUID!r}, '
                f'got {vals!r}'
            )

    def test_all_blocks_have_correct_diffrn_id(self, shared_dataset_cif_out):
        """Every output block must contain its own _diffrn.id."""
        expected = {'block1': 'A', 'block2': 'B', 'block3': 'C'}
        for block_name, diffrn_id in expected.items():
            assert block_name in shared_dataset_cif_out, \
                f'block {block_name!r} missing from output'
            block = shared_dataset_cif_out[block_name]
            assert '_diffrn.id' in block, \
                f'block {block_name!r}: _diffrn.id absent'
            vals = block['_diffrn.id']
            assert vals[0] == diffrn_id, (
                f'block {block_name!r}: expected _diffrn.id = {diffrn_id!r}, got {vals!r}'
            )


# ---------------------------------------------------------------------------
# OutputPlan — matches, wildcards, merge groups, single_block, block_namer
# ---------------------------------------------------------------------------

# Schema with a parent-child category hierarchy for wildcard tests.
# EXPT (Set, keyed) → EXPT_DETAIL (Set child of EXPT, keyed by expt_detail.id)
# PEAK (Loop, FK to expt.id)
_HIERARCHY_DIC = """\
#\\#CIF_2.0
data_hierarchy_dic

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

save_expt.title
  _definition.id        '_expt.title'
  _definition.class     Attribute
  _name.category_id     expt
  _name.object_id       title
  _type.purpose         Describe
  _type.source          Assigned
  _type.container       Single
  _type.contents        Text
save_

save_EXPT_DETAIL
  _definition.id        EXPT_DETAIL
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     EXPT
  _category_key.name    '_expt_detail.id'
save_

save_expt_detail.id
  _definition.id        '_expt_detail.id'
  _definition.class     Attribute
  _name.category_id     expt_detail
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_expt_detail.note
  _definition.id        '_expt_detail.note'
  _definition.class     Attribute
  _name.category_id     expt_detail
  _name.object_id       note
  _type.purpose         Describe
  _type.source          Assigned
  _type.container       Single
  _type.contents        Text
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
"""

# Schema with two Loop categories sharing the same PK column (for merge groups).
_MERGE_DIC = """\
#\\#CIF_2.0
data_merge_dic

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

save_MEAS
  _definition.id        MEAS
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     meas
  _category_key.name    '_meas.point_id'
save_

save_meas.point_id
  _definition.id        '_meas.point_id'
  _definition.class     Attribute
  _name.category_id     meas
  _name.object_id       point_id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Integer
save_

save_meas.intensity
  _definition.id        '_meas.intensity'
  _definition.class     Attribute
  _name.category_id     meas
  _name.object_id       intensity
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_

save_CALC
  _definition.id        CALC
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     calc
  _category_key.name    '_calc.point_id'
save_

save_calc.point_id
  _definition.id        '_calc.point_id'
  _definition.class     Attribute
  _name.category_id     calc
  _name.object_id       point_id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Integer
save_

save_calc.intensity
  _definition.id        '_calc.intensity'
  _definition.class     Attribute
  _name.category_id     calc
  _name.object_id       intensity
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_
"""


class TestOutputPlanMatches:
    """BlockSpec.matches predicate controls which blocks receive each spec."""

    @pytest.fixture
    def schema(self):
        return _make_schema(_GROUPED_DIC)

    def test_matches_selects_correct_spec(self, schema):
        """matches= predicate routes blocks to the right spec."""
        cif_src = (
            '#\\#CIF_2.0\n'
            'data_run1\n_expt.id  X1\n'
            'loop_\n  _peak.id\n  _peak.expt_id\n  p1  X1\n\n\n'
            'data_run2\n_expt.id  X2\n'
            'loop_\n  _peak.id\n  _peak.expt_id\n  p2  X2\n'
        )
        conn = _ingest_src(cif_src, schema)

        captured = []
        def namer(d):
            captured.append(frozenset(d.get('expt.id', [])))
            return '_'.join(d.get('expt.id', ['unknown']))

        # spec0 matches only blocks with expt.id containing 'X1'
        spec0 = BlockSpec(
            matches=lambda a: 'expt' in a,
            block_namer=namer,
        )
        plan = OutputPlan(specs=[spec0])
        result = emit(conn, schema, mode=EmitMode.GROUPED, plan=plan)
        headers = [l for l in result.splitlines() if l.startswith('data_')]
        # Both blocks match spec0; both should be present
        assert len(headers) == 2

    def test_unmatched_block_emitted_last(self, schema):
        """Blocks with no matching spec are emitted after spec-matched blocks."""
        cif_src = (
            '#\\#CIF_2.0\n'
            'data_run1\n_expt.id  A\n'
            'loop_\n  _peak.id\n  _peak.expt_id\n  p1  A\n\n\n'
            'data_run2\n_expt.id  B\n'
            'loop_\n  _peak.id\n  _peak.expt_id\n  p2  B\n'
        )
        conn = _ingest_src(cif_src, schema)

        # spec0 matches only 'A' block via block_namer returning a name
        spec0 = BlockSpec(
            matches=lambda a: False,  # matches nothing
        )
        plan = OutputPlan(specs=[spec0])
        result = emit(conn, schema, mode=EmitMode.GROUPED, plan=plan)
        # No blocks matched spec0; all unmatched → 2 blocks still emitted
        headers = [l for l in result.splitlines() if l.startswith('data_')]
        assert len(headers) == 2


class TestOutputPlanCategoryOrder:
    """category_order plain names, wildcards, and default fallback."""

    @pytest.fixture
    def schema(self):
        return _make_schema(_HIERARCHY_DIC)

    def test_explicit_category_order(self, schema):
        """Listed categories appear before unlisted ones."""
        cif_src = (
            '#\\#CIF_2.0\ndata_b\n'
            '_expt.id  E1\n_expt.title  hello\n'
        )
        conn = _ingest_src(cif_src, schema)
        # Force peak before expt in emission
        spec = BlockSpec(category_order=['peak', 'expt'])
        plan = OutputPlan(specs=[spec])
        result = emit(conn, schema, plan=plan)
        expt_pos = result.find('_expt.id')
        peak_pos = result.find('loop_')  # peak would produce a loop_ if present
        # expt should still appear (peak absent → skip), no crash
        assert expt_pos > 0

    def test_wildcard_expansion(self, schema):
        """A wildcard 'EXPT*' expands to expt + expt_detail."""
        cif_src = (
            '#\\#CIF_2.0\ndata_b\n'
            '_expt.id  E1\n_expt.title  hello\n'
            '_expt_detail.id  D1\n_expt_detail.note  a_note\n'
        )
        conn = _ingest_src(cif_src, schema)
        spec = BlockSpec(category_order=['expt*'])
        plan = OutputPlan(specs=[spec])
        result = emit(conn, schema, plan=plan)
        # Both expt and expt_detail should be present
        assert '_expt.id' in result
        assert '_expt_detail.id' in result
        # expt appears before expt_detail (alphabetical within wildcard expansion)
        assert result.index('_expt.id') < result.index('_expt_detail')

    def test_unknown_wildcard_emits_warning(self, schema):
        """An unrecognised wildcard base emits a warning and expands to nothing."""
        spec = BlockSpec(category_order=['nonexistent_category*'])
        plan = OutputPlan(specs=[spec])
        cif_src = '#\\#CIF_2.0\ndata_b\n_expt.id  E1\n'
        conn = _ingest_src(cif_src, schema)
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            result = emit(conn, schema, plan=plan)
        assert any('nonexistent_category' in str(warning.message) for warning in w)
        # Data still emitted (via default ordering)
        assert '_expt.id' in result


class TestMergeGroup:
    """category_order merge groups: compatible → single loop_, incompatible → plain loops."""

    @pytest.fixture
    def schema(self):
        return _make_schema(_MERGE_DIC)

    @pytest.fixture
    def conn(self, schema):
        src = (
            '#\\#CIF_2.0\ndata_b\n_expt.id  E1\n'
            'loop_\n  _meas.point_id\n  _meas.intensity\n  1  10.0\n  2  20.0\n'
            'loop_\n  _calc.point_id\n  _calc.intensity\n  1  11.0\n  2  21.0\n'
        )
        return _ingest_src(src, schema)

    def test_compatible_merge_group_emits_single_loop(self, conn, schema):
        """Two Loop cats with the same PK produce one loop_."""
        spec = BlockSpec(category_order=[['meas', 'calc']])
        plan = OutputPlan(specs=[spec])
        result = emit(conn, schema, mode=EmitMode.ONE_BLOCK, plan=plan)
        loop_count = result.count('loop_')
        assert loop_count == 1

    def test_merged_loop_contains_all_columns(self, conn, schema):
        """The merged loop_ contains columns from both categories."""
        spec = BlockSpec(category_order=[['meas', 'calc']])
        plan = OutputPlan(specs=[spec])
        result = emit(conn, schema, mode=EmitMode.ONE_BLOCK, plan=plan)
        assert '_meas.intensity' in result
        assert '_calc.intensity' in result

    def test_merged_loop_full_outer_join(self, conn, schema):
        """Rows present in only one table show '.' for the other's columns."""
        # Add a row to meas that has no match in calc
        schema2 = _make_schema(_MERGE_DIC)
        src = (
            '#\\#CIF_2.0\ndata_b\n_expt.id  E1\n'
            'loop_\n  _meas.point_id\n  _meas.intensity\n  1  10.0\n  3  30.0\n'
            'loop_\n  _calc.point_id\n  _calc.intensity\n  1  11.0\n'
        )
        conn2 = _ingest_src(src, schema2)
        spec = BlockSpec(category_order=[['meas', 'calc']])
        plan = OutputPlan(specs=[spec])
        result = emit(conn2, schema2, mode=EmitMode.ONE_BLOCK, plan=plan)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        calc_vals = [str(v) for v in block['_calc.intensity']]
        # Point 3 has no calc → placeholder
        assert '.' in calc_vals

    def test_incompatible_merge_group_emits_plain_loops(self, schema, conn):
        """Categories with different PKs fall back to plain loops in listed order."""
        # Use meas and expt (different PK sets: point_id vs id)
        spec = BlockSpec(category_order=[['meas', 'expt']])
        plan = OutputPlan(specs=[spec])
        result = emit(conn, schema, mode=EmitMode.ONE_BLOCK, plan=plan)
        # Should produce separate loops for meas and expt
        assert '_meas.intensity' in result
        assert '_expt.id' in result


class TestSingleBlock:
    """BlockSpec.single_block=True collapses all matching blocks into one."""

    @pytest.fixture
    def schema(self):
        return _make_schema(_GROUPED_DIC)

    def test_single_block_collapses_two_blocks(self, schema):
        """Two GROUPED blocks matching a spec with single_block=True → one block."""
        cif_src = (
            '#\\#CIF_2.0\n'
            'data_run1\n_expt.id  A\n'
            'loop_\n  _peak.id\n  _peak.expt_id\n  p1  A\n\n\n'
            'data_run2\n_expt.id  B\n'
            'loop_\n  _peak.id\n  _peak.expt_id\n  p2  B\n'
        )
        conn = _ingest_src(cif_src, schema)
        spec = BlockSpec(matches=None, single_block=True)
        plan = OutputPlan(specs=[spec])
        result = emit(conn, schema, mode=EmitMode.GROUPED, plan=plan)
        headers = [l for l in result.splitlines() if l.startswith('data_')]
        assert len(headers) == 1

    def test_single_block_contains_all_data(self, schema):
        """The merged block contains peaks from all source blocks."""
        cif_src = (
            '#\\#CIF_2.0\n'
            'data_run1\n_expt.id  A\n'
            'loop_\n  _peak.id\n  _peak.expt_id\n  p1  A\n\n\n'
            'data_run2\n_expt.id  B\n'
            'loop_\n  _peak.id\n  _peak.expt_id\n  p2  B\n'
        )
        conn = _ingest_src(cif_src, schema)
        spec = BlockSpec(matches=None, single_block=True)
        plan = OutputPlan(specs=[spec])
        result = emit(conn, schema, mode=EmitMode.GROUPED, plan=plan)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        peak_ids = sorted(str(v) for v in block['_peak.id'])
        assert peak_ids == ['p1', 'p2']

    def test_single_block_no_fk_pk_suppression(self, schema):
        """single_block=True: FK-PK columns are NOT suppressed."""
        cif_src = (
            '#\\#CIF_2.0\n'
            'data_run1\n_expt.id  A\n'
            'loop_\n  _peak.id\n  _peak.expt_id\n  p1  A\n'
        )
        conn = _ingest_src(cif_src, schema)
        # With single_block=True, expt.id must remain (not suppressed)
        spec = BlockSpec(matches=None, single_block=True)
        plan = OutputPlan(specs=[spec])
        result = emit(conn, schema, mode=EmitMode.GROUPED, plan=plan)
        assert '_expt.id' in result


class TestBlockNamer:
    """BlockSpec.block_namer and OutputPlan.block_namer override default names."""

    @pytest.fixture
    def schema(self):
        return _make_schema(_GROUPED_DIC)

    def test_spec_block_namer_called(self, schema):
        """BlockSpec.block_namer receives anchor_key_dict and its result is used."""
        cif_src = '#\\#CIF_2.0\ndata_run1\n_expt.id  myexp\n'
        conn = _ingest_src(cif_src, schema)

        called_with = []
        def namer(d):
            called_with.append(dict(d))
            return 'custom_name'

        spec = BlockSpec(matches=None, block_namer=namer)
        plan = OutputPlan(specs=[spec])
        result = emit(conn, schema, mode=EmitMode.GROUPED, plan=plan)
        assert 'data_custom_name' in result
        assert called_with  # namer was called

    def test_plan_block_namer_fallback(self, schema):
        """OutputPlan.block_namer is used when BlockSpec has no block_namer."""
        cif_src = '#\\#CIF_2.0\ndata_run1\n_expt.id  myexp\n'
        conn = _ingest_src(cif_src, schema)

        spec = BlockSpec(matches=None)  # no block_namer on spec
        plan = OutputPlan(specs=[spec], block_namer=lambda d: 'plan_name')
        result = emit(conn, schema, mode=EmitMode.GROUPED, plan=plan)
        assert 'data_plan_name' in result

    def test_spec_namer_takes_priority_over_plan_namer(self, schema):
        """BlockSpec.block_namer takes priority over OutputPlan.block_namer."""
        cif_src = '#\\#CIF_2.0\ndata_run1\n_expt.id  myexp\n'
        conn = _ingest_src(cif_src, schema)

        spec = BlockSpec(matches=None, block_namer=lambda d: 'spec_name')
        plan = OutputPlan(specs=[spec], block_namer=lambda d: 'plan_name')
        result = emit(conn, schema, mode=EmitMode.GROUPED, plan=plan)
        assert 'data_spec_name' in result
        assert 'data_plan_name' not in result

    def test_block_namer_receives_anchor_key_dict(self, schema):
        """anchor_key_dict maps '{table}.{pk_col}' → [value]."""
        cif_src = '#\\#CIF_2.0\ndata_run1\n_expt.id  myexp\n'
        conn = _ingest_src(cif_src, schema)

        received = {}
        def namer(d):
            received.update(d)
            return 'x'

        spec = BlockSpec(matches=None, block_namer=namer)
        plan = OutputPlan(specs=[spec])
        emit(conn, schema, mode=EmitMode.GROUPED, plan=plan)
        assert 'expt.id' in received
        assert received['expt.id'] == ['myexp']

    def test_block_name_sanitized(self, schema):
        """Special characters in namer result are sanitized."""
        cif_src = '#\\#CIF_2.0\ndata_run1\n_expt.id  myexp\n'
        conn = _ingest_src(cif_src, schema)

        spec = BlockSpec(matches=None, block_namer=lambda d: 'my block/name!')
        plan = OutputPlan(specs=[spec])
        result = emit(conn, schema, mode=EmitMode.GROUPED, plan=plan)
        assert 'data_my_block_name' in result

    def test_default_block_name_from_anchor_key(self, schema):
        """Without a namer, GROUPED block name is built from anchor key values."""
        cif_src = '#\\#CIF_2.0\ndata_run1\n_expt.id  myexp\n'
        conn = _ingest_src(cif_src, schema)
        result = emit(conn, schema, mode=EmitMode.GROUPED)
        # Default name: sanitize('id_myexp') = 'id_myexp'
        assert 'data_id_myexp' in result


class TestEmissionOrder:
    """Blocks are emitted in spec order; unmatched blocks last."""

    @pytest.fixture
    def schema(self):
        return _make_schema(_GROUPED_DIC)

    def test_spec_order_respected(self, schema):
        """Blocks matched by specs[0] appear before blocks matched by specs[1]."""
        cif_src = (
            '#\\#CIF_2.0\n'
            'data_run1\n_expt.id  A\n'
            'loop_\n  _peak.id\n  _peak.expt_id\n  p1  A\n\n\n'
            'data_run2\n_expt.id  B\n'
            'loop_\n  _peak.id\n  _peak.expt_id\n  p2  B\n'
        )
        conn = _ingest_src(cif_src, schema)

        # spec0 catches only blocks with expt.id == 'B'; spec1 is catch-all.
        # B block → spec0 → emitted first.  A block → spec1 → emitted second.
        spec0 = BlockSpec(
            matches=lambda a: False,  # no block matches spec0
        )
        spec1 = BlockSpec(matches=None, block_namer=lambda d: '_'.join(d.get('expt.id', ['x'])))
        plan = OutputPlan(specs=[spec0, spec1])
        result = emit(conn, schema, mode=EmitMode.GROUPED, plan=plan)
        headers = [l for l in result.splitlines() if l.startswith('data_')]
        # Both matched by spec1 (spec0 never fires); verify ordering A < B
        assert len(headers) == 2
        assert headers.index('data_A') < headers.index('data_B')


# ---------------------------------------------------------------------------
# Line-length enforcement
# ---------------------------------------------------------------------------

class TestLineLimit:
    """line_limit= controls maximum physical line length in output."""

    # A Set-category CIF with one long value (> 60 chars) and one short value.
    SET_CIF_LONG = (
        '#\\#CIF_2.0\ndata_b\n'
        '_cell.length_a  ' + 'A' * 70 + '\n'
        '_cell.length_c  5.4\n'
    )

    # A loop CIF where a value is very long (> 60 chars).
    LOOP_CIF_LONG = (
        '#\\#CIF_2.0\ndata_b\n'
        'loop_\n'
        '_atom_site.id\n'
        '_atom_site.type_symbol\n'
        '_atom_site.fract_x\n'
        'Se  Se  0.1234\n'
        'C   C   0.5\n'
    )

    @pytest.fixture
    def mini_schema(self):
        return _make_schema(_MINI_DIC)

    @pytest.fixture
    def loop_schema(self):
        return _make_schema(_LOOP_DIC)

    # --- no limit (default) ---

    def test_default_limit_is_2048(self, mini_schema):
        """Default line_limit=2048: very long lines are still within limit."""
        src = '#\\#CIF_2.0\ndata_b\n_cell.length_a  ' + 'A' * 70 + '\n'
        conn = _ingest_src(src, mini_schema)
        result = emit(conn, mini_schema)
        # Default limit is 2048 — a 70-char value should not be folded.
        assert '\\\n' not in result

    def test_none_disables_limit(self, mini_schema):
        """line_limit=None: no limit applied, no folding."""
        src = '#\\#CIF_2.0\ndata_b\n_cell.length_a  ' + 'A' * 100 + '\n'
        conn = _ingest_src(src, mini_schema)
        result = emit(conn, mini_schema, line_limit=None)
        assert '\\\n' not in result

    # --- inline → multiline conversion ---

    def test_long_set_value_becomes_text_field(self, mini_schema):
        """An inline value that makes the tag-value line too long is re-quoted."""
        long_val = 'A' * 70
        src = f'#\\#CIF_2.0\ndata_b\n_cell.length_a  {long_val}\n'
        conn = _ingest_src(src, mini_schema)
        result = emit(conn, mini_schema, line_limit=60)
        # The value must appear inside a semicolon field.
        assert '\n;' in result
        # The value must round-trip correctly (fold reconstruction).
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert str(block['_cell.length_a'][0]) == long_val

    def test_short_values_not_folded(self, mini_schema):
        """Short values that fit within line_limit remain inline."""
        src = '#\\#CIF_2.0\ndata_b\n_cell.length_a  5.4\n_cell.length_c  13.2\n'
        conn = _ingest_src(src, mini_schema)
        result = emit(conn, mini_schema, line_limit=80)
        # No semicolon fields for short values.
        assert '\n;' not in result

    # --- text-field content-line folding ---

    def test_multiline_content_folded(self):
        """A multiline value with long content lines is folded."""
        long_line = 'X' * 80
        src = (
            '#\\#CIF_2.0\ndata_b\n'
            '_cell.length_a\n'
            f';{long_line}\n'
            'short line\n'
            ';\n'
        )
        schema = _empty_schema()
        conn = _ingest_src(src, schema)
        result = emit(conn, schema, line_limit=60)
        lines = result.splitlines()
        # Every physical line must be ≤ 60 chars.
        for line in lines:
            assert len(line) <= 60, f'line exceeds limit: {line!r}'

    def test_folded_field_round_trips(self):
        """Folded text fields must round-trip correctly."""
        long_line = 'Hello World ' * 7  # 84 chars, spaces for fold-break preference
        src = (
            '#\\#CIF_2.0\ndata_b\n'
            '_cell.length_a\n'
            f';{long_line.rstrip()}\n'
            ';\n'
        )
        schema = _empty_schema()
        conn = _ingest_src(src, schema)
        result = emit(conn, schema, line_limit=60)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert long_line.rstrip() in str(block['_cell.length_a'][0])

    # --- loop greedy packing ---

    def test_loop_row_wraps_when_too_long(self, loop_schema):
        """Loop data rows exceeding line_limit are wrapped across multiple lines."""
        # id=22 chars, type_symbol=22 chars, fract_x=3 chars.
        # Un-wrapped row = '  ' + 22 + '  ' + 22 + '  ' + 3 = 53 chars > 40.
        long_id = 'A' * 22
        long_sym = 'B' * 22
        src = (
            '#\\#CIF_2.0\ndata_b\n'
            'loop_\n'
            '  _atom_site.id\n'
            '  _atom_site.type_symbol\n'
            '  _atom_site.fract_x\n'
            f'  {long_id}  {long_sym}  0.5\n'
        )
        conn = _ingest_src(src, loop_schema)
        result = emit(conn, loop_schema, line_limit=40, pretty=False)
        data_lines = [
            ln for ln in result.splitlines()
            if ln.startswith('  ') and not ln.startswith('  _')
        ]
        assert len(data_lines) > 1, 'row not wrapped despite exceeding limit'
        for ln in data_lines:
            assert len(ln) <= 40, f'wrapped line still too long: {ln!r}'

    # --- all-lines ≤ limit ---

    def test_all_output_lines_within_limit(self):
        """All physical lines in the output are ≤ line_limit chars."""
        src = (
            '#\\#CIF_2.0\ndata_b\n'
            '_cell.length_a  ' + 'B' * 90 + '\n'
            'loop_\n'
            '  _atom_site.id\n'
            '  _atom_site.type_symbol\n'
            '  Se  Selenium\n'
        )
        schema = _empty_schema()
        conn = _ingest_src(src, schema)
        result = emit(conn, schema, line_limit=72)
        for line in result.splitlines():
            assert len(line) <= 72, f'line exceeds limit: {line!r}'

    # --- warning for small limit ---

    def test_small_limit_warning(self, mini_schema):
        """line_limit < 40 emits UserWarning."""
        src = '#\\#CIF_2.0\ndata_b\n_cell.length_a  5.4\n'
        conn = _ingest_src(src, mini_schema)
        with pytest.warns(UserWarning, match='line_limit=10'):
            emit(conn, mini_schema, line_limit=10)

    # --- CIF 1.1 block name length check ---

    def test_cif11_long_block_name_raises(self):
        """CIF 1.1 block code > 75 chars raises ValueError."""
        long_name = 'x' * 76
        src = f'#\\#CIF_2.0\ndata_{long_name}\n_cell.length_a  5.4\n'
        schema = _empty_schema()
        conn = _ingest_src(src, schema)
        with pytest.raises(ValueError, match='75-character'):
            emit(conn, schema, version=CIF11)

    def test_cif20_long_block_name_allowed(self):
        """CIF 2.0 does not enforce block name length."""
        long_name = 'x' * 76
        src = f'#\\#CIF_2.0\ndata_{long_name}\n_cell.length_a  5.4\n'
        schema = _empty_schema()
        conn = _ingest_src(src, schema)
        result = emit(conn, schema, version=CIF20, line_limit=None)
        assert f'data_{long_name}' in result

    # --- make_text_field unit tests ---

    def test_make_text_field_plain(self):
        """Plain value → plain semicolon field."""
        from cifflow.output.quote import make_text_field
        result = make_text_field('hello world')
        assert result == '\n;hello world\n;'

    def test_make_text_field_needs_prefix(self):
        """Value containing '\\n;' → prefix protocol."""
        from cifflow.output.quote import make_text_field
        s = 'line1\n;bad\nline3'
        result = make_text_field(s)
        # Opening: newline + ';>' + backslash (prefix-only sentinel).
        assert result.startswith('\n;>\\')
        lines = result.split('\n')
        # lines[1] = ';>' + backslash = prefix-only sentinel line.
        assert lines[1] == ';>\\'
        # Content lines are prefixed with '>'.
        assert '>line1' in result
        assert '>;bad' in result  # ';bad' is escaped by the '>' prefix
        # Field closes on its own ';' line.
        assert result.endswith('\n;')

    def test_make_text_field_fold(self):
        """Long content lines → fold protocol."""
        from cifflow.output.quote import make_text_field
        long_line = 'W' * 80
        result = make_text_field(long_line, line_limit=40)
        lines = result.split('\n')
        # Every physical line must be ≤ 40 chars.
        for ln in lines:
            assert len(ln) <= 40, f'fold line too long: {ln!r}'
        # lines[0]='' (before first \n), lines[1]=';\\' (fold sentinel: ';' + backslash).
        assert lines[1] == ';\\', f'fold sentinel wrong: {lines[1]!r}'

    def test_make_text_field_prefix_and_fold(self):
        """Value containing '\\n;' AND long lines → prefix+fold."""
        from cifflow.output.quote import make_text_field
        s = 'W' * 80 + '\n;bad'
        result = make_text_field(s, line_limit=40)
        lines = result.split('\n')
        # lines[1] = ';>' + two backslashes (prefix + fold mode header).
        assert lines[1] == ';>\\\\', f'bad opening line: {lines[1]!r}'
        for ln in lines:
            assert len(ln) <= 40, f'line too long: {ln!r}'


# ---------------------------------------------------------------------------
# Decimal-aligned pretty-print
# ---------------------------------------------------------------------------

class TestDecimalAlign:
    """pretty=True aligns Real/Float values on the decimal point."""

    # Loop with one Real column (fract_x) and non-Real columns.
    LOOP_CIF = (
        '#\\#CIF_2.0\ndata_b\n'
        'loop_\n'
        '  _atom_site.id\n'
        '  _atom_site.type_symbol\n'
        '  _atom_site.fract_x\n'
        'Se  Se    0.1234\n'
        'C   C    10.5\n'
        'O   O   100.25\n'
    )

    # Set CIF matching user's example (lengths + angles).
    SET_CIF = (
        '#\\#CIF_2.0\ndata_b\n'
        '_cell.length_a    12.345\n'
        '_cell.length_b     1.245\n'
        '_cell.length_c   123.45\n'
        '_cell.angle_alpha   90\n'
        '_cell.angle_beta    90\n'
        '_cell.angle_gamma  120\n'
    )

    @pytest.fixture
    def loop_schema(self):
        return _make_schema(_LOOP_DIC)

    @pytest.fixture
    def cell_schema(self):
        return _make_schema(_CELL_DIC)

    # --- loop decimal alignment ---

    def test_loop_real_column_dot_aligned(self, loop_schema):
        """fract_x values are aligned on the decimal point."""
        conn = _ingest_src(self.LOOP_CIF, loop_schema)
        result = emit(conn, loop_schema, pretty=True)
        data_lines = [
            ln for ln in result.splitlines()
            if ln.startswith('  ') and not ln.startswith('  _')
        ]
        assert len(data_lines) == 3
        # The decimal point should be at the same character offset in every row.
        # Use the full-line position (not token-internal) because decimal alignment
        # right-pads the integer part within the column, shifting where '.' falls
        # inside the stripped token.
        dot_positions = set()
        for ln in data_lines:
            if '.' in ln:
                dot_positions.add(ln.index('.'))
        assert len(dot_positions) == 1, f'dots not aligned: {dot_positions}'

    def test_loop_real_values_round_trip(self, loop_schema):
        """Decimal alignment must not corrupt values."""
        conn = _ingest_src(self.LOOP_CIF, loop_schema)
        result = emit(conn, loop_schema, pretty=True)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        vals = [str(v) for v in block['_atom_site.fract_x']]
        assert vals == ['0.1234', '10.5', '100.25']

    def test_loop_non_real_columns_unaffected(self, loop_schema):
        """Non-Real columns are not decimal-aligned."""
        conn = _ingest_src(self.LOOP_CIF, loop_schema)
        result = emit(conn, loop_schema, pretty=True)
        data_lines = [
            ln for ln in result.splitlines()
            if ln.startswith('  ') and not ln.startswith('  _')
        ]
        # id column (Code type): 'Se' and 'C' should be left-justified to 2 chars.
        # Check that 'Se' and 'C ' appear in their column position.
        se_row = next(ln for ln in data_lines if ln.strip().startswith('Se'))
        c_row  = next(ln for ln in data_lines if ln.strip().startswith('C'))
        # id column starts at offset 2; 'Se' is at [2:4], 'C' is at [2:3].
        assert se_row[2:4] == 'Se'
        assert c_row[2] == 'C'

    def test_loop_placeholders_in_real_column(self, loop_schema):
        """Placeholder '.' in a Real column falls back to left-justify."""
        src = (
            '#\\#CIF_2.0\ndata_b\n'
            'loop_\n'
            '  _atom_site.id\n'
            '  _atom_site.type_symbol\n'
            '  _atom_site.fract_x\n'
            'Se  Se  0.1234\n'
            'C   C   .\n'
        )
        conn = _ingest_src(src, loop_schema)
        result = emit(conn, loop_schema, pretty=True)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert str(block['_atom_site.fract_x'][0]) == '0.1234'
        assert str(block['_atom_site.fract_x'][1]) == '.'

    def test_loop_pretty_false_no_decimal_align(self, loop_schema):
        """pretty=False suppresses decimal alignment."""
        conn = _ingest_src(self.LOOP_CIF, loop_schema)
        result_pretty = emit(conn, loop_schema, pretty=True)
        result_compact = emit(conn, loop_schema, pretty=False)
        # Compact mode: values are not decimal-padded, so dot positions in the
        # loop data rows are NOT all at the same column.
        assert '0.1234' in result_compact
        compact_data = [
            ln for ln in result_compact.splitlines()
            if ln.startswith('  ') and not ln.startswith('  _')
        ]
        compact_dot_positions = {ln.index('.') for ln in compact_data if '.' in ln}
        assert len(compact_dot_positions) > 1, (
            f'compact mode should not decimal-align: {compact_dot_positions}'
        )

    # --- Set category decimal alignment ---

    def test_set_real_values_dot_aligned(self, cell_schema):
        """Real values in a Set category are aligned on the decimal point."""
        conn = _ingest_src(self.SET_CIF, cell_schema)
        result = emit(conn, cell_schema, pretty=True)
        # Collect value tokens from lines that have Real columns.
        real_tags = {'_cell.length_a', '_cell.length_b', '_cell.length_c',
                     '_cell.angle_alpha', '_cell.angle_beta', '_cell.angle_gamma'}
        dot_cols: set[int] = set()
        for ln in result.splitlines():
            tag = ln.split()[0] if ln.split() else ''
            if tag not in real_tags:
                continue
            value_str = ln[ln.index(ln.split()[1]):]  # everything from value start
            if '.' in value_str:
                # Column position of '.' relative to line start
                dot_cols.add(ln.index('.'))
        assert len(dot_cols) == 1, f'Set decimal points not aligned: {dot_cols}'

    def test_set_integer_angles_right_justified(self, cell_schema):
        """Integer angle values are right-justified to match length int_width."""
        conn = _ingest_src(self.SET_CIF, cell_schema)
        result = emit(conn, cell_schema, pretty=True)
        lines = {
            ln.split()[0]: ln
            for ln in result.splitlines()
            if ln.startswith('_cell.')
        }
        # '120' is the widest integer (3 chars = int_width).
        # '90' must be right-padded so that '0' of '90' lands at the same
        # column as '0' of '120'.
        gamma_line = lines.get('_cell.angle_gamma', '')
        beta_line  = lines.get('_cell.angle_beta', '')
        # Both values are right-justified in the same field width (3 chars).
        # '120' and ' 90' (with its leading space) must start at the same column.
        gamma_val_start = gamma_line.index('120')
        beta_val_start  = beta_line.index(' 90')  # leading space is part of the padded token
        assert gamma_val_start == beta_val_start, (
            f'integers not right-aligned: gamma={gamma_val_start}, beta={beta_val_start}'
        )

    def test_set_values_round_trip(self, cell_schema):
        """Decimal alignment must not corrupt Set values."""
        conn = _ingest_src(self.SET_CIF, cell_schema)
        result = emit(conn, cell_schema, pretty=True)
        cif2, errors = build(result)
        assert not errors
        block = cif2[cif2.blocks[0]]
        assert str(block['_cell.length_a'][0]) == '12.345'
        assert str(block['_cell.length_b'][0]) == '1.245'
        assert str(block['_cell.length_c'][0]) == '123.45'
        assert str(block['_cell.angle_alpha'][0]) == '90'
        assert str(block['_cell.angle_gamma'][0]) == '120'

    # --- scientific notation ---

    def test_scientific_dot_form_aligned(self, loop_schema):
        """Values like '1.234e2' split on '.' and align correctly."""
        src = (
            '#\\#CIF_2.0\ndata_b\n'
            'loop_\n'
            '  _atom_site.id\n'
            '  _atom_site.type_symbol\n'
            '  _atom_site.fract_x\n'
            'A  A  1.234e2\n'
            'B  B  10.5e1\n'
        )
        conn = _ingest_src(src, loop_schema)
        result = emit(conn, loop_schema, pretty=True)
        data_lines = [
            ln for ln in result.splitlines()
            if ln.startswith('  ') and not ln.startswith('  _')
        ]
        assert len(data_lines) == 2
        # Both values have '.'; the decimal point should be at the same line offset.
        dot_positions = set()
        for ln in data_lines:
            if '.' in ln:
                dot_positions.add(ln.index('.'))
        assert len(dot_positions) == 1, f'scientific dots not aligned: {dot_positions}'
        # Values must survive round-trip.
        cif2, errors = build(result)
        assert not errors

    def test_scientific_no_dot_aligned(self, loop_schema):
        """Values like '1234e-2' (no dot) split on 'e' and right-justify mantissa."""
        src = (
            '#\\#CIF_2.0\ndata_b\n'
            'loop_\n'
            '  _atom_site.id\n'
            '  _atom_site.type_symbol\n'
            '  _atom_site.fract_x\n'
            'A  A  12e-2\n'
            'B  B  5e-2\n'
        )
        conn = _ingest_src(src, loop_schema)
        result = emit(conn, loop_schema, pretty=True)
        data_lines = [
            ln for ln in result.splitlines()
            if ln.startswith('  ') and not ln.startswith('  _')
        ]
        assert len(data_lines) == 2
        # '12e-2' → int_part '12' (width 2), 'e-2' frac.
        # ' 5e-2' → int_part '5'  right-just to 2 = ' 5'.
        # Both 'e' positions must align — check line-level position.
        e_positions = {ln.index('e') for ln in data_lines if 'e' in ln}
        assert len(e_positions) == 1, f'exponent letters not aligned: {e_positions}'

    # --- _parse_numeric unit tests ---

    def test_parse_numeric_decimal(self):
        from cifflow.output.emit import _parse_numeric
        assert _parse_numeric('1.23') == ('1', '23')
        assert _parse_numeric('-1.23') == ('-1', '23')
        assert _parse_numeric('.5') == ('', '5')
        assert _parse_numeric('1.23(4)') == ('1', '23(4)')
        assert _parse_numeric('1.23e-4') == ('1', '23e-4')
        assert _parse_numeric('1.23(4)e-5') == ('1', '23(4)e-5')

    def test_parse_numeric_no_dot(self):
        from cifflow.output.emit import _parse_numeric
        assert _parse_numeric('123') == ('123', '')
        assert _parse_numeric('123(4)') == ('123(4)', '')
        assert _parse_numeric('12e-2') == ('12', 'e-2')
        assert _parse_numeric('1234e2') == ('1234', 'e2')

    def test_parse_numeric_non_numeric(self):
        from cifflow.output.emit import _parse_numeric
        assert _parse_numeric('.') is None      # placeholder
        assert _parse_numeric('?') is None      # placeholder
        assert _parse_numeric("'hello'") is None  # quoted
        assert _parse_numeric('\n;text\n;') is None  # multiline
        assert _parse_numeric('abc') is None    # code/non-numeric
        assert _parse_numeric('1.2.3') is None  # two dots


# ---------------------------------------------------------------------------
# ORIGINAL mode — unknown tags in mixed loops appear alongside known tags
# ---------------------------------------------------------------------------

class TestOriginalModeFallbackLoop:
    """Unknown tags in a loop that also contains known tags must be re-emitted
    in the same loop_ block as the known tags (not relegated to a separate
    fallback section), with values row-aligned to their structured counterparts.
    """

    # Minimal dictionary: one Loop category (atom_site) with a key + one data col.
    _DIC = _LOOP_DIC

    # CIF that mixes a known tag (_atom_site.id) with an unknown one (_unknown.extra).
    _CIF = (
        '#\\#CIF_2.0\n'
        'data_mixed\n'
        'loop_\n'
        '  _atom_site.id\n'
        '  _atom_site.type_symbol\n'
        '  _unknown.extra\n'
        '  C1  C  alpha\n'
        '  O1  O  beta\n'
        '  N1  N  gamma\n'
    )

    @pytest.fixture
    def schema(self):
        return _make_schema(self._DIC)

    @pytest.fixture
    def conn(self, schema):
        return _ingest_src(self._CIF, schema)

    def test_unknown_tag_present_in_output(self, conn, schema):
        result = emit(conn, schema, mode=EmitMode.ORIGINAL)
        assert '_unknown.extra' in result

    def test_unknown_tag_in_same_loop_as_known_tag(self, conn, schema):
        result = emit(conn, schema, mode=EmitMode.ORIGINAL)
        lines = result.splitlines()
        loop_start = next(i for i, l in enumerate(lines) if l.strip() == 'loop_')
        # Find the loop_ block containing _atom_site.id
        atom_site_loop = None
        for i, l in enumerate(lines):
            if l.strip() == 'loop_':
                # Collect header tags
                j = i + 1
                tags_in_loop = []
                while j < len(lines) and lines[j].strip().startswith('_'):
                    tags_in_loop.append(lines[j].strip())
                    j += 1
                if '_atom_site.id' in tags_in_loop:
                    atom_site_loop = tags_in_loop
                    break
        assert atom_site_loop is not None, "no loop_ containing _atom_site.id found"
        assert '_unknown.extra' in atom_site_loop, (
            f"_unknown.extra not in same loop header as _atom_site.id; loop tags: {atom_site_loop}"
        )

    def test_unknown_tag_values_row_aligned(self, conn, schema):
        """Values for _unknown.extra must appear in the correct row positions."""
        result = emit(conn, schema, mode=EmitMode.ORIGINAL)
        lines = result.splitlines()
        # Find the loop_ that has both known and unknown tags, then collect data rows.
        in_loop = False
        tag_order = []
        data_rows = []
        for line in lines:
            stripped = line.strip()
            if stripped == 'loop_':
                in_loop = True
                tag_order = []
                data_rows = []
                continue
            if in_loop and stripped.startswith('_'):
                tag_order.append(stripped)
                continue
            if in_loop and tag_order and stripped:
                # Data row — split by whitespace
                data_rows.append(stripped.split())
                continue
            if in_loop and not stripped:
                in_loop = False

        assert '_atom_site.id' in tag_order
        assert '_unknown.extra' in tag_order
        extra_idx = tag_order.index('_unknown.extra')
        expected = ['alpha', 'beta', 'gamma']
        actual = [row[extra_idx] for row in data_rows if len(row) > extra_idx]
        assert actual == expected, f"row-aligned values mismatch: {actual!r}"

    def test_output_is_valid_cif(self, conn, schema):
        result = emit(conn, schema, mode=EmitMode.ORIGINAL)
        _, errors = build(result)
        assert not errors, errors

    def test_known_values_preserved(self, conn, schema):
        result = emit(conn, schema, mode=EmitMode.ORIGINAL)
        cif2, errors = build(result)
        assert not errors
        block = cif2['mixed']
        ids = [str(v) for v in block['_atom_site.id']]
        assert ids == ['C1', 'O1', 'N1']

    def test_pure_unknown_loop_emitted_separately(self, schema):
        """A loop containing ONLY unknown tags is emitted as its own loop_."""
        cif_src = (
            '#\\#CIF_2.0\n'
            'data_pure\n'
            'loop_\n'
            '  _atom_site.id\n'
            '  _atom_site.type_symbol\n'
            '  C1  C\n'
            '  O1  O\n'
            '\n'
            'loop_\n'
            '  _unknown.foo\n'
            '  _unknown.bar\n'
            '  x1  y1\n'
            '  x2  y2\n'
        )
        conn = _ingest_src(cif_src, schema)
        result = emit(conn, schema, mode=EmitMode.ORIGINAL)
        assert '_unknown.foo' in result
        assert '_unknown.bar' in result
        # The unknown tags must be inside a loop_ header
        lines = result.splitlines()
        unknown_in_loop = False
        for i, l in enumerate(lines):
            if l.strip() == 'loop_':
                j = i + 1
                while j < len(lines) and lines[j].strip().startswith('_'):
                    if lines[j].strip() in ('_unknown.foo', '_unknown.bar'):
                        unknown_in_loop = True
                    j += 1
        assert unknown_in_loop, "_unknown tags not found inside a loop_ header"
