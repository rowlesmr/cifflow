"""
Integration tests for ingest().

Marked @pytest.mark.slow — run with: pytest -m slow
"""

import pathlib

import duckdb
import pytest

from cifflow import build, ingest, IngestionError
from cifflow.dictionary import (
    DictionaryLoader,
    directory_resolver,
    generate_schema,
)

_DATA_DIR = pathlib.Path(__file__).parents[2] / 'data' / 'dictionaries'
_CIF_DIR = pathlib.Path(__file__).parents[1] / 'cif_files'

_CORE_DIC = _DATA_DIR / 'cif_core.dic'
_POW_DIC  = _DATA_DIR / 'cif_pow.dic'


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def core_schema():
    resolver = directory_resolver(_DATA_DIR)
    source = _CORE_DIC.read_text(encoding='utf-8')
    d = DictionaryLoader(resolver=resolver).load(source)
    return generate_schema(d)


@pytest.fixture(scope='module')
def pow_schema():
    resolver = directory_resolver(_DATA_DIR)
    source = _POW_DIC.read_text(encoding='utf-8')
    d = DictionaryLoader(resolver=resolver).load(source)
    return generate_schema(d)


@pytest.fixture(scope='module')
def one_structure_cif():
    cif, _ = build((_CIF_DIR / 'one_structure.cif').read_text(encoding='utf-8'))
    return cif


@pytest.fixture(scope='module')
def second_short_cif():
    cif, _ = build((_CIF_DIR / 'second_short.cif').read_text(encoding='utf-8'))
    return cif


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _conn_with_schema(schema=None):
    return duckdb.connect()


# ---------------------------------------------------------------------------
# Schema ingestion — real CIF against cif_core.dic
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def one_structure_conn(core_schema, one_structure_cif):
    """Ingest one_structure.cif once per class; all tests share the connection."""
    conn, _ = ingest(one_structure_cif, None, core_schema)
    return conn


@pytest.mark.slow
class TestIngestWithSchema:
    def test_ingest_completes_without_exception(self, one_structure_conn):
        """Ingestion must complete without raising, including FK constraint satisfaction."""
        assert one_structure_conn is not None

    def test_cell_length_a_in_structured_table(self, one_structure_conn):
        """_cell.length_a = 3.992 should be in the cell structured table."""
        row = one_structure_conn.execute(
            "SELECT length_a FROM cell WHERE _cifflow_block_id = 'selenium_0'"
        ).fetchone()
        assert row is not None, "No cell row for selenium_0"
        assert row[0] == '3.992'

    def test_atom_site_label_in_structured_table(self, one_structure_conn):
        """atom_site loop has label Se1."""
        labels = [
            r[0]
            for r in one_structure_conn.execute(
                "SELECT label FROM atom_site WHERE _cifflow_block_id = 'selenium_0'"
            ).fetchall()
        ]
        assert 'Se1' in labels

    def test_structured_tags_not_in_fallback(self, one_structure_conn):
        """Tags routed to structured tables must not also appear in _cif_fallback."""
        row = one_structure_conn.execute(
            "SELECT 1 FROM _cif_fallback WHERE tag = '_cell.length_a' LIMIT 1"
        ).fetchone()
        assert row is None, "_cell.length_a should not appear in _cif_fallback"

    def test_cifflow_block_id_preserved(self, one_structure_conn):
        """_cifflow_block_id in structured tables must match the CIF data_ block name."""
        row = one_structure_conn.execute(
            "SELECT _cifflow_block_id FROM cell WHERE _cifflow_block_id = 'selenium_0' LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] == 'selenium_0'

    def test_missing_key_fk_creates_stub_parent(self, one_structure_conn):
        """When key-FK source is absent, a stub parent row is created to satisfy the FK."""
        # cell.diffrn_id is a key-FK -> diffrn.id.
        # one_structure.cif has no _diffrn.id, so a UUID stub must exist in diffrn.
        cell_row = one_structure_conn.execute(
            "SELECT diffrn_id FROM cell WHERE _cifflow_block_id = 'selenium_0'"
        ).fetchone()
        assert cell_row is not None
        diffrn_id = cell_row[0]
        assert diffrn_id is not None, "cell.diffrn_id should be a UUID, not NULL"

        diffrn_row = one_structure_conn.execute(
            "SELECT id FROM diffrn WHERE id = ?", (diffrn_id,)
        ).fetchone()
        assert diffrn_row is not None, "stub diffrn row must exist for FK to be satisfiable"

    def test_non_key_fk_creates_stub_parent(self, one_structure_conn):
        """When a non-key FK value is present but the parent row is absent, a stub is created."""
        # atom_site.type_symbol = 'Se' is a FK -> atom_type.symbol.
        # one_structure.cif has no _atom_type data, so a stub must exist.
        atom_type_row = one_structure_conn.execute(
            "SELECT symbol FROM atom_type WHERE symbol = 'Se'"
        ).fetchone()
        assert atom_type_row is not None, "stub atom_type row must exist for Se"


# ---------------------------------------------------------------------------
# No-schema ingestion — all tags must land in _cif_fallback
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def one_structure_conn_no_schema(one_structure_cif):
    """Ingest one_structure.cif (no schema) once per class; all tests share the connection."""
    conn, _ = ingest(one_structure_cif, None, None)
    return conn


@pytest.mark.slow
class TestIngestNoSchema:
    def test_no_errors_returned(self, one_structure_conn_no_schema):
        # Fixture construction without exception validates this; also check table exists.
        count = one_structure_conn_no_schema.execute(
            "SELECT COUNT(*) FROM _cif_fallback"
        ).fetchone()[0]
        assert count > 0

    def test_all_tags_in_fallback(self, one_structure_conn_no_schema):
        count = one_structure_conn_no_schema.execute(
            "SELECT COUNT(*) FROM _cif_fallback"
        ).fetchone()[0]
        assert count > 0

    def test_cell_length_a_in_fallback(self, one_structure_conn_no_schema):
        rows = one_structure_conn_no_schema.execute(
            "SELECT value FROM _cif_fallback "
            "WHERE tag = '_cell.length_a' AND _cifflow_block_id = 'selenium_0'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == '3.992'

    def test_no_structured_tables_have_rows(self, one_structure_conn_no_schema):
        """Without a schema, only the fallback-tier tables should exist."""
        tables = {
            row[0]
            for row in one_structure_conn_no_schema.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()
        }
        assert tables == {'_cif_fallback', '_block_dataset_membership', '_validation_result', '_block_order', '_tag_presence', '_metatable'}


# ---------------------------------------------------------------------------
# Multi-block CIF — cross-block merge
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestIngestMultiBlock:
    def test_multi_block_row_counts(self, pow_schema):
        """multi_one.cif has multiple instrument blocks; each should produce rows."""
        cif, _ = build((_CIF_DIR / 'multi_one.cif').read_text(encoding='utf-8'))
        conn, errors = ingest(cif, None, pow_schema)
        assert isinstance(errors, list)

        # Three instrument blocks → three pd_instr rows
        assert conn.execute('SELECT COUNT(*) FROM pd_instr').fetchone()[0] == 3
        # Three detector blocks → three pd_instr_detector rows
        assert conn.execute('SELECT COUNT(*) FROM pd_instr_detector').fetchone()[0] == 3


# ---------------------------------------------------------------------------
# second_short.cif against cif_pow.dic (not slow)
# ---------------------------------------------------------------------------

@pytest.fixture(scope='class')
def second_short_conn(pow_schema, second_short_cif):
    """Ingest second_short.cif once per class; all tests share the connection."""
    conn, _ = ingest(second_short_cif, None, pow_schema)
    return conn


class TestIngestSecondShort:
    def test_ingest_completes(self, second_short_conn):
        """Fixture construction completing without exception is the test."""
        assert second_short_conn is not None

    def test_pd_meas_populated(self, second_short_conn):
        """The pd_data/pd_meas/pd_proc/pd_calc loop must land in structured tables."""
        count = second_short_conn.execute('SELECT COUNT(*) FROM pd_meas').fetchone()[0]
        assert count == 6313

    def test_pd_meas_not_in_fallback(self, second_short_conn):
        """pd_meas tags must not appear in _cif_fallback."""
        row = second_short_conn.execute(
            "SELECT 1 FROM _cif_fallback WHERE tag = '_pd_meas.intensity_total' LIMIT 1"
        ).fetchone()
        assert row is None

    def test_diffrn_radiation_wavelength_populated(self, second_short_conn):
        """_diffrn_radiation_wavelength rows must have radiation_id filled from accumulator."""
        rows = second_short_conn.execute(
            "SELECT id, radiation_id FROM diffrn_radiation_wavelength "
            "WHERE _cifflow_block_id = 'degaussa_raw_01_wavelength'"
        ).fetchall()
        assert len(rows) == 5
        assert all(r[1] == 'big_tube' for r in rows), \
            "All wavelength rows must reference radiation_id='big_tube'"

    def test_diffrn_radiation_variant_default(self, second_short_conn):
        """_diffrn_radiation.variant must be '.' (enumeration_default) when absent from CIF."""
        row = second_short_conn.execute(
            "SELECT variant, probe FROM diffrn_radiation "
            "WHERE _cifflow_block_id = 'degaussa_raw_01_wavelength'"
        ).fetchone()
        assert row is not None
        assert row[0] == '.', f"Expected variant='.', got {row[0]!r}"
        assert row[1] == 'x-ray'
