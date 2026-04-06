"""
CIF Lexer — hand-written state machine.

Tokenises a CIF source string and yields Token objects.
Each Token may carry LexerError objects for problems found during tokenisation;
these are never emitted as parser events directly — the parser converts them.

Version-specific behaviour is gated on the CifVersion passed at construction.
Line endings are normalised to \\n before processing; the line_offset parameter
allows correct absolute line numbers when the magic line has been consumed upstream.

The text-field delimiter is `line-term + ;` per the CIF 2.0 EBNF:
    text-delim = line-term, ';'
Content starts immediately after the opening `;` and excludes the `\\n` that
precedes the closing `;` at column 1.
"""

import re
from typing import Iterator, List, Optional

from pycifparse.types import CifVersion, TokenType, ValueType
from pycifparse.lexer.tokens import LexerError, Token


# Keywords whose full bare-word token is the keyword (prefix keywords).
_PREFIX_KEYWORDS = ('data_', 'save_')

# Keywords that match exactly (case-insensitive).
_EXACT_KEYWORDS = frozenset({'loop_', 'stop_', 'global_'})

# CIF 2.0 structural delimiter characters (emitted as standalone VALUE tokens).
_CIF2_DELIMITERS = frozenset({'[', ']', '{', '}'})

# Pattern for detecting malformed SU: word starts numeric, has '(', bad content.
_NUMERIC_PREFIX_RE = re.compile(
    r'^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\('
)
_VALID_SU_RE = re.compile(
    r'^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\(\d+\)$'
)


def _classify_bare_word(word: str) -> tuple[TokenType, ValueType | None]:
    """Classify a bare word into (TokenType, ValueType)."""
    if word.startswith('_'):
        return TokenType.TAG, None
    lower = word.lower()
    if lower in _EXACT_KEYWORDS:
        return TokenType.KEYWORD, None
    for prefix in _PREFIX_KEYWORDS:
        if lower.startswith(prefix):
            return TokenType.KEYWORD, None
    if word in ('.', '?'):
        return TokenType.VALUE, ValueType.PLACEHOLDER
    return TokenType.VALUE, ValueType.STRING


def _check_cif1_char(ch: str, line: int, col: int) -> Optional[LexerError]:
    """Return a LexerError if *ch* is not in the CIF 1.1 permitted character set.

    CIF 1.1 §22: permitted characters are HT (9), LF (10), CR (13), and
    printable ASCII positions 32–126.  VT (11) and FF (12) are explicitly
    excluded.  All non-ASCII code points (> 126) are also excluded.
    """
    code = ord(ch)
    if code == 9 or code == 10 or code == 13:
        return None       # HT, LF, CR — always ok
    if 32 <= code <= 126:
        return None       # printable ASCII — ok
    return LexerError(
        message=f'character U+{code:04X} is not permitted in CIF 1.x',
        line=line, column=col, context=ch,
    )


def _check_su(word: str, line: int, col: int) -> List[LexerError]:
    """Return a LexerError if *word* looks like numeric+SU but has a bad SU."""
    if '(' not in word:
        return []
    if not _NUMERIC_PREFIX_RE.match(word):
        return []
    if not _VALID_SU_RE.match(word):
        return [LexerError(
            message=f'invalid SU value: {word!r}',
            line=line, column=col, context=word,
        )]
    return []


class Lexer:
    """
    Streaming CIF lexer.

    Usage::

        lexer = Lexer(source, version, line_offset)
        for token in lexer.tokens():
            ...
    """

    def __init__(self, source: str, version: CifVersion, line_offset: int = 0):
        # Normalise all line-ending styles to \\n, as required by CIF 2.0 §line-term.
        self._src = source.replace('\r\n', '\n').replace('\r', '\n')
        self._version = version
        self._is_cif2 = (version == CifVersion.CIF_2_0)
        self._pos = 0
        self._line = line_offset + 1
        self._col = 1
        # True when the last thing consumed was whitespace/comment (not a token).
        # Used to decide whether ':' is a table separator (adjacent to a token)
        # or the start of a bare-word value (preceded by whitespace).
        self._last_was_ws: bool = True

    # ------------------------------------------------------------------
    # Low-level cursor helpers
    # ------------------------------------------------------------------

    def _peek(self, offset: int = 0) -> str:
        pos = self._pos + offset
        return self._src[pos] if pos < len(self._src) else ''

    def _advance(self) -> str:
        ch = self._src[self._pos]
        self._pos += 1
        if ch == '\n':
            self._line += 1
            self._col = 1
        else:
            self._col += 1
        return ch

    def _at_end(self) -> bool:
        return self._pos >= len(self._src)

    def _skip_to_eol(self) -> None:
        """Advance past all characters up to but not including the next \\n (or EOF)."""
        while not self._at_end() and self._peek() != '\n':
            self._advance()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def tokens(self) -> Iterator[Token]:
        """Yield Token objects for the entire source."""
        while not self._at_end():
            ch = self._peek()

            # ── Inline whitespace ───────────────────────────────────────
            if ch in ' \t':
                self._advance()
                self._last_was_ws = True
                continue

            # ── Line terminator ─────────────────────────────────────────
            if ch == '\n':
                self._advance()
                self._last_was_ws = True
                continue

            # ── Comment  (# to EOL) ─────────────────────────────────────
            if ch == '#':
                self._skip_to_eol()
                self._last_was_ws = True
                continue

            # ── Multiline text field  (; at column 1) ───────────────────
            if ch == ';' and self._col == 1:
                yield from self._read_multiline()
                self._last_was_ws = False
                continue

            # ── Triple-quoted strings  (CIF 2.0 only) ───────────────────
            if ch == "'" and self._is_cif2 and (
                    self._peek(1) == "'" and self._peek(2) == "'"):
                yield from self._read_triple("'")
                self._last_was_ws = False
                continue

            if ch == '"' and self._is_cif2 and (
                    self._peek(1) == '"' and self._peek(2) == '"'):
                yield from self._read_triple('"')
                self._last_was_ws = False
                continue

            # ── Single/double quoted strings ────────────────────────────
            if ch == "'":
                if not self._is_cif2 and (
                        self._peek(1) == "'" and self._peek(2) == "'"):
                    yield from self._read_triple_cif1("'")
                else:
                    yield from self._read_quoted("'")
                self._last_was_ws = False
                continue

            if ch == '"':
                if not self._is_cif2 and (
                        self._peek(1) == '"' and self._peek(2) == '"'):
                    yield from self._read_triple_cif1('"')
                else:
                    yield from self._read_quoted('"')
                self._last_was_ws = False
                continue

            # ── CIF 2.0 structural delimiters ───────────────────────────
            if self._is_cif2 and ch in _CIF2_DELIMITERS:
                line, col = self._line, self._col
                self._advance()
                yield Token(TokenType.VALUE, ch, ValueType.STRING, line, col)
                self._last_was_ws = False
                continue

            # ── CIF 2.0 table key/value separator ───────────────────────
            # Only emit ':' as a standalone token when directly adjacent to the
            # preceding token (no whitespace between them).  If whitespace
            # preceded ':', it starts a bare-word value (e.g. ':100.0').
            if self._is_cif2 and ch == ':' and not self._last_was_ws:
                line, col = self._line, self._col
                self._advance()
                yield Token(TokenType.VALUE, ':', ValueType.STRING, line, col)
                self._last_was_ws = False
                continue

            # ── Bare word ───────────────────────────────────────────────
            yield from self._read_bare_word()
            self._last_was_ws = False

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _read_bare_word(self) -> Iterator[Token]:
        line, col = self._line, self._col
        buf: List[str] = []

        while not self._at_end():
            ch = self._peek()
            # Whitespace terminates
            if ch in ' \t\n':
                break
            # Comment terminates
            if ch == '#':
                break
            # Quote characters terminate (start their own token)
            if ch in ("'", '"'):
                break
            # CIF 2.0 structural delimiters terminate
            if self._is_cif2 and ch in _CIF2_DELIMITERS:
                break
            buf.append(self._advance())

        word = ''.join(buf)
        if not word:
            # Consume one unrecognised character to avoid infinite loop
            bad_ch = self._advance()
            yield Token(
                TokenType.VALUE, bad_ch, ValueType.STRING,
                line, col,
                [LexerError(
                    f'unexpected character: {bad_ch!r}', line, col, bad_ch,
                )],
            )
            return

        errors = _check_su(word, line, col)
        token_type, value_type = _classify_bare_word(word)
        yield Token(token_type, word, value_type, line, col, errors)

    def _read_quoted(self, delimiter: str) -> Iterator[Token]:
        """Single- or double-quoted string."""
        line, col = self._line, self._col
        vtype = ValueType.SINGLE_QUOTED if delimiter == "'" else ValueType.DOUBLE_QUOTED
        errors: List[LexerError] = []

        self._advance()  # consume opening delimiter
        buf: List[str] = []

        while not self._at_end():
            ch = self._peek()

            if ch == '\n':
                # EOL terminates a single/double quoted string — do not consume it
                errors.append(LexerError(
                    message=f'unterminated {vtype.value} string',
                    line=line, column=col,
                    context=f'{delimiter}{"".join(buf[:40])}',
                ))
                yield Token(TokenType.VALUE, ''.join(buf), vtype, line, col, errors)
                return

            if ch == delimiter:
                if not self._is_cif2:
                    # CIF 1.1: closing delimiter must be followed by whitespace or EOL/EOF
                    following = self._peek(1)
                    if following not in (' ', '\t', '\n', ''):
                        char_line, char_col = self._line, self._col
                        buf.append(self._advance())
                        err = _check_cif1_char(ch, char_line, char_col)
                        if err:
                            errors.append(err)
                        continue
                self._advance()  # consume closing delimiter
                yield Token(TokenType.VALUE, ''.join(buf), vtype, line, col, errors)
                return

            if not self._is_cif2:
                err = _check_cif1_char(ch, self._line, self._col)
                if err:
                    errors.append(err)
            buf.append(self._advance())

        # EOF inside quoted string
        errors.append(LexerError(
            message=f'unterminated {vtype.value} string',
            line=line, column=col,
            context=f'{delimiter}{"".join(buf[:40])}',
        ))
        yield Token(TokenType.VALUE, ''.join(buf), vtype, line, col, errors)

    def _read_triple(self, delimiter: str) -> Iterator[Token]:
        """Triple-quoted string (CIF 2.0 only)."""
        line, col = self._line, self._col
        vtype = (ValueType.TRIPLE_SINGLE_QUOTED if delimiter == "'"
                 else ValueType.TRIPLE_DOUBLE_QUOTED)
        errors: List[LexerError] = []

        # Consume opening triple delimiter
        for _ in range(3):
            self._advance()

        buf: List[str] = []

        while not self._at_end():
            if (self._peek() == delimiter
                    and self._peek(1) == delimiter
                    and self._peek(2) == delimiter):
                # Consume closing triple delimiter
                for _ in range(3):
                    self._advance()
                yield Token(TokenType.VALUE, ''.join(buf), vtype, line, col, errors)
                return
            buf.append(self._advance())

        # EOF inside triple-quoted string
        errors.append(LexerError(
            message=f'unterminated {vtype.value} string',
            line=line, column=col,
            context=delimiter * 3 + ''.join(buf[:40]),
        ))
        yield Token(TokenType.VALUE, ''.join(buf), vtype, line, col, errors)

    def _read_triple_cif1(self, delimiter: str) -> Iterator[Token]:
        """
        Triple-quoted string encountered in CIF 1.x mode.
        Emit a lexer error and treat the content as STRING.
        """
        line, col = self._line, self._col
        triple = delimiter * 3

        errors = [LexerError(
            message=f'triple-quoted strings are not valid in CIF 1.x',
            line=line, column=col, context=triple,
        )]

        # Consume opening triple
        for _ in range(3):
            self._advance()

        buf: List[str] = []

        while not self._at_end():
            if (self._peek() == delimiter
                    and self._peek(1) == delimiter
                    and self._peek(2) == delimiter):
                for _ in range(3):
                    self._advance()
                yield Token(TokenType.VALUE, ''.join(buf), ValueType.STRING,
                            line, col, errors)
                return
            buf.append(self._advance())

        errors.append(LexerError(
            message=f'unterminated triple-quoted string',
            line=line, column=col,
            context=triple + ''.join(buf[:40]),
        ))
        yield Token(TokenType.VALUE, ''.join(buf), ValueType.STRING,
                    line, col, errors)

    def _read_multiline(self) -> Iterator[Token]:
        """
        Semicolon-delimited multiline text field.

        Per CIF 2.0 EBNF:  text-delim = line-term, ';'
        The opening delimiter is the '\\n' before this ';' (already consumed as
        whitespace) plus this ';' itself.  Content starts at the character
        immediately following the opening ';' and ends just before the '\\n'
        that precedes the closing ';' at column 1.
        """
        assert self._col == 1 and self._peek() == ';'
        line, col = self._line, self._col
        errors: List[LexerError] = []

        self._advance()  # consume opening ';'

        buf: List[str] = []

        while not self._at_end():
            ch = self._peek()

            if ch == '\n':
                # Lookahead: is the next character ';' (= closing text-delim)?
                if self._peek(1) == ';':
                    # This '\n' is the line-term of the closing text-delim.
                    # Do NOT include it in the content.
                    # text-delim = line-term, ';'  (exactly two characters).
                    # After consuming them, return to NORMAL immediately;
                    # anything following the ';' on that line is tokenised normally.
                    self._advance()          # consume '\n'
                    self._advance()          # consume closing ';'
                    yield Token(
                        TokenType.VALUE, ''.join(buf),
                        ValueType.MULTILINE_STRING, line, col, errors,
                    )
                    return
                else:
                    buf.append(self._advance())
            else:
                if not self._is_cif2:
                    err = _check_cif1_char(ch, self._line, self._col)
                    if err:
                        errors.append(err)
                buf.append(self._advance())

        # EOF inside multiline string
        errors.append(LexerError(
            message='unterminated multiline string',
            line=line, column=col, context=';',
        ))
        yield Token(
            TokenType.VALUE, ''.join(buf),
            ValueType.MULTILINE_STRING, line, col, errors,
        )
