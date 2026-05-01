"""
Integration tests for the CIF files in tests/cif_files/pycifparse/.

Each class corresponds to one file.  Fixtures are class-scoped so ingestion
runs once per class regardless of how many test methods are present.
"""

import json
import pathlib

import duckdb
import pytest

from pycifparse import build, ingest
from pycifparse.dictionary import (
    DictionaryLoader,
    directory_resolver,
    generate_schema,
)

_DATA_DIR = pathlib.Path(__file__).parents[2] / 'data' / 'dictionaries'
_CIF_DIR  = pathlib.Path(__file__).parents[1] / 'cif_files' / 'pycifparse'

_CORE_DIC = _DATA_DIR / 'cif_core.dic'
_POW_DIC  = _DATA_DIR / 'cif_pow.dic'


# ---------------------------------------------------------------------------
# Module-scoped schema fixtures (loaded once per test session)
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def core_schema():
    resolver = directory_resolver(_DATA_DIR)
    d = DictionaryLoader(resolver=resolver).load(_CORE_DIC.read_text(encoding='utf-8'))
    return generate_schema(d)


@pytest.fixture(scope='module')
def pow_schema():
    resolver = directory_resolver(_DATA_DIR)
    d = DictionaryLoader(resolver=resolver).load(_POW_DIC.read_text(encoding='utf-8'))
    return generate_schema(d)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ingest(filename, schema=None):
    cif, _ = build((_CIF_DIR / filename).read_text(encoding='utf-8'))
    conn, _ = ingest(cif, None, schema)
    return conn


def _scalar(conn, table, col, block_id):
    """Fetch one value from a structured table filtered by _block_id."""
    row = conn.execute(
        f'SELECT {col} FROM {table} WHERE _block_id=?', (block_id,)
    ).fetchone()
    return row[0] if row else None


def _fallback(conn, block_id, tag):
    """Fetch (value, value_type) from _cif_fallback for one tag."""
    row = conn.execute(
        'SELECT value, value_type FROM _cif_fallback WHERE _block_id=? AND tag=?',
        (block_id, tag),
    ).fetchone()
    return row  # (value, value_type) or None


# ===========================================================================
# cif_core group
# ===========================================================================

# ---------------------------------------------------------------------------
# core_cell_only.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def cell_only_conn(core_schema):
    return _ingest('core_cell_only.cif', core_schema)


class TestCoreCellOnly:
    def test_one_cell_row(self, cell_only_conn):
        assert cell_only_conn.execute('SELECT COUNT(*) FROM cell').fetchone()[0] == 1

    def test_length_a(self, cell_only_conn):
        assert _scalar(cell_only_conn, 'cell', 'length_a', 'test_cell_only') == '5.000'

    def test_volume(self, cell_only_conn):
        assert _scalar(cell_only_conn, 'cell', 'volume', 'test_cell_only') == '187.06'

    def test_no_atom_site_rows(self, cell_only_conn):
        all_tables = {r[0] for r in cell_only_conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()}
        assert 'atom_site' not in all_tables

    def test_stub_diffrn_created(self, cell_only_conn):
        """No _diffrn.id in file: UUID stub must be created in diffrn."""
        diffrn_id = cell_only_conn.execute(
            "SELECT diffrn_id FROM cell WHERE _block_id='test_cell_only'"
        ).fetchone()[0]
        assert diffrn_id is not None
        assert cell_only_conn.execute(
            'SELECT id FROM diffrn WHERE id=?', (diffrn_id,)
        ).fetchone() is not None

    def test_not_in_fallback(self, cell_only_conn):
        assert _fallback(cell_only_conn, 'test_cell_only', '_cell.length_a') is None


# ---------------------------------------------------------------------------
# core_cell_su.cif  (two blocks: inline SU vs explicit SU)
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def cell_su_conn(core_schema):
    return _ingest('core_cell_su.cif', core_schema)


class TestCoreCellSu:
    def test_two_cell_rows(self, cell_su_conn):
        """One cell row per block."""
        assert cell_su_conn.execute('SELECT COUNT(*) FROM cell').fetchone()[0] == 2

    def test_inline_measurand(self, cell_su_conn):
        row = cell_su_conn.execute(
            "SELECT length_a, length_c FROM cell WHERE _block_id='test_cell_inline_su'"
        ).fetchone()
        assert row == ('3.992', '3.119')

    def test_inline_su_scaled(self, cell_su_conn):
        row = cell_su_conn.execute(
            "SELECT length_a_su, length_c_su, volume_su FROM cell"
            " WHERE _block_id='test_cell_inline_su'"
        ).fetchone()
        assert row == ('0.004', '0.002', '0.05')

    def test_inline_integer_su(self, cell_su_conn):
        """90(5): no decimal places, SU = 5."""
        su = cell_su_conn.execute(
            "SELECT angle_alpha_su FROM cell WHERE _block_id='test_cell_inline_su'"
        ).fetchone()[0]
        assert su == '5'

    def test_inline_scientific_su(self, cell_su_conn):
        """0.1200e3(1): exponent +3, 4 decimal places → SU = 0.1."""
        su = cell_su_conn.execute(
            "SELECT angle_gamma_su FROM cell WHERE _block_id='test_cell_inline_su'"
        ).fetchone()[0]
        assert su == '0.1'

    def test_explicit_su_matches_scaled_inline(self, cell_su_conn):
        """Explicit _su tags in block 2 must equal the scaled inline SU from block 1."""
        inline = cell_su_conn.execute(
            "SELECT length_a_su, length_c_su, volume_su, angle_alpha_su, angle_gamma_su"
            " FROM cell WHERE _block_id='test_cell_inline_su'"
        ).fetchone()
        explicit = cell_su_conn.execute(
            "SELECT length_a_su, length_c_su, volume_su, angle_alpha_su, angle_gamma_su"
            " FROM cell WHERE _block_id='test_cell_explicit_su'"
        ).fetchone()
        assert inline == explicit


# ---------------------------------------------------------------------------
# core_atom_site_no_atom_type.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def atom_no_type_conn(core_schema):
    return _ingest('core_atom_site_no_atom_type.cif', core_schema)


class TestCoreAtomSiteNoAtomType:
    def test_three_atom_site_rows(self, atom_no_type_conn):
        assert atom_no_type_conn.execute(
            "SELECT COUNT(*) FROM atom_site WHERE _block_id='test_atom_site_no_atom_type'"
        ).fetchone()[0] == 3

    def test_stub_atom_type_fe(self, atom_no_type_conn):
        """Stub row for Fe must be created so the FK from atom_site is satisfied."""
        assert atom_no_type_conn.execute(
            "SELECT symbol FROM atom_type WHERE symbol='Fe'"
        ).fetchone() is not None

    def test_stub_atom_type_o(self, atom_no_type_conn):
        assert atom_no_type_conn.execute(
            "SELECT symbol FROM atom_type WHERE symbol='O'"
        ).fetchone() is not None

    def test_type_symbol_fk_satisfied(self, atom_no_type_conn):
        """All atom_site rows must have a matching atom_type row."""
        broken = atom_no_type_conn.execute(
            'SELECT COUNT(*) FROM atom_site a'
            ' LEFT JOIN atom_type t ON a.type_symbol=t.symbol'
            ' WHERE t.symbol IS NULL'
        ).fetchone()[0]
        assert broken == 0


# ---------------------------------------------------------------------------
# core_alias_tag.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def alias_tag_conn(core_schema):
    return _ingest('core_alias_tag.cif', core_schema)


class TestCoreAliasTag:
    def test_alias_routes_to_structured_table(self, alias_tag_conn):
        """_cell_length_a must resolve to the cell.length_a column."""
        val = _scalar(alias_tag_conn, 'cell', 'length_a', 'test_alias_tag')
        assert val == '4.123'

    def test_volume_alias(self, alias_tag_conn):
        val = _scalar(alias_tag_conn, 'cell', 'volume', 'test_alias_tag')
        assert val == '115.22'

    def test_alias_not_in_fallback(self, alias_tag_conn):
        """Aliases resolved to structured tables must not also appear in fallback."""
        row = alias_tag_conn.execute(
            "SELECT 1 FROM _cif_fallback WHERE tag LIKE '_cell%' LIMIT 1"
        ).fetchone()
        assert row is None


# ---------------------------------------------------------------------------
# core_placeholder_in_loop.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def placeholder_conn(core_schema):
    return _ingest('core_placeholder_in_loop.cif', core_schema)


class TestCorePlaceholderInLoop:
    def test_b_iso_inapplicable(self, placeholder_conn):
        """Fe1.B_iso_or_equiv = '.' (PLACEHOLDER inapplicable)."""
        row = placeholder_conn.execute(
            "SELECT B_iso_or_equiv FROM atom_site"
            " WHERE _block_id='test_placeholder_in_loop' AND label='Fe1'"
        ).fetchone()
        assert row[0] == '.'

    def test_occupancy_inapplicable(self, placeholder_conn):
        """Mn1.occupancy = '.' (PLACEHOLDER inapplicable)."""
        row = placeholder_conn.execute(
            "SELECT occupancy FROM atom_site"
            " WHERE _block_id='test_placeholder_in_loop' AND label='Mn1'"
        ).fetchone()
        assert row[0] == '.'

    def test_b_iso_unknown(self, placeholder_conn):
        """Mn1.B_iso_or_equiv = '?' (PLACEHOLDER unknown)."""
        row = placeholder_conn.execute(
            "SELECT B_iso_or_equiv FROM atom_site"
            " WHERE _block_id='test_placeholder_in_loop' AND label='Mn1'"
        ).fetchone()
        assert row[0] == '?'

    def test_real_value_unaffected(self, placeholder_conn):
        """O1 has real numeric values — not affected by placeholder handling."""
        row = placeholder_conn.execute(
            "SELECT occupancy, B_iso_or_equiv FROM atom_site"
            " WHERE _block_id='test_placeholder_in_loop' AND label='O1'"
        ).fetchone()
        assert row == ('1.0', '0.8')


# ---------------------------------------------------------------------------
# core_quoted_sentinel.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def quoted_sentinel_conn(core_schema):
    return _ingest('core_quoted_sentinel.cif', core_schema)


class TestCoreQuotedSentinel:
    def test_double_quoted_dot_encoded(self, quoted_sentinel_conn):
        """Double-quoted "." must be stored as '"."' to distinguish from PLACEHOLDER."""
        val = _scalar(quoted_sentinel_conn, 'diffrn_source', 'beamline',
                      'test_quoted_sentinel')
        assert val == '"."'

    def test_double_quoted_question_encoded(self, quoted_sentinel_conn):
        """Double-quoted "?" must be stored as '"?"'."""
        val = _scalar(quoted_sentinel_conn, 'diffrn_source', 'description',
                      'test_quoted_sentinel')
        assert val == '"?"'

    def test_triple_quoted_question_encoded(self, quoted_sentinel_conn):
        """Triple-double-quoted and triple-single-quoted "?" both encode as '"?"'."""
        details = _scalar(quoted_sentinel_conn, 'diffrn_source', 'details',
                          'test_quoted_sentinel')
        device  = _scalar(quoted_sentinel_conn, 'diffrn_source', 'device',
                          'test_quoted_sentinel')
        assert details == '"?"'
        assert device  == '"?"'

    def test_multiline_question_not_a_placeholder(self, quoted_sentinel_conn):
        """Multiline text field containing only '?' is a MULTILINE_STRING, not a
        PLACEHOLDER.  The stored value must contain '?' but not be bare '?'."""
        val = _scalar(quoted_sentinel_conn, 'diffrn_source', 'facility',
                      'test_quoted_sentinel')
        assert val is not None
        assert '?' in val


# ---------------------------------------------------------------------------
# core_unknown_tag.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def unknown_tag_conn(core_schema):
    return _ingest('core_unknown_tag.cif', core_schema)


class TestCoreUnknownTag:
    def test_known_tag_in_structured_table(self, unknown_tag_conn):
        val = _scalar(unknown_tag_conn, 'cell', 'length_a', 'test_unknown_tag')
        assert val == '4.500'

    def test_unknown_tags_in_fallback(self, unknown_tag_conn):
        tags = {
            row[0]
            for row in unknown_tag_conn.execute(
                "SELECT tag FROM _cif_fallback WHERE _block_id='test_unknown_tag'"
            ).fetchall()
        }
        assert '_my_custom.property' in tags
        assert '_my_custom.number' in tags
        assert '_another_unknown' in tags

    def test_known_tag_absent_from_fallback(self, unknown_tag_conn):
        assert _fallback(unknown_tag_conn, 'test_unknown_tag', '_cell.length_a') is None

    def test_quoted_number_not_su_split(self, unknown_tag_conn):
        """_quoted_number "37.4(2)" lands in fallback (unknown tag); no SU split in fallback."""
        row = _fallback(unknown_tag_conn, 'test_unknown_tag', '_quoted_number')
        assert row is not None
        value, vtype = row
        assert value == '37.4(2)'
        assert vtype == 'string'


# ---------------------------------------------------------------------------
# core_multiline_formula.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def multiline_conn(core_schema):
    return _ingest('core_multiline_formula.cif', core_schema)


class TestCoreMultilineFormula:
    def test_sum_contains_formula(self, multiline_conn):
        """Semicolon text field: value must contain the formula string."""
        val = _scalar(multiline_conn, 'chemical_formula', 'sum',
                      'test_multiline_formula')
        assert val is not None
        assert 'Fe2 O3' in val

    def test_iupac_prefix_folded(self, multiline_conn):
        """Prefix-folded multiline with continuation backslash unfolds to Fe2 O3."""
        val = _scalar(multiline_conn, 'chemical_formula', 'iupac',
                      'test_multiline_formula')
        assert val is not None
        assert val.strip() == 'Fe2 O3'

    def test_analytical_double_quoted(self, multiline_conn):
        val = _scalar(multiline_conn, 'chemical_formula', 'analytical',
                      'test_multiline_formula')
        assert val == 'Fe2 O3'

    def test_moiety_triple_single_quoted(self, multiline_conn):
        val = _scalar(multiline_conn, 'chemical_formula', 'moiety',
                      'test_multiline_formula')
        assert val == 'Fe2 O3'

    def test_weight(self, multiline_conn):
        val = _scalar(multiline_conn, 'chemical_formula', 'weight',
                      'test_multiline_formula')
        assert val == '159.69'


# ===========================================================================
# cif_pow group
# ===========================================================================

# ---------------------------------------------------------------------------
# pow_wavelength_propagation.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def wavelength_prop_conn(pow_schema):
    return _ingest('pow_wavelength_propagation.cif', pow_schema)


class TestPowWavelengthPropagation:
    def test_three_wavelength_rows(self, wavelength_prop_conn):
        assert wavelength_prop_conn.execute(
            "SELECT COUNT(*) FROM diffrn_radiation_wavelength"
            " WHERE _block_id='test_wavelength_propagation'"
        ).fetchone()[0] == 3

    def test_radiation_id_propagated(self, wavelength_prop_conn):
        """radiation_id must be 'Cu_tube' on every wavelength row."""
        rows = wavelength_prop_conn.execute(
            "SELECT radiation_id FROM diffrn_radiation_wavelength"
            " WHERE _block_id='test_wavelength_propagation'"
        ).fetchall()
        assert all(r[0] == 'Cu_tube' for r in rows)

    def test_probe_stored(self, wavelength_prop_conn):
        row = wavelength_prop_conn.execute(
            "SELECT probe FROM diffrn_radiation"
            " WHERE _block_id='test_wavelength_propagation'"
        ).fetchone()
        assert row is not None
        assert row[0] == 'x-ray'


# ---------------------------------------------------------------------------
# pow_enumeration_default.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def enum_default_conn(pow_schema):
    return _ingest('pow_enumeration_default.cif', pow_schema)


class TestPowEnumerationDefault:
    def test_probe_present(self, enum_default_conn):
        row = enum_default_conn.execute(
            "SELECT probe FROM diffrn_radiation"
            " WHERE _block_id='test_enumeration_default'"
        ).fetchone()
        assert row is not None
        assert row[0] == 'x-ray'

    def test_variant_filled_from_default(self, enum_default_conn):
        """_diffrn_radiation.variant is absent from the CIF; must be '.' from
        _enumeration.default defined in cif_img.dic."""
        row = enum_default_conn.execute(
            "SELECT variant FROM diffrn_radiation"
            " WHERE _block_id='test_enumeration_default'"
        ).fetchone()
        assert row is not None
        assert row[0] == '.'


# ---------------------------------------------------------------------------
# pow_small_pd_data_meas.cif  (_pd_data.point_id explicit in loop)
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def pd_data_meas_conn(pow_schema):
    return _ingest('pow_small_pd_data_meas.cif', pow_schema)


class TestPowSmallPdDataMeas:
    def test_five_pd_meas_rows(self, pd_data_meas_conn):
        assert pd_data_meas_conn.execute(
            "SELECT COUNT(*) FROM pd_meas WHERE _block_id='test_small_pd_meas'"
        ).fetchone()[0] == 5

    def test_2theta_values(self, pd_data_meas_conn):
        vals = [
            r[0]
            for r in pd_data_meas_conn.execute(
                "SELECT \"2theta_scan\" FROM pd_meas"
                " WHERE _block_id='test_small_pd_meas'"
                " ORDER BY _row_id"
            ).fetchall()
        ]
        assert vals == ['10.000', '10.100', '10.200', '10.300', '10.400']

    def test_intensity_total(self, pd_data_meas_conn):
        vals = [
            r[0]
            for r in pd_data_meas_conn.execute(
                "SELECT intensity_total FROM pd_meas"
                " WHERE _block_id='test_small_pd_meas'"
                " ORDER BY _row_id"
            ).fetchall()
        ]
        assert vals == ['100.0', '120.0', '110.0', '95.0', '105.0']


# ---------------------------------------------------------------------------
# pow_small_pd_meas_proc.cif  (no point_id key → UUID per row)
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def pd_meas_proc_conn(pow_schema):
    return _ingest('pow_small_pd_meas_proc.cif', pow_schema)


class TestPowSmallPdMeasProc:
    def test_five_pd_meas_rows(self, pd_meas_proc_conn):
        assert pd_meas_proc_conn.execute(
            "SELECT COUNT(*) FROM pd_meas"
            " WHERE _block_id='test_small_pd_meas_calc'"
        ).fetchone()[0] == 5

    def test_five_pd_calc_rows(self, pd_meas_proc_conn):
        assert pd_meas_proc_conn.execute(
            "SELECT COUNT(*) FROM pd_calc"
            " WHERE _block_id='test_small_pd_meas_calc'"
        ).fetchone()[0] == 5


# ===========================================================================
# Schema-less (fallback) group
# ===========================================================================

# ---------------------------------------------------------------------------
# fallback_scalars.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def fallback_scalars_conn():
    return _ingest('fallback_scalars.cif')


class TestFallbackScalars:
    def test_all_tags_in_fallback(self, fallback_scalars_conn):
        tags = {
            r[0]
            for r in fallback_scalars_conn.execute(
                "SELECT tag FROM _cif_fallback WHERE _block_id='test_fallback_scalars'"
            ).fetchall()
        }
        assert tags == {
            '_sample.name', '_sample.description',
            '_measurement.temp', '_measurement.pressure',
            '_result.quality', '_result.number',
        }

    def test_bare_word_value_type(self, fallback_scalars_conn):
        row = _fallback(fallback_scalars_conn, 'test_fallback_scalars', '_sample.name')
        assert row == ('iron_oxide', 'string')

    def test_single_quoted_value_type(self, fallback_scalars_conn):
        row = _fallback(fallback_scalars_conn, 'test_fallback_scalars',
                        '_sample.description')
        assert row == ('a reddish powder', 'string')

    def test_quoted_number_value_type(self, fallback_scalars_conn):
        """Quoting style is not preserved in the new model; value_type is 'string'."""
        row = _fallback(fallback_scalars_conn, 'test_fallback_scalars', '_result.number')
        assert row == ('11', 'string')

    def test_no_structured_table_rows(self, fallback_scalars_conn):
        tables = {
            r[0]
            for r in fallback_scalars_conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()
        }
        assert tables == {'_cif_fallback', '_block_dataset_membership',
                          '_validation_result', '_block_order', '_tag_presence',
                          '_metatable'}


# ---------------------------------------------------------------------------
# fallback_loop.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def fallback_loop_conn():
    return _ingest('fallback_loop.cif')


class TestFallbackLoop:
    def test_twelve_loop_rows(self, fallback_loop_conn):
        """3 columns × 4 rows = 12 loop entries in _cif_fallback."""
        count = fallback_loop_conn.execute(
            "SELECT COUNT(*) FROM _cif_fallback"
            " WHERE _block_id='test_fallback_loop' AND loop_id IS NOT NULL"
        ).fetchone()[0]
        assert count == 12

    def test_scalar_has_null_col_index(self, fallback_loop_conn):
        """Non-loop scalar must have col_index=NULL and loop_id=NULL."""
        row = fallback_loop_conn.execute(
            "SELECT loop_id, col_index FROM _cif_fallback"
            " WHERE _block_id='test_fallback_loop' AND tag='_experiment.id'"
        ).fetchone()
        assert row == (None, None)

    def test_loop_col_index_is_column_position(self, fallback_loop_conn):
        """col_index is the 0-based column position within the loop, not the row number.
        _peak.position is always column 1 (after _peak.id=0, before _peak.intensity=2)."""
        col_indices = {
            r[0]
            for r in fallback_loop_conn.execute(
                "SELECT col_index FROM _cif_fallback"
                " WHERE _block_id='test_fallback_loop' AND tag='_peak.position'"
            ).fetchall()
        }
        assert col_indices == {1}

    def test_loop_four_rows_via_row_id(self, fallback_loop_conn):
        """Four distinct _row_id values for _peak.position (one per loop iteration)."""
        row_ids = {
            r[0]
            for r in fallback_loop_conn.execute(
                "SELECT _row_id FROM _cif_fallback"
                " WHERE _block_id='test_fallback_loop' AND tag='_peak.position'"
            ).fetchall()
        }
        assert len(row_ids) == 4

    def test_loop_values(self, fallback_loop_conn):
        rows = fallback_loop_conn.execute(
            "SELECT value FROM _cif_fallback"
            " WHERE _block_id='test_fallback_loop' AND tag='_peak.intensity'"
            " ORDER BY _row_id"
        ).fetchall()
        assert [r[0] for r in rows] == ['1000', '500', '250', '125']


# ---------------------------------------------------------------------------
# fallback_containers.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def fallback_containers_conn():
    return _ingest('fallback_containers.cif')


class TestFallbackContainers:
    def test_list_value_type(self, fallback_containers_conn):
        row = _fallback(fallback_containers_conn, 'test_fallback_containers',
                        '_vector.coords')
        assert row is not None
        assert row[1] == 'list'

    def test_list_json_content(self, fallback_containers_conn):
        from pycifparse.ingestion.ingest import decode_container
        row = _fallback(fallback_containers_conn, 'test_fallback_containers',
                        '_vector.coords')
        assert decode_container(row[0]) == ['1.0', '2.0', '3.0']

    def test_table_value_type(self, fallback_containers_conn):
        row = _fallback(fallback_containers_conn, 'test_fallback_containers',
                        '_site.properties')
        assert row is not None
        assert row[1] == 'table'

    def test_nested_list_json(self, fallback_containers_conn):
        from pycifparse.ingestion.ingest import decode_container
        row = _fallback(fallback_containers_conn, 'test_fallback_containers',
                        '_matrix.row')
        assert decode_container(row[0]) == [['1', '0', '0'], ['0', '1', '0'], ['0', '0', '1']]


# ---------------------------------------------------------------------------
# fallback_multiblock.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def fallback_multiblock_conn():
    return _ingest('fallback_multiblock.cif')


class TestFallbackMultiblock:
    def test_three_distinct_block_ids(self, fallback_multiblock_conn):
        ids = {
            r[0]
            for r in fallback_multiblock_conn.execute(
                'SELECT DISTINCT _block_id FROM _cif_fallback'
            ).fetchall()
        }
        assert ids == {'block_alpha', 'block_beta', 'block_gamma'}

    def test_alpha_color_is_red(self, fallback_multiblock_conn):
        row = _fallback(fallback_multiblock_conn, 'block_alpha', '_sample.color')
        assert row[0] == 'red'

    def test_beta_color_is_blue(self, fallback_multiblock_conn):
        row = _fallback(fallback_multiblock_conn, 'block_beta', '_sample.color')
        assert row[0] == 'blue'

    def test_value_a_only_in_alpha(self, fallback_multiblock_conn):
        """_value.a appears only in block_alpha."""
        assert _fallback(fallback_multiblock_conn, 'block_alpha', '_value.a') is not None
        assert _fallback(fallback_multiblock_conn, 'block_beta',  '_value.a') is None
        assert _fallback(fallback_multiblock_conn, 'block_gamma', '_value.a') is None

    def test_identical_content_different_blocks(self, fallback_multiblock_conn):
        """block_beta and block_gamma have identical tag values but distinct _block_id."""
        beta  = _fallback(fallback_multiblock_conn, 'block_beta',  '_sample.name')
        gamma = _fallback(fallback_multiblock_conn, 'block_gamma', '_sample.name')
        assert beta is not None and gamma is not None
        assert beta[0] == gamma[0] == 'beta_sample'


# ---------------------------------------------------------------------------
# fallback_value_types.cif
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def fallback_value_types_conn():
    return _ingest('fallback_value_types.cif')


_BLK = 'test_fallback_value_types'


class TestFallbackValueTypes:
    def test_string(self, fallback_value_types_conn):
        row = _fallback(fallback_value_types_conn, _BLK, '_t.string')
        assert row == ('bare_word', 'string')

    def test_placeholder_dot(self, fallback_value_types_conn):
        row = _fallback(fallback_value_types_conn, _BLK, '_t.placeholder_dot')
        assert row == ('.', 'placeholder')

    def test_placeholder_question(self, fallback_value_types_conn):
        row = _fallback(fallback_value_types_conn, _BLK, '_t.placeholder_q')
        assert row == ('?', 'placeholder')

    def test_single_quoted(self, fallback_value_types_conn):
        row = _fallback(fallback_value_types_conn, _BLK, '_t.single_quoted')
        assert row == ('hello world', 'string')

    def test_double_quoted(self, fallback_value_types_conn):
        row = _fallback(fallback_value_types_conn, _BLK, '_t.double_quoted')
        assert row == ('hello world', 'string')

    def test_multiline_string(self, fallback_value_types_conn):
        row = _fallback(fallback_value_types_conn, _BLK, '_t.multiline')
        assert row is not None
        assert row[1] == 'string'
        assert 'line one' in row[0] and 'line two' in row[0]

    def test_triple_single_quoted(self, fallback_value_types_conn):
        row = _fallback(fallback_value_types_conn, _BLK, '_t.triple_single')
        assert row == ('triple single', 'string')

    def test_triple_double_quoted(self, fallback_value_types_conn):
        row = _fallback(fallback_value_types_conn, _BLK, '_t.triple_double')
        assert row == ('triple double', 'string')
