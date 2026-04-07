"""
SQLite schema application — executes a ``SchemaSpec`` against a live connection.
"""

import sqlite3

from pycifparse.dictionary.schema import (
    SchemaSpec,
    emit_create_statements,
    emit_fallback_create_statements,
)


def apply_schema(
    conn: sqlite3.Connection,
    schema: SchemaSpec,
    *,
    drop_existing: bool = False,
) -> None:
    """
    Apply a :class:`~pycifparse.dictionary.schema.SchemaSpec` to a SQLite
    connection.

    Enables ``PRAGMA foreign_keys = ON`` and WAL journal mode, then executes
    all ``CREATE TABLE`` statements from *schema* inside a single transaction.
    If *drop_existing* is ``True``, each table is preceded by
    ``DROP TABLE IF EXISTS``.  The entire operation is rolled back on any
    failure.

    Python's ``sqlite3`` module auto-commits DDL outside any implicit
    transaction, so this function switches the connection to autocommit mode
    (``isolation_level = None``) for the duration of the call, issues an
    explicit ``BEGIN``, and restores the original ``isolation_level``
    afterwards.

    Parameters
    ----------
    conn:
        An open ``sqlite3.Connection``.  Must not have an active transaction
        when called.  The connection is modified in place; the caller retains
        ownership and is responsible for closing it.
    schema:
        The schema specification produced by
        :func:`~pycifparse.dictionary.schema.generate_schema`.
    drop_existing:
        When ``True``, drop each table before (re-)creating it.  Defaults to
        ``False``.

    Raises
    ------
    sqlite3.Error
        If any DDL statement fails.  The transaction is rolled back before
        re-raising.
    """
    # Switch to explicit (manual) transaction control.  Python's sqlite3 does
    # not wrap DDL inside its implicit transaction, so a plain `with conn:`
    # block is insufficient to guarantee rollback on DDL failure.
    old_isolation = conn.isolation_level
    conn.isolation_level = None  # autocommit mode

    try:
        # Connection-level pragmas — issued outside the DDL transaction.
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('PRAGMA journal_mode = WAL')

        stmts = emit_create_statements(schema)

        conn.execute('BEGIN')
        try:
            if drop_existing:
                for table_name in schema.tables:
                    quoted = '"' + table_name.replace('"', '""') + '"'
                    conn.execute(f'DROP TABLE IF EXISTS {quoted}')
            for stmt in stmts:
                conn.execute(stmt)
        except sqlite3.Error:
            conn.execute('ROLLBACK')
            raise
        else:
            conn.execute('COMMIT')

    finally:
        conn.isolation_level = old_isolation


def apply_fallback_schema(
    conn: sqlite3.Connection,
    *,
    drop_existing: bool = False,
) -> None:
    """
    Create the ``_cif_fallback`` table and its lookup index on *conn*.

    This function is the fallback-tier equivalent of :func:`apply_schema`.
    It must be called on any database that will receive CIF data, whether or
    not a dictionary-defined schema has also been applied.  When both tiers
    are used, call :func:`apply_schema` first and then this function.

    Parameters
    ----------
    conn:
        An open ``sqlite3.Connection``.  Must not have an active transaction
        when called.
    drop_existing:
        When ``True``, drop ``_cif_fallback`` and its index before
        (re-)creating them.  Defaults to ``False``.

    Raises
    ------
    sqlite3.Error
        If any DDL statement fails.  The transaction is rolled back before
        re-raising.
    """
    old_isolation = conn.isolation_level
    conn.isolation_level = None

    try:
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('PRAGMA journal_mode = WAL')

        stmts = emit_fallback_create_statements()

        conn.execute('BEGIN')
        try:
            if drop_existing:
                conn.execute('DROP INDEX IF EXISTS "idx_cif_fallback_tag_block"')
                conn.execute('DROP TABLE IF EXISTS "_cif_fallback"')
            for stmt in stmts:
                conn.execute(stmt)
        except sqlite3.Error:
            conn.execute('ROLLBACK')
            raise
        else:
            conn.execute('COMMIT')

    finally:
        conn.isolation_level = old_isolation
