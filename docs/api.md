# pycifparse — API Reference

All public symbols are importable from the top-level `pycifparse` package unless
otherwise noted.

---

## Module layout

```
pycifparse/
├── __init__.py           # Top-level re-exports (all public symbols)
├── types.py              # CifVersion, ValueType, ParseError, CifParserEvents
├── pycifparse_core/      # PyO3 Rust extension (CifFile, CifBlock, CifSaveFrame,
│                         #   parse_cif, parse_arrow, parse_arrow_file)
├── lexer/
│   ├── lexer.py          # Lexer (internal)
│   └── tokens.py         # Token, LexerError (internal)
├── parser/
│   └── parser.py         # CifParser
├── cifmodel/
│   ├── model.py          # CifFile, CifBlock, CifSaveFrame, CifValue (re-exports from Rust)
│   ├── builder.py        # CifBuilder, build(), build_arrow(), build_arrow_file(), cif_to_arrow()
│   ├── writer.py         # CifWriter, BlockWriter, SaveFrameWriter, CifInput
│   ├── clean.py          # clean, CleanWarning
│   └── textfield.py      # transform_multiline (internal)
├── dictionary/
│   ├── ddlm_item.py      # DdlmItem
│   ├── ddlm_parser.py    # DdlmDictionary (data container)
│   ├── loader.py         # DictionaryLoader, directory_resolver, directory_path_resolver, SourceResolver
│   ├── cache.py          # save_dictionary, load_dictionary
│   ├── schema.py         # ForeignKeyDef, ColumnDef, TableDef, SchemaSpec,
│   │                     #   generate_schema, emit_create_statements,
│   │                     #   emit_fallback_create_statements
│   ├── schema_apply.py   # (stub — apply_schema/apply_fallback_schema removed; setup is internal to ingest())
│   ├── resolver.py       # ResolvedTag, resolve_tag
│   ├── visualise.py      # visualise_schema, visualise_schema_html
│   └── js/               # bundled JS package data (viz.js 2.1.2, svg-pan-zoom 3.6.1)
├── ingestion/
│   ├── ingest.py         # ingest(), IngestionError
│   └── duckdb_ingest.py  # DuckDB table setup and bulk-load helpers (internal)
├── database/
│   └── compact.py        # convert_database()
├── output/
│   ├── emit.py           # emit()
│   ├── plan.py           # EmitMode, OutputPlan, BlockSpec
│   └── quote.py          # quote(), make_text_field()
├── fidelity/
│   └── check.py          # check_fidelity()
├── inspect/
│   ├── _lexer.py         # inspect_lexer
│   ├── _parser.py        # inspect_parse, ParseHandler
│   ├── _model.py         # inspect_model
│   ├── _schema.py        # inspect_schema
│   └── _ingest.py        # inspect_ingest, TraceEvent
└── validation/
    └── _validate.py      # validate(), ValidationReport, ValidationIssue
```

---

## Example scripts

Two end-to-end example scripts live in the repository root.  They are runnable
as-is from the repository root and demonstrate the full public API in context.

### `example_workflow.py`

Full pipeline demonstration: dictionary loading → schema generation → CIF parsing
→ DuckDB ingestion → CIF emission in all four modes.

Steps covered:
1. Load `cif_pow.dic` via `DictionaryLoader` (with JSON cache)
2. Spot-check a tag via `resolve_tag`
3. Generate schema via `generate_schema`
4. Parse a CIF file via `build`
5. Ingest via `ingest` (returns a `duckdb.DuckDBPyConnection`; schema setup is internal)
6. Emit in `ORIGINAL` mode
7. Emit in `GROUPED` mode
8. Emit in `ONE_BLOCK` mode with a custom `OutputPlan`
9. Emit in `ALL_BLOCKS` mode
10. Type-cast export via `convert_database`
11. Fidelity checks for all four emit modes via `check_fidelity`

Output files written: `output_original.cif`, `output_grouped.cif`,
`output_one_block.cif`, `output_all_blocks.cif`.

### `example_fidelity.py`

Fidelity comparison demonstration: two semantically equivalent CIF files
(`multi_one.cif` — 24 blocks; `multi_one_as_oneblock.cif` — 1 block) are compared
using `check_fidelity`.

Steps covered:
1. Load `cif_pow.dic` and generate schema
2. Compare with schema (structured + fallback); writes `fidelity_report.txt`
3. Compare without schema (fallback only)
4. Self-comparison of each file (should always pass)

Output file written: `fidelity_report.txt`.

---

## Shared types (`pycifparse.types`)

### `CifVersion`

```python
class CifVersion(Enum):
    CIF_1_1 = "1.1"
    CIF_2_0 = "2.0"
```

Detected at parse time; never changes mid-file.

---

### `ValueType`

```python
class ValueType(Enum):
    MULTILINE_STRING      # \n;...\n;  semicolon-delimited text field
    TRIPLE_DOUBLE_QUOTED  # """..."""  (CIF 2.0 only)
    TRIPLE_SINGLE_QUOTED  # '''...'''  (CIF 2.0 only)
    DOUBLE_QUOTED         # "..."
    SINGLE_QUOTED         # '...'
    STRING                # unquoted bare word (including numeric)
    PLACEHOLDER           # unquoted . or ? (inapplicable or unknown)
```

Assigned exclusively by the lexer.  Never modified by any downstream layer.
`PLACEHOLDER` must remain unquoted on output — it is never semantically
equivalent to a quoted `.` or `?`.

---

### `ParseError`

```python
@dataclass
class ParseError:
    error_type:      Literal["lexical", "syntactic", "semantic"]
    message:         str
    line:            int
    column:          int
    context:         str
    recovery_action: str
```

- `lexical` — character-level error (unterminated string, illegal character)
- `syntactic` — token-sequence error (missing value, empty loop, unexpected keyword)
- `semantic` — structural error detected by the IR layer (loop row-count mismatch,
  duplicate block/save frame name)

---

### `CifParserEvents`

```python
class CifParserEvents(Protocol):
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
```

Any object implementing this protocol can be passed to `CifParser` or used
directly with `CifBuilder`.

---

## Parser (`pycifparse.parser.parser`)

### `CifParser`

```python
class CifParser:
    def __init__(self, handler: CifParserEvents) -> None: ...
    def parse(self, source: str) -> None: ...
```

Streaming parser.  Consumes a CIF source string, emits events to `handler`.
Handles CIF 1.1 and CIF 2.0; version is auto-detected from the magic line.

All errors are reported via `handler.on_error`; parsing never raises an
exception on malformed input.

---

## CIF model (`pycifparse.cifmodel`)

### `CifValue`

```python
CifValue = Union[str, list, dict]
```

The type of a single value element stored in the model:
- `str` — a scalar value; plain Python string exactly as it appeared in the source
- `list` — a CIF 2.0 list value (`[1 2 3]`), stored as `list[CifValue]`
- `dict` — a CIF 2.0 table value (`{"k": v}`), stored as `dict[str, CifValue]`

**Encoding conventions for scalars:**

| Stored string | Meaning | Emit behaviour |
|---|---|---|
| `'.'` or `'?'` (1 char) | PLACEHOLDER — inapplicable or unknown | bare, unquoted |
| `'"."'` or `'"?"'` (3 chars, with quotes) | Quoted dot/question-mark | emit with quotes |
| `'\x00[...]'` / `'\x00{...}'` | CIF container (JSON, `\x00` prefix) | decode JSON |
| anything else | raw CIF value | re-quote based on content |

`ValueType` is no longer exposed in the Python API; encoding conventions carry
the quoting semantics.

---

### `CifSaveFrame`

Represents one `save_name … save_` frame.

```python
class CifSaveFrame:
    name: str          # frame name as it appeared in the file
    _id:  int          # internal unique identifier (assigned by parent CifBlock)
```

**Access:**

```python
frame["_tag"]          # → list[CifValue]  (KeyError if absent)
"_tag" in frame        # → bool
frame.tags             # → list[str]  tag names in insertion order
frame.loops            # → list[list[str]]  each inner list is one loop's tags
```

**Notes:**
- A scalar tag returns a one-element list.
- A loop column returns a multi-element list.
- Both are accessed identically via `frame["_tag"]`.
- `tags` includes both scalar tags and loop tags, in file order.
- `loops` lists only the grouped loop structures; use it to determine which
  tags belong to which loop.
- Scalar values are plain Python `str`.  Container values are plain `list` and `dict`.
  See `CifValue` encoding conventions for how PLACEHOLDER and quoted sentinels are represented.

---

### `CifBlock`

Represents one `data_name …` block.  Extends `CifSaveFrame` with save frame
access.

```python
class CifBlock(CifSaveFrame):
    name: str          # block name as it appeared in the file
    _id:  int          # internal unique identifier (assigned by parent CifFile)
```

**Access:**

```python
block["_tag"]          # → list[CifValue]  (KeyError if absent)
block["frame_name"]    # → CifSaveFrame    (KeyError if absent)
"_tag" in block        # → bool
"frame_name" in block  # → bool
block.tags             # → list[str]  (same as CifSaveFrame)
block.loops            # → list[list[str]]  (same as CifSaveFrame)
block.save_frames      # → list[str]  save frame names in file order
block.get_all(name)    # → list[CifSaveFrame]  all frames with that name
```

**Dispatch rule:** `block["key"]` dispatches on the key:
- Keys beginning with `_` → tag lookup
- All other keys → save frame lookup

**Duplicate save frame names:**
`block["name"]` returns the first frame with that name.
`block.get_all("name")` returns all frames with that name in file order.
Each frame has a distinct `_id`.  A duplicate save frame name emits a semantic
`ParseError`.

---

### `CifFile`

Top-level container for a parsed CIF file.

**Access:**

```python
cif["block_name"]      # → CifBlock  (KeyError if absent)
"block_name" in cif    # → bool
cif.blocks             # → list[str]  block names in file order
cif.version            # → CifVersion  (set by build(); default CIF_2_0)
cif.get_all(name)      # → list[CifBlock]  all blocks with that name
cif.deepcopy()         # → CifFile  independent deep copy
```

**Duplicate block names:**
`cif["name"]` returns the first block with that name.
`cif.get_all("name")` returns all blocks with that name in file order.
Each block has a distinct `_id`.  A duplicate block name emits a semantic
`ParseError`.

**Empty block names:**
`data_` (bare, no name suffix) is legal with error — a syntactic `ParseError`
is emitted, and the block is stored with name `""`, accessible as `cif[""]`.

---

### `CifBuilder`

Event-driven accumulator.  Implements `CifParserEvents`.

```python
class CifBuilder:
    def __init__(
        self,
        on_error: Callable[[ParseError], None],
        mode: Literal['pad', 'strict'] = 'pad',
    ) -> None: ...

    @property
    def result(self) -> CifFile: ...
```

Receives parser events and accumulates them into a `CifFile`.  Both
parser-level errors (forwarded via `on_error`) and IR-level semantic errors
are delivered to the same `on_error` callback.

**`mode`:**
- `'pad'` (default) — on loop row-count mismatch, emit a semantic error and
  pad the incomplete final row with `'?'` placeholders.
- `'strict'` — on the first semantic error, stop accumulating.  All subsequent
  events are ignored.  The `result` reflects state up to the error.

**Multiline text transformation:**
`MULTILINE_STRING` values are passed through the transformation pipeline
(prefix stripping + line unfolding) before storage.  All other `ValueType`
values are stored as raw strings unchanged.

---

### `build()` — convenience entry point

```python
def build(
    source: str,
    *,
    mode: Literal['pad', 'strict'] = 'pad',
) -> tuple[CifFile, list[ParseError]]:
```

Parses `source` and returns `(CifFile, errors)`.

`errors` contains all `ParseError` instances emitted during parsing and IR
construction, in emission order.  Both lexical/syntactic errors (from the
parser) and semantic errors (from the IR layer) are included.

This is the primary entry point for most callers.

**Example:**

```python
from pycifparse import build

cif, errors = build(source)

for block_name in cif.blocks:
    block = cif[block_name]
    for tag in block.tags:
        values = block[tag]          # list[CifValue]
    for loop_tags in block.loops:
        columns = {t: block[t] for t in loop_tags}
    for frame_name in block.save_frames:
        frame = block[frame_name]    # CifSaveFrame
```

---

### `build_arrow(source, *, mode)` / `build_arrow_file(path, *, mode)`

```python
def build_arrow(
    source: str,
    *,
    mode: Literal['strict', 'pad'] = 'pad',
) -> tuple[list[pa.RecordBatch], list[ParseError]]: ...

def build_arrow_file(
    path: str,
    *,
    mode: Literal['strict', 'pad'] = 'pad',
) -> tuple[list[pa.RecordBatch], list[ParseError]]: ...
```

Parse CIF source (or a file path) and return Arrow RecordBatches directly.
`build_arrow_file` performs file I/O in Rust — no Python file objects are created.

Each `RecordBatch` covers one logical namespace section:

- **Scalar batch** — all scalar tags in a block/save-frame; `_loop_id = '__scalars__'`
- **Loop batch** — one loop; `_loop_id = '__loop_0__'`, `'__loop_1__'`, …

Every batch carries five metadata columns:

| Column | Type | Notes |
|---|---|---|
| `_block_idx` | `Int32` | Block index in file order |
| `_block_name` | `Utf8` | Block name |
| `_frame_idx` | `Int32` (nullable) | Save frame index; `NULL` for block-level |
| `_frame_name` | `Utf8` (nullable) | Save frame name; `NULL` for block-level |
| `_loop_id` | `Utf8` | `'__scalars__'` or `'__loop_N__'` |

Followed by one `Utf8` column per tag in that batch.  Container values are
stored as `\x00` + JSON.

---

### `cif_to_arrow(cif)`

```python
def cif_to_arrow(cif: CifFile) -> list[pa.RecordBatch]: ...
```

Convert any `CifFile` (whether from `build()` or constructed via `CifWriter`)
to the same Arrow RecordBatch format as `build_arrow()`.  No errors are
returned — the `CifFile` is already validated.

---

## CIF model — construction and editing (`pycifparse.cifmodel.writer`)

All symbols are importable from `pycifparse.cifmodel.writer` or the top-level `pycifparse` package.

### `CifInput`

```python
CifInput = Union[int, float, str, list, dict]
```

Accepted input type for all value-setting methods.  Conversion rules:
- `bool` → `"true"` / `"false"`
- `int` / `float` → `str(v)`
- `'.'` or `'?'` (unquoted single char) → stored as PLACEHOLDER sentinel
- other `str` → stored as raw string
- `list` / `dict` → recursively converted (CIF 2.0 containers), stored as `\x00` + JSON

---

### `SaveFrameWriter`

Wraps a `CifSaveFrame` (or `CifBlock`) and exposes mutation methods.  All methods return `self` for chaining.

```python
class SaveFrameWriter:
    def set_tag(self, tag: str, value: CifInput) -> 'SaveFrameWriter': ...
    def add_loop(self, columns: dict[str, list[CifInput]]) -> 'SaveFrameWriter': ...
    def add_loop_column(self, loop_tag: str, new_tag: str,
                        values: list[CifInput]) -> 'SaveFrameWriter': ...
    def reorder_loop_tags(self, loop_tag: str,
                          new_order: list[str]) -> 'SaveFrameWriter': ...
    def get_loop_tags(self, loop_tag: str) -> list[str]: ...
    def add_loop_row(self, loop_tag: str,
                     row: list[CifInput]) -> 'SaveFrameWriter': ...
    def reassign_tag(self, tag: str,
                     value: 'CifInput | list[CifInput]') -> 'SaveFrameWriter': ...
    def delete_tag(self, tag: str) -> 'SaveFrameWriter': ...
    def remove_loop_tag(self, loop_tag: str,
                        tag_to_remove: str) -> 'SaveFrameWriter': ...
```

**Method summaries:**

| Method | Effect |
|---|---|
| `set_tag(tag, value)` | Append a scalar tag–value pair |
| `add_loop(columns)` | Add a new loop; `columns` is `{tag: [values...]}` in insertion order |
| `add_loop_column(loop_tag, new_tag, values)` | Append a column to the loop containing `loop_tag` |
| `reorder_loop_tags(loop_tag, new_order)` | Reorder columns within the loop containing `loop_tag` |
| `get_loop_tags(loop_tag)` | Return the ordered tag list for the loop containing `loop_tag` |
| `add_loop_row(loop_tag, row)` | Append one row of values to the loop containing `loop_tag` |
| `reassign_tag(tag, value)` | Replace all stored values for `tag` with a new single value (or list) |
| `delete_tag(tag)` | Remove `tag` and its values; if a loop column, removes the column |
| `remove_loop_tag(loop_tag, tag_to_remove)` | Remove one column from the loop containing `loop_tag` |

`loop_tag` is any tag already in the target loop — used to identify which loop to operate on.
`set_tag` appends; use `reassign_tag` to overwrite an existing value.

---

### `BlockWriter`

Extends `SaveFrameWriter` with save frame management.

```python
class BlockWriter(SaveFrameWriter):
    def add_save_frame(self, name: str) -> 'SaveFrameWriter': ...
    def get_save_frame(self, name: str, index: int = 0) -> 'SaveFrameWriter': ...
    def remove_save_frame(self, name: str, *,
                          from_end: bool = False) -> 'BlockWriter': ...
    def rename_save_frame(self, old_name: str,
                          new_name: str) -> 'BlockWriter': ...
```

`get_save_frame(name, index=0)` collects all save frames with that name in file order and returns the one at `index`.  `KeyError` if none; `IndexError` if out of range.

`remove_save_frame(name, from_end=False)` removes one frame: from the start when `from_end=False` (keeps last), from the end when `from_end=True` (keeps first).

---

### `CifWriter`

Top-level writer.  Wraps an existing `CifFile` or creates a new one.

```python
class CifWriter:
    def __init__(self, version: CifVersion,
                 cif: CifFile | None = None) -> None: ...
    def add_block(self, name: str) -> 'BlockWriter': ...
    def get_block(self, name: str, index: int = 0) -> 'BlockWriter': ...
    def remove_block(self, name: str, *,
                     from_end: bool = False) -> 'CifWriter': ...
    def rename_block(self, old_name: str,
                     new_name: str) -> 'CifWriter': ...
    def build(self) -> CifFile: ...
```

`build()` validates the accumulated `CifFile` and returns it.  Validation checks:
- All loop columns within each loop have equal length.
- No zero-row loops.
- No scalar tag has more than one value.
- CIF 1.1: no container (`list`/`dict`) values present.

When `cif` is supplied, `CifWriter` wraps the existing object in place; `build()` returns the same object.  When a CIF 2.0 file is wrapped with `version=CIF_1_1`, a `UserWarning` is emitted.

`get_block(name, index=0)` and `remove_block`/`rename_block` follow the same semantics as the corresponding save-frame methods.

**Example:**

```python
from pycifparse import CifWriter, CifVersion

w = CifWriter(version=CifVersion.CIF_2_0)
bw = w.add_block('my_data')
bw.set_tag('_cell.length_a', '5.43')
bw.add_loop({
    '_atom_site.label':   ['C1', 'O1'],
    '_atom_site.fract_x': ['0.1', '0.4'],
    '_atom_site.fract_y': ['0.2', '0.5'],
})
cif = w.build()
```

---

## CIF model — cleaning (`pycifparse.cifmodel.clean`)

`clean()` targets artefacts that the parser introduces automatically.  For structural edits to CIF content — reassigning values, reordering, renaming, adding or removing tags and loops — use `CifWriter`.

### `CleanWarning`

```python
@dataclass
class CleanWarning:
    category:   str        # step name (see table below)
    block:      str | None
    save_frame: str | None
    message:    str
```

One warning per removal action.  `category` values:

| Category | Step |
|---|---|
| `'remove_error_values'` | Orphan `_pycifparse_error_value` tags removed |
| `'deduplicate_blocks'` | Duplicate data block removed |
| `'deduplicate_save_frames'` | Duplicate save frame removed |
| `'deduplicate_tags'` | Duplicate scalar tag deduplicated |
| `'strip_loop_padding'` | Trailing PLACEHOLDER row(s) stripped from a loop |

---

### `clean()`

```python
def clean(
    cif: CifFile,
    *,
    copy: bool = True,
    remove_error_values: bool = True,
    deduplicate_blocks: Literal['first', 'last'] | Literal[False] = 'first',
    deduplicate_save_frames: Literal['first', 'last'] | Literal[False] = 'first',
    deduplicate_tags: Literal['first', 'last'] | Literal[False] = 'first',
    strip_loop_padding: bool = True,
) -> tuple[CifFile, list[CleanWarning]]:
```

Removes well-known parse-time artefacts from a `CifFile`.  Returns `(cleaned_cif, warnings)`.

- `copy=True` (default): operates on a deep copy; input is not modified.
- `copy=False`: mutates in place; returns the same object.

**Steps (in order):**

1. **`remove_error_values`** — removes the synthetic `_pycifparse_error_value` tag that the parser inserts for orphan values with no preceding tag.
2. **`deduplicate_blocks`** — when multiple blocks share the same name, keeps `'first'` or `'last'`; `False` disables.
3. **`deduplicate_save_frames`** — same as above, applied per block.
4. **`deduplicate_tags`** — when a scalar tag has multiple values (non-loop duplicates), keeps `'first'` or `'last'`; loop columns are never touched.
5. **`strip_loop_padding`** — strips trailing rows where every column value is `PLACEHOLDER` (`'?'`), capped at `n − 1` rows.  Only fires when all columns simultaneously have trailing PLACEHOLDERs.

Every removal produces a `CleanWarning`; nothing is silently discarded.

**Example:**

```python
from pycifparse import build, clean

cif, parse_errors = build(source)
cif, warnings = clean(cif)
for w in warnings:
    print(f'[{w.category}] block={w.block}: {w.message}')
```

---

## Output layer (`pycifparse.output`)

All symbols are importable from `pycifparse.output` or the top-level `pycifparse` package.

---

### `EmitMode`

```python
class EmitMode(Enum):
    ONE_BLOCK  = "one_block"   # all data in one block named 'output'
    ALL_BLOCKS = "all_blocks"  # one block per non-empty table, plus fallback blocks
    ORIGINAL   = "original"    # one block per original _block_id (default)
    GROUPED    = "grouped"     # one block per Set-anchor key combination
```

**`ORIGINAL`** reconstructs the CIF blocks as they were before ingestion — the simple inverse of `ingest()`.

**`GROUPED`** traverses the FK graph (BFS) from each table to find the nearest Set-class ancestor.  Tables whose FK chains share the same Set anchor and the same anchor key values are emitted together.  This merges rows from multiple original blocks that carry the same Set-level identity.  Tables with no Set ancestor fall back to `_block_id` grouping and are absorbed into co-located Set-anchored blocks; truly orphaned block IDs produce standalone blocks.

**`ALL_BLOCKS`** emits one block per table, split by Set-key combination.  Raises `ValueError` if any `_cif_fallback` rows are present (unknown tags cannot be assigned to a dictionary-split block) or if any keyless Set table (one whose only PK column is `_pycifparse_id`) contains data.

Block partitioning rules:

- **Set category** — one block per row.  Block name: `{table}_{pk_val...}`.
- **Loop category, no Set-key columns** — one block for all rows.  Block name: `{table}`.
- **Loop category, one or more Set-key columns** — one block per unique combination of Set-key values.  Block name: `{table}_{set_val...}`.  Set-key values are emitted as scalar tag–value pairs above the loop (using the parent category's tag name); the corresponding FK columns are suppressed from the loop header.

`_audit_dataset.id` injection: the dataset ID is resolved per block by looking up the originating `_block_id` values in `_block_dataset_membership`.  If exactly one distinct ID is found it is injected as a scalar.  If multiple IDs are found they are injected as a `loop_`.  If none is found a fresh UUID is generated (CIF 2.0 only).  Injection is skipped for any block that already carries `_audit_dataset.id` via its structured table (`audit_dataset`) or `_cif_fallback` rows.

**`OutputPlan` in ALL_BLOCKS mode:** spec-matching (`matches`, `single_block`, `block_namer`) is not applied.  `category_order` from the first `BlockSpec` that declares one controls the order in which tables are processed (and thus the order of their blocks in the output file).  The wildcard `'*'` notation is supported.  Unlisted tables follow alphabetically (Set-class first).

---

### `OutputPlan` / `BlockSpec`

```python
@dataclass
class BlockSpec:
    matches:        Callable[[frozenset[str]], bool] | None = None
    category_order: list[str | list[str]] = field(default_factory=list)
    single_block:   bool = False
    column_order:   dict[str, list[str]] = field(default_factory=dict)
    block_namer:    Callable[[dict[str, list[str]]], str] | None = None

@dataclass
class OutputPlan:
    specs:       list[BlockSpec] = field(default_factory=list)
    block_namer: Callable[[dict[str, list[str]]], str] | None = None

    def match(self, anchor_frozenset: frozenset[str]) -> tuple[int, BlockSpec] | tuple[None, None]:
        ...
```

`BlockSpec` fields:

- **`matches`** — predicate receiving the `frozenset` of Set-category table names present in a
  candidate block; returns `True` if this spec applies.  `None` is a catch-all.  First-match
  wins across `OutputPlan.specs`.  The anchor frozenset for a block is the set of Set-class table
  names that have rows in that block.
- **`category_order`** — categories in emission order within a block.  Elements:
  - Plain `str`: name a single category.
  - `str` ending with `'*'`: wildcard — expands to that category plus all schema descendants
    (via `SchemaSpec.category_parent` BFS), sorted alphabetically.  Unrecognised base emits a
    warning and expands to nothing.
  - `list[str]`: merge group — categories sharing identical non-synthetic PK column sets are
    emitted as a single `loop_` via FULL OUTER JOIN (implemented in Python); incompatible
    categories fall back to plain loops in listed order, no warning.
  - Unlisted categories are appended alphabetically after (Set-class first, then Loop-class).
- **`single_block`** — when `True`, all blocks matching this spec are collapsed into a single
  output block; Set-category key columns are emitted as loop columns; FK-PK suppression does
  not apply.
- **`column_order`** — `{category_name: [col_name, ...]}`.  Listed columns appear first;
  remaining follow alphabetically.
- **`block_namer`** — optional block name override.  Receives `dict[str, list[str]]` mapping
  `'{table}.{pk_col}'` → `[key_value(s)]` and returns the desired block name.  Sanitization
  and disambiguation still applied.  Falls back to `OutputPlan.block_namer`, then to the
  default rule.

`OutputPlan` fields:

- **`specs`** — ordered list of `BlockSpec`.  For each output block, the emitter assigns the
  first matching spec (first-match wins).  Emission order: all blocks assigned to `specs[0]`
  first, then `specs[1]`, etc.; unmatched blocks last, alphabetically by block name.  Within a
  spec, multiple matching blocks are emitted alphabetically.
- **`block_namer`** — global fallback namer (same signature as `BlockSpec.block_namer`); used
  when the matched spec has no `block_namer`.

`OutputPlan.match(anchor_frozenset)` returns `(index, spec)` for the first matching spec, or
`(None, None)` if none match.

**Default block naming (GROUPED mode):** For each Set anchor in the block, take the `object_id`
of the anchor's domain PK column and the corresponding key value; join as
`{object_id}_{key_value}`.  Multi-anchor blocks concatenate all segments with underscores.
Result is sanitized (non-alphanumeric → `_`; consecutive underscores collapsed; leading/trailing
stripped).  Duplicate names after sanitization get `_2`, `_3` suffixes.

Pass an `OutputPlan` to `emit()` to control category and column ordering.
`OutputPlan(specs=[])` (the default) applies alphabetical ordering throughout.

---

### `emit(conn, schema, *, mode, version, plan, pretty, line_limit, line_ending, reconstruct_su, emit_defaults)`

```python
def emit(
    conn: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    *,
    mode: EmitMode = EmitMode.ORIGINAL,
    version: CifVersion = CifVersion.CIF_2_0,
    plan: OutputPlan | None = None,
    pretty: bool = True,
    line_limit: int | None = 2048,
    line_ending: str = '\n',
    reconstruct_su: bool = False,
    emit_defaults: bool = True,
) -> str:
```

Reads structured tables and `_cif_fallback` from `conn` and produces a valid
CIF string.

**Output format:**
- Magic line (`#\#CIF_2.0` or `#\#CIF_1.1`) on the first line.
- One `data_` block per partition (determined by `mode`).
- Blocks separated by two blank lines.
- Set categories emitted as scalar tag–value pairs; Loop categories as `loop_` constructs.
- `_cif_fallback` rows grouped by tag; single-value tags as scalars, multi-value as single-column loops.
- Synthetic columns (`_block_id`, `_row_id`, `_pycifparse_id`) are never emitted.
- `NULL` columns (all values NULL in all rows) are omitted.
- `NULL` values within a loop row are emitted as `.` (inapplicable placeholder).
- Default ordering: Set categories first (alphabetical), then Loop categories (alphabetical), then fallback.
- Output always terminates with a newline; no line has trailing whitespace.
- **FK-PK suppression** (`ORIGINAL` and `GROUPED` modes only): if a table's domain primary-key column is also a FK pointing to a Set-class category that is emitted in the same block, and every row carries the same FK value matching the target Set's PK, that column is omitted from the output.  The CIF block scope makes the value implicit — a reader recovers it from the target Set's own PK tag.

**Pretty-printing (`pretty=True`):**
- Set-category tag names are padded to the width of the longest tag in the category.
- Loop-category tokens are column-aligned: each column is as wide as its widest token.
- Columns containing any multiline token are excluded from width padding.
- `Real` / `Float` columns are decimal-aligned: integer parts are right-justified to the widest integer part across all values; fractional parts are left-justified to the widest fractional part.  Scientific notation is handled by splitting on `.` first, then on `e`/`E`.  Placeholders and quoted strings in a nominally-Real column fall back to plain left-justify for that value.
- `pretty=False` skips all alignment; tokens are separated by two spaces.

**Line-length enforcement (`line_limit`):**
- Multiline (semicolon-delimited) text fields whose content lines exceed `line_limit` are folded using the CIF 2.0 line-folding protocol (`;\\\n…\n;`).  When the content also contains `\n;`, the text-prefix protocol is applied and the two formats are combined as needed.
- Inline tokens whose `tag + separator + token` length exceeds `line_limit` are re-quoted as text fields.
- Loop rows whose total formatted width exceeds `line_limit` are greedy-packed across multiple physical lines.
- CIF 1.1 block names longer than 75 characters raise `ValueError`.
- `line_limit=None` disables all line-length enforcement.

**Parameters:**

| Parameter | Type | Notes |
|---|---|---|
| `conn` | `duckdb.DuckDBPyConnection` | Returned by `ingest()`; read-only during emission |
| `schema` | `SchemaSpec` | The schema used when `conn` was ingested |
| `mode` | `EmitMode` | Block partitioning strategy; default `ORIGINAL` |
| `version` | `CifVersion` | Controls magic line and quoting strategy |
| `plan` | `OutputPlan \| None` | Custom category/column ordering; `None` = default |
| `pretty` | `bool` | Column alignment and decimal alignment; default `True` |
| `line_limit` | `int \| None` | Max physical line length; triggers folding/re-quoting; default `2048` |
| `line_ending` | `str` | Line separator for the output string; default `'\n'` |
| `reconstruct_su` | `bool` | Merge `(measurand, su)` column pairs back into `value(su)` tokens |
| `emit_defaults` | `bool` | Accepted; currently has no effect |

**Returns:** Complete CIF text as a `str`.

**Example:**

```python
from pycifparse import emit, EmitMode
from pycifparse.types import CifVersion

cif_text = emit(conn, schema)                              # ORIGINAL mode, CIF 2.0
cif_text = emit(conn, schema, mode=EmitMode.GROUPED)       # grouped by Set anchor
cif_text = emit(conn, schema, version=CifVersion.CIF_1_1)
cif_text = emit(conn, schema, pretty=False)                # compact, no alignment
cif_text = emit(conn, schema, line_limit=80)               # fold/repack at 80 chars
cif_text = emit(conn, schema, line_ending='\r\n')          # Windows line endings
```

---

### `quote(stored, version)`

```python
def quote(stored: str, version: CifVersion) -> str:
```

Returns the shortest valid CIF token for `stored` under `version`'s quoting
rules, choosing among bare word, single-quoted, double-quoted, triple-quoted
(CIF 2.0 only), and semicolon-delimited forms.

`PLACEHOLDER` handling is the caller's responsibility — pass `'.'` or `'?'`
directly without quoting.

The returned token for semicolon-delimited values begins with `'\n'` (the
leading newline is part of the token so it can be distinguished from inline
forms by the caller).

---

### `make_text_field(s, line_limit=None)`

```python
def make_text_field(s: str, line_limit: int | None = None) -> str:
```

Produces a semicolon-delimited CIF text field for `s`, selecting the correct
wire format based on content requirements:

| `'\n;' in s` | line too long | Format used |
|---|---|---|
| No | No | `\n;{s}\n;` — plain semicolon |
| Yes | No | `\n;>\\\n>{line}…\n;` — prefix-only |
| No | Yes | `\n;\\\n{lines}…\n;` — fold-only |
| Yes | Yes | `\n;>\\\\\n>{line}…\n;` — prefix + fold |

*needs_prefix* is `True` when `s` contains `'\n;'` (which would otherwise
prematurely terminate the field).

*needs_fold* is `True` when `line_limit` is given and at least one content
line would produce a physical line exceeding `line_limit` characters.

Valid for both CIF 1.1 and CIF 2.0; semicolon fields exist in both.  The
prefix and folding protocols are CIF 2.0 extensions, but are accepted by most
CIF 1.1 readers in practice.

The returned string always begins with `'\n'`.

---

## Inspect utilities (`pycifparse.inspect`)

All six entry points accept `str | pathlib.Path | IO[str]` as `source`.
Output is ANSI-coloured when writing to a tty; plain otherwise.

```python
def inspect_lexer(source, *, version=None, file=sys.stdout) -> None:
```
Prints the full token stream with positions and any lexer errors.

```python
def inspect_parse(source, *, inner=None, file=sys.stdout) -> None:
```
Prints parser events indented by nesting depth.  If `inner` is provided,
all events are forwarded to it after printing.

```python
def inspect_model(source, *, mode='pad', file=sys.stdout) -> None:
```
Prints a structured summary of the parsed `CifFile` and any errors.

```python
def inspect_schema(source, *, file=sys.stdout) -> None:
```
Accepts a DDLm dictionary source (or a `DdlmDictionary` object) and prints
the derived `SchemaSpec`: tables, columns, PKs, FKs, and generation warnings.

```python
def inspect_ingest(cif, conn, schema=None, *, file=sys.stdout,
                   on_error=None) -> list[TraceEvent]:
```
Runs `ingest()` while capturing semantic warnings, errors, and FK violations
as `TraceEvent` objects.  Prints a formatted trace and returns the event list.

### `ParseHandler`

```python
class ParseHandler:
    def __init__(self, inner=None, *, file=sys.stdout) -> None: ...
```

A `CifParserEvents` implementation that prints every event, optionally
forwarding to `inner`.

### `TraceEvent`

```python
@dataclass
class TraceEvent:
    kind:    str          # 'warning', 'error', or 'fk_violation'
    message: str
    block:   str | None   # block name at point of occurrence
    tag:     str | None   # tag involved, if applicable
```

---

## Behavioural guarantees

| Guarantee | Detail |
|---|---|
| No silent data loss | Every value in the source appears in the model or in an error |
| Raw string storage | All values stored exactly as lexed; no type conversion |
| File order preserved | Blocks, tags, loop columns, save frames — all in source order |
| Duplicate tags preserved | `block["_t"]` returns all values, never just the last |
| Duplicate names preserved | Duplicate block/frame names stored with distinct `_id`; first returned by `[]`, all by `get_all()` |
| No crash on malformed input | Parser recovers and continues; errors reported via callback |
| ValueType provenance | `PLACEHOLDER` is never rewritten as a quoted type |

---

## Dictionary layer (`pycifparse.dictionary`)

All symbols below are importable directly from `pycifparse.dictionary` or from
the top-level `pycifparse` package.

---

### `SourceResolver`

```python
SourceResolver = Callable[[str], str | None]
```

Callable that maps a URI string to a raw CIF source string, or `None` if the
file is unavailable.  Passed to `DictionaryLoader`.

---

### `directory_resolver(path)`

```python
def directory_resolver(path: str | pathlib.Path) -> SourceResolver:
```

Returns a `SourceResolver` that reads files by filename (last URI path
component) from a local directory.  Returns `None` if the file is not found.

```python
from pycifparse import DictionaryLoader, directory_resolver

resolver = directory_resolver('data/dictionaries')
loader = DictionaryLoader(resolver=resolver)
```

---

### `directory_path_resolver(path)`

```python
def directory_path_resolver(path: str | pathlib.Path) -> Callable[[str], str | None]:
```

Companion to `directory_resolver`.  Returns a callable that maps a URI to the
absolute filesystem path of the corresponding file in `path`, or `None` if not
found.  Pass to `DictionaryLoader(path_resolver=...)` so that
`DdlmDictionary.source_files` (and therefore `SchemaSpec.source_files`) contains
full paths rather than bare URIs.

```python
from pycifparse import DictionaryLoader, directory_resolver, directory_path_resolver

DIC_DIR = 'data/dictionaries'
loader = DictionaryLoader(
    resolver=directory_resolver(DIC_DIR),
    path_resolver=directory_path_resolver(DIC_DIR),
)
```

---

### `DdlmItem`

```python
@dataclass
class DdlmItem:
    definition_id:      str           # _definition.id, lowercased
    scope:              str           # "Item", "Category", "Dictionary"
    definition_class:   str           # "Datum", "Attribute", "Loop", "Set",
                                      #   "Head", "Functions"
    category_id:        str | None    # _name.category_id (table name)
    object_id:          str | None    # _name.object_id   (column name)
    type_purpose:       str | None    # "Key", "Link", "SU", "Measurand", …
    type_source:        str | None    # "Assigned", "Recorded", …
    type_container:     str           # "Single" (default), "List", …
    type_contents:      str | None    # "Text", "Integer", "Real", …
    linked_item_id:     str | None    # for Link/SU items
    units_code:         str | None
    description:        str | None
    enumeration_states: list[str]     # _enumeration_set.state values
    enumeration_range:  str | None    # _enumeration.range (e.g. "0.:inf")
    category_keys:      list[str]     # _category_key.name (category frames)
    aliases:            list[str]     # _alias.definition_id (old names)
    replaced_by:        list[str]     # _definition_replaced.by; "" = no replacement
    is_deprecated:      bool          # True if any _definition_replaced row exists
    type_dimension:     str | None    # _type.dimension (e.g. "[3]", "[3,3]")
```

One save frame from a DDLm dictionary.  Produced by `DictionaryLoader.load()`.

---

### `DdlmDictionary`

```python
@dataclass
class DdlmDictionary:
    name:                    str                   # data_ block name
    title:                   str | None            # _dictionary.title
    version:                 str | None            # _dictionary.version
    categories:              dict[str, DdlmItem]   # definition_id → item
    items:                   dict[str, DdlmItem]   # definition_id → item
    tag_to_item:             dict[str, DdlmItem]   # definition_id + aliases
    alias_to_definition_id:  dict[str, str]        # old name → canonical name
    deprecated_ids:          set[str]
    warnings:                list[str]
    source_files:            list[str]             # paths/URIs of loaded files (base + constituents)
```

Produced by `DictionaryLoader.load()`.  `source_files` contains the base file
URI/path followed by all constituent file URIs/paths resolved via `_import.get`.
Full absolute paths are recorded when a `path_resolver` is supplied to
`DictionaryLoader`; otherwise bare URIs are stored.

---

### `DictionaryLoader`

```python
class DictionaryLoader:
    def __init__(
        self,
        resolver: SourceResolver | None = None,
        *,
        path_resolver: Callable[[str], str | None] | None = None,
        on_warning: Callable[[str], None] | None = None,
        ignore_head_imports: bool = False,
    ) -> None: ...

    def load(
        self,
        source: str,
        *,
        base_uri: str | None = None,
    ) -> DdlmDictionary: ...
```

Parses a DDLm CIF 2.0 source string and resolves all `_import.get` directives.
Both `mode="Contents"` (frame-level attribute merge) and `mode="Full"`
(constituent dictionary incorporation) are fully supported.

`mode="Full"` targeting a `Head` category loads the entire constituent
dictionary recursively and merges all its definitions into the result.  Local
definitions always take precedence; earlier imports take precedence over later
ones when `dupl="Ignore"`.  Circular imports are detected and skipped with a
warning.

- `path_resolver` — optional companion to `resolver` that maps a URI to its
  absolute filesystem path (rather than content).  When supplied, `source_files`
  in the resulting `DdlmDictionary` (and `SchemaSpec`) contains absolute paths.
  Use `directory_path_resolver()` to create one.
- `ignore_head_imports` — when `True`, `_import.get` directives in save frames
  with `_definition.class = Head` are silently skipped; only physically present
  save frames are parsed.

File access is delegated entirely to `resolver`.  `DictionaryLoader` never
touches the filesystem or network directly.  Parsed files are cached for the
lifetime of the loader instance.

**`_import.get` conflict resolution:**

| `dupl` | Behaviour |
|---|---|
| `"Ignore"` | Keep existing value; discard source |
| `"Replace"` | Overwrite with source; if Loop category, remove all same-category tags first |
| `"Exit"` | Warn and abort (default) |

| `miss` | Behaviour |
|---|---|
| `"Ignore"` | Warn and skip |
| `"Exit"` | Warn and abort (default) |

**Example:**

```python
from pycifparse import DictionaryLoader, directory_resolver

resolver = directory_resolver('data/dictionaries')
source = open('data/dictionaries/cif_core.dic').read()
d = DictionaryLoader(resolver=resolver).load(source)

print(d.name)          # 'CIF_CORE'
print(len(d.items))    # number of defined items
```

---

### `ForeignKeyDef`

```python
@dataclass
class ForeignKeyDef:
    source_table:   str        # table holding the FK columns
    source_columns: list[str]  # FK column names (positionally paired with target_columns)
    target_table:   str        # referenced table
    target_columns: list[str]  # referenced column names
    # always emitted as DEFERRABLE INITIALLY DEFERRED
```

Single-column FKs are the common case; multi-column FKs arise from composite
category keys.  `source_columns[i]` maps to `target_columns[i]`.

---

### `ColumnDef`

```python
@dataclass
class ColumnDef:
    name:            str         # SQL column name (= object_id)
    definition_id:   str         # _definition.id; "" for synthetic columns
    type_contents:   str | None  # DDLm _type.contents value: "Text", "Integer",
                                 #   "Real", "List", "Table", etc.; None if unknown.
                                 #   Informational only — DDL always emits TEXT.
    nullable:        bool        # False for synthetic and PK columns; True otherwise
    is_primary_key:  bool
    is_synthetic:    bool        # True for _block_id, _row_id, _pycifparse_id
    linked_item_id:      str | None  # SU items only; no FK constraint produced
    type_container:      str | None  # DDLm _type.container value: "Single", "Matrix",
                                     #   "List", "Array", etc.; None if unknown or synthetic.
                                     #   Non-"Single" columns store JSON and are always TEXT.
    enumeration_states:  list[str]   # allowed values from _enumeration_set.state; [] if none
    enumeration_range:   str | None  # _enumeration.range (e.g. "0.:inf"); None if absent
    type_dimension:      str | None  # _type.dimension (e.g. "[3]", "[3,3]"); None if absent
```

All value columns use `TEXT` storage regardless of `type_contents` or `type_container`.
These fields are used by `validate()` for content checks and are retained for type-coercion
use (see `convert_database`).
When `type_container` is not `"Single"` (e.g. `"Matrix"`, `"List"`, `"Array"`), the
column stores a JSON-encoded structure; `convert_database` casts the leaf values within
that JSON rather than the column value as a whole.

CIF presence states are encoded directly in the value column:

| Stored value | CIF meaning |
|---|---|
| `NULL` | tag absent from this block / row |
| `'.'` | inapplicable (unquoted `.` — `PLACEHOLDER`) |
| `'?'` | unknown (unquoted `?` — `PLACEHOLDER`) |
| `'"."'` | literal quoted dot |
| `'"?"'` | literal quoted question mark |
| anything else | real value, stored as raw string |

Synthetic columns (`_block_id`, `_row_id`, `_pycifparse_id`) have `definition_id=""`
and are excluded from `column_to_tag`.

---

### `TableDef`

```python
@dataclass
class TableDef:
    name:            str                  # SQL table name (= category_id)
    definition_id:   str                  # _definition.id of the category
    category_class:  str                  # "Set" or "Loop"
    columns:         list[ColumnDef]
    primary_keys:    list[str]            # column names in PK order
    foreign_keys:    list[ForeignKeyDef]
```

**Column ordering:**
1. `_block_id` — always first; informational only for keyed tables
2. `_pycifparse_id` — keyless Set tables only; synthetic UUID primary key
3. `_row_id` — all tables (Set and Loop)
4. Non-synthetic PK columns in `_category_key.name` order
5. Remaining domain columns alphabetically

---

### `SchemaSpec`

```python
@dataclass
class SchemaSpec:
    tables:                  dict[str, TableDef]
    column_to_tag:           dict[tuple[str, str], str]   # (table, col) → _definition.id
                                                          # synthetics excluded
    alias_to_definition_id:  dict[str, str]               # old name → canonical definition_id
    deprecated_ids:          set[str]                     # definition_ids marked as deprecated
    warnings:                list[str]
    bridge_columns:          list[BridgeColumnDef]        # derived-column descriptors
    propagation_links:       dict[str, list[...]]         # FK propagation metadata
    dictionary_name:         str | None                   # DdlmDictionary.name of source
    source_files:            list[str]                    # paths/URIs of loaded dictionary files
    category_parent:         dict[str, str | None]        # table → parent table (None if root/skipped)
```

`alias_to_definition_id` and `deprecated_ids` are populated by `generate_schema`
from the source `DdlmDictionary`.  They allow `ingest()` to resolve aliases and
emit deprecation warnings without retaining a reference to the dictionary.
`dictionary_name` and `source_files` are copied from `DdlmDictionary` and used
by `check_fidelity` to annotate the fidelity report header.

---

### `generate_schema(dictionary)`

```python
def generate_schema(dictionary: DdlmDictionary) -> SchemaSpec:
```

Derives a `SchemaSpec` from a loaded `DdlmDictionary`.

- `"Set"` and `"Loop"` categories → one table each.
- `"Head"` → silently skipped.  Other classes → warn and skip.
- PK from `_category_key.name`; fallback `_pycifparse_id` (keyless Set, with warning)
  or `_block_id` + `_row_id` (keyless Loop, with warning).
- `_row_id` is present on all tables (Set and Loop); `_block_id` is always present
  but is only part of the PK for keyless Loop tables.
- `"Link"` items → `ForeignKeyDef` on the source table.
- `"SU"` items → `ColumnDef.linked_item_id` only; no FK constraint.
- `ColumnDef.type_contents` is populated from `_type.contents` (e.g. `"Text"`,
  `"Integer"`, `"Real"`, `"List"`, `"Table"`); `None` if absent from the dictionary.
- `ColumnDef.type_container` is populated from `_type.container` (e.g. `"Single"`,
  `"Matrix"`, `"List"`, `"Array"`); `None` for synthetic columns or if absent from
  the dictionary.  Columns with `type_container != "Single"` store JSON.
- All SQL identifiers (table and column names) are double-quoted to handle
  reserved keywords.

---

### `emit_create_statements(schema)`

```python
def emit_create_statements(schema: SchemaSpec) -> list[str]:
```

Returns one `CREATE TABLE IF NOT EXISTS` string per table.  All value columns
are declared `TEXT` regardless of `ColumnDef.type_contents`.  All FK constraints
carry `DEFERRABLE INITIALLY DEFERRED`.  On tables where `_row_id` is not already
part of the `PRIMARY KEY`, a table-level `UNIQUE ("_block_id", "_row_id")`
constraint is emitted.

This function generates SQLite-compatible DDL for inspection and documentation
purposes.  The DuckDB ingest path (`ingest()`) creates its own tables internally
and does not use this function.

---

### `emit_fallback_create_statements()`

```python
def emit_fallback_create_statements() -> list[str]:
```

Returns SQL strings describing the infrastructure tables created internally
by `ingest()`: the `CREATE TABLE IF NOT EXISTS` for `_cif_fallback`, its
lookup index, `_block_dataset_membership`, and `_validation_result`.

This function is for inspection and documentation purposes.  The DuckDB ingest
path creates these tables internally; callers do not need to call this function.

**`_cif_fallback`** — stores all tag/value pairs not routed to a structured table.
PK: `(_block_id, _row_id, tag)`.

| Column | Type | Nullable |
|---|---|---|
| `_block_id` | TEXT | NOT NULL |
| `_row_id` | INTEGER | NOT NULL |
| `tag` | TEXT | NOT NULL |
| `value` | TEXT | nullable |
| `value_type` | TEXT | NOT NULL |
| `loop_id` | INTEGER | nullable |
| `col_index` | INTEGER | nullable |

**`_block_dataset_membership`** — records which dataset(s) each block belongs to,
and the `id_regime` determined at ingestion time.
PK: `(_block_id, _audit_dataset_id)`.

| Column | Type | Notes |
|---|---|---|
| `_block_id` | TEXT NOT NULL | Block name |
| `_audit_dataset_id` | TEXT NOT NULL | Dataset ID; `''` for general blocks |
| `id_regime` | TEXT NOT NULL | `'dataset'`, `'uuid'`, or `'assumed'` |

**`_validation_result`** — namespace validation results; rowid table (no domain PK).

| Column | Type | Notes |
|---|---|---|
| `check_name` | TEXT NOT NULL | `uuid_regime`, `uuid_reference_check` |
| `severity` | TEXT NOT NULL | `'Warning'` or `'Info'` |
| `block_id` | TEXT | nullable |
| `detail` | TEXT | nullable |
| `id_regime` | TEXT | nullable |

---

### `ResolvedTag`

```python
@dataclass
class ResolvedTag:
    definition_id: str    # current canonical tag name, lowercased
    category_id:   str    # table name (_name.category_id)
    object_id:     str    # column name (_name.object_id)
    was_alias:     bool   # True if input matched via an alias
    is_deprecated: bool   # True if the definition has been superseded
```

---

### `resolve_tag(tag, dictionary)`

```python
def resolve_tag(tag: str, dictionary: DdlmDictionary) -> ResolvedTag | None:
```

Looks up `tag` (case-insensitive) in `dictionary.tag_to_item`.  Returns `None`
if the tag is unknown — this is the signal that the tag belongs to the fallback
tier, not an error.  Does not emit warnings; the caller acts on
`was_alias` and `is_deprecated`.

```python
from pycifparse import resolve_tag

r = resolve_tag('_atom_site.fract_x', d)
if r is None:
    print('unknown tag — fallback tier')
elif r.was_alias:
    print(f'alias for {r.definition_id}')
elif r.is_deprecated:
    print(f'deprecated; use {d.items[r.definition_id].replaced_by}')
```

---

### `save_dictionary(dictionary, path)` / `load_dictionary(path)`

```python
def save_dictionary(dictionary: DdlmDictionary, path: str | pathlib.Path) -> None: ...
def load_dictionary(path: str | pathlib.Path) -> DdlmDictionary: ...
```

Serialise and deserialise a `DdlmDictionary` to/from a JSON file.  Avoids
re-parsing constituent CIF files on every program start.

Cache invalidation is the caller's responsibility.  These functions make no
attempt to detect whether the source dictionary files have changed.

`load_dictionary` raises `ValueError` if the file is missing, malformed, or
references an unknown `definition_id`.  The caller should fall back to
`DictionaryLoader.load()` on error.

**Example:**

```python
from pycifparse import (
    DictionaryLoader, directory_resolver,
    save_dictionary, load_dictionary,
)
import pathlib

cache = pathlib.Path('cif_pow_cache.json')
resolver = directory_resolver('data/dictionaries')

if cache.exists():
    d = load_dictionary(cache)
else:
    src = open('data/dictionaries/cif_pow.dic').read()
    d = DictionaryLoader(resolver=resolver).load(src)
    save_dictionary(d, cache)
```

---

## Ingestion layer (`pycifparse.ingestion`)

### `ingest(cif, db, schema, ...)`

```python
def ingest(
    cif: CifFile,
    db: duckdb.DuckDBPyConnection | str | pathlib.Path | None = None,
    schema: SchemaSpec | None = None,
    *,
    propagate_fk: bool = False,
    dataset_id: str | None = None,
) -> tuple[duckdb.DuckDBPyConnection, list[str]]:
```

Reads a parsed `CifFile` and ingests its contents into a DuckDB database.
Schema setup (table creation, infrastructure tables) is performed internally —
no separate `apply_schema` call is required.

Tags known to the schema are written to their structured tables; unknown tags
(or all tags when `schema=None`) are written to `_cif_fallback`.

**`db` parameter:**
- `None` (default) — create an in-memory DuckDB connection.
- `str` or `pathlib.Path` — open (or create) a file-backed DuckDB database.
- `duckdb.DuckDBPyConnection` — use the caller-supplied connection directly.

The returned connection is the same object supplied or created.  The caller owns
the lifecycle when passing an existing connection.

**Tag routing:**

1. Lowercase the tag name.
2. Resolve via `schema.alias_to_definition_id`; if found, use the canonical
   definition ID; otherwise use the tag as-is.
3. If the canonical ID is in `schema.deprecated_ids`, emit a non-fatal
   warning once per unique tag per block.
4. Look up the canonical ID in the inverted `column_to_tag` map.  If found →
   structured table route; otherwise → `_cif_fallback`.

**Value encoding** (presence-state encoding):

| Stored value | CIF meaning |
|---|---|
| `NULL` | tag absent from this block / row |
| `'.'` | inapplicable (PLACEHOLDER) |
| `'?'` | unknown (PLACEHOLDER) |
| `'"."'` | quoted dot |
| `'"?"'` | quoted question mark |
| `'\x00' + JSON` | CIF list or table container |
| raw string | real scalar value |

**SU splitting:** Values matching `{numeric}({digits})` are split into the
measurand column (bare numeric) and the linked SU column (digit string),
identified via `ColumnDef.linked_item_id`.

**FK propagation and parent-row stub creation:**

Key-FK columns are resolved from (in order): the same loop iteration → scalar
context from the same block (`fk_accumulator`) → a fresh UUID with warning.

Non-key FK columns are propagated only when `propagate_fk=True`.

For every resolved FK value, `ingest` ensures the referenced parent row exists;
if absent, a stub row is inserted with only the PK populated.

**Cross-block merging:** Rows with the same PK across blocks are merged (first
value wins; conflict → warning).  `_block_id` records the first contributing
block.

**Dataset namespace:**

`ingest` computes the intersection of `_audit_dataset.id` values across all
dataset blocks.  Raises `ValueError` if the intersection is empty and at least
one dataset block exists.

`dataset_id`: when provided, ingests only blocks whose `_audit_dataset.id` set
contains that value, plus all general blocks.

**Parameters:**

| Parameter | Type | Notes |
|---|---|---|
| `cif` | `CifFile` | Parsed CIF; duplicate tags are undefined behaviour |
| `db` | `DuckDBPyConnection \| str \| Path \| None` | Target database; `None` → new in-memory DB |
| `schema` | `SchemaSpec \| None` | `None` → all tags to `_cif_fallback` |
| `propagate_fk` | `bool` | Propagate non-key FK columns from block context |
| `dataset_id` | `str \| None` | Select one dataset from a multi-dataset file |

**Returns:** `tuple[duckdb.DuckDBPyConnection, list[str]]` — the database
connection and semantic error/warning strings in emission order.

**Raises:** `IngestionError` for fatal errors (key collisions, FK violations).
`ValueError` for incompatible datasets or unknown `dataset_id`.

---

## Database utilities (`pycifparse.database`)

### `convert_database(src, dst, schema, on_coercion_failure='null') -> list[str]`

One-way export that copies *src* into *dst*, casting columns to typed DuckDB
storage (`INTEGER`, `DOUBLE`, `VARCHAR`) based on `ColumnDef.type_contents`.
All tables and columns are preserved.  The source database must have been
populated by `ingest()`; all its columns are `VARCHAR`.

```python
from pycifparse import convert_database
import duckdb

src_conn, _ = ingest(cif, schema=schema)
dst_conn = duckdb.connect()

warnings = convert_database(
    src=src_conn,
    dst=dst_conn,
    schema=schema,
    on_coercion_failure='null',   # 'null' | 'keep' | 'error'
)
```

**Type mapping** (from `ColumnDef.type_contents` and `ColumnDef.type_container`):

| Condition | DuckDB type |
|---|---|
| `type_container` is not `"Single"` (e.g. `"Matrix"`, `"List"`) | `VARCHAR` (JSON) |
| `type_contents == "Integer"` | `INTEGER` |
| `type_contents` in `("Real", "Float")` | `DOUBLE` |
| anything else / `None` | `VARCHAR` |

**Special value handling:**

- **Sentinels** — `'.'` and `'?'` always become `NULL`; no warning.
- **SU suffixes** — values like `'1.23(5)'` have their trailing `(\d+)` stripped
  before numeric casting, always with a warning.
- **Non-Single containers** — JSON is decoded, each string leaf cast to the leaf type,
  then re-serialised.  The column retains `VARCHAR` type in *dst*.

**`on_coercion_failure` policy:**

| Value | Behaviour |
|---|---|
| `'null'` (default) | Store `NULL`; append warning |
| `'keep'` | Leave original value as `NULL` (DuckDB enforces column types); append warning |
| `'error'` | Raise `ValueError` immediately |

**Returns:** `list[str]` — warning messages for SU-dropped values and coercion failures.

---

## Fidelity layer (`pycifparse.fidelity`)

### `FidelityMismatch`

```python
@dataclass
class FidelityMismatch:
    kind:        str                           # machine-readable category (see below)
    source:      Literal['a', 'b', 'both']    # which source the mismatch is tied to
    description: str                           # human-readable explanation
```

**`kind` values:**

| Kind | Meaning |
|---|---|
| `'parse_error'` | Source failed to parse; no data comparison performed |
| `'ingest_error'` | Source failed to ingest; no data comparison performed |
| `'table_missing'` | Table present in one source but absent in the other |
| `'row_content'` | Row exists in one source with no equivalent in the other |
| `'value_type'` | Same tag/value in both fallback tiers but different `ValueType` |
| `'schema_mismatch'` | Tag in `_cif_fallback` in one source but in a structured table in the other |
| `'fallback_mismatch'` | Tag/value appears in `_cif_fallback` in one source but not the other |

---

### `FidelityReport`

```python
@dataclass
class FidelityReport:
    passed:     bool
    mismatches: list[FidelityMismatch]
```

`passed` is `True` iff `mismatches` is empty.

---

### `check_fidelity(source_a, source_b, schema, ...)`

```python
def check_fidelity(
    source_a: str | pathlib.Path | CifFile,
    source_b: str | pathlib.Path | CifFile,
    schema:   str | pathlib.Path | SchemaSpec | None = None,
    *,
    version:     CifVersion = CifVersion.CIF_2_0,
    report_file: str | pathlib.Path | None = None,
) -> FidelityReport:
```

Compares two CIF sources for semantic equivalence by ingesting both into
in-memory DuckDB databases and comparing the resulting data at the row level.
Never raises — all errors (parse, ingest) are captured as `FidelityMismatch`
entries in the returned report.

**Source formats:** each of `source_a` / `source_b` may be:
- A file path (`str` or `pathlib.Path`)
- A multi-line `str` containing raw CIF content
- A pre-parsed `CifFile` object

**`schema`:** controls structured-table comparison.  `None` compares only
`_cif_fallback`.  Accepts `SchemaSpec`, a `.json` cache path, or a `.dic`
DDLm dictionary path.

**`report_file`:** when supplied, a human-readable text report is written to
this path (UTF-8) before returning.

**Comparison semantics:**
- Block names, block order, and row order are irrelevant.
- UUID primary keys are matched by content fingerprint rather than by value.
- SQL `NULL`, `'.'`, and `'?'` are treated as equivalent for structured table
  columns — all mean "no data here".
- Real-valued columns are compared after normalisation (preserves significant
  figures; collapses scientific notation).
- `_cif_fallback` comparison preserves `ValueType` — `'.'` as `PLACEHOLDER`
  differs from `'.'` as `STRING`.

```python
from pycifparse import check_fidelity

report = check_fidelity(
    'tests/cif_files/multi_one.cif',
    'tests/cif_files/multi_one_as_oneblock.cif',
    schema,
    report_file='fidelity_report.txt',
)
if report.passed:
    print('PASSED')
else:
    for m in report.mismatches:
        print(f'[{m.kind}] {m.description}')
```

---

## `visualise_schema` / `visualise_schema_html`

```python
from typing import Literal
from pycifparse import visualise_schema, visualise_schema_html

def visualise_schema(
    schema: SchemaSpec,
    *,
    show_columns: Literal['all', 'sparse', 'none'] = 'sparse',
    show_bridge: bool = True,
    show_parent_edges: bool = True,
    highlight_orphans: bool = True,
    highlight_components: bool = False,
    show_orphans: bool = True,
    show_legend: bool = True,
    concentrate: bool = False,
    hide_deprecated: bool = False,
    layout: str = 'dot',
) -> str: ...

def visualise_schema_html(
    schema: SchemaSpec,
    *,
    title: str | None = None,
    show_columns: Literal['all', 'sparse', 'none'] = 'sparse',
    show_bridge: bool = True,
    show_parent_edges: bool = True,
    highlight_orphans: bool = True,
    highlight_components: bool = False,
    show_orphans: bool = True,
    show_legend: bool = True,
    concentrate: bool = False,
    hide_deprecated: bool = False,
    layout: str = 'dot',
) -> str: ...
```

`visualise_schema` returns a Graphviz DOT string.  No side effects, no file I/O.

`visualise_schema_html` calls `visualise_schema` internally and returns a
self-contained HTML string with viz.js and svg-pan-zoom inlined — write to `.html`
and open in any browser.  No network access required.

**Parameters:**

| Parameter | Default | Effect |
|---|---|---|
| `show_columns` | `'sparse'` | `'all'` every column; `'sparse'` PK + FK-source + bridge columns only; `'none'` header only |
| `show_bridge` | `True` | Include dashed bridge-column edges (always shown when target is a ghost node or table is `[BRIDGE ONLY]`) |
| `show_parent_edges` | `True` | Include dotted category-parent hierarchy edges (always shown when target is a ghost node) |
| `highlight_orphans` | `True` | Apply `[ORPHAN]` / `[BRIDGE ONLY]` badges and coloured borders |
| `highlight_components` | `False` | Wrap each connected component in a `subgraph cluster_` box |
| `show_orphans` | `True` | `False` removes `[ORPHAN]` and `[BRIDGE ONLY]` nodes and their edges |
| `show_legend` | `True` | Emit a `__legend__` node summarising node colours, connectivity badges, edge styles, and column badges; content adapts to the active flags |
| `concentrate` | `False` | Set `concentrate=true` in the DOT graph attributes; Graphviz merges parallel edges sharing a common endpoint into a shared spine, reducing clutter |
| `hide_deprecated` | `False` | Omit deprecated columns from column rows; remove any table where every non-synthetic column is deprecated (no ghost node, no edges) |
| `layout` | `'dot'` | Graphviz layout engine (`'fdp'` or `'neato'` for dense schemas) |
| `title` *(html only)* | `None` | `<title>` text; falls back to `schema.dictionary_name` then `'Schema'` |

**Node appearance:**

Each table renders as an HTML-like record node.  The header row shows the table name,
a `[Set]` or `[Loop]` badge, and a connectivity badge where applicable:

| Badge | Border | Meaning |
|---|---|---|
| *(none)* | solid | Directly connected via FK or parent-hierarchy |
| `[BRIDGE ONLY]` | yellow dashed | Isolated from pass 1; reachable only via a bridge column |
| `[ORPHAN]` | red dashed | No inter-table relationship of any kind |
| `[MISSING]` | red dashed, grey bg | Ghost node — referenced but not present in `schema.tables` |

Column rows (for `'all'` and `'sparse'`) show `object_id` with a `TOOLTIP` containing
the full `definition_id`.  Badges per column: `[PK]` (bold), `[JSON]`
(`type_container != 'Single'`), `[SU]` (`linked_item_id` set — SU annotation only,
no edges).  `type_contents` shown in parentheses when present.

**Edge types:**

| Edge | Style | Direction | Label |
|---|---|---|---|
| FK | solid arrow | `source_table` → `target_table` | omitted when source column visible; shown for single-col FK when `show_columns='none'`; always for multi-col: `(col_a → ref_a), (col_b → ref_b)` |
| Bridge | dashed `#888888` | `bc.table_name` → `bc.bridge_table` | `via {via_column}` |
| Parent-hierarchy | dotted, open arrowhead | `child` → `parent` | — |

**Ghost nodes** appear when `fk.target_table`, `bc.bridge_table`, or a
`category_parent` value is not present in `schema.tables`.  They are always shown
(even when `show_orphans=False`) and are not subject to connectivity classification.

**Connectivity algorithm** (two-pass):
- Pass 1: BFS over undirected FK + parent-hierarchy adjacency → connected components.
  Single-node components are "directly isolated".
- Pass 2: each endpoint of a `BridgeColumnDef` is independently checked against the
  directly-isolated set → `[BRIDGE ONLY]` if isolated there.
- Remaining isolated tables → `[ORPHAN]`.

`ColumnDef.linked_item_id` (SU associations) is excluded from connectivity analysis.

```python
dot = visualise_schema(schema, show_columns='sparse', highlight_orphans=True)
# write to file or paste into https://dreampuf.github.io/GraphvizOnline/

html = visualise_schema_html(schema, title='cif_core.dic schema')
with open('schema.html', 'w', encoding='utf-8') as f:
    f.write(html)
```

---

## Validation layer (`pycifparse.validation`)

```python
from pycifparse import validate, ValidationReport, ValidationIssue
```

### `ValidationIssue`

```python
@dataclass
class ValidationIssue:
    stage:      Literal['parse', 'ingest', 'database']
    severity:   Literal['Error', 'Warning', 'Info']
    check:      str           # machine-readable check name (see table below)
    message:    str           # human-readable description
    block:      str | None    # data block name; None for parse-stage issues
    tag:        str | None    # _definition.id of the failing tag
    value:      str | None    # failing value as stored in the database
    line:       int | None    # source line (parse stage only)
    col:        int | None    # source column (parse stage only)
    table:      str | None    # DuckDB table name (database stage only)
    column:     str | None    # DuckDB column name (database stage only)
    row_id:     int | None    # _row_id of the failing row (database stage only)
    key_values: dict[str, str | None] | None  # PK tag → value for the row
```

**Check names:**

| `stage` | `check` | `severity` | Meaning |
|---|---|---|---|
| `parse` | `lexical` / `syntactic` / `semantic` | Error | Parse error from `CifParser` |
| `parse` | `internal_error` | Error | Unexpected exception during parsing |
| `ingest` | `ingest` | Error or Warning | Message from `ingest()` `on_error` callback |
| `ingest` | `fk_violation` | Error | FK constraint violated during ingestion |
| `ingest` | `dataset_error` | Error | Invalid `dataset_id` or schema mismatch |
| `ingest` | `internal_error` | Error | Unexpected exception during ingestion |
| `database` | `unknown_tag` | Warning | Tag routed to `_cif_fallback` (not in schema) |
| `database` | `keyless_set_cardinality` | Error | Keyless Set table has more than one row per block |
| `database` | `type_container` | Error | Value is not valid JSON for a non-Single container |
| `database` | `table_key_not_quotable` | Error | Table key cannot be expressed as an inline CIF 2.0 string |
| `database` | `type_dimension` | Error | Container shape does not match `_type.dimension` |
| `database` | `type_contents` | Error or Warning | Leaf value does not match `_type.contents` format |
| `database` | `enumeration_range` | Error | Leaf value outside `_enumeration.range` bounds |
| `database` | `enumeration_states` | Error | Leaf value not in `_enumeration_set.state` list |
| `database` | `internal_error` | Error | Unexpected exception during DB validation |

---

### `ValidationReport`

```python
@dataclass
class ValidationReport:
    passed:   bool                    # True iff no Error-severity issues
    issues:   list[ValidationIssue]
    database: duckdb.DuckDBPyConnection | None  # in-memory DB; None if ingest failed
```

`passed` is `True` when `issues` contains no `'Error'`-severity entry.
The in-memory `database` is available for further querying after validation;
it is `None` when ingestion fails or an internal error occurs during DB validation.

---

### `validate()`

```python
def validate(
    source: str | pathlib.Path | CifFile,
    schema: SchemaSpec | None = None,
    *,
    parse_errors: list[ParseError] | None = None,
    block_id: str | None = None,
    dataset_id: str | None = None,
    propagate_fk: bool = False,
) -> ValidationReport
```

Parse (if needed), ingest to an in-memory database, and validate against the schema.
Never raises; all exceptions are captured as `internal_error` issues.

**Stages:**

1. **Parse** — if `source` is a `str` or `Path`, it is parsed with `build()` and any
   `ParseError` objects collected.  If `source` is an already-built `CifFile`, you may
   supply a pre-collected `parse_errors` list; a `UserWarning` is emitted if
   `parse_errors` is supplied alongside a `str`/`Path` source (it would be ignored).

2. **Ingest** — the `CifFile` is ingested into an in-memory DuckDB database via
   `ingest()`.  Messages sent to `on_error` become `Warning` issues; an `IngestionError`
   causes those same messages to be classified as `Error` where appropriate, and
   `database` is set to `None`.

3. **Database** — only when `schema` is not `None` and ingestion succeeded.
   Runs checks A–E (container type, dimension, contents, enumeration range, enumeration
   states) on every domain column, plus `unknown_tag` and `keyless_set_cardinality`.

**Parameters:**

| Parameter | Default | Effect |
|---|---|---|
| `source` | — | CIF source: raw string, `Path`, or pre-built `CifFile` |
| `schema` | `None` | If supplied, enables Stage 3 database checks |
| `parse_errors` | `None` | Pre-collected errors when passing a `CifFile` |
| `block_id` | `None` | Restrict Stage 3 checks to one data block |
| `dataset_id` | `None` | Passed to `ingest()` for multi-dataset merge |
| `propagate_fk` | `False` | Passed to `ingest()` |

```python
report = validate("##CIF_2.0\ndata_test\n_atom.type_symbol Cu\n", schema=schema)
if not report.passed:
    for issue in report.issues:
        print(f"[{issue.severity}] {issue.stage}/{issue.check}: {issue.message}")

# Query the ingested database directly:
if report.database:
    rows = report.database.execute('SELECT * FROM atom').fetchall()
```

---

## Known limitations

- `emit_defaults=False` in `emit()` — suppressing default-fill values requires per-value provenance tracking (not yet implemented)
- `uuid_reference_check` post-ingestion validation — stubbed; no rows written
- `inspect_ingest` full per-tag routing trace (tag → table.column) — currently captures warnings, errors, and FK violations only
