"""
CIF in-memory model.

CifSaveFrame  — one save_ frame; tag/value/loop access
CifBlock      — one data_ block; extends CifSaveFrame with save frame access
CifFile       — top-level container; holds CifBlocks in file order

These types are implemented as PyO3 Rust classes in pycifparse_core and
re-exported here so that the rest of the codebase imports from this module.

Tag values are always stored as lists.  A scalar tag produces a one-element
list; a loop column produces a multi-element list.  Container values (lists
and tables) appear as Python list or dict elements within the list.

Missing tags and missing blocks raise KeyError.

Duplicate block and save frame names are preserved in file order.
``__getitem__`` returns the first match; ``get_all(name)`` returns all matches.
"""

from __future__ import annotations
from typing import Union

from pycifparse import pycifparse_core as _core

# Re-export the PyO3-backed types under their canonical names.
CifSaveFrame = _core.CifSaveFrame
CifBlock     = _core.CifBlock
CifFile      = _core.CifFile

# A CIF value stored in the model: scalar string, or a nested container.
CifValue = Union[str, list, dict]
