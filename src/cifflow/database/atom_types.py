"""standardise_atom_type_symbols — normalize oxidation-state notation in atom_type.symbol."""

from __future__ import annotations

import re

import duckdb

from cifflow.dictionary.schema import SchemaSpec

# Matches element + sign + optional_digits only  (Al+3, O-2, Li+, O-)
# Symbols already in canonical form (Al3+, O2-) do NOT match and are left unchanged.
_ELEM_SIGN_DIGITS = re.compile(r'^([A-Z][a-z]?)([+\-])(\d*)$')


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _standardise_symbol(symbol: str) -> str:
    """Return *symbol* in canonical ``element + digit + sign`` form.

    Only converts the ``element + sign + optional_digit`` form (e.g. ``'Al+3'``,
    ``'Li+'``).  Symbols already in canonical form (``'Al3+'``) or with no
    oxidation notation (``'Si'``) are returned unchanged.
    Absent digits default to ``'1'`` (e.g. ``'Li+'`` → ``'Li1+'``).
    """
    m = _ELEM_SIGN_DIGITS.match(symbol)
    if m:
        elem, sign, digits = m.group(1), m.group(2), m.group(3)
        return elem + (digits or '1') + sign
    return symbol


def standardise_atom_type_symbols(
    connection: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
) -> int:
    """Normalize oxidation-state notation in ``atom_type.symbol`` and all FK columns.

    Converts both input forms to the canonical ``element + digit + sign`` format:

    - ``'Al+3'`` → ``'Al3+'``
    - ``'O-2'``  → ``'O2-'``
    - ``'Li+'``  → ``'Li1+'``  (absent digit filled as ``1``)
    - ``'Si'``   → ``'Si'``    (no oxidation state — unchanged)

    If the canonical symbol already exists as a separate ``atom_type`` row,
    FK references are updated to point to the existing row and the redundant
    row is deleted.  Otherwise the ``symbol`` PK is renamed in-place.

    All FK columns referencing ``atom_type.symbol`` are updated to match.

    Parameters
    ----------
    connection
        Open DuckDB connection containing ingested schema tables.
    schema
        Schema descriptor produced by
        :func:`~cifflow.dictionary.schema.generate_schema`.

    Returns
    -------
    int
        Number of ``atom_type.symbol`` values changed.
    """
    # Collect all FK columns pointing to atom_type.symbol.
    fk_cols: list[tuple[str, str]] = []
    for tname, td in schema.tables.items():
        for fk in td.foreign_keys:
            if fk.target_table != 'atom_type':
                continue
            for src_col, tgt_col in zip(fk.source_columns, fk.target_columns):
                if tgt_col == 'symbol':
                    fk_cols.append((tname, src_col))

    # Fetch current symbols.
    rows = connection.execute('SELECT "symbol" FROM atom_type').fetchall()
    symbols = [r[0] for r in rows if r[0] is not None]

    changes: dict[str, str] = {}
    for sym in symbols:
        canonical = _standardise_symbol(sym)
        if canonical != sym:
            changes[sym] = canonical

    if not changes:
        return 0

    connection.execute('BEGIN TRANSACTION')
    total = 0
    ok = False
    try:
        for old_sym, new_sym in changes.items():
            # Update FK columns first so no dangling references remain.
            for tname, col in fk_cols:
                connection.execute(
                    f'UPDATE {_qi(tname)} SET {_qi(col)} = ? WHERE {_qi(col)} = ?',
                    [new_sym, old_sym],
                )
            # Rename or merge the atom_type row.
            existing = connection.execute(
                'SELECT COUNT(*) FROM atom_type WHERE "symbol" = ?', [new_sym]
            ).fetchone()[0]
            if existing:
                # Canonical symbol already has a row — delete the now-unreferenced old row.
                connection.execute(
                    'DELETE FROM atom_type WHERE "symbol" = ?', [old_sym]
                )
            else:
                connection.execute(
                    'UPDATE atom_type SET "symbol" = ? WHERE "symbol" = ?',
                    [new_sym, old_sym],
                )
            total += 1
        ok = True
    except Exception:
        raise
    finally:
        if ok:
            connection.execute('COMMIT')
        else:
            try:
                connection.execute('ROLLBACK')
            except duckdb.Error:
                pass

    return total
