"""inspect_ingest — trace what happens during CIF ingestion."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Optional, TextIO

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
        - ``'error'``        — fatal semantic error (e.g. conflicting values)
        - ``'fk_violation'`` — FK constraint violation detected before COMMIT
    detail:
        Human-readable description of the event.
    block_id:
        CIF data-block name where the event occurred, if known.
    table:
        SQLite table name involved, if applicable.
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
    conn: sqlite3.Connection,
    schema=None,
    *,
    propagate_fk: bool = False,
    dataset_id=None,
    file: Optional[TextIO] = None,
) -> list[TraceEvent]:
    """Run ingestion, capture events, and pretty-print a diagnostic trace.

    Identical to :func:`~pycifparse.ingest` in behaviour but captures semantic
    warnings, errors, and FK violations as :class:`TraceEvent` objects.  If any
    FK constraint would fail at ``COMMIT``, a human-readable violation report is
    printed to *file* before the rollback.

    Parameters
    ----------
    cif:
        Parsed ``CifFile`` from ``build()``.
    conn:
        Open ``sqlite3.Connection`` with schema applied.
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

    Raises
    ------
    IngestionError
        Re-raised (after printing diagnostics) if ingestion fails.
    """
    if file is None:
        file = sys.stdout

    from pycifparse.ingestion.ingest import _Ingester, IngestionError

    events: list[TraceEvent] = []

    def _on_error(message: str) -> None:
        ev = TraceEvent(kind='warning', detail=message)
        events.append(ev)

    def _pre_commit(ingestor: _Ingester) -> None:
        if schema is None:
            return
        violations_found = False
        for tbl_name in schema.tables:
            try:
                pragma_rows = ingestor.conn.execute(
                    f'PRAGMA foreign_key_check("{tbl_name}")'
                ).fetchall()
            except sqlite3.Error:
                continue

            if not pragma_rows:
                continue

            fk_list_rows = ingestor.conn.execute(
                f'PRAGMA foreign_key_list("{tbl_name}")'
            ).fetchall()
            fk_by_id: dict[int, tuple[str, list[str], list[str]]] = {}
            for fk_row in fk_list_rows:
                fk_id, _seq, parent_tbl, from_col, to_col = (
                    fk_row[0], fk_row[1], fk_row[2], fk_row[3], fk_row[4]
                )
                if fk_id not in fk_by_id:
                    fk_by_id[fk_id] = (parent_tbl, [], [])
                fk_by_id[fk_id][1].append(from_col)
                fk_by_id[fk_id][2].append(to_col)

            for prow in pragma_rows:
                child_tbl, rowid, parent_tbl_name, fkid = prow
                info = fk_by_id.get(fkid)

                if info is None:
                    detail = (
                        f"FK violation in '{child_tbl}' rowid={rowid}"
                        f" → '{parent_tbl_name}' (fkid={fkid})"
                    )
                    events.append(TraceEvent(
                        kind='fk_violation', detail=detail, table=child_tbl,
                    ))
                    violations_found = True
                    continue

                _, from_cols, to_cols = info
                try:
                    col_list = ', '.join(f'"{col}"' for col in from_cols)
                    val_row = ingestor.conn.execute(
                        f'SELECT {col_list} FROM "{child_tbl}" WHERE rowid = ?',
                        (rowid,),
                    ).fetchone()
                    if val_row:
                        vals = (
                            val_row[0] if len(from_cols) == 1
                            else dict(zip(from_cols, val_row))
                        )
                    else:
                        vals = '<unknown>'
                except sqlite3.Error:
                    vals = '<unknown>'

                if len(from_cols) == 1:
                    detail = (
                        f"'{child_tbl}'.'{from_cols[0]}' = {vals!r}"
                        f"  →  '{parent_tbl_name}'.'{to_cols[0]}'"
                        f"  (no matching parent row)"
                    )
                else:
                    detail = (
                        f"'{child_tbl}'.{from_cols} = {vals}"
                        f"  →  '{parent_tbl_name}'.{to_cols}"
                        f"  (no matching parent row)"
                    )

                events.append(TraceEvent(
                    kind='fk_violation', detail=detail, table=child_tbl,
                ))
                violations_found = True

        if not violations_found and schema is not None:
            _print_line(c('  No FK violations detected.', GREEN, file=file), file)

    def _print_line(text: str, f: TextIO) -> None:
        print(text, file=f)

    print(c('-- inspect_ingest --', BOLD, DIM, file=file), file=file)

    ingestor = _Ingester(
        cif, conn, schema,
        propagate_fk=propagate_fk,
        dataset_id=dataset_id,
        on_error=_on_error,
    )

    semantic_errors: list[str] = []
    try:
        semantic_errors = ingestor.run(_pre_commit_hook=_pre_commit)
    except IngestionError as exc:
        is_commit = exc.errors and exc.errors[0].startswith('COMMIT failed:')
        if is_commit:
            print(c('COMMIT FAILED — FK constraint violation:', RED, BOLD, file=file), file=file)
        else:
            print(
                c(f'INGESTION FAILED — {len(exc.errors)} semantic error(s):', RED, BOLD, file=file),
                file=file,
            )
        for err in exc.errors:
            events.append(TraceEvent(kind='error', detail=err))
            print(f'  {c("!", RED, file=file)} {err}', file=file)
        _print_trace_summary(events, file)
        raise

    # Promote semantic_errors from the ingestor (returned, not raised, errors)
    for msg in semantic_errors:
        events.append(TraceEvent(kind='error', detail=msg))

    # Print captured warnings
    warnings = [e for e in events if e.kind == 'warning']
    fk_violations = [e for e in events if e.kind == 'fk_violation']

    if warnings:
        print(c(f'  {len(warnings)} semantic warning(s):', YELLOW, file=file), file=file)
        for ev in warnings:
            print(f'    {c("~", YELLOW, file=file)} {ev.detail}', file=file)

    if fk_violations:
        print(c('FK violations found before COMMIT:', RED, BOLD, file=file), file=file)
        for ev in fk_violations:
            print(f'  {c("!", RED, file=file)} {ev.detail}', file=file)

    if not warnings and not fk_violations:
        print(c('  Ingestion completed with no warnings.', GREEN, file=file), file=file)

    _print_trace_summary(events, file)
    return events


def _print_trace_summary(events: list[TraceEvent], file: TextIO) -> None:
    warnings  = sum(1 for e in events if e.kind == 'warning')
    errors    = sum(1 for e in events if e.kind == 'error')
    fk_viols  = sum(1 for e in events if e.kind == 'fk_violation')
    print(
        c(
            f'  [{warnings} warning(s)  {errors} error(s)  {fk_viols} FK violation(s)]',
            DIM, file=file,
        ),
        file=file,
    )
    print(file=file)
