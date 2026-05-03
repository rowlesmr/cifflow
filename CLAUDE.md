# cifflow — Project Context

## Project Overview

A Python library for parsing, storing, and outputting Crystallographic Information Files (CIF).
The system is streaming, event-driven, dictionary-aware, and designed for correctness above all else.

Full specifications and future prompts are in the `prompts/` directory.
Reference material (CIF specifications, grammars, related papers) is in the `references/` directory.

### References

| File | Purpose |
|------|---------|
| `references/CIF2.0 specification.pdf` | Authoritative CIF 2.0 spec |
| `references/CIF1-1 specification.pdf` | Authoritative CIF 1.1 spec |
| `references/CIF1-1 File syntax.html` | CIF 1.1 file syntax detail |
| `references/CIF2-ENBF.txt` | CIF 2.0 EBNF grammar |
| `references/CIF1-1 grammar.txt` | CIF 1.1 grammar |
| `references/an error-correcting CIF1-1 parser.pdf` | Prior art: error-correcting CIF 1.1 parser |
| `references/example program that would use this API.pdf` | Target use case for this library |

When in doubt about CIF syntax or behaviour, consult the relevant reference before implementing.

---

## Architecture

```
Parser -> Event Stream -> IR -> Dictionary-aware Mapping -> SQLite -> Output/API
```

Layer responsibilities are strictly separated. This separation MUST be preserved throughout.

| Layer | Responsibility |
|-------|---------------|
| **Lexer** | Tokenisation, raw content preservation, ValueType and TokenType assignment only |
| **Parser** | Token sequence interpretation, event emission, stack state, error events |
| **IR** | Event accumulation, loop validation, multiline text transformation |
| **Dictionary** | DDLm parsing, schema derivation, semantic mapping |
| **SQLite** | Persistent storage; structured tables when a dictionary is present, fallback tier otherwise |
| **Output** | Valid CIF regeneration, Python/NumPy/pandas API |

Do not allow responsibilities to bleed between layers. If a proposed change would blur a boundary,
raise it explicitly before implementing.

---

## Non-Negotiable Constraints

These must never be violated under any circumstances:

1. No silent data loss
2. All parsed values emitted as raw strings
3. Event ordering must exactly match file order
4. Duplicate tag values must be preserved — never overwritten
5. Parser must not crash on malformed input
6. All malformed constructs must generate explicit on_error events
7. When no dictionary is provided, all tags are routed to the fallback tier (`_cif_fallback`); no data is discarded
8. The output layer must never emit invalid CIF

---

## Priorities (in order)

1. Correctness and data preservation
2. Error tolerance and recovery
3. Streaming / low-memory operation
4. Near-linear performance scaling
5. Grammar formality

Optimise only after correctness is established.

---

## Implementation Stages

Work incrementally. Produce working, testable code at each stage. Validate before advancing.

| Stage | Focus |
|-------|-------|
| 1 | CIF 2.0 parser (data blocks, tag-value, loops) then CIF 1.1/1.0 |
| 2 | Error handling and recovery; malformed structure support |
| 3 | IR implementation; parser to IR integration |
| 4 | DDLm dictionary parsing (Phase 1 only); SQLite schema generation |
| 5 | SQLite ingestion via dictionary-defined schema |
| 6+ | Dictionary Phase 2 (imports, metadictionaries); output layer; performance |

Implement CIF 2.0 first. The downstream pipeline (IR, SQLite, output) is shared across CIF versions.
DDL1 and DDL2 are out of scope.

---

## Version Detection

Version detection occurs before any tokens are consumed. The lexer is instantiated with the result.

Scan past leading whitespace-only lines to find the first non-whitespace candidate line.
Match against the file-heading grammar production:

```
file-heading = [ U+FEFF ], magic-code, { inline-wspace } ;
```

| Candidate line | Action |
|---|---|
| #\\#CIF_2.0 | CIF 2.0; consume line |
| #\\#CIF_1.1 | CIF 1.1; consume line |
| #\\#CIF_{anything} | on_error("unrecognised CIF version: {raw}"); default CIF 2.0; consume line |
| EOF before non-whitespace | CIF 1.1; no line consumed |
| Anything else | CIF 1.1; line left for normal processing |

- BOM (U+FEFF) and trailing inline whitespace are permitted on the magic line
- Magic lines appearing after the candidate position are plain comments; no re-evaluation
- Version is fixed at parse time and never changes mid-file
- Output MUST only emit #\\#CIF_2.0 or #\\#CIF_1.1

---

## Key Types

### ValueType (lexer-assigned only; never modified by any downstream layer)

```python
from enum import Enum

class ValueType(Enum):
    MULTILINE_STRING      = "multiline_string"      # <EOL>;...<EOL>;
    TRIPLE_DOUBLE_QUOTED  = "triple_double_quoted"  # """..."""  CIF 2.0 only
    TRIPLE_SINGLE_QUOTED  = "triple_single_quoted"  # '''...'''  CIF 2.0 only
    DOUBLE_QUOTED         = "double_quoted"          # "..."
    SINGLE_QUOTED         = "single_quoted"          # '...'
    STRING                = "string"                 # unquoted bare word
    PLACEHOLDER           = "placeholder"            # unquoted . or ?
```

Assignment rules:
- Unquoted . or ? -> PLACEHOLDER only
- Quoted . or ? -> appropriate quoted ValueType, NOT PLACEHOLDER
- Numeric values with or without SU (e.g. 12.34(5)) -> STRING
- Triple-quoted types are CIF 2.0 only; encountering them in CIF 1.x is a lexical error
- ValueType must survive round-tripping: PLACEHOLDER must never become a quoted string on output
- No layer other than the lexer may assign or modify ValueType

### TokenType (lexer-internal; not propagated to the event interface)

```python
class TokenType(Enum):
    TAG      = "tag"      # bare word beginning with _
    KEYWORD  = "keyword"  # data_, save_, loop_, stop_, global_  (case-insensitive)
    VALUE    = "value"    # everything else
```

- data_ and save_ are prefix keywords: the full raw token is emitted (e.g. data_my_block)
- Name suffix extraction is the parser's responsibility, not the lexer's
- _data_foo is always a TAG, never a keyword
- The lexer classifies tokens; it never validates syntactic position

### ParseError

```python
class ParseError:
    error_type: Literal["lexical", "syntactic", "semantic"]
    message: str
    line: int
    column: int
    context: str
    recovery_action: str
```

---

## Lexer State Machine

States:

```
NORMAL
SINGLE_QUOTED           terminated by closing '
DOUBLE_QUOTED           terminated by closing "
TRIPLE_SINGLE_QUOTED    terminated by '''   (CIF 2.0 only)
TRIPLE_DOUBLE_QUOTED    terminated by """   (CIF 2.0 only)
MULTILINE               terminated by ; at column 1
```

Key rules:
- Triple-quote disambiguation: on ' or " in NORMAL state, peek at next two chars;
  if all three are identical quote chars, enter the triple-quoted state
- Triple-quoted states are unreachable in CIF 1.x mode; encountering them is a lexical error
- A semicolon at column 1 inside a triple-quoted string is NOT a MULTILINE delimiter
- CIF 1.x character set restrictions are validated per-state;
  violations emit on_error without terminating the current string
- EOF in any string state: emit accumulated content as unterminated token,
  emit on_error("unterminated string"), terminate lexing
- A regex-based lexer is insufficient; use a line-aware or streaming implementation

---

## Event Interface

```python
from typing import List, Protocol

class CifParserEvents(Protocol):
    def on_data_block(self, name: str): ...
    def on_save_frame_start(self, name: str): ...
    def on_save_frame_end(self): ...
    def add_tag(self, tag_name: str): ...
    def add_value(self, value: str, value_type: ValueType): ...
    def on_list_start(self): ...
    def on_list_end(self): ...
    def on_table_start(self): ...
    def on_table_end(self): ...
    def on_table_key(self, key: str, value_type: ValueType): ...
    def on_loop_start(self, tags: List[str]): ...
    def on_loop_end(self): ...
    def on_error(self, error: ParseError): ...
```

---

## Critical Parser Rules

**Tags**
- One tag at a time via add_tag; tag stays active until its value or container is closed
- New tag while previous still active: on_error + assign ? placeholder to previous tag
- Loop encountered while tag active: on_error + ? placeholder, close tag, process loop

**Loops**
- on_loop_start(tags) collects all loop tags at once
- Values emitted flat between on_loop_start and on_loop_end; no column alignment enforced by parser
- on_loop_end emitted on: EOF, new tag, new loop, new save frame, new data block, STOP_
- If loop termination occurs while inside a container: implicitly close all open containers
  in LIFO order (emitting on_list_end/on_table_end + on_error for each), then emit on_loop_end
- Nested loops are non-recoverable: on_error, terminate cleanly
- Loop structural validation (row count) is IR responsibility only

**Lists and Tables**
- Arbitrarily nestable inside loops and each other
- Table keys MUST be quoted strings; unquoted key is a parse error (handle leniently)
- Table key not followed by value before next key: on_error + ? placeholder for that key
- Duplicate table keys: preserve in insertion order (same rule as duplicate tags)

**Save Frames**
- Cannot be nested; if a new one is encountered while one is open:
  on_error("nested save frame"), implicitly close current, start new
- Correctly terminated by: save_, EOF, or new data block

**Orphan Values** (values outside loops with no preceding tag)
- on_error + attach to synthetic tag _error_value
- _error_value is scoped to the current namespace and follows duplicate value semantics

**Keywords**
- global_ anywhere is fatal: on_error, stop parsing immediately
- Keyword in value position: on_error + ? placeholder, continue
- Quoted keywords are always VALUE tokens, never structural events

---

## IR Rules

- Schema-agnostic; must not depend on dictionary availability
- Incrementally constructed from parser events; optimised for low memory
- All values stored as strings; scalars stored as tag -> list[str]
- Loop value assignment: tag_index = value_index mod len(tags)
- Value index increments only on complete value (scalar add_value OR fully closed container)
- IR maintains its own container nesting depth to track container completion
- On on_loop_end: validate total value count is divisible by tag count; if not:
  - strict mode: emit error, stop
  - pad mode: emit warning, pad incomplete final row with ? placeholders

**Multiline text transformation pipeline (IR layer only; applies to MULTILINE_STRING only):**
1. Split into physical lines (preserve EOL semantics)
2. Apply prefix detection and removal (if applicable)
3. Apply line unfolding (if fold separators present)
4. Reconstruct final logical string

Fold separator detection MUST occur after prefix removal, operating on prefix-stripped lines.

---

## Round-Trip Fidelity

Round-tripping is defined as semantic fidelity, not textual fidelity.
Only guaranteed for input that produced no on_error events during parsing.

Permitted transformations:
- Comments stripped (discarded by parser; output layer has no access to them)
- Line ordering may differ, subject to canonical formatting rules
- Magic code normalised to canonical form
- Whitespace and quoting may be normalised

Must be preserved exactly:
- All data block and save frame names
- All tag names
- All values as raw strings
- ValueType provenance (PLACEHOLDER must remain unquoted on output)
- Loop structure and column order

Never permitted in output:
- Invalid CIF constructs
- Non-canonical magic codes
- Duplicate tags (error condition; file is not round-trippable if they are present)

---

## Project Roadmap

This prompt covers the parser (Stages 1-3) and the dictionary/schema/ingestion/output layers
(Stages 4-6+). Future prompts in prompts/ will cover:

- Dictionary parsing and SQLite schema generation
- Database population (ingestion pipeline)
- Output layer (CIF regeneration, programmatic API)

When starting a new stage, check prompts/ for the relevant specification before beginning.

---

## Task Management

1. Write plan to tasks/todo.md before implementing
2. Confirm plan before starting
3. Mark items complete as you go
4. Summarise changes at each step
5. Add review notes to tasks/todo.md on completion
6. After any correction, record the lesson in tasks/lessons.md

---

## Guiding Principle

> Be liberal in what you accept, strict in what you emit.

- Correctness and transparency of errors above all
- Avoid unnecessary abstraction
- Make every change as simple as possible; prefer deleting lines to adding them
- Find root causes; no temporary fixes
- Only touch what is necessary
