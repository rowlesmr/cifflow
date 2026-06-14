"""Branch-coverage tests for consolidate_component_intensities.

Tests lock down current behaviour before refactoring the E-grade function.
All tests run against an in-memory DuckDB connection — no schema or IR needed.
"""
import json

import duckdb
import pytest

from cifflow.database.component_intensities import (
    _all_dot,
    consolidate_component_intensities,
)
from cifflow.ingestion.ingest import _CONTAINER_PREFIX


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _make_db() -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB with the three tables the function requires."""
    db = duckdb.connect()
    db.execute("""
        CREATE TABLE pd_calc_component (
            "_cifflow_block_id" TEXT,
            "point_id"          TEXT,
            "diffractogram_id"  TEXT,
            "phase_id"          TEXT,
            "intensity_net"     TEXT,
            "intensity_total"   TEXT
        )
    """)
    db.execute("""
        CREATE TABLE pd_calc (
            "_cifflow_block_id"           TEXT,
            "point_id"                    TEXT,
            "diffractogram_id"            TEXT,
            "component_intensities_net"   TEXT,
            "component_intensities_total" TEXT
        )
    """)
    db.execute("""
        CREATE TABLE pd_calc_overall (
            "diffractogram_id"              TEXT,
            "component_presentation_order"  TEXT
        )
    """)
    return db


def _insert_component(db, point_id, diff_id, phase_id,
                      net=None, total=None, block_id='blk1'):
    db.execute(
        'INSERT INTO pd_calc_component VALUES (?, ?, ?, ?, ?, ?)',
        [block_id, point_id, diff_id, phase_id, net, total],
    )


def _insert_pd_calc(db, point_id, diff_id, block_id='blk1'):
    db.execute(
        'INSERT INTO pd_calc ("_cifflow_block_id", "point_id", "diffractogram_id")'
        ' VALUES (?, ?, ?)',
        [block_id, point_id, diff_id],
    )


def _insert_overall(db, diff_id, pres_order=None):
    db.execute(
        'INSERT INTO pd_calc_overall VALUES (?, ?)',
        [diff_id, pres_order],
    )


def _get_net(db, point_id, diff_id):
    row = db.execute(
        'SELECT "component_intensities_net" FROM pd_calc'
        ' WHERE "point_id"=? AND "diffractogram_id"=?',
        [point_id, diff_id],
    ).fetchone()
    return row[0] if row else None


def _get_total(db, point_id, diff_id):
    row = db.execute(
        'SELECT "component_intensities_total" FROM pd_calc'
        ' WHERE "point_id"=? AND "diffractogram_id"=?',
        [point_id, diff_id],
    ).fetchone()
    return row[0] if row else None


def _get_pres(db, diff_id):
    row = db.execute(
        'SELECT "component_presentation_order" FROM pd_calc_overall'
        ' WHERE "diffractogram_id"=?',
        [diff_id],
    ).fetchone()
    return row[0] if row else None


def _has_column(db, table, col) -> bool:
    try:
        db.execute(f'SELECT "{col}" FROM {table} LIMIT 1')
        return True
    except duckdb.Error:
        return False


def _decode(val: str) -> list:
    """Strip chr(0) prefix and json.loads."""
    if val and val.startswith(_CONTAINER_PREFIX):
        val = val[1:]
    return json.loads(val)


# ---------------------------------------------------------------------------
# Tests: _all_dot
# ---------------------------------------------------------------------------

class TestAllDot:
    def test_all_dot_true_for_all_dot_list(self):
        stored = _CONTAINER_PREFIX + '[".", "."]'
        assert _all_dot(stored)

    def test_all_dot_false_for_mixed_list(self):
        stored = _CONTAINER_PREFIX + '[".", "1.0"]'
        assert not _all_dot(stored)

    def test_all_dot_false_for_empty_list(self):
        stored = _CONTAINER_PREFIX + '[]'
        assert not _all_dot(stored)

    def test_all_dot_false_for_unprefixed_raw_dot(self):
        # Raw to_json() output (no prefix) — used during assembly check
        assert _all_dot('["."]')

    def test_all_dot_false_for_invalid_json(self):
        assert not _all_dot('not json')


# ---------------------------------------------------------------------------
# Tests: consolidate_component_intensities
# ---------------------------------------------------------------------------

class TestConsolidateComponentIntensities:
    def test_no_table_returns_zero(self):
        """Missing pd_calc_component → duckdb.Error caught, return 0."""
        db = duckdb.connect()
        result = consolidate_component_intensities(db, None)
        assert result == 0

    def test_empty_component_table_returns_zero(self):
        db = _make_db()
        result = consolidate_component_intensities(db, None)
        assert result == 0

    def test_single_phase_single_point_assembled(self):
        db = _make_db()
        _insert_component(db, 'p1', 'd1', 'phA', net='100.0', total='110.0')
        _insert_pd_calc(db, 'p1', 'd1')
        _insert_overall(db, 'd1')
        n = consolidate_component_intensities(db, None)
        assert n == 1
        assert _decode(_get_net(db, 'p1', 'd1')) == ['100.0']
        assert _decode(_get_total(db, 'p1', 'd1')) == ['110.0']

    def test_phase_ids_sorted_alphabetically_in_list(self):
        db = _make_db()
        # Insert in reverse order — presentation order must be sorted
        _insert_component(db, 'p1', 'd1', 'phZ', net='10.0')
        _insert_component(db, 'p1', 'd1', 'phA', net='20.0')
        _insert_pd_calc(db, 'p1', 'd1')
        _insert_overall(db, 'd1')
        consolidate_component_intensities(db, None)
        pres = _get_pres(db, 'd1')
        assert _decode(pres) == ['phA', 'phZ']
        # phA=20.0 first, phZ=10.0 second
        assert _decode(_get_net(db, 'p1', 'd1')) == ['20.0', '10.0']

    def test_missing_phase_for_point_filled_with_dot(self):
        db = _make_db()
        _insert_component(db, 'p1', 'd1', 'phA', net='10.0')
        _insert_component(db, 'p1', 'd1', 'phB', net='20.0')
        _insert_component(db, 'p2', 'd1', 'phA', net='30.0')
        # p2 has no phB row → slot must be '.'
        _insert_pd_calc(db, 'p1', 'd1')
        _insert_pd_calc(db, 'p2', 'd1')
        _insert_overall(db, 'd1')
        consolidate_component_intensities(db, None)
        vals = _decode(_get_net(db, 'p2', 'd1'))
        assert vals[1] == '.'  # phB slot

    def test_all_dot_net_column_dropped(self):
        """All net values are NULL → assembled to '.' → column dropped."""
        db = _make_db()
        _insert_component(db, 'p1', 'd1', 'phA', net=None, total='10.0')
        _insert_pd_calc(db, 'p1', 'd1')
        _insert_overall(db, 'd1')
        consolidate_component_intensities(db, None)
        assert not _has_column(db, 'pd_calc', 'component_intensities_net')
        assert _has_column(db, 'pd_calc', 'component_intensities_total')

    def test_all_dot_total_column_dropped(self):
        db = _make_db()
        _insert_component(db, 'p1', 'd1', 'phA', net='10.0', total=None)
        _insert_pd_calc(db, 'p1', 'd1')
        _insert_overall(db, 'd1')
        consolidate_component_intensities(db, None)
        assert _has_column(db, 'pd_calc', 'component_intensities_net')
        assert not _has_column(db, 'pd_calc', 'component_intensities_total')

    def test_both_all_dot_drops_presentation_order_column(self):
        """When both intensity columns dropped, component_presentation_order is too."""
        db = _make_db()
        _insert_component(db, 'p1', 'd1', 'phA', net=None, total=None)
        _insert_pd_calc(db, 'p1', 'd1')
        _insert_overall(db, 'd1')
        consolidate_component_intensities(db, None)
        assert not _has_column(db, 'pd_calc_overall', 'component_presentation_order')

    def test_presentation_order_updated_for_existing_diff(self):
        """Existing pd_calc_overall row is UPDATEd, not duplicated."""
        db = _make_db()
        _insert_component(db, 'p1', 'd1', 'phA', net='5.0')
        _insert_pd_calc(db, 'p1', 'd1')
        _insert_overall(db, 'd1', pres_order='old_value')
        consolidate_component_intensities(db, None)
        pres = _get_pres(db, 'd1')
        assert _decode(pres) == ['phA']
        count = db.execute(
            'SELECT COUNT(*) FROM pd_calc_overall WHERE "diffractogram_id"=?', ['d1']
        ).fetchone()[0]
        assert count == 1  # no duplicate inserted

    def test_presentation_order_inserted_for_absent_diff(self):
        """No pd_calc_overall row → new row INSERTed."""
        db = _make_db()
        _insert_component(db, 'p1', 'd1', 'phA', net='5.0')
        _insert_pd_calc(db, 'p1', 'd1')
        # Intentionally no _insert_overall
        consolidate_component_intensities(db, None)
        pres = _get_pres(db, 'd1')
        assert _decode(pres) == ['phA']

    def test_clear_source_deletes_component_rows(self):
        db = _make_db()
        _insert_component(db, 'p1', 'd1', 'phA', net='5.0')
        _insert_pd_calc(db, 'p1', 'd1')
        _insert_overall(db, 'd1')
        consolidate_component_intensities(db, None, clear_source=True)
        count = db.execute('SELECT COUNT(*) FROM pd_calc_component').fetchone()[0]
        assert count == 0

    def test_pd_calc_row_auto_created_when_absent(self):
        """pd_calc row missing for a component point → auto-inserted."""
        db = _make_db()
        _insert_component(db, 'p1', 'd1', 'phA', net='5.0')
        # No _insert_pd_calc
        _insert_overall(db, 'd1')
        n = consolidate_component_intensities(db, None)
        assert n == 1
        assert _get_net(db, 'p1', 'd1') is not None

    def test_pd_calc_rows_without_component_data_get_all_dot(self):
        """pd_calc rows for a diffractogram with no component entries get all-'.' lists."""
        db = _make_db()
        _insert_component(db, 'p1', 'd1', 'phA', net='5.0')
        _insert_pd_calc(db, 'p1', 'd1')
        # p2 exists in pd_calc but has no component row
        _insert_pd_calc(db, 'p2', 'd1')
        _insert_overall(db, 'd1')
        consolidate_component_intensities(db, None)
        assert _decode(_get_net(db, 'p2', 'd1')) == ['.']

    def test_multiple_diffractograms_handled_independently(self):
        db = _make_db()
        _insert_component(db, 'p1', 'd1', 'phA', net='5.0')
        _insert_component(db, 'p2', 'd2', 'phB', net='10.0')
        _insert_pd_calc(db, 'p1', 'd1')
        _insert_pd_calc(db, 'p2', 'd2')
        _insert_overall(db, 'd1')
        _insert_overall(db, 'd2')
        n = consolidate_component_intensities(db, None)
        assert n == 2
        assert _decode(_get_net(db, 'p1', 'd1')) == ['5.0']
        assert _decode(_get_net(db, 'p2', 'd2')) == ['10.0']

    def test_returns_count_of_pd_calc_rows_updated(self):
        db = _make_db()
        _insert_component(db, 'p1', 'd1', 'phA', net='1.0')
        _insert_component(db, 'p2', 'd1', 'phA', net='2.0')
        _insert_pd_calc(db, 'p1', 'd1')
        _insert_pd_calc(db, 'p2', 'd1')
        _insert_overall(db, 'd1')
        n = consolidate_component_intensities(db, None)
        assert n == 2

    def test_null_net_coalesced_to_dot_in_list(self):
        """NULL intensity_net in component → '.' in assembled list."""
        db = _make_db()
        _insert_component(db, 'p1', 'd1', 'phA', net=None, total='5.0')
        _insert_component(db, 'p1', 'd1', 'phB', net='3.0', total='4.0')
        _insert_pd_calc(db, 'p1', 'd1')
        _insert_overall(db, 'd1')
        consolidate_component_intensities(db, None)
        vals = _decode(_get_net(db, 'p1', 'd1'))
        # phA slot should be '.'
        idx_a = _decode(_get_pres(db, 'd1')).index('phA')
        assert vals[idx_a] == '.'
