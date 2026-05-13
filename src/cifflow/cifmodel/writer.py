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

import unicodedata
import warnings
from typing import Union


def _casefold(s: str) -> str:
    """Apply Unicode NFC(casefold(NFD)) normalisation for canonical caseless comparison."""
    return unicodedata.normalize('NFC', unicodedata.normalize('NFD', s).casefold())

from cifflow.types import CifVersion
from cifflow.cifmodel.model import CifBlock, CifFile, CifSaveFrame, CifValue

# Recursive type alias (documentation only — Python does not enforce it at runtime)
# CifInput = int | float | str | list[CifInput] | dict[str, CifInput]
CifInput = Union[int, float, str, list, dict]


# ─────────────────────────────────────────────────────────────────────────────
# Naming validation
# ─────────────────────────────────────────────────────────────────────────────

def _is_legal_name(name: str, version: CifVersion) -> bool:
    """Return True if name contains only characters legal for the given CIF version."""
    if not name:
        return False
    if version == CifVersion.CIF_1_1:
        return all(0x21 <= ord(c) <= 0x7E for c in name)
    return all(not c.isspace() and c.isprintable() for c in name)


def _check_name(name: str, version: CifVersion, kind: str) -> None:
    """Raise ValueError if name is not a legal CIF identifier for the given version."""
    if not _is_legal_name(name, version):
        raise ValueError(
            f"Illegal CIF {kind} name {name!r} for version {version.value}"
        )


def _check_tag(tag: str, version: CifVersion) -> None:
    """Raise ValueError if tag does not start with '_' or is not a legal CIF name."""
    if not tag.startswith('_'):
        raise ValueError(f"Tag name must start with '_': {tag!r}")
    _check_name(tag[1:], version, "tag")


# ─────────────────────────────────────────────────────────────────────────────
# ValueType inference
# ─────────────────────────────────────────────────────────────────────────────

def _infer(value: CifInput) -> CifValue:
    """Convert a CifInput value to its CifValue representation."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_infer(e) for e in value]
    if isinstance(value, dict):
        return {k: _infer(v) for k, v in value.items()}
    raise TypeError(f"Unsupported CifInput type: {type(value)!r}")


def _infer_column(values: list) -> list[CifValue]:
    """Convert a list of CifInput values to a list of CifValues."""
    return [_infer(v) for v in values]


# ─────────────────────────────────────────────────────────────────────────────
# CIF 1.1 container check (used at build() time)
# ─────────────────────────────────────────────────────────────────────────────

def _contains_container(value: CifValue) -> bool:
    """Return True if value is a list or dict (CIF 2.0 container)."""
    if isinstance(value, list):
        return True
    if isinstance(value, dict):
        return True
    return False


def _any_container_in_values(values: list[CifValue]) -> bool:
    """Return True if any value in the list is a CIF 2.0 container."""
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
    """Return the index of the loop containing loop_tag; raise KeyError if not found."""
    for i, loop in enumerate(ns._loops):
        if loop_tag in loop:
            return i
    raise KeyError(loop_tag)


def _loop_row_count(ns: CifSaveFrame, loop_index: int) -> int:
    """Return the number of rows in the loop at the given index."""
    tags = ns._loops[loop_index]
    if not tags:
        return 0
    return len(ns._tags.get(tags[0], []))


# ─────────────────────────────────────────────────────────────────────────────
# SaveFrameWriter
# ─────────────────────────────────────────────────────────────────────────────

class SaveFrameWriter:
    """Write handle for one CifSaveFrame; obtained via BlockWriter.add_save_frame or get_save_frame."""

    def __init__(self, ns: CifSaveFrame, version: CifVersion) -> None:
        self._ns = ns
        self._version = version

    def _tag_in_any_loop(self, tag: str) -> bool:
        """Return True if tag appears in any loop in this namespace."""
        return any(tag in loop for loop in self._ns._loops)

    # ── Inspection ────────────────────────────────────────────────────────────

    @property
    def tags(self) -> list[str]:
        """Ordered list of tag names present in this namespace."""
        return self._ns.tags

    @property
    def loops(self) -> list[list[str]]:
        """Loop definitions as a list of tag-name groups."""
        return self._ns.loops

    def __getitem__(self, tag: str) -> list:
        return self._ns[_casefold(tag)]

    def get(self, tag: str, default: list | None = None) -> list | None:
        """Return the value list for tag, or default if the tag is absent."""
        try:
            return self._ns[_casefold(tag)]
        except KeyError:
            return default

    # ── Tag / scalar values ───────────────────────────────────────────────────

    def set_tag(self, tag: str, value: CifInput) -> 'SaveFrameWriter':
        """
        Add a new scalar tag with the given value.

        Parameters
        ----------
        tag
            Fully qualified CIF tag name, e.g. ``_cell.length_a``.
        value
            Scalar value or CIF 2.0 container (list or dict).

        Returns
        -------
        SaveFrameWriter
            Returns self for method chaining.

        Raises
        ------
        ValueError
            If the tag name is not a legal CIF identifier for the current
            version, or if the tag already exists in this namespace.
        """
        _check_tag(tag, self._version)
        tag = _casefold(tag)
        if tag in self._ns._tags:
            raise ValueError(
                f"Tag {tag!r} already exists in namespace {self._ns.name!r}"
            )
        self._ns._append_value(tag, _infer(value))
        return self

    # ── Loop values ───────────────────────────────────────────────────────────

    def add_loop(self, columns: dict[str, list[CifInput]]) -> 'SaveFrameWriter':
        """
        Add a new loop with the given columns.

        Parameters
        ----------
        columns
            Mapping of tag name to value list.  All lists must have the same
            length and no tag may already exist in this namespace.

        Returns
        -------
        SaveFrameWriter
            Returns self for method chaining.

        Raises
        ------
        ValueError
            If ``columns`` is empty, any tag name is not a legal CIF identifier,
            any tag already exists, or column lengths differ.
        """
        if not columns:
            raise ValueError("columns must not be empty")
        for tag in columns:
            _check_tag(tag, self._version)
        columns = {_casefold(k): v for k, v in columns.items()}
        tags = list(columns.keys())
        for tag in tags:
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
        """
        Append a new column to an existing loop.

        Parameters
        ----------
        loop_tag
            Any tag already in the target loop (used to identify it).
            Raises ``KeyError`` if not found in any loop.
        new_tag
            Tag name for the new column.
        values
            Value list; length must equal the loop's current row count.

        Returns
        -------
        SaveFrameWriter
            Returns self for method chaining.

        Raises
        ------
        ValueError
            If ``new_tag`` is not a legal CIF identifier, ``new_tag`` already
            exists, or ``values`` length does not match the loop row count.
        """
        loop_idx = _find_loop_index(self._ns, _casefold(loop_tag))
        _check_tag(new_tag, self._version)
        new_tag = _casefold(new_tag)
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
        """
        Reorder the columns of an existing loop.

        Parameters
        ----------
        loop_tag
            Any tag already in the target loop (used to identify it).
            Raises ``KeyError`` if not found in any loop.
        new_order
            Complete list of tag names in the desired order; must be a
            permutation of the loop's current tag list.

        Returns
        -------
        SaveFrameWriter
            Returns self for method chaining.

        Raises
        ------
        ValueError
            If ``new_order`` is not a permutation of the current loop tags.
        """
        loop_idx = _find_loop_index(self._ns, _casefold(loop_tag))
        new_order = [_casefold(t) for t in new_order]
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
        """Return a copy of the ordered tag list for the loop containing loop_tag.

        Parameters
        ----------
        loop_tag
            Any tag already in the target loop (used to identify it).

        Returns
        -------
        list[str]
            Ordered list of tag names in the loop.  Raises ``KeyError`` if
            ``loop_tag`` is not found in any loop.
        """
        loop_idx = _find_loop_index(self._ns, _casefold(loop_tag))
        return list(self._ns._loops[loop_idx])

    def add_loop_row(
        self,
        loop_tag: str,
        row: list[CifInput],
    ) -> 'SaveFrameWriter':
        """
        Append one row to an existing loop.

        Parameters
        ----------
        loop_tag
            Any tag already in the target loop (used to identify it).
            Raises ``KeyError`` if not found in any loop.
        row
            Ordered list of values matching the loop's tag order.

        Returns
        -------
        SaveFrameWriter
            Returns self for method chaining.

        Raises
        ------
        ValueError
            If ``row`` length does not match the loop's tag count.
        """
        loop_idx = _find_loop_index(self._ns, _casefold(loop_tag))
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
        """
        Replace the value(s) of an existing tag.

        Parameters
        ----------
        tag
            Tag name to update.
        value
            New value.  For loop columns, pass a list of values with the same
            length as the loop row count.

        Returns
        -------
        SaveFrameWriter
            Returns self for method chaining.

        Raises
        ------
        KeyError
            If the tag does not exist in this namespace.
        ValueError
            If the tag is a loop column and ``value`` is not a list.
        """
        tag = _casefold(tag)
        if tag not in self._ns._tags:
            raise KeyError(tag)
        if self._tag_in_any_loop(tag):
            if not isinstance(value, list):
                raise ValueError(
                    f"Tag {tag!r} is a loop column; value must be a list"
                )
            self._ns._tags[tag] = _infer_column(value)
        else:
            if isinstance(value, list) and len(value) == 1:
                value = value[0]
            self._ns._tags[tag] = [_infer(value)]
        return self

    def delete_tag(self, tag: str) -> 'SaveFrameWriter':
        """
        Delete a tag; removes it from its loop if it is a loop column.

        Parameters
        ----------
        tag
            Tag name to delete.

        Returns
        -------
        SaveFrameWriter
            Returns self for method chaining.

        Raises
        ------
        KeyError
            If the tag does not exist in this namespace.
        """
        tag = _casefold(tag)
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
        """
        Remove one tag from a loop, deleting the loop entirely if it becomes empty.

        Parameters
        ----------
        loop_tag
            Any tag already in the target loop (used to identify it).
        tag_to_remove
            Tag name to remove from the loop.

        Returns
        -------
        SaveFrameWriter
            Returns self for method chaining.

        Raises
        ------
        KeyError
            If ``loop_tag`` is not found in any loop, or if ``tag_to_remove``
            is not in the identified loop.
        """
        loop_idx = _find_loop_index(self._ns, _casefold(loop_tag))
        tag_to_remove = _casefold(tag_to_remove)
        if tag_to_remove not in self._ns._loops[loop_idx]:
            raise KeyError(tag_to_remove)
        self._remove_loop_tag_impl(loop_idx, tag_to_remove)
        return self

    def _remove_loop_tag_impl(self, loop_idx: int, tag: str) -> None:
        """Remove tag from loop and tags dicts; delete the loop list if now empty."""
        self._ns._loops[loop_idx].remove(tag)
        del self._ns._tags[tag]
        self._ns._tag_order.remove(tag)
        if not self._ns._loops[loop_idx]:
            del self._ns._loops[loop_idx]


# ─────────────────────────────────────────────────────────────────────────────
# BlockWriter
# ─────────────────────────────────────────────────────────────────────────────

class BlockWriter(SaveFrameWriter):
    """Write handle for one CifBlock; obtained via CifWriter.add_block or get_block."""

    def __init__(self, block: CifBlock, version: CifVersion) -> None:
        super().__init__(block, version)
        self._block = block

    # ── Covariant return overrides ────────────────────────────────────────────

    def set_tag(self, tag: str, value: CifInput) -> 'BlockWriter':
        """Add a new scalar tag; returns BlockWriter for method chaining."""
        super().set_tag(tag, value)
        return self

    def add_loop(self, columns: dict[str, list[CifInput]]) -> 'BlockWriter':
        """Add a new loop; returns BlockWriter for method chaining."""
        super().add_loop(columns)
        return self

    def add_loop_column(self, loop_tag: str, new_tag: str, values: list[CifInput]) -> 'BlockWriter':
        """Append a column to an existing loop; returns BlockWriter for method chaining."""
        super().add_loop_column(loop_tag, new_tag, values)
        return self

    def reorder_loop_tags(self, loop_tag: str, new_order: list[str]) -> 'BlockWriter':
        """Reorder loop columns; returns BlockWriter for method chaining."""
        super().reorder_loop_tags(loop_tag, new_order)
        return self

    def add_loop_row(self, loop_tag: str, row: list[CifInput]) -> 'BlockWriter':
        """Append a loop row; returns BlockWriter for method chaining."""
        super().add_loop_row(loop_tag, row)
        return self

    def reassign_tag(self, tag: str, value: 'CifInput | list[CifInput]') -> 'BlockWriter':
        """Replace a tag value; returns BlockWriter for method chaining."""
        super().reassign_tag(tag, value)
        return self

    def delete_tag(self, tag: str) -> 'BlockWriter':
        """Delete a tag; returns BlockWriter for method chaining."""
        super().delete_tag(tag)
        return self

    def remove_loop_tag(self, loop_tag: str, tag_to_remove: str) -> 'BlockWriter':
        """Remove a loop column; returns BlockWriter for method chaining."""
        super().remove_loop_tag(loop_tag, tag_to_remove)
        return self

    # ── Inspection ────────────────────────────────────────────────────────────

    @property
    def save_frames(self) -> list[str]:
        """Ordered list of save frame names in this block."""
        return self._block.save_frames

    # ── Save frame management ─────────────────────────────────────────────────

    def add_save_frame(self, name: str) -> SaveFrameWriter:
        """
        Add a new save frame to this block and return its writer.

        Parameters
        ----------
        name
            Save frame name (without the ``save_`` prefix).

        Returns
        -------
        SaveFrameWriter
            Writer handle for the new save frame.

        Raises
        ------
        ValueError
            If ``name`` is not a legal CIF identifier for the current version,
            or if a save frame with that name already exists in the block.
        """
        _check_name(name, self._version, "save-frame")
        name = _casefold(name)
        if name in self._block._save_frames:
            raise ValueError(
                f"Save frame {name!r} already exists in block {self._block.name!r}"
            )
        frame = CifSaveFrame(name)
        self._block._add_save_frame(frame)
        return SaveFrameWriter(frame, self._version)

    def get_save_frame(self, name: str, index: int = 0) -> SaveFrameWriter:
        """
        Return a writer handle for an existing save frame.

        Parameters
        ----------
        name
            Save frame name to look up (case-insensitive).
        index
            Which occurrence to return when there are duplicate names; 0-based.

        Returns
        -------
        SaveFrameWriter
            Writer handle for the requested save frame.

        Raises
        ------
        KeyError
            If no save frame with that name exists in the block.
        """
        name = _casefold(name)
        matches = [sf for sf in self._block._save_frame_list if sf.name == name]
        if not matches:
            raise KeyError(name)
        return SaveFrameWriter(matches[index], self._version)

    def remove_save_frame(self, name: str, *, from_end: bool = False) -> 'BlockWriter':
        """
        Remove one save frame from this block.

        Parameters
        ----------
        name
            Save frame name to remove (case-insensitive).
        from_end
            If ``True``, remove the last occurrence when names are duplicated;
            otherwise remove the first occurrence.

        Returns
        -------
        BlockWriter
            Returns self for method chaining.

        Raises
        ------
        KeyError
            If no save frame with that name exists in the block.
        """
        name = _casefold(name)
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
        """
        Rename a save frame.

        Parameters
        ----------
        old_name
            Current save frame name (case-insensitive).
        new_name
            New save frame name.

        Returns
        -------
        BlockWriter
            Returns self for method chaining.

        Raises
        ------
        KeyError
            If ``old_name`` does not exist in the block.
        ValueError
            If ``new_name`` is not a legal CIF identifier for the current
            version, or if ``new_name`` already exists in the block.
        """
        old_name = _casefold(old_name)
        if old_name not in self._block._save_frames:
            raise KeyError(old_name)
        _check_name(new_name, self._version, "save-frame")
        new_name = _casefold(new_name)
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
    """
    File-level container for programmatic CIF construction.

    Parameters
    ----------
    version
        CIF specification version to validate new names and values against.
    cif
        Existing :class:`~cifflow.cifflow_core.CifFile` to wrap for editing.
        If ``None``, a new empty :class:`~cifflow.cifflow_core.CifFile` is created.
    """

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
        """CIF specification version this writer validates new names against."""
        return self._version

    # ── Inspection ────────────────────────────────────────────────────────────

    @property
    def blocks(self) -> list[str]:
        """Ordered list of block names in this CifWriter."""
        return self._file.blocks

    # ── Read access ───────────────────────────────────────────────────────────

    def __getitem__(self, name: str) -> CifBlock:
        return self._file[name]

    def __contains__(self, name: str) -> bool:
        return name in self._file

    def get(self, name: str, default: CifBlock | None = None) -> CifBlock | None:
        """Return the CifBlock for name, or default if the block is absent."""
        if name in self._file:
            return self._file[name]
        return default

    # ── Block management ──────────────────────────────────────────────────────

    def add_block(self, name: str) -> BlockWriter:
        """
        Add a new block to this CifFile and return its writer.

        Parameters
        ----------
        name
            Block name (without the ``data_`` prefix).

        Returns
        -------
        BlockWriter
            Writer handle for the new block.

        Raises
        ------
        ValueError
            If ``name`` is not a legal CIF identifier for the current version,
            or if a block with that name already exists.
        """
        _check_name(name, self._version, "block")
        name = _casefold(name)
        if name in self._file:
            raise ValueError(f"Block {name!r} already exists in this CifWriter")
        block = CifBlock(name)
        self._file._add_block(block)
        return BlockWriter(block, self._version)

    def get_block(self, name: str, index: int = 0) -> BlockWriter:
        """
        Return a writer handle for an existing block.

        Parameters
        ----------
        name
            Block name to look up (case-insensitive).
        index
            Which occurrence to return when names are duplicated; 0-based.

        Returns
        -------
        BlockWriter
            Writer handle for the requested block.

        Raises
        ------
        KeyError
            If no block with that name exists.
        """
        name = _casefold(name)
        matches = [b for b in self._file._block_list if b.name == name]
        if not matches:
            raise KeyError(name)
        return BlockWriter(matches[index], self._version)

    def remove_block(self, name: str, *, from_end: bool = False) -> 'CifWriter':
        """
        Remove one block from this CifFile.

        Parameters
        ----------
        name
            Block name to remove (case-insensitive).
        from_end
            If ``True``, remove the last occurrence when names are duplicated;
            otherwise remove the first occurrence.

        Returns
        -------
        CifWriter
            Returns self for method chaining.

        Raises
        ------
        KeyError
            If no block with that name exists.
        """
        name = _casefold(name)
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
        """
        Rename a block.

        Parameters
        ----------
        old_name
            Current block name (case-insensitive).
        new_name
            New block name.

        Returns
        -------
        CifWriter
            Returns self for method chaining.

        Raises
        ------
        KeyError
            If ``old_name`` does not exist.
        ValueError
            If ``new_name`` is not a legal CIF identifier for the current
            version, or if ``new_name`` already exists.
        """
        old_name = _casefold(old_name)
        if old_name not in self._file:
            raise KeyError(old_name)
        _check_name(new_name, self._version, "block")
        new_name = _casefold(new_name)
        if new_name in self._file:
            raise ValueError(f"Block {new_name!r} already exists in this CifWriter")
        block = self._file._blocks.pop(old_name)
        block.name = new_name
        self._file._blocks[new_name] = block
        return self

    # ── Result ────────────────────────────────────────────────────────────────

    def build(self) -> CifFile:
        """
        Validate and return the completed CifFile.

        Returns
        -------
        CifFile
            The constructed CifFile, ready for ingestion or emission.

        Raises
        ------
        ValueError
            If the file is empty, any loop has unequal column lengths or zero
            rows, any scalar tag has a value count other than 1, or any tag
            holds a container value in CIF 1.1 mode.
        """
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
    """Return True if value is a CIF 2.0 list or dict container."""
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
    """Append validation error strings to errors for any structural problems in ns."""
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
