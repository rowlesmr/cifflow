"""
Regex-based CIF tokenizer — fast path replacing the generator-based Lexer.

Two-pass strategy:
  1. Pre-scan for triple-quoted strings and semicolon text fields, building a
     list of multiline spans.  Triple-quoted strings are skipped so their
     content is never misidentified as a multiline opener.
  2. Run re.finditer over the non-multiline segments for all other tokens.

Returns a flat list[Token].  The generator Lexer (lexer.py) is unchanged and
remains the reference implementation used by the lexer test suite.
"""

import re
from bisect import bisect_right
from typing import List, Optional, Tuple

from pycifparse.types import CifVersion, TokenType, ValueType
from pycifparse.lexer.tokens import LexerError, Token


# ── Line-position index ────────────────────────────────────────────────────────

def _build_line_starts(src: str) -> List[int]:
    """Return the byte offset at which each line begins (0-indexed)."""
    starts = [0]
    pos = 0
    while True:
        idx = src.find('\n', pos)
        if idx == -1:
            break
        starts.append(idx + 1)
        pos = idx + 1
    return starts


def _line_col(offset: int, line_starts: List[int], line_offset: int) -> Tuple[int, int]:
    """Return 1-based (line, col) for *offset*, adjusted by *line_offset*."""
    idx = bisect_right(line_starts, offset) - 1
    return idx + 1 + line_offset, offset - line_starts[idx] + 1


# ── Keyword classification ─────────────────────────────────────────────────────

_EXACT_KW = frozenset({'loop_', 'stop_', 'global_'})
_PREFIX_KW = ('data_', 'save_')


def _classify_bare(word: str) -> Tuple[TokenType, Optional[ValueType]]:
    if word.startswith('_'):
        return TokenType.TAG, None
    lower = word.lower()
    if lower in _EXACT_KW:
        return TokenType.KEYWORD, None
    for p in _PREFIX_KW:
        if lower.startswith(p):
            return TokenType.KEYWORD, None
    if word in ('.', '?'):
        return TokenType.VALUE, ValueType.PLACEHOLDER
    return TokenType.VALUE, ValueType.STRING


# ── Multiline pre-scan ─────────────────────────────────────────────────────────

# Pre-scan: finds triple-quote openers (to skip over), '\n;' multiline openers,
# and '^;' multiline openers at offset 0.  Triple-quoted regions are tracked
# with skip_until so their content is never mistaken for a multiline delimiter.
_PRESCAN_RE = re.compile(
    r"(?P<TQ>  ''' | \"\"\"  )  |  (?P<NL_ML>  \n;  )  |  (?P<SOF_ML>  ^;  )",
    re.VERBOSE | re.MULTILINE,
)


def _find_multiline_spans(src: str) -> List[Tuple[int, int, bool]]:
    """
    Return list of (ml_start, ml_end, terminated) for each multiline text field.
    ml_start  — offset of the opening ';'
    ml_end    — offset one past the closing ';' (or len(src) if unterminated)
    terminated — True if a closing '\\n;' was found

    Triple-quoted strings are skipped so that ';' at column 1 inside them is
    not misidentified as a multiline text-field delimiter.
    """
    result: List[Tuple[int, int, bool]] = []
    skip_until = 0
    n = len(src)

    for m in _PRESCAN_RE.finditer(src):
        if m.start() < skip_until:
            continue

        kind = m.lastgroup

        if kind == 'TQ':
            delim = m.group(0)
            close = src.find(delim, m.end())
            skip_until = (close + 3) if close != -1 else n

        else:   # NL_ML or SOF_ML
            ml_start = m.start() if kind == 'SOF_ML' else m.start() + 1
            close = src.find('\n;', m.end())
            if close != -1:
                ml_end = close + 2
                result.append((ml_start, ml_end, True))
            else:
                result.append((ml_start, n, False))
            skip_until = result[-1][1]

    return result


# ── Main token patterns ────────────────────────────────────────────────────────

# CIF 2.0: triple-quoted supported; closing quote needs no trailing whitespace.
# ':' is structural only when immediately following a closing quote/bracket,
# replicating the lexer's _last_was_ws=False check.  Inside bare words ':' is
# a plain character (e.g. '16:00' is one token).
_CIF2_RE = re.compile(r"""
    (?P<TDQ>     "{3}  [\s\S]*?  "{3}  )  |     # triple double-quoted (terminated)
    (?P<TDQ_UNT> "{3}  [\s\S]*          )  |     # triple double-quoted (unterminated → EOF)
    (?P<TSQ>     '{3}  [\s\S]*?  '{3}  )  |     # triple single-quoted (terminated)
    (?P<TSQ_UNT> '{3}  [\s\S]*          )  |     # triple single-quoted (unterminated → EOF)
    (?P<DQ>      "  [^"\n]*  "          )  |     # double-quoted
    (?P<DQ_UNT>  "  [^"\n]*             )  |     # unterminated double-quoted
    (?P<SQ>      '  [^'\n]*  '          )  |     # single-quoted
    (?P<SQ_UNT>  '  [^'\n]*             )  |     # unterminated single-quoted
    (?P<CMT>     \#  [^\n]*             )  |     # comment — discard
    (?P<TAG>     _\S+                   )  |     # tag
    (?P<DEL>     [\[\]{}]               )  |     # structural delimiters
    (?P<COL>     (?<=[\"'\]\}]):        )  |     # colon after closing quote/bracket
    (?P<BW>      [^\s"'#\[\]{}][^\s"'#\[\]{}]*  )   # bare word (may contain ':')
""", re.VERBOSE | re.DOTALL)

# CIF 1.x: no triple-quoted (error if seen); closing quote must be followed by
#          whitespace/EOF; '[', ']', '{', '}', ':' are bare word characters.
_CIF1_RE = re.compile(r"""
    (?P<TDQ>     "{3}  [\s\S]*?  "{3}  )  |     # triple double-quoted (error)
    (?P<TDQ_UNT> "{3}  [\s\S]*          )  |     # triple double-quoted (unterminated)
    (?P<TSQ>     '{3}  [\s\S]*?  '{3}  )  |     # triple single-quoted (error)
    (?P<TSQ_UNT> '{3}  [\s\S]*          )  |     # triple single-quoted (unterminated)
    (?P<DQ>      "  (?:[^"\n]  |  "(?![ \t\n]|$))*  "(?=[ \t\n]|$)  )  |   # double-quoted
    (?P<DQ_UNT>  "  [^"\n]*             )  |     # unterminated double-quoted
    (?P<SQ>      '  (?:[^'\n]  |  '(?![ \t\n]|$))*  '(?=[ \t\n]|$)  )  |   # single-quoted
    (?P<SQ_UNT>  '  [^'\n]*             )  |     # unterminated single-quoted
    (?P<CMT>     \#  [^\n]*             )  |     # comment — discard
    (?P<TAG>     _\S+                   )  |     # tag
    (?P<BW>      [^\s"'#][^\s"'#]*      )        # bare word
""", re.VERBOSE | re.DOTALL)


# ── Token construction ─────────────────────────────────────────────────────────

def _make_lex_err(msg: str, line: int, col: int, ctx: str) -> LexerError:
    return LexerError(message=msg, line=line, column=col, context=ctx)


def _match_to_token(
    m: 're.Match[str]',
    src: str,
    line_starts: List[int],
    line_offset: int,
    is_cif2: bool,
) -> Optional[Token]:
    """Convert a regex match to a Token, or None for discarded tokens (comments)."""
    kind = m.lastgroup
    raw  = m.group(0)
    ln, col = _line_col(m.start(), line_starts, line_offset)

    if kind == 'CMT':
        return None

    if kind in ('DQ', 'SQ'):
        content = raw[1:-1]
        vtype   = ValueType.DOUBLE_QUOTED if kind == 'DQ' else ValueType.SINGLE_QUOTED
        errors: List[LexerError] = []
        if not is_cif2:
            base = m.start() + 1
            for i, ch in enumerate(content):
                code = ord(ch)
                if not (code == 9 or code == 10 or code == 13 or 32 <= code <= 126):
                    cl, cc = _line_col(base + i, line_starts, line_offset)
                    errors.append(_make_lex_err(
                        f'character U+{code:04X} is not permitted in CIF 1.x',
                        cl, cc, ch))
        return Token(TokenType.VALUE, content, vtype, ln, col, errors)

    if kind in ('TDQ', 'TSQ'):
        content = raw[3:-3]
        if is_cif2:
            vtype = (ValueType.TRIPLE_DOUBLE_QUOTED if kind == 'TDQ'
                     else ValueType.TRIPLE_SINGLE_QUOTED)
            return Token(TokenType.VALUE, content, vtype, ln, col)
        else:
            triple = '"""' if kind == 'TDQ' else "'''"
            err = _make_lex_err(
                'triple-quoted strings are not valid in CIF 1.x', ln, col, triple)
            return Token(TokenType.VALUE, content, ValueType.STRING, ln, col, [err])

    if kind in ('TDQ_UNT', 'TSQ_UNT'):
        content   = raw[3:]
        triple    = '"""' if kind == 'TDQ_UNT' else "'''"
        vtype_str = 'triple_double_quoted' if kind == 'TDQ_UNT' else 'triple_single_quoted'
        errors: List[LexerError] = []
        if not is_cif2:
            errors.append(_make_lex_err(
                'triple-quoted strings are not valid in CIF 1.x', ln, col, triple))
        errors.append(_make_lex_err(
            f'unterminated {vtype_str} string', ln, col, triple + raw[3:40]))
        if is_cif2:
            vtype = (ValueType.TRIPLE_DOUBLE_QUOTED if kind == 'TDQ_UNT'
                     else ValueType.TRIPLE_SINGLE_QUOTED)
        else:
            vtype = ValueType.STRING
        return Token(TokenType.VALUE, content, vtype, ln, col, errors)

    if kind in ('DQ_UNT', 'SQ_UNT'):
        content   = raw[1:]
        vtype     = ValueType.DOUBLE_QUOTED if kind == 'DQ_UNT' else ValueType.SINGLE_QUOTED
        delim     = '"' if kind == 'DQ_UNT' else "'"
        vtype_str = 'double_quoted' if kind == 'DQ_UNT' else 'single_quoted'
        err = _make_lex_err(
            f'unterminated {vtype_str} string', ln, col,
            f'{delim}{content[:40]}')
        return Token(TokenType.VALUE, content, vtype, ln, col, [err])

    if kind in ('DEL', 'COL'):
        return Token(TokenType.VALUE, raw, ValueType.STRING, ln, col)

    # TAG or BW
    tok_type, vtype = _classify_bare(raw)
    errors: List[LexerError] = []
    if not is_cif2 and raw[0] in ('[', '$'):
        errors.append(_make_lex_err(
            f'bare word beginning with {raw[0]!r} is not permitted in CIF 1.x',
            ln, col, raw[0]))
    return Token(tok_type, raw, vtype, ln, col, errors)


# ── Public entry point ─────────────────────────────────────────────────────────

def tokenize(source: str, version: CifVersion, line_offset: int = 0) -> List[Token]:
    """
    Tokenise *source* and return a flat list of Token objects.

    Equivalent to ``list(Lexer(source, version, line_offset).tokens())`` but
    uses two C-level regex passes instead of a Python generator, giving roughly
    a 20-30× speedup on large files.
    """
    src     = source.replace('\r\n', '\n').replace('\r', '\n')
    is_cif2 = version == CifVersion.CIF_2_0
    ls      = _build_line_starts(src)

    # ── Phase 1: locate multiline text field boundaries ───────────────────────

    ml_spans = _find_multiline_spans(src)

    # Build Token objects for each multiline field.
    ml_list: List[Tuple[int, int, Token]] = []
    for ml_start, ml_end, terminated in ml_spans:
        # Content is between the opening ';' and the closing '\n;'.
        if terminated:
            content = src[ml_start + 1 : ml_end - 2]   # strip opening ';' and '\n;'
        else:
            content = src[ml_start + 1 : ml_end]        # strip only opening ';'

        ln, col = _line_col(ml_start, ls, line_offset)
        errors: List[LexerError] = []

        if not terminated:
            errors.append(_make_lex_err(
                'unterminated multiline string', ln, col, ';'))

        if not is_cif2:
            base = ml_start + 1
            for i, ch in enumerate(content):
                code = ord(ch)
                if not (code == 9 or code == 10 or code == 13 or 32 <= code <= 126):
                    cl, cc = _line_col(base + i, ls, line_offset)
                    errors.append(_make_lex_err(
                        f'character U+{code:04X} is not permitted in CIF 1.x',
                        cl, cc, ch))

        ml_list.append((ml_start, ml_end, Token(
            TokenType.VALUE, content, ValueType.MULTILINE_STRING, ln, col, errors,
        )))

    # ── Phase 2: main regex in the gaps between multiline spans ───────────────

    pat = _CIF2_RE if is_cif2 else _CIF1_RE

    # segments[i] is the source range to regex-scan before multiline ml_list[i].
    seg_start = 0
    segments: List[Tuple[int, int]] = []
    for ml_start, ml_end, _ in ml_list:
        segments.append((seg_start, ml_start))
        seg_start = ml_end
    segments.append((seg_start, len(src)))

    tokens: List[Token] = []

    for i, (seg_s, seg_e) in enumerate(segments):
        if seg_s < seg_e:
            for m in pat.finditer(src, seg_s, seg_e):
                tok = _match_to_token(m, src, ls, line_offset, is_cif2)
                if tok is not None:
                    tokens.append(tok)
        if i < len(ml_list):
            tokens.append(ml_list[i][2])

    return tokens
