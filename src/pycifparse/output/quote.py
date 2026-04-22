"""
Value quoting for CIF output.

``quote(stored, version)`` converts a value as stored in the SQLite database
back to a valid CIF token, selecting the least-restrictive delimiter that
produces a correctly round-trippable result.

Storage encoding (from ``ingest.encode_value``):
- PLACEHOLDER ``.`` / ``?``        → stored as ``.`` / ``?``   (length 1)
- Quoted ``.`` / ``?``             → stored as ``"."`` / ``"?"`` (length 3)
- Container (list / table)         → stored as JSON text (CIF 2.0 only)
- Everything else                  → stored as raw string
"""

from __future__ import annotations

from pycifparse.ingestion.ingest import _CONTAINER_PREFIX, decode_container
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

def is_table_key_quotable(key: str) -> bool:
    """Return True if key can be expressed as an inline CIF 2.0 quoted string."""
    result = _quote_string(key, CifVersion.CIF_2_0)
    return not result.startswith('\n')


def _format_container(value: list | dict, version: CifVersion) -> str:
    """Render a decoded CIF container back to CIF 2.0 token syntax."""
    if isinstance(value, list):
        parts = [_format_container_element(v, version) for v in value]
        return '[' + ' '.join(parts) + ']'
    # dict → table
    parts = []
    for k, v in value.items():
        key_token = quote(k, version)
        val_token = _format_container_element(v, version)
        parts.append(f'{key_token}: {val_token}')
    return '{' + ' '.join(parts) + '}'


def _format_container_element(v: object, version: CifVersion) -> str:
    if isinstance(v, list):
        return _format_container(v, version)
    if isinstance(v, dict):
        return _format_container(v, version)
    return quote(str(v), version)


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
    if version == CifVersion.CIF_2_0 and stored.startswith(_CONTAINER_PREFIX):
        return _format_container(decode_container(stored), version)
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

    The single ``\\`` on the opening line signals prefix-only mode (no folding).
    Un-prefixing: strip prefix from every line; discard the first line (which
    reduces to ``\\``, the prefix-mode sentinel).
    """
    lines = s.split('\n')
    body = '\n'.join(f'{_PREFIX}{line}' for line in lines)
    return f'\n;{_PREFIX}\\\n{body}\n;'


def _fold_content_lines(logical_lines: list[str], max_width: int) -> list[str]:
    """Split each logical line into physical segments of at most *max_width* chars.

    Segments that require continuation (not the last for a given logical line)
    have ``'\\'`` appended — the CIF 2.0 line-folding continuation marker.

    Prefers breaking at the last space within the window; falls back to a hard
    break at *max_width* if no space is found.  The space itself is kept at the
    start of the following segment so that fold reconstruction (removing
    ``'\\\\<newline>'``) reproduces the original string exactly.
    """
    result: list[str] = []
    for line in logical_lines:
        while len(line) > max_width:
            break_at = line.rfind(' ', 0, max_width)
            if break_at <= 0:
                break_at = max_width
            result.append(line[:break_at] + '\\')
            line = line[break_at:]
        result.append(line)
    return result


def _make_folded_semicolon(s: str, line_limit: int) -> str:
    """Semicolon field with line-folding but no prefix.  Result begins with '\\n'.

    Wire format::

        ;\\
        line 1 content that may be folded\\
        continuation of line 1
        line 2 content
        ;

    The first content line ``\\`` activates fold mode.  A backslash at the end
    of any subsequent content line joins it to the next with no inserted char.

    Each physical content line is guaranteed to be at most *line_limit* chars.
    """
    max_width = line_limit - 1  # leave room for the '\\' fold marker on non-final segments
    physical = _fold_content_lines(s.split('\n'), max_width)
    body = '\n'.join(physical)
    return f'\n;\\\n{body}\n;'


def _make_prefixed_folded_semicolon(s: str, line_limit: int) -> str:
    """Semicolon field with both prefix and line-folding.  Result begins with '\\n'.

    Wire format (prefix ``>``)::

        ;>\\\\
        >line 1 content that may be folded\\
        >continuation of line 1
        >line 2 content
        ;

    The opening ``>\\\\`` (two backslashes after stripping the prefix) signals
    prefix + fold mode.  Each content line has ``>`` prepended; lines ending
    with ``\\`` are folded (continuation character removed on parse).

    Each physical content line is guaranteed to be at most *line_limit* chars.
    """
    # Each physical line is '{_PREFIX}{segment}[\\]', so the segment budget is
    # line_limit - len(_PREFIX) - 1 (the -1 reserves space for the fold marker).
    max_width = max(line_limit - len(_PREFIX) - 1, 1)
    physical = _fold_content_lines(s.split('\n'), max_width)
    body = '\n'.join(f'{_PREFIX}{seg}' for seg in physical)
    return f'\n;{_PREFIX}\\\\\n{body}\n;'


# ---------------------------------------------------------------------------
# Public text-field factory (used by emit layer for line-limit enforcement)
# ---------------------------------------------------------------------------

def make_text_field(s: str, line_limit: int | None = None) -> str:
    """Produce a semicolon-delimited CIF text field for *s*.

    Selects the correct wire format based on content requirements:

    +--------------+-------------+-----------------------------+
    | needs_prefix | needs_fold  | format used                 |
    +==============+=============+=============================+
    | False        | False       | plain semicolon             |
    | True         | False       | prefix-only semicolon       |
    | False        | True        | fold-only semicolon         |
    | True         | True        | prefix + fold semicolon     |
    +--------------+-------------+-----------------------------+

    *needs_prefix* is ``True`` when *s* contains ``'\\n;'``, which would
    otherwise prematurely terminate the field.

    *needs_fold* is ``True`` when *line_limit* is given and at least one
    content line in the text field would produce a physical line exceeding
    *line_limit* characters.

    Valid for both CIF 1.1 and CIF 2.0 (semicolon fields exist in both).
    """
    needs_prefix = '\n;' in s
    needs_fold = False
    if line_limit is not None:
        if needs_prefix:
            # Physical line = '{_PREFIX}{content}', so content must fit in
            # line_limit - len(_PREFIX) chars.
            needs_fold = any(
                len(line) > line_limit - len(_PREFIX) for line in s.split('\n')
            )
        else:
            needs_fold = any(len(line) > line_limit for line in s.split('\n'))

    if needs_prefix and needs_fold:
        return _make_prefixed_folded_semicolon(s, line_limit)
    if needs_prefix:
        return _make_prefixed_semicolon(s)
    if needs_fold:
        return _make_folded_semicolon(s, line_limit)
    return _make_semicolon(s)


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
        if not has_single:
            return f"'{s}'"
        if not has_double:
            return f'"{s}"'

    if not has_triple_single and not has_ending_single:
        return f"'''{s}'''"
    if not has_triple_double and not has_ending_double:
        return f'"""{s}"""'

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

if __name__ == '__main__':
    strings = ["hello world how you", 
               
               "hello 'world how you", "hello ''world how you", "hello '''world how you",  
               'hello "world how you', 'hello ""world how you', 'hello """world how you', 
               
               "hello 'world \"how you", "hello ''world \"how you", "hello '''world \"how you",
               "hello 'world \"\"how you", "hello ''world \"\"how you", "hello '''world \"\"how you",
               "hello 'world \"\"\"how you", "hello ''world \"\"\"how you", "hello '''world \"\"\"how you",
               
               'hello "world \'how you', 'hello ""world \'how you', 'hello """world \'how you',
               'hello "world \'\'how you', 'hello ""world \'\'how you', 'hello """world \'\'how you',
               'hello "world \'\'\'how you', 'hello ""world \'\'\'how you', 'hello """world \'\'\'how you',

               "hello 'world \'how you", "hello ''world \'how you", "hello '''world \'how you",
               "hello 'world \'\'how you", "hello ''world \'\'how you", "hello '''world \'\'how you",
               "hello 'world \'\'\'how you", "hello ''world \'\'\'how you", "hello '''world \'\'\'how you",

               'hello "world \"how you', 'hello ""world \"how you', 'hello """world \"how you',
               'hello "world \"\"how you', 'hello ""world \"\"how you', 'hello """world \"\"how you',
               'hello "world \"\"\"how you', 'hello ""world \"\"\"how you', 'hello """world \"\"\"how you',

               "hello world how you\'",

               "hello 'world how you\'", "hello ''world how you\'", "hello '''world how you\'",
               'hello "world how you\'', 'hello ""world how you\'', 'hello """world how you\'',

               "hello 'world \"how you\'", "hello ''world \"how you\'", "hello '''world \"how you\'",
               "hello 'world \"\"how you\'", "hello ''world \"\"how you\'", "hello '''world \"\"how you\'",
               "hello 'world \"\"\"how you\'", "hello ''world \"\"\"how you\'", "hello '''world \"\"\"how you\'",

               'hello "world \'how you\'', 'hello ""world \'how you\'', 'hello """world \'how you\'',
               'hello "world \'\'how you\'', 'hello ""world \'\'how you\'', 'hello """world \'\'how you\'',
               'hello "world \'\'\'how you\'', 'hello ""world \'\'\'how you\'', 'hello """world \'\'\'how you\'',

               "hello 'world \'how you\'", "hello ''world \'how you\'", "hello '''world \'how you\'",
               "hello 'world \'\'how you\'", "hello ''world \'\'how you\'", "hello '''world \'\'how you\'",
               "hello 'world \'\'\'how you\'", "hello ''world \'\'\'how you\'", "hello '''world \'\'\'how you\'",

               'hello "world \"how you\'', 'hello ""world \"how you\'', 'hello """world \"how you\'',
               'hello "world \"\"how you\'', 'hello ""world \"\"how you\'', 'hello """world \"\"how you\'',
               'hello "world \"\"\"how you\'', 'hello ""world \"\"\"how you\'', 'hello """world \"\"\"how you\'',

               "hello world how you\"",

               "hello 'world how you\"", "hello ''world how you\"", "hello '''world how you\"",
               'hello "world how you\"', 'hello ""world how you\"', 'hello """world how you\"',

               "hello 'world \"how you\"", "hello ''world \"how you\"", "hello '''world \"how you\"",
               "hello 'world \"\"how you\"", "hello ''world \"\"how you\"", "hello '''world \"\"how you\"",
               "hello 'world \"\"\"how you\"", "hello ''world \"\"\"how you\"", "hello '''world \"\"\"how you\"",

               'hello "world \'how you\"', 'hello ""world \'how you\"', 'hello """world \'how you\"',
               'hello "world \'\'how you\"', 'hello ""world \'\'how you\"', 'hello """world \'\'how you\"',
               'hello "world \'\'\'how you\"', 'hello ""world \'\'\'how you\"', 'hello """world \'\'\'how you\"',

               "hello 'world \'how you\"", "hello ''world \'how you\"", "hello '''world \'how you\"",
               "hello 'world \'\'how you\"", "hello ''world \'\'how you\"", "hello '''world \'\'how you\"",
               "hello 'world \'\'\'how you\"", "hello ''world \'\'\'how you\"", "hello '''world \'\'\'how you\"",

               'hello "world \"how you\"', 'hello ""world \"how you\"', 'hello """world \"how you\"',
               'hello "world \"\"how you\"', 'hello ""world \"\"how you\"', 'hello """world \"\"how you\"',
               'hello "world \"\"\"how you\"', 'hello ""world \"\"\"how you\"', 'hello """world \"\"\"how you\"',

               "hello wor\nld how you",

               "hello 'wor\nld how you", "hello ''wor\nld how you", "hello '''wor\nld how you",
               'hello "wor\nld how you', 'hello ""wor\nld how you', 'hello """wor\nld how you',

               "hello 'wor\nld \"how you", "hello ''wor\nld \"how you", "hello '''wor\nld \"how you",
               "hello 'wor\nld \"\"how you", "hello ''wor\nld \"\"how you", "hello '''wor\nld \"\"how you",
               "hello 'wor\nld \"\"\"how you", "hello ''wor\nld \"\"\"how you", "hello '''wor\nld \"\"\"how you",

               'hello "wor\nld \'how you', 'hello ""wor\nld \'how you', 'hello """wor\nld \'how you',
               'hello "wor\nld \'\'how you', 'hello ""wor\nld \'\'how you', 'hello """wor\nld \'\'how you',
               'hello "wor\nld \'\'\'how you', 'hello ""wor\nld \'\'\'how you', 'hello """wor\nld \'\'\'how you',

               "hello 'wor\nld \'how you", "hello ''wor\nld \'how you", "hello '''wor\nld \'how you",
               "hello 'wor\nld \'\'how you", "hello ''wor\nld \'\'how you", "hello '''wor\nld \'\'how you",
               "hello 'wor\nld \'\'\'how you", "hello ''wor\nld \'\'\'how you", "hello '''wor\nld \'\'\'how you",

               'hello "wor\nld \"how you', 'hello ""wor\nld \"how you', 'hello """wor\nld \"how you',
               'hello "wor\nld \"\"how you', 'hello ""wor\nld \"\"how you', 'hello """wor\nld \"\"how you',
               'hello "wor\nld \"\"\"how you', 'hello ""wor\nld \"\"\"how you', 'hello """wor\nld \"\"\"how you',

               "hello wor\nld how you\'",

               "hello 'wor\nld how you\'", "hello ''wor\nld how you\'", "hello '''wor\nld how you\'",
               'hello "wor\nld how you\'', 'hello ""wor\nld how you\'', 'hello """wor\nld how you\'',

               "hello 'wor\nld \"how you\'", "hello ''wor\nld \"how you\'", "hello '''wor\nld \"how you\'",
               "hello 'wor\nld \"\"how you\'", "hello ''wor\nld \"\"how you\'", "hello '''wor\nld \"\"how you\'",
               "hello 'wor\nld \"\"\"how you\'", "hello ''wor\nld \"\"\"how you\'", "hello '''wor\nld \"\"\"how you\'",

               'hello "wor\nld \'how you\'', 'hello ""wor\nld \'how you\'', 'hello """wor\nld \'how you\'',
               'hello "wor\nld \'\'how you\'', 'hello ""wor\nld \'\'how you\'', 'hello """wor\nld \'\'how you\'',
               'hello "wor\nld \'\'\'how you\'', 'hello ""wor\nld \'\'\'how you\'', 'hello """wor\nld \'\'\'how you\'',

               "hello 'wor\nld \'how you\'", "hello ''wor\nld \'how you\'", "hello '''wor\nld \'how you\'",
               "hello 'wor\nld \'\'how you\'", "hello ''wor\nld \'\'how you\'", "hello '''wor\nld \'\'how you\'",
               "hello 'wor\nld \'\'\'how you\'", "hello ''wor\nld \'\'\'how you\'", "hello '''wor\nld \'\'\'how you\'",

               'hello "wor\nld \"how you\'', 'hello ""wor\nld \"how you\'', 'hello """wor\nld \"how you\'',
               'hello "wor\nld \"\"how you\'', 'hello ""wor\nld \"\"how you\'', 'hello """wor\nld \"\"how you\'',
               'hello "wor\nld \"\"\"how you\'', 'hello ""wor\nld \"\"\"how you\'', 'hello """wor\nld \"\"\"how you\'',

               "hello wor\nld how you\"",

               "hello 'wor\nld how you\"", "hello ''wor\nld how you\"", "hello '''wor\nld how you\"",
               'hello "wor\nld how you\"', 'hello ""wor\nld how you\"', 'hello """wor\nld how you\"',

               "hello 'wor\nld \"how you\"", "hello ''wor\nld \"how you\"", "hello '''wor\nld \"how you\"",
               "hello 'wor\nld \"\"how you\"", "hello ''wor\nld \"\"how you\"", "hello '''wor\nld \"\"how you\"",
               "hello 'wor\nld \"\"\"how you\"", "hello ''wor\nld \"\"\"how you\"", "hello '''wor\nld \"\"\"how you\"",

               'hello "wor\nld \'how you\"', 'hello ""wor\nld \'how you\"', 'hello """wor\nld \'how you\"',
               'hello "wor\nld \'\'how you\"', 'hello ""wor\nld \'\'how you\"', 'hello """wor\nld \'\'how you\"',
               'hello "wor\nld \'\'\'how you\"', 'hello ""wor\nld \'\'\'how you\"', 'hello """wor\nld \'\'\'how you\"',

               "hello 'wor\nld \'how you\"", "hello ''wor\nld \'how you\"", "hello '''wor\nld \'how you\"",
               "hello 'wor\nld \'\'how you\"", "hello ''wor\nld \'\'how you\"", "hello '''wor\nld \'\'how you\"",
               "hello 'wor\nld \'\'\'how you\"", "hello ''wor\nld \'\'\'how you\"", "hello '''wor\nld \'\'\'how you\"",

               'hello "wor\nld \"how you\"', 'hello ""wor\nld \"how you\"', 'hello """wor\nld \"how you\"',
               'hello "wor\nld \"\"how you\"', 'hello ""wor\nld \"\"how you\"', 'hello """wor\nld \"\"how you\"',
               'hello "wor\nld \"\"\"how you\"', 'hello ""wor\nld \"\"\"how you\"', 'hello """wor\nld \"\"\"how you\"',

               "hello world how\n; you",

               "hello 'world how\n; you", "hello ''world how\n; you", "hello '''world how\n; you",
               'hello "world how\n; you', 'hello ""world how\n; you', 'hello """world how\n; you',

               "hello 'world \"how\n; you", "hello ''world \"how\n; you", "hello '''world \"how\n; you",
               "hello 'world \"\"how\n; you", "hello ''world \"\"how\n; you", "hello '''world \"\"how\n; you",
               "hello 'world \"\"\"how\n; you", "hello ''world \"\"\"how\n; you", "hello '''world \"\"\"how\n; you",

               'hello "world \'how\n; you', 'hello ""world \'how\n; you', 'hello """world \'how\n; you',
               'hello "world \'\'how\n; you', 'hello ""world \'\'how\n; you', 'hello """world \'\'how\n; you',
               'hello "world \'\'\'how\n; you', 'hello ""world \'\'\'how\n; you', 'hello """world \'\'\'how\n; you',

               "hello 'world \'how\n; you", "hello ''world \'how\n; you", "hello '''world \'how\n; you",
               "hello 'world \'\'how\n; you", "hello ''world \'\'how\n; you", "hello '''world \'\'how\n; you",
               "hello 'world \'\'\'how\n; you", "hello ''world \'\'\'how\n; you", "hello '''world \'\'\'how\n; you",

               'hello "world \"how\n; you', 'hello ""world \"how\n; you', 'hello """world \"how\n; you',
               'hello "world \"\"how\n; you', 'hello ""world \"\"how\n; you', 'hello """world \"\"how\n; you',
               'hello "world \"\"\"how\n; you', 'hello ""world \"\"\"how\n; you', 'hello """world \"\"\"how\n; you',

               "hello world how\n; you\'",

               "hello 'world how\n; you\'", "hello ''world how\n; you\'", "hello '''world how\n; you\'",
               'hello "world how\n; you\'', 'hello ""world how\n; you\'', 'hello """world how\n; you\'',

               "hello 'world \"how\n; you\'", "hello ''world \"how\n; you\'", "hello '''world \"how\n; you\'",
               "hello 'world \"\"how\n; you\'", "hello ''world \"\"how\n; you\'", "hello '''world \"\"how\n; you\'",
               "hello 'world \"\"\"how\n; you\'", "hello ''world \"\"\"how\n; you\'", "hello '''world \"\"\"how\n; you\'",

               'hello "world \'how\n; you\'', 'hello ""world \'how\n; you\'', 'hello """world \'how\n; you\'',
               'hello "world \'\'how\n; you\'', 'hello ""world \'\'how\n; you\'', 'hello """world \'\'how\n; you\'',
               'hello "world \'\'\'how\n; you\'', 'hello ""world \'\'\'how\n; you\'', 'hello """world \'\'\'how\n; you\'',

               "hello 'world \'how\n; you\'", "hello ''world \'how\n; you\'", "hello '''world \'how\n; you\'",
               "hello 'world \'\'how\n; you\'", "hello ''world \'\'how\n; you\'", "hello '''world \'\'how\n; you\'",
               "hello 'world \'\'\'how\n; you\'", "hello ''world \'\'\'how\n; you\'", "hello '''world \'\'\'how\n; you\'",

               'hello "world \"how\n; you\'', 'hello ""world \"how\n; you\'', 'hello """world \"how\n; you\'',
               'hello "world \"\"how\n; you\'', 'hello ""world \"\"how\n; you\'', 'hello """world \"\"how\n; you\'',
               'hello "world \"\"\"how\n; you\'', 'hello ""world \"\"\"how\n; you\'', 'hello """world \"\"\"how\n; you\'',

               "hello world how\n; you\"",

               "hello 'world how\n; you\"", "hello ''world how\n; you\"", "hello '''world how\n; you\"",
               'hello "world how\n; you\"', 'hello ""world how\n; you\"', 'hello """world how\n; you\"',

               "hello 'world \"how\n; you\"", "hello ''world \"how\n; you\"", "hello '''world \"how\n; you\"",
               "hello 'world \"\"how\n; you\"", "hello ''world \"\"how\n; you\"", "hello '''world \"\"how\n; you\"",
               "hello 'world \"\"\"how\n; you\"", "hello ''world \"\"\"how\n; you\"", "hello '''world \"\"\"how\n; you\"",

               'hello "world \'how\n; you\"', 'hello ""world \'how\n; you\"', 'hello """world \'how\n; you\"',
               'hello "world \'\'how\n; you\"', 'hello ""world \'\'how\n; you\"', 'hello """world \'\'how\n; you\"',
               'hello "world \'\'\'how\n; you\"', 'hello ""world \'\'\'how\n; you\"', 'hello """world \'\'\'how\n; you\"',

               "hello 'world \'how\n; you\"", "hello ''world \'how\n; you\"", "hello '''world \'how\n; you\"",
               "hello 'world \'\'how\n; you\"", "hello ''world \'\'how\n; you\"", "hello '''world \'\'how\n; you\"",
               "hello 'world \'\'\'how\n; you\"", "hello ''world \'\'\'how\n; you\"", "hello '''world \'\'\'how\n; you\"",

               'hello "world \"how\n; you\"', 'hello ""world \"how\n; you\"', 'hello """world \"how\n; you\"',
               'hello "world \"\"how\n; you\"', 'hello ""world \"\"how\n; you\"', 'hello """world \"\"how\n; you\"',
               'hello "world \"\"\"how\n; you\"', 'hello ""world \"\"\"how\n; you\"', 'hello """world \"\"\"how\n; you\"',

               "hello wor\nld how\n; you",

               "hello 'wor\nld how\n; you", "hello ''wor\nld how\n; you", "hello '''wor\nld how\n; you",
               'hello "wor\nld how\n; you', 'hello ""wor\nld how\n; you', 'hello """wor\nld how\n; you',

               "hello 'wor\nld \"how\n; you", "hello ''wor\nld \"how\n; you", "hello '''wor\nld \"how\n; you",
               "hello 'wor\nld \"\"how\n; you", "hello ''wor\nld \"\"how\n; you", "hello '''wor\nld \"\"how\n; you",
               "hello 'wor\nld \"\"\"how\n; you", "hello ''wor\nld \"\"\"how\n; you", "hello '''wor\nld \"\"\"how\n; you",

               'hello "wor\nld \'how\n; you', 'hello ""wor\nld \'how\n; you', 'hello """wor\nld \'how\n; you',
               'hello "wor\nld \'\'how\n; you', 'hello ""wor\nld \'\'how\n; you', 'hello """wor\nld \'\'how\n; you',
               'hello "wor\nld \'\'\'how\n; you', 'hello ""wor\nld \'\'\'how\n; you', 'hello """wor\nld \'\'\'how\n; you',

               "hello 'wor\nld \'how\n; you", "hello ''wor\nld \'how\n; you", "hello '''wor\nld \'how\n; you",
               "hello 'wor\nld \'\'how\n; you", "hello ''wor\nld \'\'how\n; you", "hello '''wor\nld \'\'how\n; you",
               "hello 'wor\nld \'\'\'how\n; you", "hello ''wor\nld \'\'\'how\n; you", "hello '''wor\nld \'\'\'how\n; you",

               'hello "wor\nld \"how\n; you', 'hello ""wor\nld \"how\n; you', 'hello """wor\nld \"how\n; you',
               'hello "wor\nld \"\"how\n; you', 'hello ""wor\nld \"\"how\n; you', 'hello """wor\nld \"\"how\n; you',
               'hello "wor\nld \"\"\"how\n; you', 'hello ""wor\nld \"\"\"how\n; you', 'hello """wor\nld \"\"\"how\n; you',

               "hello wor\nld how\n; you\'",

               "hello 'wor\nld how\n; you\'", "hello ''wor\nld how\n; you\'", "hello '''wor\nld how\n; you\'",
               'hello "wor\nld how\n; you\'', 'hello ""wor\nld how\n; you\'', 'hello """wor\nld how\n; you\'',

               "hello 'wor\nld \"how\n; you\'", "hello ''wor\nld \"how\n; you\'", "hello '''wor\nld \"how\n; you\'",
               "hello 'wor\nld \"\"how\n; you\'", "hello ''wor\nld \"\"how\n; you\'", "hello '''wor\nld \"\"how\n; you\'",
               "hello 'wor\nld \"\"\"how\n; you\'", "hello ''wor\nld \"\"\"how\n; you\'", "hello '''wor\nld \"\"\"how\n; you\'",

               'hello "wor\nld \'how\n; you\'', 'hello ""wor\nld \'how\n; you\'', 'hello """wor\nld \'how\n; you\'',
               'hello "wor\nld \'\'how\n; you\'', 'hello ""wor\nld \'\'how\n; you\'', 'hello """wor\nld \'\'how\n; you\'',
               'hello "wor\nld \'\'\'how\n; you\'', 'hello ""wor\nld \'\'\'how\n; you\'', 'hello """wor\nld \'\'\'how\n; you\'',

               "hello 'wor\nld \'how\n; you\'", "hello ''wor\nld \'how\n; you\'", "hello '''wor\nld \'how\n; you\'",
               "hello 'wor\nld \'\'how\n; you\'", "hello ''wor\nld \'\'how\n; you\'", "hello '''wor\nld \'\'how\n; you\'",
               "hello 'wor\nld \'\'\'how\n; you\'", "hello ''wor\nld \'\'\'how\n; you\'", "hello '''wor\nld \'\'\'how\n; you\'",

               'hello "wor\nld \"how\n; you\'', 'hello ""wor\nld \"how\n; you\'', 'hello """wor\nld \"how\n; you\'',
               'hello "wor\nld \"\"how\n; you\'', 'hello ""wor\nld \"\"how\n; you\'', 'hello """wor\nld \"\"how\n; you\'',
               'hello "wor\nld \"\"\"how\n; you\'', 'hello ""wor\nld \"\"\"how\n; you\'', 'hello """wor\nld \"\"\"how\n; you\'',

               "hello wor\nld how\n; you\"",

               "hello 'wor\nld how\n; you\"", "hello ''wor\nld how\n; you\"", "hello '''wor\nld how\n; you\"",
               'hello "wor\nld how\n; you\"', 'hello ""wor\nld how\n; you\"', 'hello """wor\nld how\n; you\"',

               "hello 'wor\nld \"how\n; you\"", "hello ''wor\nld \"how\n; you\"", "hello '''wor\nld \"how\n; you\"",
               "hello 'wor\nld \"\"how\n; you\"", "hello ''wor\nld \"\"how\n; you\"", "hello '''wor\nld \"\"how\n; you\"",
               "hello 'wor\nld \"\"\"how\n; you\"", "hello ''wor\nld \"\"\"how\n; you\"", "hello '''wor\nld \"\"\"how\n; you\"",

               'hello "wor\nld \'how\n; you\"', 'hello ""wor\nld \'how\n; you\"', 'hello """wor\nld \'how\n; you\"',
               'hello "wor\nld \'\'how\n; you\"', 'hello ""wor\nld \'\'how\n; you\"', 'hello """wor\nld \'\'how\n; you\"',
               'hello "wor\nld \'\'\'how\n; you\"', 'hello ""wor\nld \'\'\'how\n; you\"', 'hello """wor\nld \'\'\'how\n; you\"',

               "hello 'wor\nld \'how\n; you\"", "hello ''wor\nld \'how\n; you\"", "hello '''wor\nld \'how\n; you\"",
               "hello 'wor\nld \'\'how\n; you\"", "hello ''wor\nld \'\'how\n; you\"", "hello '''wor\nld \'\'how\n; you\"",
               "hello 'wor\nld \'\'\'how\n; you\"", "hello ''wor\nld \'\'\'how\n; you\"", "hello '''wor\nld \'\'\'how\n; you\"",

               'hello "wor\nld \"how\n; you\"', 'hello ""wor\nld \"how\n; you\"', 'hello """wor\nld \"how\n; you\"',
               'hello "wor\nld \"\"how\n; you\"', 'hello ""wor\nld \"\"how\n; you\"', 'hello """wor\nld \"\"how\n; you\"',
               'hello "wor\nld \"\"\"how\n; you\"', 'hello ""wor\nld \"\"\"how\n; you\"', 'hello """wor\nld \"\"\"how\n; you\"',

               ]

    from pycifparse.lexer._tokenize_re import tokenize
    version = CifVersion.CIF_2_0
    #s = "a \n;string"

    print("round tripping failures:")
    for i,s in enumerate(strings):
        quoted = _quote_string(s, version)
        tokens = tokenize(quoted, version)

        if s != tokens[0].value:
            print(f"{i}: {s!r} -> {quoted!r} -> {tokens[0].value!r} ({tokens=})")

    

    
    
    
    
    
    
    
    
    
    
    
    