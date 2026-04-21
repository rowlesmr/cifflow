"""Per-check helper functions for the database validation stage."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from pycifparse.dictionary.schema import ColumnDef
from pycifparse.ingestion.ingest import decode_container
from pycifparse.output.quote import is_table_key_quotable

_SENTINELS = frozenset({'.', '?'})

_URI_RE = re.compile(
    r'^([A-Za-z][A-Za-z0-9+\-.]*:)?'
    r'(//[^/?#]*)?'
    r'[^?#]*'
    r'(\?[^#]*)?'
    r'(#.*)?$'
)

_SU_STRIP = re.compile(r'\(\d+\)$')

# Marker for JSON null in leaf extraction.
_NULL_LEAF = object()

# (check_name, severity, message, value_str)
CheckResult = tuple[str, str, str, str]


# ---------------------------------------------------------------------------
# Dimension string parser
# ---------------------------------------------------------------------------

def parse_type_dimension(dim_str: str) -> tuple[int, ...] | None:
    """
    Parse '[3,4]' → (3, 4).  Returns None for '[]', malformed strings, or any
    dimension value <= 0.
    """
    s = dim_str.strip()
    if not (s.startswith('[') and s.endswith(']')):
        return None
    inner = s[1:-1].strip()
    if not inner:
        return None  # '[]' → unknown; skip
    result: list[int] = []
    for p in inner.split(','):
        p = p.strip()
        if not p.isdigit():
            return None
        n = int(p)
        if n <= 0:
            return None
        result.append(n)
    return tuple(result)


# ---------------------------------------------------------------------------
# type_contents validators (defined before the rules table)
# ---------------------------------------------------------------------------

def _valid_datetime(v: str) -> bool:
    try:
        datetime.fromisoformat(v)
        return True
    except ValueError:
        return False


def _valid_real(v: str) -> bool:
    s = _SU_STRIP.sub('', v)
    try:
        float(s)
        return True
    except ValueError:
        return False


def _valid_range(v: str) -> bool:
    m = re.match(r'^(-?\S+)?:(-?\S+)?$', v)
    if not m:
        return False
    for side in (m.group(1), m.group(2)):
        if side is not None:
            try:
                float(side)
            except ValueError:
                return False
    return True


# (predicate returning True if VALID, severity for failure, display name)
# None predicate = always valid.  'skip' string = skip without checking.
_TYPE_CONTENTS_RULES: dict[str, tuple[Any, str, str]] = {
    'Text':        (None,                                                          '',        'Text'),
    'Word':        (lambda v: not re.search(r'\s', v),                            'Warning', 'Word'),
    'Code':        (lambda v: not re.search(r'\s', v),                            'Warning', 'Code'),
    'Name':        (lambda v: bool(re.match(r'^[A-Za-z0-9_]+$', v)),             'Warning', 'Name'),
    'Tag':         (lambda v: bool(re.match(r'^_\S+$', v)),                       'Warning', 'Tag'),
    'Uri':         (lambda v: bool(_URI_RE.match(v)) and not re.search(r'\s', v), 'Warning', 'Uri'),
    'Iri':         (lambda v: not re.search(r'[\t\n\r]', v),                      'Warning', 'Iri'),
    'Date':        (lambda v: bool(re.match(r'^\d{4}-\d{2}-\d{2}$', v)),         'Warning', 'Date'),
    'DateTime':    (_valid_datetime,                                               'Warning', 'DateTime'),
    'Version':     (lambda v: bool(re.match(r'^\d+\.\d+\.\d+', v)),              'Warning', 'Version'),
    'Dimension':   (lambda v: bool(re.match(r'^\[(\d+(,\d+)*)?\]$', v)),         'Warning', 'Dimension'),
    'Range':       (_valid_range,                                                  'Warning', 'Range'),
    'Integer':     (lambda v: bool(re.match(r'^[+-]?\d+$', v)),                  'Error',   'Integer'),
    'Real':        (_valid_real,                                                   'Error',   'Real'),
    'Symop':       (lambda v: bool(re.match(r'^\d+([_ ]\d{3,})?$', v)),          'Warning', 'Symop'),
    'Imag':        (None,    '', 'Imag'),
    'Complex':     (None,    '', 'Complex'),
    'Implied':     ('skip',  '', 'Implied'),
    'ByReference': ('skip',  '', 'ByReference'),
    'Inherited':   ('skip',  '', 'Inherited'),
}


# ---------------------------------------------------------------------------
# Check A — type_container
# ---------------------------------------------------------------------------

def check_type_container(
    value: str,
    col: ColumnDef,
) -> tuple[list[CheckResult], bool, Any]:
    """
    Returns (results, block_bce, parsed_json).

    block_bce is True when Checks B–E must be skipped for this value.
    parsed_json is the decoded JSON object, or None for Single / parse failure.
    """
    tc = col.type_container

    if tc == 'Implied':
        msg = "'Implied' container is only valid in DDLm Reference Dictionaries"
        return [('type_container', 'Error', msg, value)], True, None

    if tc is None or tc == 'Single':
        try:
            parsed = decode_container(value)
        except (json.JSONDecodeError, ValueError):
            return [], False, None
        if isinstance(parsed, list):
            return [('type_container', 'Error', "Expected scalar, got JSON array", value)], True, None
        if isinstance(parsed, dict):
            return [('type_container', 'Error', "Expected scalar, got JSON object", value)], True, None
        return [], False, None

    if tc in ('List', 'Array', 'Matrix'):
        try:
            parsed = decode_container(value)
        except (json.JSONDecodeError, ValueError):
            return [('type_container', 'Error', "Expected JSON array, got scalar", value)], True, None
        if isinstance(parsed, dict):
            return [('type_container', 'Error', "Expected JSON array, got JSON object", value)], True, None
        if not isinstance(parsed, list):
            return [('type_container', 'Error', "Expected JSON array, got scalar", value)], True, None
        return [], False, parsed

    if tc == 'Table':
        try:
            parsed = decode_container(value)
        except (json.JSONDecodeError, ValueError):
            return [('type_container', 'Error', "Expected JSON object, got scalar", value)], True, None
        if isinstance(parsed, list):
            return [('type_container', 'Error', "Expected JSON object, got JSON array", value)], True, None
        if not isinstance(parsed, dict):
            return [('type_container', 'Error', "Expected JSON object, got scalar", value)], True, None
        # Unquotable-key failures do NOT block Checks C–E.
        results: list[CheckResult] = []
        for key in parsed:
            if not is_table_key_quotable(key):
                results.append((
                    'type_container', 'Error',
                    f"Table key {key!r} cannot be expressed as an inline quoted CIF string",
                    key,
                ))
        return results, False, parsed

    return [], False, None


# ---------------------------------------------------------------------------
# Check B — type_dimension
# ---------------------------------------------------------------------------

def _check_dims_recursive(
    value: Any,
    dims: tuple[int, ...],
    depth: int,
    strict: bool,
    parent: Any = None,
) -> list[CheckResult]:
    """Recursively validate nested-list shape against dims."""
    if depth >= len(dims):
        return []

    expected = dims[depth]

    if not isinstance(value, list):
        ndim = len(dims)
        actual_ndim = depth
        msg = f"Expected {ndim}-D container, got {actual_ndim}-D"
        report_val = json.dumps(parent) if parent is not None else json.dumps(value)
        return [('type_dimension', 'Warning', msg, report_val)]

    actual = len(value)
    if actual != expected:
        k = depth + 1
        msg = f"Expected {expected} elements at dimension {k}, got {actual}"
        return [('type_dimension', 'Warning', msg, json.dumps(value))]

    if depth + 1 >= len(dims):
        return []

    for elem in value:
        if not strict and elem is None:
            continue
        sub = _check_dims_recursive(elem, dims, depth + 1, strict, parent=value)
        if sub:
            return sub

    return []


def check_type_dimension(
    parsed: Any,
    col: ColumnDef,
    strict_container_nulls: bool,
) -> list[CheckResult]:
    if col.type_dimension is None:
        return []
    if col.type_container not in ('List', 'Array', 'Matrix'):
        return []
    dims = parse_type_dimension(col.type_dimension)
    if dims is None:
        return []
    return _check_dims_recursive(parsed, dims, 0, strict_container_nulls)


# ---------------------------------------------------------------------------
# Leaf extraction for Checks C/D/E
# ---------------------------------------------------------------------------

def extract_leaves(parsed: Any) -> list:
    """
    Recursively extract leaf values from a parsed JSON container.

    Each element is either a str or _NULL_LEAF (for JSON null).
    """
    if parsed is None:
        return [_NULL_LEAF]
    if isinstance(parsed, list):
        result = []
        for item in parsed:
            result.extend(extract_leaves(item))
        return result
    if isinstance(parsed, dict):
        result = []
        for v in parsed.values():
            result.extend(extract_leaves(v))
        return result
    if isinstance(parsed, str):
        return [parsed]
    return [str(parsed)]  # number or bool → coerce to str


# ---------------------------------------------------------------------------
# Check C — type_contents
# ---------------------------------------------------------------------------

def check_type_contents_leaf(leaf: str, col: ColumnDef) -> list[CheckResult]:
    tc = col.type_contents or 'Text'
    rule = _TYPE_CONTENTS_RULES.get(tc)
    if rule is None:
        return []
    predicate, severity, type_name = rule
    if predicate is None or predicate == 'skip':
        return []
    if predicate(leaf):
        return []
    msg = f"Expected {type_name}, got {leaf!r}"
    return [('type_contents', severity, msg, leaf)]


# ---------------------------------------------------------------------------
# Check D — enumeration_range
# ---------------------------------------------------------------------------

def check_enumeration_range_leaf(leaf: str, col: ColumnDef) -> list[CheckResult]:
    if col.enumeration_range is None:
        return []
    tc = col.type_contents or 'Text'
    if tc not in ('Integer', 'Real'):
        return []

    s = _SU_STRIP.sub('', leaf)
    try:
        v = float(s)
    except ValueError:
        return []  # not parseable; type_contents Error already recorded

    range_str = col.enumeration_range
    parts = range_str.split(':')
    if len(parts) != 2:
        return []
    lo_str, hi_str = parts[0], parts[1]

    if lo_str:
        try:
            lo = float(lo_str)
            if v < lo:
                return [('enumeration_range', 'Error',
                         f"Value {leaf!r} is below lower bound {lo} (range {range_str!r})", leaf)]
        except ValueError:
            pass

    if hi_str:
        try:
            hi = float(hi_str)
            if v > hi:
                return [('enumeration_range', 'Error',
                         f"Value {leaf!r} is above upper bound {hi} (range {range_str!r})", leaf)]
        except ValueError:
            pass

    return []


# ---------------------------------------------------------------------------
# Check E — enumeration_states
# ---------------------------------------------------------------------------

def _format_states(states: list[str]) -> str:
    if len(states) <= 10:
        return repr(states)
    extra = len(states) - 10
    return repr(states[:10]) + f'... and {extra} more'


def check_enumeration_states_leaf(leaf: str, col: ColumnDef) -> list[CheckResult]:
    if not col.enumeration_states:
        return []

    tc = col.type_contents or 'Text'
    case_insensitive = tc in ('Code', 'Name')

    if case_insensitive:
        if leaf.lower() in {s.lower() for s in col.enumeration_states}:
            return []
    else:
        if leaf in col.enumeration_states:
            return []

    msg = f"Value {leaf!r} is not in allowed set {_format_states(col.enumeration_states)}"
    return [('enumeration_states', 'Error', msg, leaf)]
