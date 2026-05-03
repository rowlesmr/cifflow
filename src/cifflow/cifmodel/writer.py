"""
Programmatic CIF construction API.

CifWriter       — file-level container; manages blocks and holds the CifFile.
BlockWriter     — write handle for one CifBlock.
SaveFrameWriter — write handle for one CifSaveFrame (base class for BlockWriter).

Usage::

    writer = CifWriter(CifVersion.CIF_2_0)
    block = writer.add_block("my_block")
    block.set_tag("_cell_length_a", "5.0")
    cif = writer.build()
"""

from __future__ import annotations

import warnings
from typing import Union

from cifflow.types import CifVersion, ValueType
from cifflow.cifmodel.model import CifBlock, CifFile, CifSaveFrame, CifValue
from cifflow.cifmodel.scalar import CifScalar

# Recursive type alias (documentation only — Python does not enforce it at runtime)
# CifInput = int | float | str | CifScalar | list[CifInput] | dict[str, CifInput]
CifInput = Union[int, float, str, CifScalar, list, dict]


# ─────────────────────────────────────────────────────────────────────────────
# Naming validation
# ─────────────────────────────────────────────────────────────────────────────

def _is_legal_name(name: str, version: CifVersion) -> bool:
    if not name:
        return False
    if version == CifVersion.CIF_1_1:
        return all(0x21 <= ord(c) <= 0x7E for c in name)
    return all(not c.isspace() and c.isprintable() for c in name)


def _check_name(name: str, version: CifVersion, kind: str) -> None:
    if not _is_legal_name(name, version):
        raise ValueError(
            f"Illegal CIF {kind} name {name!r} for version {version.value}"
        )


def _check_tag(tag: str, version: CifVersion) -> None:
    if not tag.startswith('_'):
        raise ValueError(f"Tag name must start with '_': {tag!r}")
    _check_name(tag[1:], version, "tag")


# ─────────────────────────────────────────────────────────────────────────────
# ValueType inference
# ─────────────────────────────────────────────────────────────────────────────

def _infer(value: CifInput) -> CifValue:
    if isinstance(value, CifScalar):
        return value
    if isinstance(value, bool):
        return CifScalar(str(value), ValueType.STRING)
    if isinstance(value, (int, float)):
        return CifScalar(str(value), ValueType.STRING)
    if isinstance(value, str):
        if value in ('.', '?'):
            return CifScalar(value, ValueType.PLACEHOLDER)
        return CifScalar(value, ValueType.STRING)
    if isinstance(value, list):
        return [_infer(e) for e in value]
    if isinstance(value, dict):
        return {k: _infer(v) for k, v in value.items()}
    raise TypeError(f"Unsupported CifInput type: {type(value)!r}")


def _infer_column(values: list) -> list[CifValue]:
    return [_infer(v) for v in values]


# ─────────────────────────────────────────────────────────────────────────────
# CIF 1.1 container check (used at build() time)
# ─────────────────────────────────────────────────────────────────────────────

def _contains_container(value: CifValue) -> bool:
    if isinstance(value, list):
        return True
    if isinstance(value, dict):
        return True
    return False


def _any_container_in_values(values: list[CifValue]) -> bool:
    for v in values:
        if isinstance(v, list):
            return True
        if isinstance(v, dict):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Loop lookup helper
# ─────────────────────────────────────────────────────────────────────────────

def _find_loop_index(ns: CifSaveFrame, loop_tag: str) -> int:
    for i, loop in enumerate(ns._loops):
        if loop_tag in loop:
            return i
    raise KeyError(loop_tag)


def _loop_row_count(ns: CifSaveFrame, loop_index: int) -> int:
    tags = ns._loops[loop_index]
    if not tags:
        return 0
    return len(ns._tags.get(tags[0], []))


# ─────────────────────────────────────────────────────────────────────────────
# SaveFrameWriter
# ─────────────────────────────────────────────────────────────────────────────

class SaveFrameWriter:
    """
    Write handle for one CifSaveFrame.  Base class for BlockWriter.
    Obtained from BlockWriter.add_save_frame() or BlockWriter.get_save_frame().
    """

    def __init__(self, ns: CifSaveFrame, version: CifVersion) -> None:
        self._ns = ns
        self._version = version

    def _tag_in_any_loop(self, tag: str) -> bool:
        return any(tag in loop for loop in self._ns._loops)

    # ── Tag / scalar values ───────────────────────────────────────────────────

    def set_tag(self, tag: str, value: CifInput) -> 'SaveFrameWriter':
        _check_tag(tag, self._version)
        if tag in self._ns._tags:
            raise ValueError(
                f"Tag {tag!r} already exists in namespace {self._ns.name!r}"
            )
        self._ns._append_value(tag, _infer(value))
        return self

    # ── Loop values ───────────────────────────────────────────────────────────

    def add_loop(self, columns: dict[str, list[CifInput]]) -> 'SaveFrameWriter':
        if not columns:
            raise ValueError("columns must not be empty")
        tags = list(columns.keys())
        for tag in tags:
            _check_tag(tag, self._version)
            if tag in self._ns._tags:
                raise ValueError(
                    f"Tag {tag!r} already exists in namespace {self._ns.name!r}"
                )
        lengths = [len(v) for v in columns.values()]
        if len(set(lengths)) > 1:
            raise ValueError(
                f"All loop columns must have the same length; got {lengths}"
            )
        buffers: dict[str, list[CifValue]] = {
            tag: _infer_column(vals) for tag, vals in columns.items()
        }
        self._ns._add_loop(tags, buffers)
        return self

    def add_loop_column(
        self,
        loop_tag: str,
        new_tag: str,
        values: list[CifInput],
    ) -> 'SaveFrameWriter':
        loop_idx = _find_loop_index(self._ns, loop_tag)
        _check_tag(new_tag, self._version)
        if new_tag in self._ns._tags:
            raise ValueError(
                f"Tag {new_tag!r} already exists in namespace {self._ns.name!r}"
            )
        row_count = _loop_row_count(self._ns, loop_idx)
        if len(values) != row_count:
            raise ValueError(
                f"Column length {len(values)} does not match loop row count {row_count}"
            )
        converted = _infer_column(values)
        # Insert into _tag_order immediately after the loop's last tag
        last_tag = self._ns._loops[loop_idx][-1]
        insert_pos = next(
            i for i in range(len(self._ns._tag_order) - 1, -1, -1)
            if self._ns._tag_order[i] == last_tag
        )
        self._ns._tag_order.insert(insert_pos + 1, new_tag)
        self._ns._tags[new_tag] = converted
        self._ns._loops[loop_idx].append(new_tag)
        return self

    def reorder_loop_tags(
        self,
        loop_tag: str,
        new_order: list[str],
    ) -> 'SaveFrameWriter':
        loop_idx = _find_loop_index(self._ns, loop_tag)
        current = self._ns._loops[loop_idx]
        if sorted(new_order) != sorted(current):
            raise ValueError(
                f"new_order {new_order!r} is not a permutation of {current!r}"
            )
        # Find start of contiguous block in _tag_order and overwrite
        start = next(
            i for i, t in enumerate(self._ns._tag_order) if t in current
        )
        for i, t in enumerate(new_order):
            self._ns._tag_order[start + i] = t
        self._ns._loops[loop_idx] = list(new_order)
        return self

    def get_loop_tags(self, loop_tag: str) -> list[str]:
        loop_idx = _find_loop_index(self._ns, loop_tag)
        return list(self._ns._loops[loop_idx])

    def add_loop_row(
        self,
        loop_tag: str,
        row: list[CifInput],
    ) -> 'SaveFrameWriter':
        loop_idx = _find_loop_index(self._ns, loop_tag)
        loop_tags = self._ns._loops[loop_idx]
        if len(row) != len(loop_tags):
            raise ValueError(
                f"Row length {len(row)} does not match loop tag count {len(loop_tags)}"
            )
        for tag, val in zip(loop_tags, _infer_column(row)):
            self._ns._tags[tag].append(val)
        return self

    # ── Mutation — tags ───────────────────────────────────────────────────────

    def reassign_tag(
        self,
        tag: str,
        value: 'CifInput | list[CifInput]',
    ) -> 'SaveFrameWriter':
        if tag not in self._ns._tags:
            raise KeyError(tag)
        if self._tag_in_any_loop(tag):
            if not isinstance(value, list):
                raise ValueError(
                    f"Tag {tag!r} is a loop column; value must be a list"
                )
            self._ns._tags[tag] = _infer_column(value)
        else:
            self._ns._tags[tag] = [_infer(value)]
        return self

    def delete_tag(self, tag: str) -> 'SaveFrameWriter':
        if tag not in self._ns._tags:
            raise KeyError(tag)
        if self._tag_in_any_loop(tag):
            loop_idx = _find_loop_index(self._ns, tag)
            self._remove_loop_tag_impl(loop_idx, tag)
        else:
            del self._ns._tags[tag]
            self._ns._tag_order.remove(tag)
        return self

    # ── Mutation — loops ──────────────────────────────────────────────────────

    def remove_loop_tag(
        self,
        loop_tag: str,
        tag_to_remove: str,
    ) -> 'SaveFrameWriter':
        loop_idx = _find_loop_index(self._ns, loop_tag)
        if tag_to_remove not in self._ns._loops[loop_idx]:
            raise KeyError(tag_to_remove)
        self._remove_loop_tag_impl(loop_idx, tag_to_remove)
        return self

    def _remove_loop_tag_impl(self, loop_idx: int, tag: str) -> None:
        self._ns._loops[loop_idx].remove(tag)
        del self._ns._tags[tag]
        self._ns._tag_order.remove(tag)
        if not self._ns._loops[loop_idx]:
            del self._ns._loops[loop_idx]


# ─────────────────────────────────────────────────────────────────────────────
# BlockWriter
# ─────────────────────────────────────────────────────────────────────────────

class BlockWriter(SaveFrameWriter):
    """
    Write handle for one CifBlock.  Obtained from CifWriter.add_block() or
    CifWriter.get_block().
    """

    def __init__(self, block: CifBlock, version: CifVersion) -> None:
        super().__init__(block, version)
        self._block = block

    # ── Covariant return overrides ────────────────────────────────────────────

    def set_tag(self, tag: str, value: CifInput) -> 'BlockWriter':
        super().set_tag(tag, value)
        return self

    def add_loop(self, columns: dict[str, list[CifInput]]) -> 'BlockWriter':
        super().add_loop(columns)
        return self

    def add_loop_column(self, loop_tag: str, new_tag: str, values: list[CifInput]) -> 'BlockWriter':
        super().add_loop_column(loop_tag, new_tag, values)
        return self

    def reorder_loop_tags(self, loop_tag: str, new_order: list[str]) -> 'BlockWriter':
        super().reorder_loop_tags(loop_tag, new_order)
        return self

    def add_loop_row(self, loop_tag: str, row: list[CifInput]) -> 'BlockWriter':
        super().add_loop_row(loop_tag, row)
        return self

    def reassign_tag(self, tag: str, value: 'CifInput | list[CifInput]') -> 'BlockWriter':
        super().reassign_tag(tag, value)
        return self

    def delete_tag(self, tag: str) -> 'BlockWriter':
        super().delete_tag(tag)
        return self

    def remove_loop_tag(self, loop_tag: str, tag_to_remove: str) -> 'BlockWriter':
        super().remove_loop_tag(loop_tag, tag_to_remove)
        return self

    # ── Save frame management ─────────────────────────────────────────────────

    def add_save_frame(self, name: str) -> SaveFrameWriter:
        _check_name(name, self._version, "save-frame")
        if name in self._block._save_frames:
            raise ValueError(
                f"Save frame {name!r} already exists in block {self._block.name!r}"
            )
        frame = CifSaveFrame(name)
        self._block._add_save_frame(frame)
        return SaveFrameWriter(frame, self._version)

    def get_save_frame(self, name: str, index: int = 0) -> SaveFrameWriter:
        matches = [sf for sf in self._block._save_frame_list if sf.name == name]
        if not matches:
            raise KeyError(name)
        return SaveFrameWriter(matches[index], self._version)

    def remove_save_frame(self, name: str, *, from_end: bool = False) -> 'BlockWriter':
        if name not in self._block._save_frames:
            raise KeyError(name)
        lst = self._block._save_frame_list
        if from_end:
            idx = max(i for i, sf in enumerate(lst) if sf.name == name)
        else:
            idx = next(i for i, sf in enumerate(lst) if sf.name == name)
        lst.pop(idx)
        survivors = [sf for sf in lst if sf.name == name]
        if survivors:
            self._block._save_frames[name] = survivors[0]
        else:
            del self._block._save_frames[name]
        return self

    def rename_save_frame(self, old_name: str, new_name: str) -> 'BlockWriter':
        if old_name not in self._block._save_frames:
            raise KeyError(old_name)
        _check_name(new_name, self._version, "save-frame")
        if new_name in self._block._save_frames:
            raise ValueError(
                f"Save frame {new_name!r} already exists in block {self._block.name!r}"
            )
        frame = self._block._save_frames.pop(old_name)
        frame.name = new_name
        self._block._save_frames[new_name] = frame
        return self


# ─────────────────────────────────────────────────────────────────────────────
# CifWriter
# ─────────────────────────────────────────────────────────────────────────────

class CifWriter:
    """File-level container for programmatic CIF construction."""

    def __init__(
        self,
        version: CifVersion,
        cif: CifFile | None = None,
    ) -> None:
        self._version = version
        if cif is not None:
            if cif.version == CifVersion.CIF_2_0 and version == CifVersion.CIF_1_1:
                warnings.warn(
                    "Wrapping a CIF 2.0 file with a CIF 1.1 writer — existing "
                    "CIF 2.0 constructs will not be removed but new ones will be rejected.",
                    UserWarning,
                    stacklevel=2,
                )
            self._file = cif
        else:
            self._file = CifFile(version=version)

    @property
    def version(self) -> CifVersion:
        return self._version

    # ── Read access ───────────────────────────────────────────────────────────

    def __getitem__(self, name: str) -> CifBlock:
        return self._file[name]

    def __contains__(self, name: str) -> bool:
        return name in self._file

    def get(self, name: str, default: CifBlock | None = None) -> CifBlock | None:
        if name in self._file:
            return self._file[name]
        return default

    # ── Block management ──────────────────────────────────────────────────────

    def add_block(self, name: str) -> BlockWriter:
        _check_name(name, self._version, "block")
        if name in self._file:
            raise ValueError(f"Block {name!r} already exists in this CifWriter")
        block = CifBlock(name)
        self._file._add_block(block)
        return BlockWriter(block, self._version)

    def get_block(self, name: str, index: int = 0) -> BlockWriter:
        matches = [b for b in self._file._block_list if b.name == name]
        if not matches:
            raise KeyError(name)
        return BlockWriter(matches[index], self._version)

    def remove_block(self, name: str, *, from_end: bool = False) -> 'CifWriter':
        if name not in self._file:
            raise KeyError(name)
        lst = self._file._block_list
        if from_end:
            idx = max(i for i, b in enumerate(lst) if b.name == name)
        else:
            idx = next(i for i, b in enumerate(lst) if b.name == name)
        lst.pop(idx)
        survivors = [b for b in lst if b.name == name]
        if survivors:
            self._file._blocks[name] = survivors[0]
        else:
            del self._file._blocks[name]
        return self

    def rename_block(self, old_name: str, new_name: str) -> 'CifWriter':
        if old_name not in self._file:
            raise KeyError(old_name)
        _check_name(new_name, self._version, "block")
        if new_name in self._file:
            raise ValueError(f"Block {new_name!r} already exists in this CifWriter")
        block = self._file._blocks.pop(old_name)
        block.name = new_name
        self._file._blocks[new_name] = block
        return self

    # ── Result ────────────────────────────────────────────────────────────────

    def build(self) -> CifFile:
        if not self._file._block_list:
            raise ValueError("CifFile must contain at least one block")
        errors: list[str] = []
        for block in self._file._block_list:
            _validate_namespace(block, self._version, errors)
            for sf in block._save_frame_list:
                _validate_namespace(sf, self._version, errors)
        if errors:
            raise ValueError(
                "CifWriter.build() validation failed:\n" + "\n".join(errors)
            )
        return self._file


# ─────────────────────────────────────────────────────────────────────────────
# build() validation helper
# ─────────────────────────────────────────────────────────────────────────────

def _has_container(value: CifValue) -> bool:
    if isinstance(value, list):
        return True
    if isinstance(value, dict):
        return True
    return False


def _validate_namespace(
    ns: CifSaveFrame,
    version: CifVersion,
    errors: list[str],
) -> None:
    loop_tags: set[str] = {tag for loop in ns._loops for tag in loop}

    for loop in ns._loops:
        lengths = [len(ns._tags.get(t, [])) for t in loop]
        if len(set(lengths)) > 1:
            errors.append(
                f"Loop in {ns.name!r} has unequal column lengths: "
                f"{dict(zip(loop, lengths))}"
            )
        elif lengths and lengths[0] == 0:
            errors.append(f"Loop in {ns.name!r} has zero rows: {loop!r}")

    for tag in ns._tag_order:
        if tag not in loop_tags:
            n = len(ns._tags.get(tag, []))
            if n != 1:
                errors.append(
                    f"Scalar tag {tag!r} in {ns.name!r} has {n} value(s) (expected 1)"
                )

    if version == CifVersion.CIF_1_1:
        for tag, vals in ns._tags.items():
            for v in vals:
                if _has_container(v):
                    errors.append(
                        f"Tag {tag!r} in {ns.name!r} contains a list or dict "
                        f"value, which is not valid in CIF 1.1"
                    )
                    break
