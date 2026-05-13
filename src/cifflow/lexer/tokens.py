"""Token and LexerError dataclasses produced by the CIF lexer."""

from dataclasses import dataclass, field
from typing import List, Optional

from cifflow.types import TokenType, ValueType


@dataclass
class LexerError:
    """A lexical error recorded on a Token."""

    message: str
    line: int
    column: int
    context: str


@dataclass
class Token:
    """A single token produced by the CIF lexer."""

    token_type: TokenType
    value: str
    value_type: Optional[ValueType]  # None for TAG and KEYWORD tokens
    line: int
    column: int
    errors: List[LexerError] = field(default_factory=list)
