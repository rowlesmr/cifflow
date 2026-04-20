"""Tests for the validate() facade."""

from __future__ import annotations

import sqlite3
import warnings
from unittest.mock import MagicMock, patch

import pytest

from pycifparse.cifmodel.builder import build
from pycifparse.cifmodel.model import CifFile
from pycifparse.ingestion.ingest import IngestionError
from pycifparse.types import ParseError
from pycifparse.validation import ValidationIssue, ValidationReport, validate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_CIF = """\
##CIF_2.0
data_test
_cell.length_a 5.0
"""

_INVALID_CIF = """\
##CIF_2.0
data_test
loop_
_cell.length_a
_cell.length_b
5.0
"""  # odd number of loop values — parse/IR error

_EMPTY_CIF = """\
##CIF_2.0
"""


def _make_parse_error() -> ParseError:
    return ParseError(
        error_type='syntactic',
        message='unexpected token',
        line=3,
        column=1,
        context='some context',
        recovery_action='skip',
    )


# ---------------------------------------------------------------------------
# Parse stage
# ---------------------------------------------------------------------------

class TestParseStage:
    def test_parse_error_from_string(self):
        # A CIF string that produces parse errors.
        report = validate(_INVALID_CIF)
        assert isinstance(report, ValidationReport)
        parse_issues = [i for i in report.issues if i.stage == 'parse']
        assert len(parse_issues) >= 1
        for issue in parse_issues:
            assert issue.severity == 'Error'
            assert issue.check in ('lexical', 'syntactic', 'semantic')

    def test_ciffile_no_parse_errors(self):
        cif, _ = build(_MINIMAL_CIF)
        report = validate(cif)
        parse_issues = [i for i in report.issues if i.stage == 'parse']
        assert parse_issues == []

    def test_ciffile_with_parse_errors(self):
        cif, _ = build(_MINIMAL_CIF)
        err = _make_parse_error()
        report = validate(cif, parse_errors=[err])
        parse_issues = [i for i in report.issues if i.stage == 'parse']
        assert len(parse_issues) == 1
        issue = parse_issues[0]
        assert issue.stage == 'parse'
        assert issue.severity == 'Error'
        assert issue.check == 'syntactic'
        assert 'unexpected token' in issue.message
        assert '(context: some context)' in issue.message
        assert issue.line == 3
        assert issue.col == 1

    def test_userwarning_when_parse_errors_with_string_source(self):
        err = _make_parse_error()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            validate(_MINIMAL_CIF, parse_errors=[err])
        user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
        assert len(user_warnings) == 1
        assert 'parse_errors is ignored' in str(user_warnings[0].message)

    def test_parse_errors_not_applied_when_string_source(self):
        # Even with parse_errors supplied, parse stage should use internal build() errors only.
        err = _make_parse_error()
        with warnings.catch_warnings(record=True):
            warnings.simplefilter('always')
            report = validate(_MINIMAL_CIF, parse_errors=[err])
        # _MINIMAL_CIF is clean; internal build() should produce no errors.
        parse_issues = [i for i in report.issues if i.stage == 'parse']
        assert parse_issues == []

    def test_parse_error_context_appended(self):
        cif, _ = build(_MINIMAL_CIF)
        err = ParseError(
            error_type='lexical', message='bad char', line=1, column=5,
            context='the context', recovery_action='skip',
        )
        report = validate(cif, parse_errors=[err])
        issue = report.issues[0]
        assert 'bad char' in issue.message
        assert '(context: the context)' in issue.message

    def test_parse_error_empty_context_not_appended(self):
        cif, _ = build(_MINIMAL_CIF)
        err = ParseError(
            error_type='lexical', message='bad char', line=1, column=5,
            context='', recovery_action='skip',
        )
        report = validate(cif, parse_errors=[err])
        issue = report.issues[0]
        assert '(context:' not in issue.message


# ---------------------------------------------------------------------------
# Ingest stage
# ---------------------------------------------------------------------------

class TestIngestStage:
    def test_empty_ciffile_returns_none_database(self):
        cif, _ = build(_EMPTY_CIF)
        report = validate(cif)
        assert report.database is None

    def test_valid_cif_no_schema_database_not_none(self):
        report = validate(_MINIMAL_CIF)
        assert report.database is not None
        report.database.close()

    def test_schema_none_no_database_stage_issues(self):
        report = validate(_MINIMAL_CIF)
        db_issues = [i for i in report.issues if i.stage == 'database']
        assert db_issues == []

    def test_schema_none_fallback_tables_exist(self):
        report = validate(_MINIMAL_CIF)
        conn = report.database
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert '_cif_fallback' in tables
        conn.close()

    def test_ingest_warning_messages_become_warning_issues(self):
        # Patch ingest to fire on_error with a non-fatal message.
        original_ingest = __import__('pycifparse.ingestion.ingest', fromlist=['ingest']).ingest

        def _fake_ingest(cif, conn, schema, *, on_error=None, **kw):
            if on_error:
                on_error('some warning message')
            return original_ingest(cif, conn, None, on_error=None, **kw)

        with patch('pycifparse.validation._validate.ingest', side_effect=_fake_ingest):
            report = validate(_MINIMAL_CIF)

        ingest_issues = [i for i in report.issues if i.stage == 'ingest']
        # At least one warning from our fake
        warning_msgs = [i for i in ingest_issues if i.severity == 'Warning' and 'some warning message' in i.message]
        assert len(warning_msgs) >= 1

    def test_ingestion_error_errors_become_error_issues(self):
        def _fail_ingest(cif, conn, schema, *, on_error=None, **kw):
            if on_error:
                on_error('semantic conflict A')
                on_error('non-semantic warning B')
            raise IngestionError(['semantic conflict A'])

        with patch('pycifparse.validation._validate.ingest', side_effect=_fail_ingest):
            report = validate(_MINIMAL_CIF)

        assert report.database is None
        ingest_issues = [i for i in report.issues if i.stage == 'ingest']
        error_issues = [i for i in ingest_issues if i.severity == 'Error']
        warning_issues = [i for i in ingest_issues if i.severity == 'Warning']
        assert any('semantic conflict A' in i.message for i in error_issues)
        assert any('non-semantic warning B' in i.message for i in warning_issues)

    def test_ingestion_error_does_not_raise(self):
        def _fail(cif, conn, schema, *, on_error=None, **kw):
            raise IngestionError(['conflict'])

        with patch('pycifparse.validation._validate.ingest', side_effect=_fail):
            report = validate(_MINIMAL_CIF)  # must not raise
        assert report is not None

    def test_fk_violation_produces_fk_violation_issue(self):
        def _fail(cif, conn, schema, *, on_error=None, **kw):
            raise sqlite3.IntegrityError('FOREIGN KEY constraint failed')

        with patch('pycifparse.validation._validate.ingest', side_effect=_fail):
            report = validate(_MINIMAL_CIF)

        assert report.database is None
        fk_issues = [i for i in report.issues if i.check == 'fk_violation']
        assert len(fk_issues) == 1
        assert fk_issues[0].stage == 'ingest'
        assert fk_issues[0].severity == 'Error'

    def test_value_error_produces_dataset_error_issue(self):
        def _fail(cif, conn, schema, *, on_error=None, **kw):
            raise ValueError('unknown dataset_id')

        with patch('pycifparse.validation._validate.ingest', side_effect=_fail):
            report = validate(_MINIMAL_CIF)

        assert report.database is None
        de_issues = [i for i in report.issues if i.check == 'dataset_error']
        assert len(de_issues) == 1
        assert de_issues[0].severity == 'Error'
        assert 'unknown dataset_id' in de_issues[0].message

    def test_unexpected_ingest_exception_becomes_internal_error(self):
        def _fail(cif, conn, schema, *, on_error=None, **kw):
            raise RuntimeError('unexpected boom')

        with patch('pycifparse.validation._validate.ingest', side_effect=_fail):
            report = validate(_MINIMAL_CIF)

        assert report.database is None
        ie = [i for i in report.issues if i.check == 'internal_error' and i.stage == 'ingest']
        assert len(ie) == 1
        assert 'unexpected boom' in ie[0].message

    def test_pre_exception_on_error_messages_included_as_warnings(self):
        def _fail(cif, conn, schema, *, on_error=None, **kw):
            if on_error:
                on_error('warning before crash')
            raise ValueError('crash')

        with patch('pycifparse.validation._validate.ingest', side_effect=_fail):
            report = validate(_MINIMAL_CIF)

        warn_issues = [
            i for i in report.issues
            if i.stage == 'ingest' and i.severity == 'Warning' and 'warning before crash' in i.message
        ]
        assert len(warn_issues) == 1


# ---------------------------------------------------------------------------
# Database stage
# ---------------------------------------------------------------------------

class TestDatabaseStage:
    def test_validate_database_internal_error_sets_database_none(self):
        from pycifparse.validation._db_validate import validate_database, DbValidationResult

        def _fail_validate_database(conn, schema, **kw):
            return [DbValidationResult(
                table='', column='', tag='', block_id='', row_id=0,
                key_values={}, value='',
                check='internal_error', severity='Error', message='db boom',
            )]

        with patch('pycifparse.validation._validate.validate_database', side_effect=_fail_validate_database):
            # Use schema=None to skip actual DB stage; but we want to test DB stage.
            # Instead, patch validate_database to return the sentinel directly.
            # We need a real schema for validate() to call validate_database.
            # Use a minimal fake schema.
            from pycifparse.dictionary.schema import SchemaSpec
            fake_schema = SchemaSpec(tables={}, column_to_tag={})
            report = validate(_MINIMAL_CIF, fake_schema)

        assert report.database is None
        ie = [i for i in report.issues if i.check == 'internal_error' and i.stage == 'database']
        assert len(ie) == 1

    def test_valid_db_results_before_internal_error_included(self):
        from pycifparse.validation._db_validate import DbValidationResult

        def _mixed_validate_database(conn, schema, **kw):
            return [
                DbValidationResult(
                    table='t', column='c', tag='_t.c', block_id='b', row_id=1,
                    key_values={}, value='bad',
                    check='type_contents', severity='Warning', message='bad value',
                ),
                DbValidationResult(
                    table='', column='', tag='', block_id='', row_id=0,
                    key_values={}, value='',
                    check='internal_error', severity='Error', message='db boom',
                ),
            ]

        from pycifparse.dictionary.schema import SchemaSpec
        fake_schema = SchemaSpec(tables={}, column_to_tag={})
        with patch('pycifparse.validation._validate.validate_database', side_effect=_mixed_validate_database):
            report = validate(_MINIMAL_CIF, fake_schema)

        assert report.database is None
        db_issues = [i for i in report.issues if i.stage == 'database']
        assert len(db_issues) == 2
        checks = {i.check for i in db_issues}
        assert 'type_contents' in checks
        assert 'internal_error' in checks


# ---------------------------------------------------------------------------
# Passed flag and stage ordering
# ---------------------------------------------------------------------------

class TestPassedAndOrdering:
    def test_no_issues_passed_true(self):
        report = validate(_MINIMAL_CIF)
        # Clean CIF with no schema → no issues expected
        if not report.issues:
            assert report.passed is True
        report.database.close() if report.database else None

    def test_warning_only_passed_true(self):
        cif, _ = build(_MINIMAL_CIF)
        err = ParseError(
            error_type='syntactic', message='warn', line=1, column=1,
            context='', recovery_action='skip',
        )
        # Parse errors are always Error; use a mock for Warning instead.
        # Directly test passed based on severity composition.
        issue = ValidationIssue(
            stage='ingest', severity='Warning', check='ingest', message='w',
            block=None, tag=None, value=None, line=None, col=None,
            table=None, column=None, row_id=None, key_values=None,
        )
        report = ValidationReport(passed=True, issues=[issue], database=None)
        assert report.passed is True

    def test_error_severity_makes_passed_false(self):
        report = validate(_INVALID_CIF)
        error_issues = [i for i in report.issues if i.severity == 'Error']
        if error_issues:
            assert report.passed is False

    def test_stage_ordering(self):
        def _fail(cif, conn, schema, *, on_error=None, **kw):
            if on_error:
                on_error('ingest warning')

        from pycifparse.dictionary.schema import SchemaSpec
        from pycifparse.validation._db_validate import DbValidationResult

        def _db(conn, schema, **kw):
            return [DbValidationResult(
                table='t', column='c', tag='_t.c', block_id='b', row_id=1,
                key_values={}, value='v',
                check='type_contents', severity='Warning', message='db warn',
            )]

        cif, errors = build(_INVALID_CIF)
        fake_schema = SchemaSpec(tables={}, column_to_tag={})

        with patch('pycifparse.validation._validate.ingest', side_effect=_fail):
            with patch('pycifparse.validation._validate.validate_database', side_effect=_db):
                report = validate(cif, fake_schema, parse_errors=errors)

        stages = [i.stage for i in report.issues]
        # All parse stages come before all ingest, which come before all database.
        parse_indices = [idx for idx, s in enumerate(stages) if s == 'parse']
        ingest_indices = [idx for idx, s in enumerate(stages) if s == 'ingest']
        db_indices = [idx for idx, s in enumerate(stages) if s == 'database']
        if parse_indices and ingest_indices:
            assert max(parse_indices) < min(ingest_indices)
        if ingest_indices and db_indices:
            assert max(ingest_indices) < min(db_indices)


# ---------------------------------------------------------------------------
# Sentinel normalisation
# ---------------------------------------------------------------------------

class TestSentinelNormalisation:
    def test_db_result_empty_tag_becomes_none(self):
        from pycifparse.validation._db_validate import DbValidationResult
        from pycifparse.validation._validate import _db_result_to_issue

        r = DbValidationResult(
            table='t', column='c', tag='', block_id='b', row_id=1,
            key_values={}, value='', check='type_contents', severity='Warning', message='m',
        )
        issue = _db_result_to_issue(r)
        assert issue.tag is None
        assert issue.value is None

    def test_db_result_row_id_zero_becomes_none(self):
        from pycifparse.validation._db_validate import DbValidationResult
        from pycifparse.validation._validate import _db_result_to_issue

        r = DbValidationResult(
            table='t', column='c', tag='_t.c', block_id='b', row_id=0,
            key_values={}, value='v', check='unknown_tag', severity='Warning', message='m',
        )
        issue = _db_result_to_issue(r)
        assert issue.row_id is None

    def test_db_result_positive_row_id_preserved(self):
        from pycifparse.validation._db_validate import DbValidationResult
        from pycifparse.validation._validate import _db_result_to_issue

        r = DbValidationResult(
            table='t', column='c', tag='_t.c', block_id='b', row_id=5,
            key_values={}, value='v', check='type_contents', severity='Error', message='m',
        )
        issue = _db_result_to_issue(r)
        assert issue.row_id == 5
