"""
CifBuilder — constructs a CifFile from the CifParserEvents stream.

CifBuilder implements CifParserEvents and is wired directly to the Rust parser:

    builder = CifBuilder(on_error=handler.on_error)
    version = cifflow_core.parse(source, builder)
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

import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Literal, Union


def _casefold(s: str) -> str:
    """Apply Unicode NFC(casefold(NFD)) normalisation for canonical caseless comparison."""
    return unicodedata.normalize('NFC', unicodedata.normalize('NFD', s).casefold())

from cifflow.types import ParseError, ValueType
from cifflow.cifmodel.model import CifBlock, CifFile, CifSaveFrame, CifValue
from cifflow.cifmodel.textfield import transform_multiline


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
    Implements :class:`~cifflow.types.CifParserEvents`; accumulates events into a CifFile.

    Parameters
    ----------
    on_error
        Called with a :class:`~cifflow.types.ParseError` for each semantic error
        detected by the IR layer.  Pass ``handler.on_error`` to unify parser and
        IR errors into a single stream.
    mode
        ``'strict'`` — stop accumulating on the first semantic error.
        ``'pad'`` — continue and pad incomplete loop rows with ``'?'`` placeholders.
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
        """Return the active namespace: the current save frame, or the current block."""
        return self._save_frame if self._save_frame is not None else self._block

    def _semantic_error(self, message: str, recovery: str) -> None:
        """Emit a semantic error event and set stopped=True in strict mode."""
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
        """Casefold name, emit error on duplicate, and open a new data block."""
        if self._stopped:
            return
        name = _casefold(name)
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
        """Casefold name, emit error on duplicate, and open a new save frame."""
        if self._stopped or self._block is None:
            return
        name = _casefold(name)
        if name in self._block:
            self._semantic_error(
                message=f'duplicate save frame name: {name!r}',
                recovery='duplicate save frame stored with distinct internal id',
            )
        self._save_frame = CifSaveFrame(name)

    def on_save_frame_end(self) -> None:
        """Close the current save frame and register it with the block."""
        if self._stopped or self._block is None:
            return
        if self._save_frame is not None:
            self._block._add_save_frame(self._save_frame)
        self._save_frame = None

    def add_tag(self, tag_name: str) -> None:
        """Casefold and register the pending tag name."""
        if self._stopped:
            return
        self._active_tag = _casefold(tag_name)

    def add_value(self, value: str, value_type: ValueType) -> None:
        """Transform multiline fields; quote bare dots/questions; dispatch the value."""
        if self._stopped:
            return
        if value_type == ValueType.MULTILINE_STRING:
            value = transform_multiline(value)
        elif value_type != ValueType.PLACEHOLDER and value in ('.', '?'):
            value = f'"{value}"'
        self._dispatch_value(value)

    def on_list_start(self) -> None:
        """Push a new list container onto the nesting stack."""
        if self._stopped:
            return
        self._container_stack.append([])

    def on_list_end(self) -> None:
        """Pop the completed list and dispatch it as a value."""
        if self._stopped or not self._container_stack:
            return
        completed = self._container_stack.pop()
        self._dispatch_value(completed)

    def on_table_start(self) -> None:
        """Push a new table container onto the nesting stack."""
        if self._stopped:
            return
        self._container_stack.append(_TableInProgress())

    def on_table_key(self, key: str, value_type: ValueType) -> None:
        """Set the current key on the pending table container."""
        if self._stopped or not self._container_stack:
            return
        top = self._container_stack[-1]
        if isinstance(top, _TableInProgress):
            top.current_key = key

    def on_table_end(self) -> None:
        """Pop the completed table and dispatch it as a value."""
        if self._stopped or not self._container_stack:
            return
        top = self._container_stack.pop()
        if isinstance(top, _TableInProgress):
            self._dispatch_value(top.data)

    def on_loop_start(self, tags: list[str]) -> None:
        """Initialise loop tracking with casefolded tag names and empty buffers."""
        if self._stopped:
            return
        self._in_loop = True
        self._loop_tags = [_casefold(t) for t in tags]
        self._loop_value_index = 0
        self._loop_buffers = {_casefold(t): [] for t in tags}

    def on_loop_end(self) -> None:
        """Validate row count; pad in pad mode; commit loop to the namespace."""
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
                self._loop_buffers[tag].append('?')
                self._loop_value_index += 1

        if ns is not None:
            ns._add_loop(self._loop_tags, self._loop_buffers)

        self._in_loop = False
        self._loop_tags = []
        self._loop_value_index = 0
        self._loop_buffers = {}

    def on_error(self, error: ParseError) -> None:
        """Forward the parse error to the registered on_error callback."""
        self._on_error(error)


# ─────────────────────────────────────────────────────────────────────────────
# Convenience function
# ─────────────────────────────────────────────────────────────────────────────

def cif_to_arrow(cif: CifFile) -> list:
    r"""
    Convert any CifFile (parsed or programmatic) to Arrow RecordBatches.

    Produces the same batch format as :func:`build_arrow`: five metadata columns
    plus one UTF-8 column per tag, one batch per scalar group and per loop.
    Container values (Python list/dict) are encoded as ``\x00`` + JSON.

    Parameters
    ----------
    cif
        The CifFile to convert.

    Returns
    -------
    list
        A list of ``pa.RecordBatch`` objects, one per logical namespace section.
        No errors are returned — the CifFile is assumed to be valid.
    """
    import json  # noqa: PLC0415
    import pyarrow as pa  # noqa: PLC0415

    def _enc(v) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            return v
        return '\x00' + json.dumps(v, separators=(',', ':'), ensure_ascii=False)

    def _batch(block_idx, block_name, frame_idx, frame_name, loop_id, tags, tag_data, n):
        fields = [
            pa.field('_cifflow_block_idx',  pa.int32(),  nullable=False),
            pa.field('_block_name', pa.utf8(),   nullable=False),
            pa.field('_frame_idx',  pa.int32(),  nullable=True),
            pa.field('_frame_name', pa.utf8(),   nullable=True),
            pa.field('_loop_id',    pa.utf8(),   nullable=False),
        ]
        for tag in tags:
            fields.append(pa.field(tag, pa.utf8(), nullable=True))
        schema = pa.schema(fields)
        arrays = [
            pa.array([block_idx]  * n, type=pa.int32()),
            pa.array([block_name] * n, type=pa.utf8()),
            pa.array([frame_idx]  * n, type=pa.int32()),
            pa.array([frame_name] * n, type=pa.utf8()),
            pa.array([loop_id]    * n, type=pa.utf8()),
        ]
        for tag in tags:
            col = tag_data.get(tag, [None] * n)
            arrays.append(pa.array(col, type=pa.utf8()))
        return pa.RecordBatch.from_arrays(arrays, schema=schema)

    def _ns_batches(ns, block_idx, block_name, frame_idx, frame_name):
        result = []
        loop_tag_set = {t for loop in ns.loops for t in loop}
        scalar_tags = [t for t in ns.tags if t not in loop_tag_set]

        if scalar_tags:
            n = max(len(ns[t]) for t in scalar_tags)
            if n > 0:
                tag_data = {}
                for tag in scalar_tags:
                    col = [_enc(v) for v in ns[tag]]
                    col += [None] * (n - len(col))
                    tag_data[tag] = col
                result.append(_batch(block_idx, block_name, frame_idx, frame_name,
                                     '__scalars__', scalar_tags, tag_data, n))

        for li, loop_tags in enumerate(ns.loops):
            if not loop_tags:
                continue
            n = len(ns[loop_tags[0]])
            if n == 0:
                continue
            tag_data = {t: [_enc(v) for v in ns[t]] for t in loop_tags}
            result.append(_batch(block_idx, block_name, frame_idx, frame_name,
                                 f'__loop_{li}__', loop_tags, tag_data, n))
        return result

    batches = []
    for bi, block_name in enumerate(cif.blocks):
        block = cif[block_name]
        batches.extend(_ns_batches(block, bi, block_name, None, None))
        fi = 0
        for sf_name in dict.fromkeys(block.save_frames):
            for sf in block.get_all(sf_name):
                batches.extend(_ns_batches(sf, bi, block_name, fi, sf_name))
                fi += 1
    return batches


def build_arrow(
    source: str,
    *,
    mode: Literal['strict', 'pad'] = 'pad',
) -> tuple[list, list[ParseError]]:
    """
    Parse CIF source text and return Arrow RecordBatches.

    Each batch covers one logical namespace section: the scalar tags of a
    block/save frame, or one loop within it.  The per-batch schema contains
    five metadata columns plus one column per tag present in that section.

    Parameters
    ----------
    source
        Full CIF source text.
    mode
        Error-handling mode: ``'pad'`` (default) or ``'strict'``.

    Returns
    -------
    tuple[list, list[ParseError]]
        A ``(batches, errors)`` pair.
    """
    from cifflow import cifflow_core  # noqa: PLC0415
    batches, error_dicts = cifflow_core.parse_arrow(source, mode)
    errors = [ParseError(**e) for e in error_dicts]
    return batches, errors


def build_arrow_file(
    path: str,
    *,
    mode: Literal['strict', 'pad'] = 'pad',
) -> tuple[list, list[ParseError]]:
    """
    Parse a CIF file from disk and return Arrow RecordBatches.

    File I/O is performed entirely in Rust — no Python file objects are created.

    Parameters
    ----------
    path
        Filesystem path to the CIF file.
    mode
        Error-handling mode: ``'pad'`` (default) or ``'strict'``.

    Returns
    -------
    tuple[list, list[ParseError]]
        A ``(batches, errors)`` pair.
    """
    from cifflow import cifflow_core  # noqa: PLC0415
    batches, error_dicts = cifflow_core.parse_arrow_file(path, mode)
    errors = [ParseError(**e) for e in error_dicts]
    return batches, errors


def build(
    source: str,
    *,
    mode: Literal['strict', 'pad'] = 'pad',
) -> tuple[CifFile, list[ParseError]]:
    """
    Parse CIF source text and return a CifFile.

    Uses the Rust IR builder directly — no per-token Python callbacks and no
    intermediate Python dict.  Both parser-level and IR-level errors are
    returned in emission order.

    Parameters
    ----------
    source
        Full CIF source text.
    mode
        Error-handling mode: ``'pad'`` (default) or ``'strict'``.

    Returns
    -------
    tuple[CifFile, list[ParseError]]
        A ``(cif, errors)`` pair.  ``errors`` is empty for well-formed input.
    """
    from cifflow import cifflow_core  # noqa: PLC0415
    cif, error_dicts = cifflow_core.parse_cif(source, mode)
    errors = [ParseError(**e) for e in error_dicts]
    return cif, errors
