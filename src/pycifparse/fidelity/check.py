"""
Fidelity comparison for CIF sources.

``check_fidelity`` compares two CIF sources — files, paths, or pre-parsed
``CifFile`` objects — by ingesting both into in-memory SQLite databases and
comparing the resulting data at the row level.

Known limitations
-----------------
**ValueType for structured tables**
    ``ValueType`` is not stored for structured table columns; only the raw
    string value is persisted.  ``ValueType`` fidelity for schema-known tags
    is therefore not checkable.  For ``_cif_fallback``, ``value_type`` is
    stored and compared directly.

**SU fidelity in ``_cif_fallback``**
    For structured tables, SU columns are normalised with
    ``Decimal.normalize()`` so that ``0.001`` and ``0.0010`` compare equal.
    For ``_cif_fallback``, SU values are embedded in the full ``value(su)``
    string (e.g. ``3.992(1)``) and are compared as raw strings.  Equivalent
    SU representations such as ``3.992(1)`` and ``3.9920(10)`` will compare
    as unequal.

**Default-filled values (``_cif_synthetic``)**
    Values filled from ``enumeration_default`` during ingestion are excluded
    from comparison.  An explicit value in one source and a default-filled
    value in the other will produce a ``"row_content"`` mismatch even if
    identical.  (``_cif_synthetic`` is specced but not yet implemented in the
    ingestion layer; this step is a no-op until it is.)

**``version`` parameter**
    The ``version`` parameter is not yet propagated to the parser as a
    fallback default.  Version detection uses the file magic line; files
    without a magic line are parsed as CIF 1.1 regardless of ``version``.

**UUID-keyed tables**
    When comparing sources where one uses natural primary keys and another
    uses generated UUID keys (e.g. ALL_BLOCKS output merging multiple CIF
    blocks), all PK columns of UUID-keyed tables and all FK columns pointing
    to those tables are stripped from the row representation in *both*
    connections.  This allows content comparison without key-structure
    comparison.
"""

from __future__ import annotations

import pathlib
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

import duckdb

from pycifparse.cifmodel.model import CifFile
from pycifparse.cifmodel.builder import build
from pycifparse.dictionary.schema import SchemaSpec, TableDef, ColumnDef
from pycifparse.ingestion.ingest import ingest
from pycifparse.types import CifVersion


_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

_SYNTHETIC_COLS = frozenset({'_block_id', '_row_id', '_pycifparse_id'})


def _is_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value))


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class FidelityMismatch:
    kind: str                           # machine-readable category
    source: Literal['a', 'b', 'both']  # which source(s) the mismatch is tied to
    description: str                    # human-readable explanation


@dataclass
class FidelityReport:
    passed: bool
    mismatches: list[FidelityMismatch]


# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------

def _load_schema(schema) -> SchemaSpec | None:
    """Resolve *schema* to a ``SchemaSpec`` or ``None``."""
    if schema is None:
        return None
    if isinstance(schema, SchemaSpec):
        return schema
    if isinstance(schema, dict):
        raise TypeError(
            "dict schema not yet supported; pass a SchemaSpec, file path, or None"
        )
    path = pathlib.Path(schema)
    suffix = path.suffix.lower()
    if suffix == '.json':
        from pycifparse.dictionary.cache import load_dictionary
        from pycifparse.dictionary.schema import generate_schema
        return generate_schema(load_dictionary(path))
    if suffix == '.dic':
        from pycifparse.dictionary.loader import DictionaryLoader, directory_resolver
        from pycifparse.dictionary.schema import generate_schema
        raw = path.read_text(encoding='utf-8')
        loader = DictionaryLoader(resolver=directory_resolver(path.parent))
        return generate_schema(loader.load(raw))
    raise ValueError(f'unrecognised schema file extension: {path.suffix!r}')


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------

def _load_source(source, _version: CifVersion) -> tuple[CifFile | None, list]:
    """Parse *source* into ``(CifFile, parse_errors)``.

    Accepts a ``CifFile`` (used directly), a ``pathlib.Path`` or single-line
    ``str`` (treated as a file path), or a multi-line ``str`` (treated as raw
    CIF content).
    """
    if isinstance(source, CifFile):
        return source, []
    if isinstance(source, str) and '\n' in source:
        # Raw CIF content
        cif, errors = build(source)
        return cif, errors
    text = pathlib.Path(source).read_text(encoding='utf-8')
    cif, errors = build(text)
    return cif, errors


# ---------------------------------------------------------------------------
# Real / SU normalisation helpers
# ---------------------------------------------------------------------------

def _strip_su_suffix(value: str) -> str:
    """Strip a trailing ``(digits)`` SU suffix from *value*."""
    idx = value.rfind('(')
    if idx >= 0 and value.endswith(')'):
        return value[:idx]
    return value


def _canonical_real(value: str, is_su: bool) -> str:
    """Return the canonical form of a Real column value."""
    stripped = _strip_su_suffix(value)
    try:
        d = Decimal(stripped)
    except InvalidOperation:
        return value
    return str(d.normalize()) if is_su else format(d, 'f')


# ---------------------------------------------------------------------------
# Schema precomputation helpers
# ---------------------------------------------------------------------------

def _is_su_col(col: ColumnDef) -> bool:
    """Return True if *col* is a SU column (has linked_item_id pointing to its measurand)."""
    return col.linked_item_id is not None


def _col_map(schema: SchemaSpec) -> dict[tuple[str, str], ColumnDef]:
    """Return ``{(table_name, col_name): ColumnDef}``."""
    return {
        (tname, col.name): col
        for tname, tdef in schema.tables.items()
        for col in tdef.columns
    }


# ---------------------------------------------------------------------------
# UUID-keyed table detection
# ---------------------------------------------------------------------------

def _uuid_pk_tables(
    conn_a: duckdb.DuckDBPyConnection,
    conn_b: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
) -> frozenset[str]:
    """Return table names where either connection has UUID values in any PK column.

    Samples up to 500 rows per PK column; sufficient for detection since UUID
    PKs are generated uniformly per row.
    """
    result: set[str] = set()
    for tname, tdef in schema.tables.items():
        for pk in tdef.primary_keys:
            for conn in (conn_a, conn_b):
                try:
                    rows = conn.execute(
                        f'SELECT "{pk}" FROM "{tname}" LIMIT 500'
                    ).fetchall()
                    if any(_is_uuid(str(v)) for (v,) in rows if v is not None):
                        result.add(tname)
                        break
                except Exception:
                    pass
            if tname in result:
                break
    return frozenset(result)


def _fk_to_uuid_cols(
    schema: SchemaSpec,
    uuid_tbls: frozenset[str],
) -> dict[str, frozenset[str]]:
    """Return ``{table: frozenset(FK columns pointing to a UUID-PK table)}``."""
    result: dict[str, frozenset[str]] = {}
    for tname, tdef in schema.tables.items():
        cols: set[str] = set()
        for fkdef in tdef.foreign_keys:
            if fkdef.target_table in uuid_tbls:
                cols.update(fkdef.source_columns)
        result[tname] = frozenset(cols)
    return result


# ---------------------------------------------------------------------------
# DuckDB row-dict helpers
# ---------------------------------------------------------------------------

def _fetchall_dicts(cursor) -> list[dict]:
    """Convert DuckDB cursor results to list of column-keyed dicts."""
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Row normalisation
# ---------------------------------------------------------------------------

def _load_synthetic_set(conn: duckdb.DuckDBPyConnection) -> set[tuple]:
    """Return ``{(table_name, row_id, column_name)}`` from ``_cif_synthetic``.

    Returns an empty set if the table does not exist (not yet implemented in
    the ingestion layer).
    """
    try:
        rows = conn.execute(
            'SELECT "table_name", "row_id", "column_name" FROM "_cif_synthetic"'
        ).fetchall()
        return {(r[0], r[1], r[2]) for r in rows}
    except Exception:
        return set()


def _table_present(
    conn: duckdb.DuckDBPyConnection,
    tname: str,
    tdef: TableDef,
) -> bool:
    """Return True if *tname* has at least one non-synthetic, non-NULL value."""
    non_syn = [c.name for c in tdef.columns if not c.is_synthetic]
    if not non_syn:
        return False
    conditions = ' OR '.join(f'"{c}" IS NOT NULL' for c in non_syn)
    try:
        return conn.execute(
            f'SELECT 1 FROM "{tname}" WHERE {conditions} LIMIT 1'
        ).fetchone() is not None
    except Exception:
        return False


def _normalised_rows(
    conn: duckdb.DuckDBPyConnection,
    tname: str,
    tdef: TableDef,
    uuid_tbls: frozenset[str],
    uuid_fk_cols: dict[str, frozenset[str]],
    cmap: dict[tuple[str, str], ColumnDef],
    synthetic_set: set[tuple],
) -> list[frozenset]:
    """Return normalised rows for *tname* as a list of frozensets.

    For tables in *uuid_tbls*, all PK columns are stripped from both
    connections so that generated UUID keys don't prevent content matching.
    FK columns pointing to UUID-PK tables are also stripped for the same
    reason.
    """
    strip_pks: set[str] = set(tdef.primary_keys) if tname in uuid_tbls else set()
    strip_fks: frozenset[str] = uuid_fk_cols.get(tname, frozenset())

    try:
        all_rows = _fetchall_dicts(conn.execute(f'SELECT * FROM "{tname}"'))
    except Exception:
        return []

    result = []
    for row in all_rows:
        row_id = row.get('_row_id')
        normalised: dict[str, object] = {}

        for col_name, val in row.items():
            if col_name in _SYNTHETIC_COLS:
                continue
            if col_name in strip_pks:
                continue
            if col_name in strip_fks:
                continue
            if val is None:
                continue
            str_val = str(val)
            if str_val in ('.', '?'):
                continue
            if row_id is not None and (tname, row_id, col_name) in synthetic_set:
                continue
            col_def = cmap.get((tname, col_name))
            if col_def is not None and col_def.type_contents == 'Real':
                normalised[col_name] = _canonical_real(str_val, _is_su_col(col_def))
            else:
                normalised[col_name] = str_val

        result.append(frozenset(normalised.items()))
    return result


def _row_diff_hint(row: frozenset, candidates: list[frozenset]) -> str:
    """Return a compact diff string between *row* and its closest candidate."""
    if not candidates:
        pairs = sorted((k, v) for k, v in row if not isinstance(v, frozenset))
        parts = [f'{k}={v}' for k, v in pairs[:2]]
        return f' [{", ".join(parts)}]' if parts else ''

    best = max(candidates, key=lambda c: len(row & c))
    row_d = dict(row)
    best_d = dict(best)

    diffs: list[str] = []
    for k in sorted(set(row_d) | set(best_d)):
        va, vb = row_d.get(k), best_d.get(k)
        if isinstance(va, frozenset) or isinstance(vb, frozenset):
            continue
        if va != vb:
            if va is None:
                diffs.append(f'-{k}={vb}')
            elif vb is None:
                diffs.append(f'+{k}={va}')
            else:
                diffs.append(f'{k}: {va}!={vb}')

    if not diffs:
        return ''
    if len(diffs) > 3:
        hint = ', '.join(diffs[:3]) + f', +{len(diffs) - 3} more'
    else:
        hint = ', '.join(diffs)
    return f' [{hint}]'


def _compare_structured(
    conn_a: duckdb.DuckDBPyConnection,
    conn_b: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    uuid_tbls: frozenset[str],
    uuid_fk_cols: dict[str, frozenset[str]],
) -> list[FidelityMismatch]:
    cmap = _col_map(schema)
    syn_a = _load_synthetic_set(conn_a)
    syn_b = _load_synthetic_set(conn_b)

    mismatches: list[FidelityMismatch] = []

    for tname, tdef in schema.tables.items():
        present_a = _table_present(conn_a, tname, tdef)
        present_b = _table_present(conn_b, tname, tdef)

        if not present_a and not present_b:
            continue

        if present_a != present_b:
            src = 'a' if present_a else 'b'
            other = 'B' if present_a else 'A'
            mismatches.append(FidelityMismatch(
                kind='table_missing',
                source=src,
                description=(
                    f'table {tname!r} present in {src.upper()} '
                    f'but absent in {other}'
                ),
            ))
            continue

        rows_a = _normalised_rows(
            conn_a, tname, tdef, uuid_tbls, uuid_fk_cols, cmap, syn_a
        )
        rows_b = _normalised_rows(
            conn_b, tname, tdef, uuid_tbls, uuid_fk_cols, cmap, syn_b
        )

        ctr_a = Counter(rows_a)
        ctr_b = Counter(rows_b)
        surplus_a = ctr_a - ctr_b
        surplus_b = ctr_b - ctr_a

        hint_pool_b = rows_b[:200]
        hint_pool_a = rows_a[:200]
        for row, count in surplus_a.items():
            hint = _row_diff_hint(row, hint_pool_b)
            for _ in range(count):
                mismatches.append(FidelityMismatch(
                    kind='row_content',
                    source='both',
                    description=f'table {tname!r}: row in A has no equivalent in B{hint}',
                ))
        for row, count in surplus_b.items():
            hint = _row_diff_hint(row, hint_pool_a)
            for _ in range(count):
                mismatches.append(FidelityMismatch(
                    kind='row_content',
                    source='both',
                    description=f'table {tname!r}: row in B has no equivalent in A{hint}',
                ))

    return mismatches


# ---------------------------------------------------------------------------
# Step 4: compare _cif_fallback
# ---------------------------------------------------------------------------

def _compare_fallback(
    conn_a: duckdb.DuckDBPyConnection,
    conn_b: duckdb.DuckDBPyConnection,
) -> list[FidelityMismatch]:
    def _fetch(conn: duckdb.DuckDBPyConnection) -> list[tuple[str, str, str]]:
        try:
            rows = conn.execute(
                'SELECT "tag", "value", "value_type" FROM "_cif_fallback"'
            ).fetchall()
            return [(r[0], r[1], r[2]) for r in rows]
        except Exception:
            return []

    tuples_a = _fetch(conn_a)
    tuples_b = _fetch(conn_b)

    ctr_a = Counter(tuples_a)
    ctr_b = Counter(tuples_b)

    surplus_a = ctr_a - ctr_b
    surplus_b = ctr_b - ctr_a

    if not surplus_a and not surplus_b:
        return []

    # Group surplus by (tag, value)
    a_by_tv: dict[tuple[str, str], list[str]] = {}  # → [value_type, ...]
    b_by_tv: dict[tuple[str, str], list[str]] = {}

    for (tag, value, vtype), count in surplus_a.items():
        key = (tag, value)
        a_by_tv.setdefault(key, []).extend([vtype] * count)

    for (tag, value, vtype), count in surplus_b.items():
        key = (tag, value)
        b_by_tv.setdefault(key, []).extend([vtype] * count)

    all_keys = set(a_by_tv) | set(b_by_tv)
    mismatches: list[FidelityMismatch] = []

    for tv_key in all_keys:
        tag, value = tv_key
        a_vtypes = a_by_tv.get(tv_key, [])
        b_vtypes = b_by_tv.get(tv_key, [])
        n_a = len(a_vtypes)
        n_b = len(b_vtypes)

        if n_a == n_b and n_a > 0:
            for _ in range(n_a):
                mismatches.append(FidelityMismatch(
                    kind='value_type',
                    source='both',
                    description=(
                        f'tag {tag!r} value {value!r}: '
                        f'value_type differs'
                    ),
                ))
        else:
            for vtype in a_vtypes:
                mismatches.append(FidelityMismatch(
                    kind='fallback_mismatch',
                    source='both',
                    description=(
                        f'tag {tag!r} value {value!r} '
                        f'(type={vtype}) in A but not B'
                    ),
                ))
            for vtype in b_vtypes:
                mismatches.append(FidelityMismatch(
                    kind='fallback_mismatch',
                    source='both',
                    description=(
                        f'tag {tag!r} value {value!r} '
                        f'(type={vtype}) in B but not A'
                    ),
                ))

    return mismatches


# ---------------------------------------------------------------------------
# Step 5: schema mismatch detection
# ---------------------------------------------------------------------------

def _compare_schema_mismatch(
    conn_a: duckdb.DuckDBPyConnection,
    conn_b: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
) -> list[FidelityMismatch]:
    # Build reverse map: canonical_tag → [(table_name, col_name)]
    defid_to_cols: dict[str, list[tuple[str, str]]] = {}
    for (tname, cname), defid in schema.column_to_tag.items():
        defid_to_cols.setdefault(defid, []).append((tname, cname))

    def _in_structured(conn: duckdb.DuckDBPyConnection, tag: str) -> bool:
        """Return True if *tag* has at least one non-NULL value in a structured table."""
        defid = schema.alias_to_definition_id.get(tag, tag)
        for tname, cname in defid_to_cols.get(defid, []):
            try:
                r = conn.execute(
                    f'SELECT 1 FROM "{tname}" WHERE "{cname}" IS NOT NULL LIMIT 1'
                ).fetchone()
                if r is not None:
                    return True
            except Exception:
                pass
        return False

    def _fallback_tags(conn: duckdb.DuckDBPyConnection) -> list[str]:
        try:
            return [
                r[0] for r in conn.execute(
                    'SELECT "tag" FROM "_cif_fallback"'
                ).fetchall()
            ]
        except Exception:
            return []

    mismatches: list[FidelityMismatch] = []

    for tag in _fallback_tags(conn_a):
        if _in_structured(conn_b, tag):
            mismatches.append(FidelityMismatch(
                kind='schema_mismatch',
                source='both',
                description=(
                    f'tag {tag!r} in _cif_fallback in A '
                    f'but in structured table in B'
                ),
            ))

    for tag in _fallback_tags(conn_b):
        if _in_structured(conn_a, tag):
            mismatches.append(FidelityMismatch(
                kind='schema_mismatch',
                source='both',
                description=(
                    f'tag {tag!r} in _cif_fallback in B '
                    f'but in structured table in A'
                ),
            ))

    return mismatches


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def _format_report(
    report: FidelityReport,
    label_a: str,
    label_b: str,
    schema_spec: 'SchemaSpec | None' = None,
) -> str:
    """Return a human-readable text summary of *report*."""
    lines: list[str] = []
    lines.append('Fidelity Report')
    lines.append('=' * 60)
    lines.append(f'Source A : {label_a}')
    lines.append(f'Source B : {label_b}')
    if schema_spec is None:
        lines.append('Schema   : none (fallback comparison only)')
    elif schema_spec.dictionary_name:
        if schema_spec.source_files:
            files = ', '.join(schema_spec.source_files)
            lines.append(f'Schema   : {schema_spec.dictionary_name} ({files})')
        else:
            lines.append(f'Schema   : {schema_spec.dictionary_name}')
    else:
        lines.append('Schema   : (unknown)')
    lines.append('')

    if report.passed:
        lines.append('Result   : PASSED — sources are semantically identical')
        return '\n'.join(lines) + '\n'

    lines.append(f'Result   : FAILED — {len(report.mismatches)} mismatch(es)')
    lines.append('')

    # Summary by kind
    by_kind: dict[str, list[FidelityMismatch]] = {}
    for m in report.mismatches:
        by_kind.setdefault(m.kind, []).append(m)

    lines.append('Summary by kind:')
    for kind in sorted(by_kind):
        lines.append(f'  {kind:<22s}  {len(by_kind[kind]):>5}')
    lines.append('')

    # Detail grouped by kind
    lines.append('Detail:')
    for kind in sorted(by_kind):
        lines.append(f'  [{kind}]')
        for m in by_kind[kind]:
            lines.append(f'    ({m.source}) {m.description}')
        lines.append('')

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_fidelity(
    source_a: 'str | pathlib.Path | CifFile',
    source_b: 'str | pathlib.Path | CifFile',
    schema: 'str | pathlib.Path | SchemaSpec | dict | None' = None,
    *,
    version: CifVersion = CifVersion.CIF_2_0,
    report_file: 'str | pathlib.Path | None' = None,
) -> FidelityReport:
    """Compare two CIF sources for semantic equivalence.

    Parameters
    ----------
    source_a, source_b:
        CIF sources to compare.  Each may be a file path (``str`` or
        ``pathlib.Path``) or a pre-parsed ``CifFile`` object.
    schema:
        Schema to use for ingestion.  ``None`` compares only
        ``_cif_fallback``.  Accepts ``SchemaSpec``, ``.json`` cache path, or
        ``.dic`` DDLm dictionary path.
    version:
        Fallback CIF version for files without a magic line.  Default
        ``CIF_2_0``.  (Not yet propagated to the parser; see module
        docstring.)
    report_file:
        Optional path for a human-readable text report.  If provided, the
        report is written (UTF-8) before returning.  The file is always
        written regardless of whether the comparison passed or failed.

    Returns
    -------
    FidelityReport
        Never raises.  Parse and ingestion errors are captured in the report.

    Raises
    ------
    Exception
        Schema loading failures propagate directly (programming error, not
        data error).
    """
    mismatches: list[FidelityMismatch] = []

    def _label(src: object) -> str:
        if isinstance(src, CifFile):
            return 'CifFile object'
        return str(src)

    label_a = _label(source_a)
    label_b = _label(source_b)

    def _finish(ms: list[FidelityMismatch]) -> FidelityReport:
        rep = FidelityReport(passed=len(ms) == 0, mismatches=ms)
        if report_file is not None:
            pathlib.Path(report_file).write_text(
                _format_report(rep, label_a, label_b, schema_spec), encoding='utf-8'
            )
        return rep

    # Schema loading — propagates on failure (programming error)
    schema_spec = _load_schema(schema)

    # --- Step 1: load and parse sources ---
    cif_a, parse_errors_a = _load_source(source_a, version)
    for e in parse_errors_a:
        loc = f' at line {e.line}' if e.line else ''
        mismatches.append(FidelityMismatch(
            kind='parse_error', source='a',
            description=f'{e.error_type} error in A{loc}: {e.message}',
        ))

    cif_b, parse_errors_b = _load_source(source_b, version)
    for e in parse_errors_b:
        loc = f' at line {e.line}' if e.line else ''
        mismatches.append(FidelityMismatch(
            kind='parse_error', source='b',
            description=f'{e.error_type} error in B{loc}: {e.message}',
        ))

    if any(m.kind == 'parse_error' for m in mismatches):
        return _finish(mismatches)

    # --- Step 1 (continued): ingest ---
    conn_a = duckdb.connect()
    conn_b = duckdb.connect()

    ingest_ok_a = True
    ingest_ok_b = True

    try:
        conn_a, errors_a = ingest(cif_a, conn_a, schema=schema_spec)
        for msg in errors_a:
            mismatches.append(FidelityMismatch(
                kind='ingest_error', source='a', description=msg,
            ))
    except Exception as exc:
        ingest_ok_a = False
        mismatches.append(FidelityMismatch(
            kind='ingest_error', source='a', description=str(exc),
        ))

    try:
        conn_b, errors_b = ingest(cif_b, conn_b, schema=schema_spec)
        for msg in errors_b:
            mismatches.append(FidelityMismatch(
                kind='ingest_error', source='b', description=msg,
            ))
    except Exception as exc:
        ingest_ok_b = False
        mismatches.append(FidelityMismatch(
            kind='ingest_error', source='b', description=str(exc),
        ))

    if not ingest_ok_a or not ingest_ok_b:
        return _finish(mismatches)

    # --- Step 2: detect UUID-keyed tables ---
    if schema_spec is not None:
        uuid_tbls = _uuid_pk_tables(conn_a, conn_b, schema_spec)
        uuid_fk_cols = _fk_to_uuid_cols(schema_spec, uuid_tbls)
    else:
        uuid_tbls = frozenset()
        uuid_fk_cols = {}

    # --- Step 3: compare structured tables ---
    if schema_spec is not None:
        mismatches.extend(
            _compare_structured(conn_a, conn_b, schema_spec, uuid_tbls, uuid_fk_cols)
        )

    # --- Step 4: compare _cif_fallback ---
    mismatches.extend(_compare_fallback(conn_a, conn_b))

    # --- Step 5: schema mismatch detection ---
    if schema_spec is not None:
        mismatches.extend(_compare_schema_mismatch(conn_a, conn_b, schema_spec))

    return _finish(mismatches)
