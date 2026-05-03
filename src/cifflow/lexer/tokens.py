from dataclasses import dataclass, field
from typing import List, Optional

from cifflow.types import TokenType, ValueType


@dataclass
class LexerError:
    message: str
    line: int
    column: int
    context: str


@dataclass
class Token:
    token_type: TokenType
    value: str
    value_type: Optional[ValueType]  # None for TAG and KEYWORD tokens
    line: int
    column: int
    errors: List[LexerError] = field(default_factory=list)
