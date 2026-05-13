"""Type stubs for the cifflow_core Rust extension module.

Five functions and three PyO3 model types are exposed. Import via the
top-level ``cifflow`` package, not from this module directly.
"""

from typing import Any

import pyarrow as pa

from cifflow.types import CifVersion


class CifSaveFrame:
    """A CIF save frame backed by a PyO3 Rust struct.

    Parameters
    ----------
    name
        Save frame name in canonical caseless form (without the ``save_`` prefix).
    id
        Internal numeric identifier; defaults to 0.

    Attributes
    ----------
    name
        Save frame name in canonical caseless form (without the ``save_`` prefix).
    """

    name: str
    _id: int
    _tags: dict[str, list]
    _tag_order: list[str]
    _loops: list[list[str]]

    def __init__(self, name: str, id: int = 0) -> None: ...
    def __getitem__(self, key: str) -> list: ...
    def __contains__(self, key: str) -> bool: ...

    @property
    def tags(self) -> list[str]:
        """Ordered list of tag names present in this save frame."""
        ...

    @property
    def loops(self) -> list[list[str]]:
        """Loop definitions as a list of tag-name groups."""
        ...

    def _append_value(self, tag: str, value: Any) -> None:
        """Append a value to an existing tag buffer."""
        ...

    def _add_loop(self, tags: list[str], buffers: dict[str, list]) -> None:
        """Register a loop definition with pre-populated value buffers."""
        ...


class CifBlock:
    """A CIF data block backed by a PyO3 Rust struct.

    Parameters
    ----------
    name
        Data block name in canonical caseless form (without the ``data_`` prefix).
    id
        Internal numeric identifier; defaults to 0.

    Attributes
    ----------
    name
        Data block name in canonical caseless form (without the ``data_`` prefix).
    """

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
    def tags(self) -> list[str]:
        """Ordered list of tag names present in this block."""
        ...

    @property
    def loops(self) -> list[list[str]]:
        """Loop definitions as a list of tag-name groups."""
        ...

    @property
    def save_frames(self) -> list[str]:
        """Ordered list of save frame names present in this block."""
        ...

    def get_all(self, name: str) -> list[CifSaveFrame]:
        """Return all save frames with the given name.

        Parameters
        ----------
        name
            Save frame name to look up (case-insensitive).

        Returns
        -------
        list[CifSaveFrame]
            All matching save frames; empty list if none found.
        """
        ...

    def _append_value(self, tag: str, value: Any) -> None:
        """Append a value to an existing tag buffer."""
        ...

    def _add_loop(self, tags: list[str], buffers: dict[str, list]) -> None:
        """Register a loop definition with pre-populated value buffers."""
        ...

    def _add_save_frame(self, frame: CifSaveFrame) -> bool:
        """Insert a save frame; return False if a duplicate name was detected."""
        ...


class CifFile:
    """Top-level CIF container backed by a PyO3 Rust struct.

    Parameters
    ----------
    version
        CIF specification version.  Detected automatically by the parser;
        pass ``None`` to leave unset until parsing begins.
    """

    _blocks: dict[str, CifBlock]
    _block_list: list[CifBlock]

    def __init__(self, version: CifVersion | None = None) -> None: ...
    def __getitem__(self, name: str) -> CifBlock: ...
    def __contains__(self, name: str) -> bool: ...

    @property
    def version(self) -> CifVersion:
        """CIF specification version detected from the magic header."""
        ...

    @version.setter
    def version(self, v: CifVersion) -> None: ...

    @property
    def blocks(self) -> list[str]:
        """Ordered list of data block names in canonical caseless form."""
        ...

    def get_all(self, name: str) -> list[CifBlock]:
        """Return all blocks with the given name.

        Parameters
        ----------
        name
            Block name to look up (case-insensitive).

        Returns
        -------
        list[CifBlock]
            All matching blocks; empty list if none found.
        """
        ...

    def _add_block(self, block: CifBlock) -> bool:
        """Insert a block; return False if a duplicate name was detected."""
        ...

    def deepcopy(self) -> CifFile:
        """Return a deep copy of this CifFile with independent Rust-owned data.

        Returns
        -------
        CifFile
            New CifFile containing independent copies of all blocks and save frames.
        """
        ...


def parse(
    source: str,
    handler: Any,
) -> CifVersion:
    """Parse CIF text, firing CifParserEvents callbacks on handler."""
    ...


def parse_raw(
    source: str,
    mode: str | None = None,
) -> dict[str, Any]:
    """Parse CIF text entirely in Rust, returning a raw Python dict."""
    ...


def parse_cif(
    source: str,
    mode: str | None = None,
) -> tuple[CifFile, list[dict[str, Any]]]:
    """Parse CIF text entirely in Rust and return a CifFile.

    Parameters
    ----------
    source
        Full CIF source text.
    mode
        Parse mode; ``None`` selects the default.

    Returns
    -------
    tuple[CifFile, list[dict[str, Any]]]
        A ``(CifFile, error_dicts)`` pair.  Each entry in ``error_dicts``
        is a dict with keys ``"error_type"``, ``"message"``, ``"line"``,
        ``"column"``, ``"context"``, and ``"recovery_action"``.
    """
    ...


def parse_arrow(
    source: str,
    mode: str | None = None,
) -> tuple[list[pa.RecordBatch], list[dict[str, Any]]]:
    """Parse CIF text in Rust and return Arrow RecordBatches with no IPC round-trip."""
    ...


def parse_arrow_file(
    path: str,
    mode: str | None = None,
) -> tuple[list[pa.RecordBatch], list[dict[str, Any]]]:
    """Read a CIF file in Rust and return Arrow RecordBatches with no Python file objects."""
    ...
