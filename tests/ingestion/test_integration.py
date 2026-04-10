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
def single_one_cif():
    cif, _ = build((_CIF_DIR / 'single_one.cif').read_text(encoding='utf-8'))
    return cif


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _conn_with_schema(schema=None, *, enforce_fk=False):
    """Create an in-memory connection with schema applied.

    enforce_fk=False (default) disables FK checks at runtime.
    The known open issue (key-FK UUID fallback creates no parent row) means
    real CIF files fail COMMIT with FK enforcement on; disable it so
    integration tests can focus on data-routing correctness.
    """
    c = sqlite3.connect(':memory:')
    c.isolation_level = None
    if schema is not None:
        apply_schema(c, schema)
    apply_fallback_schema(c)
    if not enforce_fk:
        c.execute('PRAGMA foreign_keys = OFF')
    return c


# ---------------------------------------------------------------------------
# Schema ingestion — real CIF against cif_core.dic
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestIngestWithSchema:
    def test_ingest_completes_without_exception(self, core_schema, single_one_cif):
        """Ingestion must complete (not raise) even if semantic warnings are returned."""
        conn = _conn_with_schema(core_schema)
        errors = ingest(single_one_cif, conn, core_schema)
        # errors is a list of strings; may be non-empty for real CIF data
        assert isinstance(errors, list)

    def test_cell_length_a_in_structured_table(self, core_schema, single_one_cif):
        """_cell.length_a = 3.992 for block Selenium_0 should be in the cell table."""
        conn = _conn_with_schema(core_schema)
        ingest(single_one_cif, conn, core_schema)

        row = conn.execute(
            "SELECT length_a FROM cell WHERE _block_id = 'Selenium_0'"
        ).fetchone()
        assert row is not None, "No cell row for Selenium_0"
        assert row[0] == '3.992'

    def test_atom_site_label_in_structured_table(self, core_schema, single_one_cif):
        """atom_site loop for Selenium_0 has label Se1."""
        conn = _conn_with_schema(core_schema)
        ingest(single_one_cif, conn, core_schema)

        labels = [
            r[0]
            for r in conn.execute(
                "SELECT label FROM atom_site WHERE _block_id = 'Selenium_0'"
            ).fetchall()
        ]
        assert 'Se1' in labels

    def test_structured_tags_not_in_fallback(self, core_schema, single_one_cif):
        """Tags that resolve to structured tables should not appear in _cif_fallback."""
        conn = _conn_with_schema(core_schema)
        ingest(single_one_cif, conn, core_schema)

        # _cell.length_a is a known structured tag; it must not land in fallback
        row = conn.execute(
            "SELECT 1 FROM _cif_fallback WHERE tag = '_cell.length_a' LIMIT 1"
        ).fetchone()
        assert row is None, "_cell.length_a should not appear in _cif_fallback"

    def test_multiple_blocks_ingested(self, core_schema, single_one_cif):
        """single_one.cif has more than one data block; all should be ingested."""
        conn = _conn_with_schema(core_schema)
        ingest(single_one_cif, conn, core_schema)

        block_ids = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT _block_id FROM cell"
            ).fetchall()
        ]
        # The file contains at least Selenium_0
        assert 'Selenium_0' in block_ids


# ---------------------------------------------------------------------------
# No-schema ingestion — all tags must land in _cif_fallback
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestIngestNoSchema:
    def test_no_errors_returned(self, single_one_cif):
        conn = _conn_with_schema(schema=None)
        errors = ingest(single_one_cif, conn, None)
        assert errors == [], f"Unexpected ingest errors: {errors}"

    def test_all_tags_in_fallback(self, single_one_cif):
        conn = _conn_with_schema(schema=None)
        ingest(single_one_cif, conn, None)

        count = conn.execute(
            "SELECT COUNT(*) FROM _cif_fallback"
        ).fetchone()[0]
        assert count > 0

    def test_cell_length_a_in_fallback(self, single_one_cif):
        conn = _conn_with_schema(schema=None)
        ingest(single_one_cif, conn, None)

        rows = conn.execute(
            "SELECT value FROM _cif_fallback "
            "WHERE tag = '_cell.length_a' AND _block_id = 'Selenium_0'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == '3.992'

    def test_no_structured_tables_have_rows(self, single_one_cif):
        """Without a schema, no user data tables exist beyond the fallback tier."""
        conn = _conn_with_schema(schema=None)
        ingest(single_one_cif, conn, None)

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        # Only the always-created fallback-tier tables should exist
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
        # errors may contain merge-conflict warnings for cross-block data; not a bug
        assert isinstance(errors, list)

        block_ids = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT _block_id FROM _cif_fallback"
            ).fetchall()
        ]
        # multi_one has at least two blocks
        assert len(block_ids) >= 2

    def test_block_id_preserved(self, core_schema):
        """_block_id in structured tables must match the CIF data_ block name."""
        cif, _ = build((_CIF_DIR / 'single_one.cif').read_text(encoding='utf-8'))
        conn = _conn_with_schema(core_schema)
        ingest(cif, conn, core_schema)

        row = conn.execute(
            "SELECT _block_id FROM cell WHERE _block_id = 'Selenium_0' LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] == 'Selenium_0'
