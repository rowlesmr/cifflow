"""
Value quoting for CIF output.

``quote(stored, version)`` converts a value as stored in the SQLite database
back to a valid CIF token, selecting the least-restrictive delimiter that
produces a correctly round-trippable result.

Storage encoding (from ``ingest.encode_value``):
- PLACEHOLDER ``.`` / ``?``        → stored as ``.`` / ``?``   (length 1)
- Quoted ``.`` / ``?``             → stored as ``"."`` / ``"?"`` (length 3)
- Container (list / table)         → stored as JSON text
- Everything else                  → stored as raw string
"""

from __future__ import annotations

from pycifparse.types import CifVersion

# Hardcoded prefix for prefixed semicolon-delimited fields.
# Must not start with ';'.  See Stage 6 spec for wire-format details.
_PREFIX = '>'

# Characters that are illegal as the first character of a bare word,
# for both CIF 1.1 and CIF 2.0 output.
# Single and double quotes are included: a value starting with ' or " would be
# mis-tokenised as the opening delimiter of a quoted string by any CIF reader.
_ILLEGAL_FIRST = frozenset({'_', '#', '$', '[', '{', "'", '"'})

# Reserved keywords: exact match (case-insensitive) requires quoting.
_RESERVED_EXACT = frozenset({'loop_', 'stop_', 'global_'})

# Reserved prefixes: any bare word starting with these (case-insensitive)
# would be mis-tokenised by a reader.
_RESERVED_PREFIX = ('data_', 'save_')


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def quote(stored: str, version: CifVersion) -> str:
    """Return a valid CIF token for *stored*, suitable for the given *version*.

    Parameters
    ----------
    stored:
        The value as retrieved from the SQLite database.  Presence-state
        encoding from ``encode_value`` is decoded here:

        - ``'.'`` or ``'?'`` (length 1) → PLACEHOLDER → returned unquoted.
        - ``'"."'`` or ``'"?"'`` (length 3) → quoted dot/question-mark →
          the inner character is re-quoted as a regular string.
        - All other values pass through the full quoting decision tree.

    version:
        ``CifVersion.CIF_2_0`` or ``CifVersion.CIF_1_1``.  Controls which
        delimiter types are available (triple-quoted strings are CIF 2.0 only).

    Returns
    -------
    str
        A valid CIF token.  Semicolon-delimited tokens begin with ``'\\n'``
        so the caller can distinguish them from inline tokens.
    """
    if stored in ('.', '?'):
        return stored                          # PLACEHOLDER — always unquoted
    if stored in ('"."', '"?"'):
        return _quote_string(stored[1], version)   # quoted dot/question-mark
    return _quote_string(stored, version)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _illegal_start(s: str) -> bool:
    """Return True if *s* cannot be the start of an unquoted bare word."""
    if not s:
        return True
    c = s[0]
    if c in _ILLEGAL_FIRST or c.isspace():
        return True
    sl = s.lower()
    if sl in _RESERVED_EXACT:
        return True
    return any(sl.startswith(p) for p in _RESERVED_PREFIX)


def _make_semicolon(s: str) -> str:
    """Wrap *s* in semicolon delimiters.  Result begins with '\\n'."""
    return f'\n;{s}\n;'


def _make_prefixed_semicolon(s: str) -> str:
    """Wrap *s* in a prefixed semicolon field.  Result begins with '\\n'.

    Wire format (prefix ``>``)::

        ;>\\
        >line 1
        >line 2 which might start with ;
        ;

    The ``\\`` on the opening line is the fold indicator from the CIF 2.0
    spec.  Un-prefixing: strip prefix from every line; discard the first
    line (which reduces to ``\\``, not ``\\\\``).
    """
    lines = s.split('\n')
    body = '\n'.join(f'{_PREFIX}{line}' for line in lines)
    return f'\n;{_PREFIX}\\\n{body}\n;'


def _quote_string(s: str, version: CifVersion) -> str:
    has_newline = '\n' in s
    has_single  = "'" in s
    has_double  = '"' in s
    has_space   = ' ' in s or '\t' in s
    bad_start   = _illegal_start(s)

    if version == CifVersion.CIF_2_0:
        return _quote_cif2(s, has_newline, has_single, has_double,
                           has_space, bad_start)
    else:
        return _quote_cif11(s, has_newline, has_single, has_double,
                            has_space, bad_start)


def _quote_cif2(
    s: str,
    has_newline: bool,
    has_single: bool,
    has_double: bool,
    has_space: bool,
    bad_start: bool,
) -> str:
    # Rule 2 — bare word.  '.' and '?' are excluded: as bare words they would
    # be re-parsed as PLACEHOLDER, not as the string values they represent.
    # Single and double quotes are also excluded mid-word: a CIF reader entering
    # NORMAL state mid-token will re-enter a quoted-string state on ' or ".
    if (not has_newline and not has_space and not has_single and not has_double
            and not bad_start and s not in ('.', '?')):
        return s

    has_triple_single = "'''" in s
    has_triple_double = '"""' in s

    has_ending_single = s.endswith("'")
    has_ending_double = s.endswith('"')

    if not has_newline:
        # Rules 3 & 4 — use single quotes when no single-quote in value
        if not has_single:
            return f"'{s}'"
        # Rule 5 — use double quotes when no double-quote in value
        if not has_double:
            return f'"{s}"'
        # Rule 6 — both quote types present, no newline.
        # Must still check for triple-quote conflicts before choosing delimiter.
        if not has_triple_single and not has_triple_double and not has_ending_single:
            return f"'''{s}'''"
        if not has_triple_single and not has_triple_double and not has_ending_double:
            return f'"""{s}"""'
        if has_triple_single and not has_triple_double and not has_ending_double:
            return f'"""{s}"""'
        if has_triple_double and not has_triple_single and not has_ending_single:
            return f"'''{s}'''"
        # Both triple types present — fall through to semicolon below
    else:
        # has_newline is True
        # Rule 7 — newline, no triple quotes present
        if not has_triple_single and not has_triple_double and not has_ending_single:
            return f"'''{s}'''"
        if not has_triple_single and not has_triple_double and not has_ending_double:
            return f'"""{s}"""'
        # Rule 8 — contains ''' but not """
        if has_triple_single and not has_triple_double and not has_ending_double:
            return f'"""{s}"""'
        # Rule 9 — contains """ but not '''
        if has_triple_double and not has_triple_single and not has_ending_single:
            return f"'''{s}'''"
        # Both triple types present — fall through to semicolon below
    # Rules 10 & 11 — contains both triple types → semicolon
    if '\n;' not in s:
        return _make_semicolon(s)
    return _make_prefixed_semicolon(s)


def _quote_cif11(
    s: str,
    has_newline: bool,
    has_single: bool,
    has_double: bool,
    has_space: bool,
    bad_start: bool,
) -> str:
    # Rule 2 — bare word.  '.' and '?' excluded for the same reason as CIF 2.0.
    # Single and double quotes excluded mid-word for the same reason.
    if (not has_newline and not has_space and not has_single and not has_double
            and not bad_start and s not in ('.', '?')):
        return s

    if not has_newline:
        # Rules 3 & 4 — single quotes when no single-quote in value
        if not has_single:
            return f"'{s}'"
        # Rule 5 — double quotes when no double-quote in value
        if not has_double:
            return f'"{s}"'
        # Rule 6 — both quote types, no newline → semicolon (no triple in 1.1)
        return _make_semicolon(s)

    # has_newline → must use semicolon in CIF 1.1 (no triple-quoted strings)
    if '\n;' not in s:
        return _make_semicolon(s)
    return _make_prefixed_semicolon(s)
