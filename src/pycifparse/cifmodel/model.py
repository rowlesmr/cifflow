"""
CIF in-memory model.

CifSaveFrame  — one save_ frame; tag/value/loop access
CifBlock      — one data_ block; extends CifSaveFrame with save frame access
CifFile       — top-level container; holds CifBlocks in file order

Tag values are always stored as lists.  A scalar tag produces a one-element
list; a loop column produces a multi-element list.  Container values (lists
and tables) appear as Python list or dict elements within the list.

Missing tags and missing blocks raise KeyError.
"""

from __future__ import annotations
from typing import Union

# A CIF value stored in the model: scalar string, or a nested container.
CifValue = Union[str, list, dict]


class CifSaveFrame:
    """A save_ frame.  Provides tag and loop access."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._tags: dict[str, list[CifValue]] = {}
        self._tag_order: list[str] = []
        self._loops: list[list[str]] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def __getitem__(self, key: str) -> list[CifValue]:
        try:
            return self._tags[key]
        except KeyError:
            raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return key in self._tags

    @property
    def tags(self) -> list[str]:
        """All tag names in insertion order."""
        return list(self._tag_order)

    @property
    def loops(self) -> list[list[str]]:
        """Each inner list is the ordered tag names for one loop."""
        return [list(loop) for loop in self._loops]

    # ── Internal mutation (used by CifBuilder only) ───────────────────────────

    def _append_value(self, tag: str, value: CifValue) -> None:
        if tag not in self._tags:
            self._tags[tag] = []
            self._tag_order.append(tag)
        self._tags[tag].append(value)

    def _add_loop(self, tags: list[str], buffers: dict[str, list[CifValue]]) -> None:
        self._loops.append(list(tags))
        for tag in tags:
            if tag not in self._tags:
                self._tag_order.append(tag)
            self._tags[tag] = list(buffers.get(tag, []))


class CifBlock(CifSaveFrame):
    """
    A data_ block.  Extends CifSaveFrame with save frame lookup.

    ``block["_tag"]``      → list of values for that tag  (KeyError if absent)
    ``block["save_name"]`` → CifSaveFrame                 (KeyError if absent)
    ``block.save_frames``  → list of save frame names in file order
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._save_frames: dict[str, CifSaveFrame] = {}
        self._save_frame_order: list[str] = []

    def __getitem__(self, key: str) -> Union[list[CifValue], CifSaveFrame]:
        if key.startswith('_'):
            try:
                return self._tags[key]
            except KeyError:
                raise KeyError(key)
        try:
            return self._save_frames[key]
        except KeyError:
            raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        if key.startswith('_'):
            return key in self._tags
        return key in self._save_frames

    @property
    def save_frames(self) -> list[str]:
        """Save frame names in file order."""
        return list(self._save_frame_order)

    def _add_save_frame(self, frame: CifSaveFrame) -> None:
        self._save_frames[frame.name] = frame
        self._save_frame_order.append(frame.name)


class CifFile:
    """Top-level container for a parsed CIF file."""

    def __init__(self) -> None:
        self._blocks: dict[str, CifBlock] = {}
        self._block_order: list[str] = []

    def __getitem__(self, name: str) -> CifBlock:
        try:
            return self._blocks[name]
        except KeyError:
            raise KeyError(name)

    def __contains__(self, name: str) -> bool:
        return name in self._blocks

    @property
    def blocks(self) -> list[str]:
        """Block names in file order."""
        return list(self._block_order)

    def _add_block(self, block: CifBlock) -> None:
        self._blocks[block.name] = block
        self._block_order.append(block.name)
