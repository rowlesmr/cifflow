"""
Type stubs for the pycifparse_core Rust extension module.

Two entry points are exposed:

parse      — streaming path; calls Python CifParserEvents callbacks as tokens
             are consumed.  Used by CifBuilder for programmatic construction.
parse_raw  — zero-callback path; parses entirely in Rust and returns a single
             Python dict.  Used by build() for maximum throughput.
"""

from typing import Any

from pycifparse.types import CifVersion

def parse(
    source: str,
    handler: Any,
) -> CifVersion:
    """
    Parse *source* CIF text, firing CifParserEvents callbacks on *handler*.

    Returns the detected CifVersion.  All parser and lexer errors are
    delivered via ``handler.on_error(ParseError)``.
    """
    ...

def parse_raw(
    source: str,
    mode: str | None = None,
) -> dict[str, Any]:
    """
    Parse *source* CIF text entirely in Rust with no Python callbacks.

    *mode* is ``'pad'`` (default) or ``'strict'``.

    Returns a dict::

        {
            "version": str,          # "CIF_1_1" or "CIF_2_0"
            "errors": [              # parser + semantic errors in emission order
                {
                    "error_type":      str,   # "lexical" | "syntactic" | "semantic"
                    "message":         str,
                    "line":            int,
                    "column":          int,
                    "context":         str,
                    "recovery_action": str,
                },
                ...
            ],
            "blocks": [
                {
                    "name":       str,
                    "tag_order":  list[str],
                    "loops":      list[list[str]],
                    "tags":       dict[str, list],   # scalar values are (str, str) tuples
                    "save_frames": [
                        {
                            "name":      str,
                            "tag_order": list[str],
                            "loops":     list[list[str]],
                            "tags":      dict[str, list],
                        },
                        ...
                    ],
                },
                ...
            ],
        }

    Scalar tag values are stored as ``(value_str, value_type_name)`` 2-tuples
    so that ``CifSaveFrame.__getitem__`` can reconstruct ``CifScalar`` objects
    lazily on first access.  Container values (CIF lists and tables) are stored
    as plain Python ``list`` / ``dict`` with ``str`` leaf values.
    """
    ...
