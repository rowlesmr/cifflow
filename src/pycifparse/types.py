from dataclasses import dataclass, field
from enum import Enum
from typing import List, Literal, Protocol


class CIFVersion(Enum):
    CIF_1_1 = "1.1"
    CIF_2_0 = "2.0"


class ValueType(Enum):
    MULTILINE_STRING     = "multiline_string"
    TRIPLE_DOUBLE_QUOTED = "triple_double_quoted"
    TRIPLE_SINGLE_QUOTED = "triple_single_quoted"
    DOUBLE_QUOTED        = "double_quoted"
    SINGLE_QUOTED        = "single_quoted"
    STRING               = "string"
    PLACEHOLDER          = "placeholder"


class TokenType(Enum):
    TAG     = "tag"
    KEYWORD = "keyword"
    VALUE   = "value"


@dataclass
class ParseError:
    error_type:      Literal["lexical", "syntactic", "semantic"]
    message:         str
    line:            int
    column:          int
    context:         str
    recovery_action: str


class CIFParserEvents(Protocol):
    def on_data_block(self, name: str) -> None: ...
    def on_save_frame_start(self, name: str) -> None: ...
    def on_save_frame_end(self) -> None: ...
    def add_tag(self, tag_name: str) -> None: ...
    def add_value(self, value: str, value_type: ValueType) -> None: ...
    def on_list_start(self) -> None: ...
    def on_list_end(self) -> None: ...
    def on_table_start(self) -> None: ...
    def on_table_end(self) -> None: ...
    def on_table_key(self, key: str, value_type: ValueType) -> None: ...
    def on_loop_start(self, tags: List[str]) -> None: ...
    def on_loop_end(self) -> None: ...
    def on_error(self, error: ParseError) -> None: ...
