"""inspect_ingest — trace what happens during CIF ingestion."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Optional, TextIO

import duckdb

from pycifparse.inspect._common import (
    c, BOLD, DIM, RED, YELLOW, GREEN, CYAN,
)


@dataclass
class TraceEvent:
    """One event captured during :func:`inspect_ingest`.

    Attributes
    ----------
    kind:
        Category of event.  One of:

        - ``'warning'``      — non-fatal semantic issue (e.g. unrecognised tag)
        - ``'error'``        — fatal semantic error
    detail:
        Human-readable description of the event.
    block_id:
        CIF data-block name where the event occurred, if known.
    table:
        DuckDB table name involved, if applicable.
    tag:
        CIF tag involved, if applicable.
    """

    kind: str
    detail: str
    block_id: Optional[str] = None
    table: Optional[str] = None
    tag: Optional[str] = None


def inspect_ingest(
    cif,
    db: duckdb.DuckDBPyConnection | None = None,
    schema=None,
    *,
    propagate_fk: bool = False,
    dataset_id=None,
    file: Optional[TextIO] = None,
) -> list[TraceEvent]:
    """Run ingestion, capture events, and pretty-print a diagnostic trace.

    Parameters
    ----------
    cif:
        Parsed ``CifFile`` from ``build()``.
    db:
        Open ``duckdb.DuckDBPyConnection``, or ``None`` for a fresh in-memory DB.
    schema:
        ``SchemaSpec`` used to route tags, or ``None`` to route all to fallback.
    propagate_fk:
        Forwarded to ``ingest()``.
    dataset_id:
        Forwarded to ``ingest()``.
    file:
        Where to write the trace.  Defaults to ``sys.stdout``.

    Returns
    -------
    list[TraceEvent]
        All captured events in occurrence order.
    """
    if file is None:
        file = sys.stdout

    from pycifparse.ingestion.ingest import ingest

    events: list[TraceEvent] = []

    print(c('-- inspect_ingest --', BOLD, DIM, file=file), file=file)

    try:
        _, ingest_errors = ingest(
            cif, db, schema=schema,
            propagate_fk=propagate_fk,
            dataset_id=dataset_id,
        )
        for msg in ingest_errors:
            events.append(TraceEvent(kind='warning', detail=msg))

    except ValueError as exc:
        events.append(TraceEvent(kind='error', detail=str(exc)))

    except Exception as exc:
        events.append(TraceEvent(kind='error', detail=str(exc)))

    warnings_ev = [e for e in events if e.kind == 'warning']
    errors_ev = [e for e in events if e.kind == 'error']

    if warnings_ev:
        print(c(f'  {len(warnings_ev)} semantic warning(s):', YELLOW, file=file), file=file)
        for ev in warnings_ev:
            print(f'    {c("~", YELLOW, file=file)}  {ev.detail}', file=file)

    if errors_ev:
        print(c(f'  {len(errors_ev)} error(s):', RED, BOLD, file=file), file=file)
        for ev in errors_ev:
            print(f'    {c("!", RED, file=file)}  {ev.detail}', file=file)

    if not warnings_ev and not errors_ev:
        print(c('  Ingestion completed with no warnings.', GREEN, file=file), file=file)

    _print_trace_summary(events, file)
    return events


def _print_trace_summary(events: list[TraceEvent], file: TextIO) -> None:
    warnings  = sum(1 for e in events if e.kind == 'warning')
    errors    = sum(1 for e in events if e.kind == 'error')
    print(
        c(
            f'  [{warnings} warning(s)  {errors} error(s)]',
            DIM, file=file,
        ),
        file=file,
    )
    print(file=file)
