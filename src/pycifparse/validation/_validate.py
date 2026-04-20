"""Unified validation facade for pycifparse."""

from __future__ import annotations

import pathlib
import sqlite3
import warnings
from dataclasses import dataclass, field
from typing import Literal

from pycifparse.cifmodel.builder import build
from pycifparse.cifmodel.model import CifFile
from pycifparse.dictionary.schema import SchemaSpec
from pycifparse.dictionary.schema_apply import apply_fallback_schema, apply_schema
from pycifparse.ingestion.ingest import IngestionError, ingest
from pycifparse.types import ParseError
from pycifparse.validation._db_validate import DbValidationResult, validate_database


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class ValidationIssue:
    stage:      Literal['parse', 'ingest', 'database']
    severity:   Literal['Error', 'Warning']
    check:      str
    message:    str
    block:      str | None
    tag:        str | None
    value:      str | None
    line:       int | None
    col:        int | None
    table:      str | None
    column:     str | None
    row_id:     int | None
    key_values: dict[str, str | None] | None


@dataclass
class ValidationReport:
    passed:   bool
    issues:   list[ValidationIssue]
    database: sqlite3.Connection | None


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------

def _parse_error_to_issue(err: ParseError) -> ValidationIssue:
    msg = err.message
    if err.context:
        msg += f" (context: {err.context})"
    return ValidationIssue(
        stage='parse',
        severity='Error',
        check=err.error_type,
        message=msg,
        block=None, tag=None, value=None,
        line=err.line, col=err.column,
        table=None, column=None, row_id=None, key_values=None,
    )


def _ingest_msg_to_issue(msg: str, severity: Literal['Error', 'Warning']) -> ValidationIssue:
    return ValidationIssue(
        stage='ingest',
        severity=severity,
        check='ingest',
        message=msg,
        block=None, tag=None, value=None,
        line=None, col=None,
        table=None, column=None, row_id=None, key_values=None,
    )


def _ingest_exc_to_issue(
    check: str,
    message: str,
    severity: Literal['Error', 'Warning'] = 'Error',
) -> ValidationIssue:
    return ValidationIssue(
        stage='ingest',
        severity=severity,
        check=check,
        message=message,
        block=None, tag=None, value=None,
        line=None, col=None,
        table=None, column=None, row_id=None, key_values=None,
    )


def _db_result_to_issue(r: DbValidationResult) -> ValidationIssue:
    return ValidationIssue(
        stage='database',
        severity=r.severity,
        check=r.check,
        message=r.message,
        block=r.block_id or None,
        tag=r.tag or None,
        value=r.value or None,
        line=None,
        col=None,
        table=r.table or None,
        column=r.column or None,
        row_id=r.row_id if r.row_id != 0 else None,
        key_values=r.key_values,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate(
    source: str | pathlib.Path | CifFile,
    schema: SchemaSpec | None = None,
    *,
    parse_errors: list[ParseError] | None = None,
    block_id: str | None = None,
    dataset_id: str | None = None,
    propagate_fk: bool = False,
) -> ValidationReport:
    """
    Parse (if needed), ingest to an in-memory database, and validate against
    the schema.  Returns a unified ValidationReport.  Never raises.
    """
    issues: list[ValidationIssue] = []
    conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ #
    # Stage 1 — Parse                                                      #
    # ------------------------------------------------------------------ #
    try:
        if isinstance(source, CifFile):
            cif = source
            if parse_errors is not None:
                for err in parse_errors:
                    issues.append(_parse_error_to_issue(err))
        else:
            if parse_errors is not None:
                warnings.warn(
                    "parse_errors is ignored when source is a str or Path; "
                    "errors are collected internally from build()",
                    UserWarning,
                    stacklevel=2,
                )
            src_str = (
                pathlib.Path(source).read_text(encoding='utf-8')
                if isinstance(source, pathlib.Path)
                else source
            )
            cif, raw_errors = build(src_str)
            for err in raw_errors:
                issues.append(_parse_error_to_issue(err))
    except Exception as exc:
        issues.append(ValidationIssue(
            stage='parse', severity='Error', check='internal_error',
            message=str(exc),
            block=None, tag=None, value=None,
            line=None, col=None,
            table=None, column=None, row_id=None, key_values=None,
        ))
        return ValidationReport(
            passed=not any(i.severity == 'Error' for i in issues),
            issues=issues,
            database=None,
        )

    # ------------------------------------------------------------------ #
    # Stage 2 — Ingest                                                     #
    # ------------------------------------------------------------------ #
    if not cif.blocks:
        return ValidationReport(
            passed=not any(i.severity == 'Error' for i in issues),
            issues=issues,
            database=None,
        )

    collected: list[str] = []

    def _collect(msg: str) -> None:
        collected.append(msg)

    ingest_ok = False
    try:
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        if schema is not None:
            apply_schema(conn, schema)

        ingest(
            cif, conn, schema,
            on_error=_collect,
            dataset_id=dataset_id,
            propagate_fk=propagate_fk,
        )
        ingest_ok = True

        for msg in collected:
            issues.append(_ingest_msg_to_issue(msg, 'Warning'))

    except IngestionError as exc:
        error_set = set(exc.errors)
        for msg in collected:
            sev: Literal['Error', 'Warning'] = 'Error' if msg in error_set else 'Warning'
            issues.append(_ingest_msg_to_issue(msg, sev))
        conn = None

    except sqlite3.IntegrityError as exc:
        if 'FOREIGN KEY' in str(exc):
            for msg in collected:
                issues.append(_ingest_msg_to_issue(msg, 'Warning'))
            issues.append(_ingest_exc_to_issue(
                'fk_violation',
                "FK constraint violated during ingestion; this likely indicates "
                "a bug in stub row creation",
            ))
        else:
            for msg in collected:
                issues.append(_ingest_msg_to_issue(msg, 'Warning'))
            issues.append(_ingest_exc_to_issue('internal_error', str(exc)))
        conn = None

    except ValueError as exc:
        for msg in collected:
            issues.append(_ingest_msg_to_issue(msg, 'Warning'))
        issues.append(_ingest_exc_to_issue('dataset_error', str(exc)))
        conn = None

    except Exception as exc:
        for msg in collected:
            issues.append(_ingest_msg_to_issue(msg, 'Warning'))
        issues.append(_ingest_exc_to_issue('internal_error', str(exc)))
        conn = None

    # ------------------------------------------------------------------ #
    # Stage 3 — Database                                                   #
    # ------------------------------------------------------------------ #
    if ingest_ok and schema is not None and conn is not None:
        db_results = validate_database(conn, schema, block_id=block_id, strict_container_nulls=True)
        has_internal_error = any(r.check == 'internal_error' for r in db_results)
        for r in db_results:
            if r.check == 'internal_error':
                issues.append(ValidationIssue(
                    stage='database', severity='Error', check='internal_error',
                    message=r.message,
                    block=None, tag=None, value=None,
                    line=None, col=None,
                    table=None, column=None, row_id=None, key_values=None,
                ))
            else:
                issues.append(_db_result_to_issue(r))
        if has_internal_error:
            conn = None

    return ValidationReport(
        passed=not any(i.severity == 'Error' for i in issues),
        issues=issues,
        database=conn,
    )
