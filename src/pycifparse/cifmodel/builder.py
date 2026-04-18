"""
CifBuilder — constructs a CifFile from the CifParserEvents stream.

CifBuilder implements CifParserEvents and is wired directly to CifParser:

    builder = CifBuilder(on_error=handler.on_error)
    CifParser(builder).parse(source)
    cif = builder.result

Semantic errors (empty loop, row-count mismatch) are reported via the
on_error callable using error_type='semantic'.  In strict mode the builder
stops accumulating after the first semantic error; in pad mode it continues
and pads incomplete loop rows with '?' placeholders.

Multiline text field values (ValueType.MULTILINE_STRING) are passed through
the transformation pipeline (prefix removal + line unfolding) before storage.
All other value types are stored as raw strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Union

from pycifparse.types import ParseError, ValueType
from pycifparse.cifmodel.model import CifBlock, CifFile, CifSaveFrame, CifValue
from pycifparse.cifmodel.scalar import CifScalar
from pycifparse.cifmodel.textfield import transform_multiline
from pycifparse.parser.parser import CifParser
from pycifparse.parser.version import detect_version


# ─────────────────────────────────────────────────────────────────────────────
# Internal container types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _TableInProgress:
    data: dict = field(default_factory=dict)
    current_key: str | None = None


_Container = Union[list, _TableInProgress]


# ─────────────────────────────────────────────────────────────────────────────
# CifBuilder
# ─────────────────────────────────────────────────────────────────────────────

class CifBuilder:
    """
    Implements CifParserEvents.  Accumulates events into a CifFile.

    Parameters
    ----------
    on_error:
        Called with a ParseError for each semantic error detected by the IR
        layer.  Pass ``handler.on_error`` to unify parser and IR errors.
    mode:
        'strict' — stop accumulating on the first semantic error.
        'pad'    — emit a warning and pad incomplete loop rows with '?'.
    """

    def __init__(
        self,
        on_error: Callable[[ParseError], None],
        mode: Literal['strict', 'pad'] = 'pad',
    ) -> None:
        self._on_error = on_error
        self._mode = mode
        self._file = CifFile()

        # Block / save-frame state
        self._block: CifBlock | None = None
        self._save_frame: CifSaveFrame | None = None

        # Active tag (awaiting a value)
        self._active_tag: str | None = None

        # Loop state
        self._in_loop = False
        self._loop_tags: list[str] = []
        self._loop_value_index = 0
        self._loop_buffers: dict[str, list[CifValue]] = {}

        # Container nesting stack
        self._container_stack: list[_Container] = []

        # Set to True in strict mode after a semantic error
        self._stopped = False

    # ── Result ────────────────────────────────────────────────────────────────

    @property
    def result(self) -> CifFile:
        """The CifFile accumulated so far."""
        return self._file

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def _current_ns(self) -> CifBlock | CifSaveFrame | None:
        return self._save_frame if self._save_frame is not None else self._block

    def _semantic_error(self, message: str, recovery: str) -> None:
        self._on_error(ParseError(
            error_type='semantic',
            message=message,
            line=0, column=0,
            context='CifBuilder',
            recovery_action=recovery,
        ))
        if self._mode == 'strict':
            self._stopped = True

    def _dispatch_value(self, value: CifValue) -> None:
        """Route a complete value (scalar or closed container) to its destination."""
        if self._container_stack:
            top = self._container_stack[-1]
            if isinstance(top, list):
                top.append(value)
            else:
                if top.current_key is not None:
                    top.data[top.current_key] = value
                    top.current_key = None
            return

        if self._in_loop:
            n = len(self._loop_tags)
            tag = self._loop_tags[self._loop_value_index % n]
            self._loop_buffers[tag].append(value)
            self._loop_value_index += 1
            return

        ns = self._current_ns
        if ns is not None and self._active_tag is not None:
            ns._append_value(self._active_tag, value)
            self._active_tag = None

    # ── CifParserEvents ───────────────────────────────────────────────────────

    def on_data_block(self, name: str) -> None:
        if self._stopped:
            return
        if name in self._file:
            self._semantic_error(
                message=f'duplicate data block name: {name!r}',
                recovery='duplicate block stored with distinct internal id',
            )
        self._block = CifBlock(name)
        self._file._add_block(self._block)
        self._save_frame = None
        self._active_tag = None
        self._in_loop = False
        self._loop_tags = []
        self._loop_value_index = 0
        self._loop_buffers = {}
        self._container_stack = []

    def on_save_frame_start(self, name: str) -> None:
        if self._stopped or self._block is None:
            return
        if name in self._block:
            self._semantic_error(
                message=f'duplicate save frame name: {name!r}',
                recovery='duplicate save frame stored with distinct internal id',
            )
        self._save_frame = CifSaveFrame(name)

    def on_save_frame_end(self) -> None:
        if self._stopped or self._block is None:
            return
        if self._save_frame is not None:
            self._block._add_save_frame(self._save_frame)
        self._save_frame = None

    def add_tag(self, tag_name: str) -> None:
        if self._stopped:
            return
        self._active_tag = tag_name

    def add_value(self, value: str, value_type: ValueType) -> None:
        if self._stopped:
            return
        if value_type == ValueType.MULTILINE_STRING:
            value = transform_multiline(value)
        self._dispatch_value(CifScalar(value, value_type))

    def on_list_start(self) -> None:
        if self._stopped:
            return
        self._container_stack.append([])

    def on_list_end(self) -> None:
        if self._stopped or not self._container_stack:
            return
        completed = self._container_stack.pop()
        self._dispatch_value(completed)

    def on_table_start(self) -> None:
        if self._stopped:
            return
        self._container_stack.append(_TableInProgress())

    def on_table_key(self, key: str, value_type: ValueType) -> None:
        if self._stopped or not self._container_stack:
            return
        top = self._container_stack[-1]
        if isinstance(top, _TableInProgress):
            top.current_key = key

    def on_table_end(self) -> None:
        if self._stopped or not self._container_stack:
            return
        top = self._container_stack.pop()
        if isinstance(top, _TableInProgress):
            self._dispatch_value(top.data)

    def on_loop_start(self, tags: list[str]) -> None:
        if self._stopped:
            return
        self._in_loop = True
        self._loop_tags = list(tags)
        self._loop_value_index = 0
        self._loop_buffers = {tag: [] for tag in tags}

    def on_loop_end(self) -> None:
        if self._stopped:
            return
        n = len(self._loop_tags)
        total = self._loop_value_index
        ns = self._current_ns

        if n == 0:
            self._in_loop = False
            return

        if total % n != 0:
            missing = n - (total % n)
            tag_list = ', '.join(self._loop_tags)
            self._semantic_error(
                message=(
                    f'loop value count {total} is not divisible by tag count {n} '
                    f'({missing} value(s) missing from final row); '
                    f'tags: {tag_list}'
                ),
                recovery='stopped' if self._mode == 'strict' else f'padded {missing} placeholder(s)',
            )
            if self._stopped:
                self._in_loop = False
                return
            # Pad mode: fill incomplete final row with '?'
            for _ in range(missing):
                tag = self._loop_tags[self._loop_value_index % n]
                self._loop_buffers[tag].append(CifScalar('?', ValueType.PLACEHOLDER))
                self._loop_value_index += 1

        if ns is not None:
            ns._add_loop(self._loop_tags, self._loop_buffers)

        self._in_loop = False
        self._loop_tags = []
        self._loop_value_index = 0
        self._loop_buffers = {}

    def on_error(self, error: ParseError) -> None:
        self._on_error(error)


# ─────────────────────────────────────────────────────────────────────────────
# Convenience function
# ─────────────────────────────────────────────────────────────────────────────

def build(
    source: str,
    *,
    mode: Literal['strict', 'pad'] = 'pad',
) -> tuple[CifFile, list[ParseError]]:
    """
    Parse *source* and return ``(CifFile, errors)``.

    *errors* contains both parser-level and IR-level errors in emission order.
    """
    errors: list[ParseError] = []
    version = detect_version(source)
    builder = CifBuilder(on_error=errors.append, mode=mode)
    CifParser(builder).parse(source)
    builder.result.version = version
    return builder.result, errors
