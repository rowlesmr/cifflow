"""
Type stubs for the cifflow_core Rust extension module.

Five entry points are exposed:

parse            — streaming path; calls Python CifParserEvents callbacks as
                   tokens are consumed.  Used by CifBuilder.
parse_raw        — zero-callback path; returns a single Python dict.
parse_cif        — zero-callback path; returns CifFile directly.  Used by build().
parse_arrow      — zero-callback path; returns pa.RecordBatch list directly
                   (no IPC round-trip).  Used by build_arrow().
parse_arrow_file — same as parse_arrow but reads the file path in Rust.
                   Used by build_arrow_file().

Three PyO3 model types are also exposed:

CifSaveFrame, CifBlock, CifFile — the CIF model types backed by Rust data.
"""

from typing import Any

import pyarrow as pa

from cifflow.types import CifVersion


class CifSaveFrame:
    """A save_ frame backed by a PyO3 Rust struct."""

    name: str
    _id: int
    _tags: dict[str, list]       # live Python dict — mutations reflected in Rust
    _tag_order: list[str]        # live Python list
    _loops: list[list[str]]      # live Python list

    def __init__(self, name: str, id: int = 0) -> None: ...
    def __getitem__(self, key: str) -> list: ...
    def __contains__(self, key: str) -> bool: ...

    @property
    def tags(self) -> list[str]: ...
    @property
    def loops(self) -> list[list[str]]: ...

    def _append_value(self, tag: str, value: Any) -> None: ...
    def _add_loop(self, tags: list[str], buffers: dict[str, list]) -> None: ...


class CifBlock:
    """A data_ block backed by a PyO3 Rust struct."""

    name: str
    _id: int
    _tags: dict[str, list]
    _tag_order: list[str]
    _loops: list[list[str]]
    _save_frames: dict[str, CifSaveFrame]
    _save_frame_list: list[CifSaveFrame]

    def __init__(self, name: str, id: int = 0) -> None: ...
    def __getitem__(self, key: str) -> list | CifSaveFrame: ...
    def __contains__(self, key: str) -> bool: ...

    @property
    def tags(self) -> list[str]: ...
    @property
    def loops(self) -> list[list[str]]: ...
    @property
    def save_frames(self) -> list[str]: ...

    def get_all(self, name: str) -> list[CifSaveFrame]: ...
    def _append_value(self, tag: str, value: Any) -> None: ...
    def _add_loop(self, tags: list[str], buffers: dict[str, list]) -> None: ...
    def _add_save_frame(self, frame: CifSaveFrame) -> bool: ...


class CifFile:
    """Top-level CIF container backed by a PyO3 Rust struct."""

    _blocks: dict[str, CifBlock]
    _block_list: list[CifBlock]

    def __init__(self, version: CifVersion | None = None) -> None: ...
    def __getitem__(self, name: str) -> CifBlock: ...
    def __contains__(self, name: str) -> bool: ...

    @property
    def version(self) -> CifVersion: ...
    @version.setter
    def version(self, v: CifVersion) -> None: ...
    @property
    def blocks(self) -> list[str]: ...

    def get_all(self, name: str) -> list[CifBlock]: ...
    def _add_block(self, block: CifBlock) -> bool: ...
    def deepcopy(self) -> CifFile: ...


def parse(
    source: str,
    handler: Any,
) -> CifVersion:
    """
    Parse *source* CIF text, firing CifParserEvents callbacks on *handler*.
    Returns the detected CifVersion.
    """
    ...


def parse_raw(
    source: str,
    mode: str | None = None,
) -> dict[str, Any]:
    """
    Parse *source* entirely in Rust.  Returns a Python dict with keys
    ``"version"``, ``"errors"``, ``"blocks"``.
    """
    ...


def parse_cif(
    source: str,
    mode: str | None = None,
) -> tuple[CifFile, list[dict[str, Any]]]:
    """
    Parse *source* entirely in Rust.  Returns ``(CifFile, error_dicts)``
    with no intermediate Python dict.
    """
    ...


def parse_arrow(
    source: str,
    mode: str | None = None,
) -> tuple[list[pa.RecordBatch], list[dict[str, Any]]]:
    """
    Parse *source* entirely in Rust.  Returns ``(list[pa.RecordBatch], error_dicts)``
    with direct Arrow memory handoff — no IPC bytes.
    """
    ...


def parse_arrow_file(
    path: str,
    mode: str | None = None,
) -> tuple[list[pa.RecordBatch], list[dict[str, Any]]]:
    """
    Read the CIF file at *path* in Rust and return ``(list[pa.RecordBatch], error_dicts)``.
    No Python file objects or IPC bytes are created.
    """
    ...
