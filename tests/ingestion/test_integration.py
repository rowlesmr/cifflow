"""
Integration tests for ingest().

Marked @pytest.mark.slow — run with: pytest -m slow
"""

import pathlib
import sqlite3

import pytest

from pycifparse import build, ingest
from pycifparse.dictionary import (
    DictionaryLoader,
    apply_schema,
    directory_resolver,
    generate_schema,
)
from pycifparse.dictionary.schema_apply import apply_fallback_schema

_DATA_DIR = pathlib.Path(__file__).parents[2] / 'data' / 'dictionaries'
_CIF_DIR = pathlib.Path(__file__).parents[1] / 'cif_files'

_CORE_DIC = _DATA_DIR / 'cif_core.dic'


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
def one_structure_cif():
    cif, _ = build((_CIF_DIR / 'one_structure.cif').read_text(encoding='utf-8'))
    return cif


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _conn_with_schema(schema=None):
    c = sqlite3.connect(':memory:')
    c.isolation_level = None
    if schema is not None:
        apply_schema(c, schema)
    apply_fallback_schema(c)
    return c


# ---------------------------------------------------------------------------
# Schema ingestion — real CIF against cif_core.dic
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestIngestWithSchema:
    def test_ingest_completes_without_exception(self, core_schema, one_structure_cif):
        """Ingestion must complete without raising, including FK constraint satisfaction."""
        conn = _conn_with_schema(core_schema)
        errors = ingest(one_structure_cif, conn, core_schema)
        assert isinstance(errors, list)

    def test_cell_length_a_in_structured_table(self, core_schema, one_structure_cif):
        """_cell.length_a = 3.992 should be in the cell structured table."""
        conn = _conn_with_schema(core_schema)
        ingest(one_structure_cif, conn, core_schema)

        row = conn.execute(
            "SELECT length_a FROM cell WHERE _block_id = 'Selenium_0'"
        ).fetchone()
        assert row is not None, "No cell row for Selenium_0"
        assert row[0] == '3.992'

    def test_atom_site_label_in_structured_table(self, core_schema, one_structure_cif):
        """atom_site loop has label Se1."""
        conn = _conn_with_schema(core_schema)
        ingest(one_structure_cif, conn, core_schema)

        labels = [
            r[0]
            for r in conn.execute(
                "SELECT label FROM atom_site WHERE _block_id = 'Selenium_0'"
            ).fetchall()
        ]
        assert 'Se1' in labels

    def test_structured_tags_not_in_fallback(self, core_schema, one_structure_cif):
        """Tags routed to structured tables must not also appear in _cif_fallback."""
        conn = _conn_with_schema(core_schema)
        ingest(one_structure_cif, conn, core_schema)

        row = conn.execute(
            "SELECT 1 FROM _cif_fallback WHERE tag = '_cell.length_a' LIMIT 1"
        ).fetchone()
        assert row is None, "_cell.length_a should not appear in _cif_fallback"

    def test_block_id_preserved(self, core_schema, one_structure_cif):
        """_block_id in structured tables must match the CIF data_ block name."""
        conn = _conn_with_schema(core_schema)
        ingest(one_structure_cif, conn, core_schema)

        row = conn.execute(
            "SELECT _block_id FROM cell WHERE _block_id = 'Selenium_0' LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] == 'Selenium_0'

    def test_missing_key_fk_creates_stub_parent(self, core_schema, one_structure_cif):
        """When key-FK source is absent, a stub parent row is created to satisfy the FK."""
        conn = _conn_with_schema(core_schema)
        ingest(one_structure_cif, conn, core_schema)

        # cell.diffrn_id is a key-FK -> diffrn.id.
        # one_structure.cif has no _diffrn.id, so a UUID stub must exist in diffrn.
        cell_row = conn.execute(
            "SELECT diffrn_id FROM cell WHERE _block_id = 'Selenium_0'"
        ).fetchone()
        assert cell_row is not None
        diffrn_id = cell_row[0]
        assert diffrn_id is not None, "cell.diffrn_id should be a UUID, not NULL"

        diffrn_row = conn.execute(
            "SELECT id FROM diffrn WHERE id = ?", (diffrn_id,)
        ).fetchone()
        assert diffrn_row is not None, "stub diffrn row must exist for FK to be satisfiable"

    def test_non_key_fk_creates_stub_parent(self, core_schema, one_structure_cif):
        """When a non-key FK value is present but the parent row is absent, a stub is created."""
        conn = _conn_with_schema(core_schema)
        ingest(one_structure_cif, conn, core_schema)

        # atom_site.type_symbol = 'Se' is a FK -> atom_type.symbol.
        # one_structure.cif has no _atom_type data, so a stub must exist.
        atom_type_row = conn.execute(
            "SELECT symbol FROM atom_type WHERE symbol = 'Se'"
        ).fetchone()
        assert atom_type_row is not None, "stub atom_type row must exist for Se"


# ---------------------------------------------------------------------------
# No-schema ingestion — all tags must land in _cif_fallback
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestIngestNoSchema:
    def test_no_errors_returned(self, one_structure_cif):
        conn = _conn_with_schema(schema=None)
        errors = ingest(one_structure_cif, conn, None)
        assert errors == [], f"Unexpected ingest errors: {errors}"

    def test_all_tags_in_fallback(self, one_structure_cif):
        conn = _conn_with_schema(schema=None)
        ingest(one_structure_cif, conn, None)

        count = conn.execute(
            "SELECT COUNT(*) FROM _cif_fallback"
        ).fetchone()[0]
        assert count > 0

    def test_cell_length_a_in_fallback(self, one_structure_cif):
        conn = _conn_with_schema(schema=None)
        ingest(one_structure_cif, conn, None)

        rows = conn.execute(
            "SELECT value FROM _cif_fallback "
            "WHERE tag = '_cell.length_a' AND _block_id = 'Selenium_0'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == '3.992'

    def test_no_structured_tables_have_rows(self, one_structure_cif):
        """Without a schema, only the fallback-tier tables should exist."""
        conn = _conn_with_schema(schema=None)
        ingest(one_structure_cif, conn, None)

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert tables == {'_cif_fallback', '_block_dataset_membership', '_validation_result'}


# ---------------------------------------------------------------------------
# Multi-block CIF — cross-block merge
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestIngestMultiBlock:
    def test_multi_block_row_counts(self, core_schema):
        """multi_one.cif has multiple blocks; each should contribute rows."""
        cif, _ = build((_CIF_DIR / 'multi_one.cif').read_text(encoding='utf-8'))
        conn = _conn_with_schema(core_schema)
        errors = ingest(cif, conn, core_schema)
        assert isinstance(errors, list)

        block_ids = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT _block_id FROM _cif_fallback"
            ).fetchall()
        ]
        assert len(block_ids) >= 2
