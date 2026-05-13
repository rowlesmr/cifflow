"""Core types shared across all cifflow layers."""

from dataclasses import dataclass
from enum import Enum
from typing import List, Literal, Protocol


class CifVersion(Enum):
    r"""CIF specification version indicated by the ``#\CIF_x.x`` magic header.

    Attributes
    ----------
    CIF_1_1
        CIF 1.1 specification.
    CIF_2_0
        CIF 2.0 specification.
    """

    CIF_1_1 = "1.1"
    CIF_2_0 = "2.0"


class ValueType(Enum):
    """Lexer-assigned encoding category for a CIF value.

    Assigned by the lexer only; never modified downstream.

    Attributes
    ----------
    MULTILINE_STRING
        Semicolon-delimited multi-line text field.
    TRIPLE_DOUBLE_QUOTED
        Triple double-quoted string (CIF 2.0).
    TRIPLE_SINGLE_QUOTED
        Triple single-quoted string (CIF 2.0).
    DOUBLE_QUOTED
        Double-quoted string.
    SINGLE_QUOTED
        Single-quoted string.
    STRING
        Bare (unquoted) string.
    PLACEHOLDER
        Bare ``.`` (inapplicable) or ``?`` (unknown).
    """

    MULTILINE_STRING     = "multiline_string"
    TRIPLE_DOUBLE_QUOTED = "triple_double_quoted"
    TRIPLE_SINGLE_QUOTED = "triple_single_quoted"
    DOUBLE_QUOTED        = "double_quoted"
    SINGLE_QUOTED        = "single_quoted"
    STRING               = "string"
    PLACEHOLDER          = "placeholder"


class TokenType(Enum):
    """Lexer token classification used in parser trace events."""

    TAG     = "tag"
    KEYWORD = "keyword"
    VALUE   = "value"


@dataclass
class ParseError:
    """Parse error event emitted when the parser encounters malformed input.

    Attributes
    ----------
    error_type
        Category of error: ``"lexical"``, ``"syntactic"``, or ``"semantic"``.
    message
        Human-readable description of the error.
    line
        1-based line number where the error was detected.
    column
        1-based column number where the error was detected.
    context
        Raw source text surrounding the error location.
    recovery_action
        Description of how the parser recovered and continued.
    """

    error_type:      Literal["lexical", "syntactic", "semantic"]
    message:         str
    line:            int
    column:          int
    context:         str
    recovery_action: str


class CifParserEvents(Protocol):
    """Protocol that a CIF event handler must implement.

    The parser calls these methods in file order as tokens are consumed.
    Implement this protocol to accumulate CIF content from a stream of events.
    """

    def on_data_block(self, name: str) -> None:
        """Begin a new data block.

        Parameters
        ----------
        name
            The data block name (without the ``data_`` prefix).
        """
        ...

    def on_save_frame_start(self, name: str) -> None:
        """Begin a save frame.

        Parameters
        ----------
        name
            The save frame name (without the ``save_`` prefix).
        """
        ...

    def on_save_frame_end(self) -> None:
        """Close the current save frame."""
        ...

    def add_tag(self, tag_name: str) -> None:
        """Register a tag name; the next ``add_value`` call supplies its value.

        Parameters
        ----------
        tag_name
            Fully qualified CIF tag, e.g. ``_cell.length_a``.
        """
        ...

    def add_value(self, value: str, value_type: ValueType) -> None:
        """Supply the value for the most recently registered tag.

        Parameters
        ----------
        value
            Raw string value exactly as it appeared in the source file.
        value_type
            Lexer-assigned encoding category for the value.
        """
        ...

    def on_list_start(self) -> None:
        """Begin a CIF 2.0 list value."""
        ...

    def on_list_end(self) -> None:
        """Close the current CIF 2.0 list value."""
        ...

    def on_table_start(self) -> None:
        """Begin a CIF 2.0 table value."""
        ...

    def on_table_end(self) -> None:
        """Close the current CIF 2.0 table value."""
        ...

    def on_table_key(self, key: str, value_type: ValueType) -> None:
        """Supply the current table key; the next ``add_value`` call is its value.

        Parameters
        ----------
        key
            Raw key string as it appeared in the source file.
        value_type
            Lexer-assigned encoding category for the key.
        """
        ...

    def on_loop_start(self, tags: List[str]) -> None:
        """Begin a loop with the given ordered tag names.

        Parameters
        ----------
        tags
            Ordered list of fully qualified tag names in the loop header.
        """
        ...

    def on_loop_end(self) -> None:
        """Close the current loop."""
        ...

    def on_error(self, error: ParseError) -> None:
        """Deliver a parse error; the parser continues after recovery.

        Parameters
        ----------
        error
            Details of the error and the recovery action taken.
        """
        ...
