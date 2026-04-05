"""
CIF Parser — streaming, event-driven.

Consumes the token stream from the Lexer, emits events to a CifParserEvents
handler.  Lexer errors attached to tokens are converted to on_error calls
before the token is processed structurally.

Version detection is performed first; the Lexer is then instantiated with the
result and a correct line offset so that reported positions are absolute.
"""

from dataclasses import dataclass
from typing import Iterator, List, Optional

from pycifparse.types import (
    CifVersion, ValueType, TokenType, ParseError, CifParserEvents,
)
from pycifparse.lexer.lexer import Lexer
from pycifparse.lexer.tokens import Token
from pycifparse.parser.version import detect_version


# ─────────────────────────────────────────────────────────────────────────────
# Internal frame types for the container stack
# ─────────────────────────────────────────────────────────────────────────────

class _ListFrame:
    __slots__ = ()


@dataclass
class _TableFrame:
    # 'key'   – expecting a quoted key or '}'
    # 'colon' – key buffered, expecting ':'
    # 'value' – key+colon emitted, expecting value
    state: str = 'key'
    pending_key: Optional[str] = None
    pending_key_vtype: Optional[ValueType] = None
    pending_key_tok: Optional['Token'] = None   # retained for adjacency check


@dataclass
class _FakeToken:
    """Minimal token substitute used for EOF error construction."""
    line: int
    column: int
    value: str = 'EOF'


# ─────────────────────────────────────────────────────────────────────────────
# Peekable token stream wrapper
# ─────────────────────────────────────────────────────────────────────────────

class _PeekableTokens:
    def __init__(self, gen: Iterator[Token]) -> None:
        self._gen = gen
        self._buf: List[Token] = []

    def peek(self) -> Optional[Token]:
        if not self._buf:
            try:
                self._buf.append(next(self._gen))
            except StopIteration:
                return None
        return self._buf[0]

    def next(self) -> Optional[Token]:
        if self._buf:
            return self._buf.pop(0)
        try:
            return next(self._gen)
        except StopIteration:
            return None

    def at_end(self) -> bool:
        return self.peek() is None


# ValueType values that are valid for table keys (quoted strings only).
_QUOTED_VTYPES = frozenset({
    ValueType.SINGLE_QUOTED,
    ValueType.DOUBLE_QUOTED,
    ValueType.TRIPLE_SINGLE_QUOTED,
    ValueType.TRIPLE_DOUBLE_QUOTED,
})


# ─────────────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────────────

class CifParser:
    """
    Streaming CIF parser.

    Usage::

        parser = CifParser(handler)
        parser.parse(cif_source_string)
    """

    def __init__(self, handler: CifParserEvents) -> None:
        self._h = handler

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def parse(self, source: str) -> None:
        version, remaining, line_offset, v_errors = detect_version(source)
        for ve in v_errors:
            self._h.on_error(ParseError(
                error_type='lexical',
                message=ve.message,
                line=ve.line, column=ve.column,
                context=ve.context,
                recovery_action=ve.recovery_action,
            ))

        lexer = Lexer(remaining, version, line_offset)
        self._stream = _PeekableTokens(lexer.tokens())
        self._version = version

        self._in_data_block: bool = False
        self._in_save_frame: bool = False
        self._in_loop: bool = False
        self._loop_tags: List[str] = []
        self._loop_has_values: bool = False  # True once any complete value is emitted in loop
        self._active_tag: Optional[str] = None
        self._tag_base_depth: int = 0      # container depth when tag was opened
        self._container_stack: list = []   # _ListFrame | _TableFrame
        self._halted: bool = False
        self._last_line: int = 1
        self._last_col: int = 1

        while not self._stream.at_end() and not self._halted:
            tok = self._stream.next()
            if tok is None:
                break
            self._flush_errors(tok)
            self._last_line, self._last_col = tok.line, tok.column
            self._dispatch(tok)

        if not self._halted:
            self._handle_eof()

    # ------------------------------------------------------------------
    # Error helpers
    # ------------------------------------------------------------------

    def _flush_errors(self, tok: Token) -> None:
        for le in tok.errors:
            self._h.on_error(ParseError(
                error_type='lexical',
                message=le.message,
                line=le.line, column=le.column,
                context=le.context,
                recovery_action='lexer recovery',
            ))

    def _err(self, etype: str, msg: str, tok, recovery: str = '') -> ParseError:
        return ParseError(
            error_type=etype, message=msg,
            line=tok.line, column=tok.column,
            context=tok.value, recovery_action=recovery,
        )

    def _err_at(self, etype: str, msg: str,
                line: int, col: int,
                ctx: str = '', recovery: str = '') -> ParseError:
        return ParseError(
            error_type=etype, message=msg,
            line=line, column=col,
            context=ctx, recovery_action=recovery,
        )

    # ------------------------------------------------------------------
    # Container lifecycle helpers
    # ------------------------------------------------------------------

    def _cleanup_table_frame(self, frame: _TableFrame, tok) -> None:
        """Emit corrections for an incomplete table frame before closing it."""
        if frame.state == 'colon' and frame.pending_key is not None:
            self._h.on_error(self._err(
                'syntactic',
                f'table key {frame.pending_key!r} missing : separator',
                tok, 'emitted on_table_key; inserted ? placeholder'))
            self._h.on_table_key(frame.pending_key, frame.pending_key_vtype)
            self._h.add_value('?', ValueType.PLACEHOLDER)
        elif frame.state == 'value':
            self._h.on_error(self._err(
                'syntactic', 'table key has no value',
                tok, 'inserted ? placeholder'))
            self._h.add_value('?', ValueType.PLACEHOLDER)

    def _close_all_containers(self, tok, reason: str) -> None:
        """Implicitly close all open containers LIFO with errors."""
        while self._container_stack:
            frame = self._container_stack.pop()
            if isinstance(frame, _ListFrame):
                self._h.on_list_end()
                self._h.on_error(self._err(
                    'syntactic',
                    f'implicitly closed unclosed list ({reason})',
                    tok, 'emitted on_list_end'))
            else:  # _TableFrame
                self._cleanup_table_frame(frame, tok)
                self._h.on_table_end()
                self._h.on_error(self._err(
                    'syntactic',
                    f'implicitly closed unclosed table ({reason})',
                    tok, 'emitted on_table_end'))
        self._active_tag = None

    def _close_active_tag(self, tok, reason: str) -> None:
        if self._active_tag is not None:
            self._h.on_error(self._err(
                'syntactic',
                f'tag {self._active_tag!r} has no value ({reason})',
                tok, 'inserted ? placeholder'))
            self._h.add_value('?', ValueType.PLACEHOLDER)
            self._active_tag = None

    def _close_loop(self, tok, reason: str) -> None:
        if self._container_stack:
            self._close_all_containers(tok, reason)
            self._h.on_error(self._err(
                'syntactic',
                f'unterminated container(s) in loop value ({reason})',
                tok, 'containers implicitly closed'))
        if not self._loop_has_values:
            self._h.on_error(self._err(
                'syntactic',
                f'loop has tags {self._loop_tags!r} but no values',
                tok, 'loop emitted empty'))
        self._h.on_loop_end()
        self._in_loop = False
        self._loop_tags = []
        self._loop_has_values = False

    def _after_close_container(self) -> None:
        """Called immediately after a container frame is popped."""
        # Notify a parent table that its value container just closed.
        if (self._container_stack
                and isinstance(self._container_stack[-1], _TableFrame)):
            top = self._container_stack[-1]
            if top.state == 'value':
                top.state = 'key'
        # Close the active tag if its outermost container is now done.
        if (self._active_tag is not None
                and len(self._container_stack) == self._tag_base_depth):
            self._active_tag = None
        # A top-level container closing inside a loop means values were received.
        if self._in_loop and not self._container_stack:
            self._loop_has_values = True

    # ------------------------------------------------------------------
    # Pre-keyword cleanup
    # ------------------------------------------------------------------

    def _prepare_for_keyword(self, tok, keyword: str) -> None:
        """Close any open loop / containers / active tag before a keyword."""
        if self._in_loop:
            self._close_loop(tok, f'terminated by {keyword}')
        else:
            if self._container_stack:
                self._close_all_containers(tok, f'terminated by {keyword}')
            self._close_active_tag(tok, f'terminated by {keyword}')

    # ------------------------------------------------------------------
    # Main dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, tok: Token) -> None:
        if tok.token_type == TokenType.KEYWORD:
            self._handle_keyword(tok)
        elif tok.token_type == TokenType.TAG:
            self._handle_tag(tok)
        else:
            self._handle_value(tok)

    # ------------------------------------------------------------------
    # Keyword handling
    # ------------------------------------------------------------------

    def _handle_keyword(self, tok: Token) -> None:
        lower = tok.value.lower()

        # ── global_: fatal ─────────────────────────────────────────────
        if lower == 'global_':
            self._handle_global(tok)
            return

        # ── stop_: loop terminator (checked before general cleanup) ────
        if lower == 'stop_':
            if self._in_loop:
                self._close_loop(tok, 'stop_')
            else:
                self._h.on_error(self._err(
                    'syntactic', 'stop_ outside loop', tok, 'ignored'))
            return

        # ── loop_: a new loop_ always terminates any active loop ──────────
        if lower == 'loop_':
            self._prepare_for_keyword(tok, 'loop_')
            if not self._in_data_block:
                self._h.on_error(self._err(
                    'syntactic', 'loop_ outside data block', tok, 'continuing'))
            self._start_loop(tok)
            return

        # ── data_ / save_: general cleanup then process ─────────────────
        self._prepare_for_keyword(tok, tok.value)

        if lower.startswith('data_'):
            name = tok.value[5:]
            if not name:
                self._h.on_error(self._err(
                    'syntactic', 'data block with empty name',
                    tok, 'using empty string'))
            if self._in_save_frame:
                self._h.on_save_frame_end()
                self._in_save_frame = False
            self._in_data_block = True
            self._h.on_data_block(name)

        elif lower.startswith('save_') and len(lower) > 5:
            name = tok.value[5:]
            if not self._in_data_block:
                self._h.on_error(self._err(
                    'syntactic', 'save frame outside data block',
                    tok, 'continuing'))
            if self._in_save_frame:
                self._h.on_error(self._err(
                    'syntactic', 'nested save frame',
                    tok, 'implicitly closed previous save frame'))
                self._h.on_save_frame_end()
            self._in_save_frame = True
            self._h.on_save_frame_start(name)

        elif lower == 'save_':
            if self._in_save_frame:
                self._h.on_save_frame_end()
                self._in_save_frame = False
            else:
                self._h.on_error(self._err(
                    'syntactic', 'save_ (frame close) outside save frame',
                    tok, 'ignored'))

    def _handle_global(self, tok: Token) -> None:
        """global_ is fatal: close all open structures then halt."""
        if self._in_loop:
            self._close_loop(tok, 'global_')
        else:
            if self._container_stack:
                self._close_all_containers(tok, 'global_')
            self._close_active_tag(tok, 'global_')
        if self._in_save_frame:
            self._h.on_save_frame_end()
            self._in_save_frame = False
        self._h.on_error(self._err(
            'syntactic',
            'global_ is reserved and not permitted in CIF',
            tok, 'parsing halted'))
        self._halted = True

    def _start_loop(self, tok: Token) -> None:
        """Collect loop tag names, then emit on_loop_start."""
        tags: List[str] = []
        while not self._stream.at_end():
            nxt = self._stream.peek()
            if nxt is None or nxt.token_type != TokenType.TAG:
                break
            nxt = self._stream.next()
            self._flush_errors(nxt)
            tags.append(nxt.value)

        if not tags:
            self._h.on_error(self._err(
                'syntactic', 'loop_ with no tags — loop skipped',
                tok, 'loop ignored'))
            return

        self._in_loop = True
        self._loop_tags = tags[:]
        self._loop_has_values = False
        self._h.on_loop_start(tags)

    # ------------------------------------------------------------------
    # Tag handling
    # ------------------------------------------------------------------

    def _handle_tag(self, tok: Token) -> None:
        # Tags terminate the current loop.
        if self._in_loop:
            self._close_loop(tok, f'new tag {tok.value!r}')
        elif self._container_stack:
            # Tag inside a container (outside loop) — close containers.
            self._h.on_error(self._err(
                'syntactic',
                f'tag {tok.value!r} encountered inside open container',
                tok, 'implicitly closing containers'))
            self._close_all_containers(tok, f'tag {tok.value!r}')

        # Close any previously active tag (consecutive tags).
        self._close_active_tag(tok, f'new tag {tok.value!r}')

        if not self._in_data_block:
            self._h.on_error(self._err(
                'syntactic', f'tag {tok.value!r} outside data block',
                tok, 'continuing'))

        self._active_tag = tok.value
        self._tag_base_depth = len(self._container_stack)
        self._h.add_tag(tok.value)

    # ------------------------------------------------------------------
    # Value handling
    # ------------------------------------------------------------------

    def _handle_value(self, tok: Token) -> None:
        value, vtype = tok.value, tok.value_type

        # CIF 2.0 structural delimiters and table separator.
        if self._version == CifVersion.CIF_2_0:
            if value == '[':
                self._open_list(tok); return
            if value == ']':
                self._close_list(tok); return
            if value == '{':
                self._open_table(tok); return
            if value == '}':
                self._close_table(tok); return
            if value == ':':
                if (self._container_stack
                        and isinstance(self._container_stack[-1], _TableFrame)):
                    self._handle_table_colon(tok)
                else:
                    # ':' outside table context — scalar value.
                    self._dispatch_scalar_value(value, vtype, tok)
                return

        self._dispatch_scalar_value(value, vtype, tok)

    # ── Container open/close ───────────────────────────────────────────

    def _ensure_value_context(self, tok: Token) -> None:
        """
        If there is no enclosing context for a container value, set up a
        synthetic _error_value tag so the container has somewhere to go.
        """
        if (not self._in_loop
                and self._active_tag is None
                and not self._container_stack):
            self._h.on_error(self._err(
                'syntactic', 'container without preceding tag',
                tok, 'attached to _error_value'))
            self._h.add_tag('_error_value')
            self._active_tag = '_error_value'
            self._tag_base_depth = 0

    def _notify_parent_table_of_container_open(self, tok: Token) -> None:
        """
        When a container opens while inside a table, ensure the table is in
        'value' state (adjusting from 'key' or 'colon' with errors if needed).
        """
        if not (self._container_stack
                and isinstance(self._container_stack[-1], _TableFrame)):
            return
        top = self._container_stack[-1]
        if top.state == 'key':
            self._h.on_error(self._err(
                'syntactic', 'container in table key position',
                tok, 'treating container as table value (no key)'))
            top.state = 'value'
        elif top.state == 'colon':
            self._h.on_error(self._err(
                'syntactic',
                f'table key {top.pending_key!r} missing : separator',
                tok, 'emitted on_table_key; treating container as value'))
            self._h.on_table_key(top.pending_key, top.pending_key_vtype)
            top.pending_key = None
            top.state = 'value'
        # state == 'value' is the normal path — nothing to do.

    def _open_list(self, tok: Token) -> None:
        self._ensure_value_context(tok)
        self._notify_parent_table_of_container_open(tok)
        self._container_stack.append(_ListFrame())
        self._h.on_list_start()

    def _close_list(self, tok: Token) -> None:
        if not self._container_stack or not isinstance(
                self._container_stack[-1], _ListFrame):
            self._h.on_error(self._err(
                'syntactic', 'unexpected ] — no open list', tok, 'ignored'))
            return
        self._container_stack.pop()
        self._h.on_list_end()
        self._after_close_container()

    def _open_table(self, tok: Token) -> None:
        self._ensure_value_context(tok)
        self._notify_parent_table_of_container_open(tok)
        self._container_stack.append(_TableFrame())
        self._h.on_table_start()

    def _close_table(self, tok: Token) -> None:
        if not self._container_stack or not isinstance(
                self._container_stack[-1], _TableFrame):
            self._h.on_error(self._err(
                'syntactic', 'unexpected } — no open table', tok, 'ignored'))
            return
        frame = self._container_stack[-1]
        self._cleanup_table_frame(frame, tok)
        self._container_stack.pop()
        self._h.on_table_end()
        self._after_close_container()

    # ── Table colon and key/value dispatch ────────────────────────────

    @staticmethod
    def _key_adjacent_col(key_tok: Token) -> Optional[int]:
        """
        Return the column at which a colon would sit if immediately adjacent
        to *key_tok* (no intervening whitespace).  Returns None for token
        types where the calculation is unreliable (e.g. multi-line triple
        quoted keys).
        """
        vt = key_tok.value_type
        if vt in (ValueType.SINGLE_QUOTED, ValueType.DOUBLE_QUOTED):
            # token width = 1 (open quote) + len(value) + 1 (close quote)
            return key_tok.column + len(key_tok.value) + 2
        if vt in (ValueType.TRIPLE_SINGLE_QUOTED, ValueType.TRIPLE_DOUBLE_QUOTED):
            # Only reliable if the value contains no newlines.
            if '\n' not in key_tok.value:
                return key_tok.column + len(key_tok.value) + 6
        return None

    def _handle_table_colon(self, tok: Token) -> None:
        frame: _TableFrame = self._container_stack[-1]
        if frame.state == 'colon':
            # Check that the colon is immediately adjacent to the key (same
            # line, no intervening whitespace).  A gap is valid structurally
            # but non-conformant per the CIF 2.0 EBNF.
            if frame.pending_key_tok is not None:
                adj = self._key_adjacent_col(frame.pending_key_tok)
                if adj is not None and (
                        tok.line != frame.pending_key_tok.line
                        or tok.column != adj):
                    self._h.on_error(self._err(
                        'syntactic',
                        f'whitespace between table key '
                        f'{frame.pending_key!r} and : separator',
                        tok, 'accepted'))
            self._h.on_table_key(frame.pending_key, frame.pending_key_vtype)
            frame.pending_key = None
            frame.pending_key_vtype = None
            frame.pending_key_tok = None
            frame.state = 'value'
        elif frame.state == 'key':
            self._h.on_error(self._err(
                'syntactic', 'unexpected : in table — no pending key',
                tok, 'ignored'))
        else:  # 'value'
            self._h.on_error(self._err(
                'syntactic', 'unexpected : in table value position',
                tok, 'ignored'))

    def _dispatch_scalar_in_table(self, value: str,
                                   vtype: Optional[ValueType],
                                   tok: Token) -> None:
        frame: _TableFrame = self._container_stack[-1]

        if frame.state == 'key':
            if vtype not in _QUOTED_VTYPES:
                self._h.on_error(self._err(
                    'syntactic',
                    f'table key must be a quoted string, got unquoted: {value!r}',
                    tok, 'treating as key anyway'))
            frame.pending_key = value
            frame.pending_key_vtype = vtype
            frame.pending_key_tok = tok
            frame.state = 'colon'

        elif frame.state == 'colon':
            # A value where ':' was expected — emit the pending key with error.
            self._h.on_error(self._err(
                'syntactic',
                f'table key {frame.pending_key!r} not followed by : separator',
                tok, 'emitted on_table_key; treating current token as value'))
            self._h.on_table_key(frame.pending_key, frame.pending_key_vtype)
            frame.pending_key = None
            frame.pending_key_vtype = None
            frame.pending_key_tok = None
            self._h.add_value(value, vtype)
            frame.state = 'key'

        else:  # 'value'
            self._h.add_value(value, vtype)
            frame.state = 'key'

    def _dispatch_scalar_value(self, value: str,
                                vtype: Optional[ValueType],
                                tok: Token) -> None:
        """Route a scalar value to the correct context."""
        if self._container_stack:
            top = self._container_stack[-1]
            if isinstance(top, _TableFrame):
                self._dispatch_scalar_in_table(value, vtype, tok)
            else:  # _ListFrame
                self._h.add_value(value, vtype)
        elif self._in_loop:
            self._h.add_value(value, vtype)
            self._loop_has_values = True
        elif self._active_tag is not None:
            self._h.add_value(value, vtype)
            self._active_tag = None
        else:
            self._h.on_error(self._err(
                'syntactic',
                f'value {value!r} has no preceding tag',
                tok, 'attached to _error_value'))
            self._h.add_tag('_error_value')
            self._h.add_value(value, vtype)

    # ------------------------------------------------------------------
    # EOF handler
    # ------------------------------------------------------------------

    def _handle_eof(self) -> None:
        line, col = self._last_line, self._last_col
        eof = _FakeToken(line, col)

        if self._in_loop:
            self._close_loop(eof, 'EOF')

        # Close any remaining containers outside a loop.
        while self._container_stack:
            frame = self._container_stack.pop()
            if isinstance(frame, _ListFrame):
                self._h.on_list_end()
                self._h.on_error(self._err_at(
                    'syntactic', 'unterminated list at EOF',
                    line, col, '', 'emitted on_list_end'))
            else:
                self._cleanup_table_frame(frame, eof)
                self._h.on_table_end()
                self._h.on_error(self._err_at(
                    'syntactic', 'unterminated table at EOF',
                    line, col, '', 'emitted on_table_end'))

        # Active tag with no value.
        if self._active_tag is not None:
            self._h.on_error(self._err_at(
                'syntactic',
                f'tag {self._active_tag!r} has no value at EOF',
                line, col, self._active_tag, 'inserted ? placeholder'))
            self._h.add_value('?', ValueType.PLACEHOLDER)
            self._active_tag = None

        # Save frame: EOF is a valid terminator — no error emitted.
        if self._in_save_frame:
            self._h.on_save_frame_end()
            self._in_save_frame = False
