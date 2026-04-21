# pycifparse — Output Specification Reference

This document describes how CIF emission is controlled: the `EmitMode` enum, `OutputPlan`,
`BlockSpec`, the `emit()` function, and the formatting options that govern the final text.

See `docs/api.md` for the broader API reference.  See `example_workflow.py` for end-to-end
usage.

---

## Overview

The output layer reads from a SQLite database that was previously populated by `ingest()`.
It does not write to the database.

The central entry point is `emit()`:

```python
from pycifparse import emit, EmitMode, OutputPlan, BlockSpec, CifVersion

cif_text = emit(conn, schema, mode=EmitMode.ORIGINAL, version=CifVersion.CIF_2_0)
```

Three interacting decisions control what comes out:

| Decision | Controlled by |
|---|---|
| How rows are partitioned into CIF blocks | `EmitMode` |
| Which blocks appear together and in what order | `OutputPlan` + `BlockSpec` |
| How the text is formatted | `emit()` keyword arguments |

---

## EmitMode

```python
from pycifparse import EmitMode
```

`EmitMode` is an enum with four values.  Exactly one is chosen per `emit()` call.

---

### `EmitMode.ORIGINAL` (default)

One output block per distinct `_block_id` value.  Reconstructs the original CIF blocks
exactly as they were before ingestion — the inverse of `ingest()`.

- FK-PK columns are suppressed: if a FK column is redundant because the referenced Set
  category is co-emitted in the same block, the FK column is omitted.
- Loop FK-PK columns are also suppressed (`suppress_loop_fk_pk=True`).
- Block order follows the `_block_order` table if present (written during ingestion);
  falls back to sorted order for databases that pre-date that table.
- `_audit_dataset.id` is **not** injected.

---

### `EmitMode.GROUPED`

One block per unique combination of Set-category anchor key values.  Rows from multiple
original blocks that share the same Set-level identity are merged into one output block.

**Anchor determination:**  For each table, the emitter performs a BFS over the FK graph
to find the nearest reachable Set-class ancestor.  Three outcomes are possible:

1. A Set-class table is directly reachable → that Set is the anchor.
2. A Loop-class intermediate is reachable and has an onward FK to a Set → one-hop chain;
   that Set is the anchor.
3. No Set is reachable → the table falls back to `_block_id` grouping.

Keyless Set tables (whose only domain PK is `_pycifparse_id`) have no cross-block
identity and also fall back to `_block_id` grouping.

- FK-PK suppression is enabled (`suppress_fk_pk=True`).
- `_audit_dataset.id` is **not** injected.

---

### `EmitMode.ONE_BLOCK`

All data from the database is collapsed into a single CIF block named `output`.

- FK-PK suppression is **disabled** (suppress_fk_pk=False).
- `_audit_dataset.id` is **not** injected.
- If dictionary metadata is available (`title`, `version`, or `uri` on the dictionary)
  and the database does not already contain `audit_conform` data, conformance tags are
  injected at the top of the block:
  - `_audit.schema  Custom`
  - `_audit_conform.dict_name` ← `_dictionary.title`
  - `_audit_conform.dict_version` ← `_dictionary.version`
  - `_audit_conform.dict_location` ← `_dictionary.uri`
- The block name is always `output`; `BlockSpec.block_namer` and `OutputPlan.block_namer`
  are not consulted.

---

### `EmitMode.ALL_BLOCKS`

One CIF block per schema table, further split by Set-key combination.  This mode is
dictionary-driven: each block corresponds to a single schema-defined concept.

**Block generation rules per category type:**

| Category type | Blocks generated | Block name |
|---|---|---|
| Set | One block per row | `{table}_{pk_val…}` |
| Loop, no Set-key FKs | One block for all rows | `{table}` |
| Loop, with Set-key FKs | One block per unique Set-key combination | `{table}_{set_val…}` |

For Loop categories with Set-key FKs, the Set-key values are emitted as scalar
tag–value pairs above the loop body; the corresponding FK columns are suppressed from
the loop header.

**Guards — `ValueError` is raised if:**
- Any `_cif_fallback` rows are present.  Unknown tags cannot be unambiguously assigned
  to a dictionary-split block.
- Any keyless Set table (one whose only domain PK is `_pycifparse_id`) contains rows.

**Dataset ID injection:**  Per block, the emitter resolves the originating `_block_id`
values from `_block_dataset_membership` and injects `_audit_dataset.id`:

- Exactly one distinct ID found → inject as a scalar value.
- Multiple IDs found → inject as a single-column `loop_`.
- None found → generate a fresh UUID (CIF 2.0 only); the same UUID is shared across
  all blocks that have no membership data.

**Table ordering** follows `OutputPlan.category_order` from the first `BlockSpec` that
declares one.  Unspecified tables follow alphabetically (Set-class before Loop-class).
Spec matching (`BlockSpec.matches`) is **not** applied in ALL_BLOCKS mode.

---

## OutputPlan

```python
from pycifparse import OutputPlan
```

`OutputPlan` is an optional dataclass passed to `emit()` via the `plan` parameter.
When `plan=None` (the default), all blocks use alphabetical category ordering.

```python
plan = OutputPlan(specs=[...], block_namer=my_namer)
cif_text = emit(conn, schema, plan=plan)
```

### Fields

#### `specs: list[BlockSpec]` — default `[]`

Ordered list of `BlockSpec` objects.  For each output block the emitter evaluates specs
in index order and assigns the **first matching spec** (first-match-wins).  Blocks with
no matching spec use default alphabetical ordering.

Emission order:
1. All blocks assigned to `specs[0]`, sorted alphabetically by block name.
2. All blocks assigned to `specs[1]`, sorted alphabetically by block name.
3. … (subsequent specs in order)
4. All unmatched blocks, sorted alphabetically.

In `ALL_BLOCKS` mode, spec matching is skipped.  Only `category_order` from the first
spec that declares one is used; the rest of the spec machinery is bypassed.

#### `block_namer: Callable[[dict[str, list[str]]], str] | None` — default `None`

Global fallback block name override.  Called when the matched `BlockSpec` has no
`block_namer` of its own.

Signature: `(anchor_key_dict: dict[str, list[str]]) -> str`

The argument maps `'{table_name}.{pk_col}'` to a list of key values.  In GROUPED mode
each list has one element; when `single_block=True` on the matched spec, it may have
multiple.

Fallback chain (first non-None result wins):
1. `BlockSpec.block_namer` on the matched spec.
2. `OutputPlan.block_namer`.
3. Default construction rule (see [Default block naming](#default-block-naming) below).

---

## BlockSpec

```python
from pycifparse import BlockSpec
```

`BlockSpec` is a dataclass that pairs a predicate (which blocks match) with emission
behaviour (how those blocks are rendered).

```python
spec = BlockSpec(
    matches=lambda fs: 'cell' in fs,
    category_order=['symmetry', 'cell', 'atom_site'],
    column_order={'atom_site': ['label', 'type_symbol', 'fract_x', 'fract_y', 'fract_z']},
    single_block=False,
    block_namer=None,
)
```

### Fields

#### `matches: Callable[[frozenset[str]], bool] | None` — default `None`

Predicate applied to the block's anchor frozenset (the set of Set-category table names
present in the block).  Returns `True` if the spec applies.

`None` acts as a catch-all: the spec matches every block.  Useful as the last entry in
`OutputPlan.specs` to set default formatting for all unmatched blocks.

```python
# Match blocks containing both 'cell' and 'atom_site'
matches=lambda fs: 'cell' in fs and 'atom_site' in fs

# Match blocks containing any table whose name starts with 'pd_'
matches=lambda fs: any(t.startswith('pd_') for t in fs)

# Catch-all
matches=None
```

#### `category_order: list[str | list[str]]` — default `[]`

Emission order of categories (tables) within a block.  Unlisted categories are appended
after listed items, alphabetically (Set-class before Loop-class).

Each element may be:

**Plain string** — names a single category:
```python
category_order=['symmetry', 'cell', 'atom_site']
```

**String ending with `*`** — wildcard, expands to that category plus all schema
descendants (via `category_parent` BFS), sorted alphabetically.  Unknown base names
emit a warning and expand to nothing:
```python
category_order=['symmetry', 'atom*']
# 'atom*' might expand to: 'atom', 'atom_site', 'atom_site_aniso', ...
```

**`list[str]`** — a merge group.  The listed categories are checked for key
compatibility (identical non-synthetic PK columns).  If compatible they are emitted as
a single `loop_` via FULL OUTER JOIN.  If not compatible, they fall back to plain loops
in listed order with no warning.  Elements within a merge group may include wildcards:
```python
category_order=[
    'symmetry',
    ['atom_site', 'atom_site_aniso'],   # merge group — single loop_ if PKs match
    'cell*',                             # wildcard
]
```

#### `single_block: bool` — default `False`

When `True`, all blocks matching this spec are collapsed into one output block via an
internal merge step.

Effects when `True`:
- `table_rows` from all source blocks are combined.
- `fallback_rows` from all source blocks are combined.
- `anchor_key_dict` entries are merged (no duplicates).
- FK-PK suppression is disabled in the merged block.
- The resulting block name is resolved from the merged `anchor_key_dict`.

When `False` (default), one output block is produced per unique combination of anchor
key values matching this spec.

#### `column_order: dict[str, list[str]]` — default `{}`

Emission order of columns within specific categories.  Maps category (table) name to an
ordered list of column names.

Listed columns appear in declared order.  Remaining columns follow alphabetically.
Synthetic columns (`_block_id`, `_row_id`, `_pycifparse_id`) and null-only columns are
never emitted regardless of this setting.

```python
column_order={
    'atom_site': ['label', 'type_symbol', 'fract_x', 'fract_y', 'fract_z'],
    'cell': ['length_a', 'length_b', 'length_c', 'angle_alpha', 'angle_beta', 'angle_gamma'],
}
```

In ALL_BLOCKS mode, `column_order` is inherited even when `preferred_category_order`
overrides the category ordering.

#### `block_namer: Callable[[dict[str, list[str]]], str] | None` — default `None`

Per-spec block name override.  Same signature and semantics as `OutputPlan.block_namer`
but takes priority over it.

---

## Default block naming

When no `block_namer` is supplied (or one returns `None`/empty string), block names are
constructed from anchor key values:

1. For each `(key, values)` pair in the sorted `anchor_key_dict`:
   - Extract the column name from the key: `'cell.length_a'` → `'length_a'`
   - For each value, append `'{col}_{value}'` to a parts list.
2. Join all parts with underscores.
3. Sanitize:
   - Replace any non-alphanumeric character (except `_`) with `_`.
   - Collapse consecutive underscores to one.
   - Strip leading/trailing underscores.
4. If the result is empty, fall back to `'block'`.
5. Deduplicate: when multiple blocks produce the same sanitized name, append `_2`, `_3`,
   etc. to later occurrences.

---

## `emit()` function

```python
from pycifparse import emit

cif_text: str = emit(
    conn,
    schema,
    *,
    mode=EmitMode.ORIGINAL,
    version=CifVersion.CIF_2_0,
    plan=None,
    reconstruct_su=False,
    emit_defaults=True,
    line_ending='\n',
    pretty=True,
    line_limit=2048,
)
```

### Required parameters

#### `conn: sqlite3.Connection`

Open SQLite connection populated by `ingest()`.  The emitter reads from it but does
not write.  Lifecycle management (open/close) is the caller's responsibility.

#### `schema: SchemaSpec`

The `SchemaSpec` that was used when the database was ingested.  Provides table
definitions, column metadata, and tag-to-column mappings.  Emission from a fallback-only
database (no dictionary) works as long as `_cif_fallback` is populated.

### Keyword parameters

#### `mode: EmitMode` — default `EmitMode.ORIGINAL`

Block partitioning strategy.  See [EmitMode](#emitmode) above.

#### `version: CifVersion` — default `CifVersion.CIF_2_0`

Output CIF version.  Controls:
- Magic line: `##CIF_2.0` vs `##CIF_1.1`.
- Quoting: triple-quoted strings and text-prefix protocol are CIF 2.0 only.
- Container values (lists, tables): CIF 2.0 only.
- CIF 1.1 identifier length cap: block names, data names, and frame codes must not
  exceed 75 characters; `emit()` raises `ValueError` if any would exceed this.

#### `plan: OutputPlan | None` — default `None`

Custom ordering and grouping specification.  `None` uses alphabetical defaults.

In ALL_BLOCKS mode only `category_order` from the first spec is consulted; spec matching
is bypassed.

#### `reconstruct_su: bool` — default `False`

When `True`, paired `(measurand_col, su_col)` columns are merged during emission into
`value(su)` tokens (e.g. `1.234(5)`).  The pairing is determined by `ColumnDef.linked_item_id`.
The stored data is not modified; this is a presentation-only transformation.

#### `emit_defaults: bool` — default `True`

Accepted but currently has no effect.  Future: when `False`, columns filled entirely
from `enumeration_default` would be suppressed.

#### `line_ending: str` — default `'\n'`

Line separator written between every line and at the end of output.  Common values:
`'\n'` (Unix), `'\r\n'` (Windows), `'\r'` (legacy CR).  Line length enforcement
(`line_limit`) is measured before `line_ending` is applied.

#### `pretty: bool` — default `True`

When `True`:
- Set-category tag names are padded to the width of the longest tag in that category.
- Loop columns are aligned: each column as wide as its widest token.
- Real/Float columns are decimal-aligned (integer parts right-justified, fractional
  parts left-justified).
- Columns containing multiline tokens are excluded from alignment.

When `False`, compact output: tokens separated by two spaces with no padding.
Recommended for very large loop tables where the alignment pass is expensive.

#### `line_limit: int | None` — default `2048`

Maximum physical line length in characters (before `line_ending`).  `None` disables
all line-length enforcement.  Values less than 40 cause a `UserWarning`.

Enforcement rules:
- **Multiline text fields:** Content lines exceeding `line_limit` are folded using the
  CIF 2.0 line-folding protocol.  If the content also contains `\n;`, the text-prefix
  protocol is combined.
- **Inline scalar tokens:** If `tag + separator + token` exceeds `line_limit`, the
  token is re-quoted as a semicolon-delimited text field.
- **Loop data rows:** Tokens are greedy-packed across multiple physical lines when a
  formatted row would exceed `line_limit`.  Individual tokens are never split.

CIF 1.1 identifier length (75 characters) is a hard limit independent of `line_limit`.

### Return value

A complete CIF string.  Structure:
- Magic line as the first line.
- One or more `data_` blocks, separated by two blank lines.
- Categories emitted as Set (scalar tag–value) or Loop constructs.
- Fallback rows grouped by tag.
- No trailing whitespace on any line.
- Always ends with `line_ending`.

### Exceptions

| Condition | Exception |
|---|---|
| ALL_BLOCKS + `_cif_fallback` rows present | `ValueError` |
| ALL_BLOCKS + keyless Set table has rows | `ValueError` |
| CIF 1.1 + block/data-name/frame-code > 75 chars | `ValueError` |

---

## Category ordering in detail

The order in which categories are emitted within a block is determined as follows:

1. If the matched `BlockSpec` has a non-empty `category_order`, apply it (see
   `BlockSpec.category_order` above for wildcard and merge-group expansion).
2. Otherwise, if `_BlockData.preferred_category_order` is set (ALL_BLOCKS mode: parent
   tables before child), apply that.
3. Otherwise, alphabetical order (Set-class before Loop-class within the block).

In all cases, categories not mentioned in the ordering are appended alphabetically after
the explicitly ordered ones.

---

## Column ordering in detail

Within a single category, column order is:

1. Columns listed in `BlockSpec.column_order[table_name]`, in declared order.
2. Remaining non-synthetic, non-null columns, alphabetically (PK columns first among
   the remainder).

Synthetic columns (`_block_id`, `_row_id`, `_pycifparse_id`) and null-only columns are
never emitted.

---

## FK-PK suppression

In ORIGINAL and GROUPED modes, columns that are redundant because the referenced Set
category is co-emitted in the same block are suppressed.

Suppression applies when:
- All FK source columns on a table are part of the primary key.
- The referenced Set category has exactly one row in the same block.
- All rows' FK values match that row's PK values.

The effect is that the FK column disappears from the output, because the tag–value pair
at the Set level already carries that information.

In ORIGINAL mode, this suppression is also applied to Loop categories
(`suppress_loop_fk_pk=True`).  In GROUPED and ONE_BLOCK modes it is not.

---

## Merge groups

A merge group (`list[str]` inside `category_order`) emits two or more categories as a
single `loop_`.

**Compatibility check:** The categories must share identical non-synthetic PK column
sets.  If they do not, they are emitted as separate plain loops in listed order (no
warning is raised).

**FULL OUTER JOIN algorithm:**
1. Each category is indexed by PK tuple.
2. The full set of unique PK tuples is collected in encounter order.
3. The merged loop header is: shared PK columns (from the first category), then each
   category's non-PK columns in listed order.
4. For each unique PK tuple, values from all categories are combined; `NULL` fills in
   for missing rows.

---

## Quoting

The `quote()` function selects the shortest valid CIF token for a stored value.
It is also exported for use outside `emit()`:

```python
from pycifparse import quote
token = quote('hello world', CifVersion.CIF_2_0)  # → "'hello world'"
```

### Stored-value encoding

Values in the database use a presence-state encoding (Lesson 19):
- `'.'` or `'?'` (length 1) → PLACEHOLDER; emitted unquoted.
- `'"."'` or `'"?"'` (length 3) → quoted dot/question-mark; the inner character is
  re-quoted as a normal string value.
- Container values (CIF 2.0) → start with an internal prefix; decoded and rendered as
  `[…]` (list) or `{…}` (table).
- Everything else → treated as a plain string.

### CIF 2.0 quoting priority

1. **Bare word** — if the value contains no whitespace, no quote characters, does not
   start with `_`, `#`, `$`, `[`, `{`, `'`, or `"`, is not `.` or `?`, and is not a
   CIF keyword (`loop_`, `stop_`, `global_`) or reserved prefix (`data_`, `save_`).
2. **Single-quoted** — `'value'` — if the value has no newline and no single-quote.
3. **Double-quoted** — `"value"` — if the value has no newline and no double-quote.
4. **Triple-single-quoted** — `'''value'''` — CIF 2.0 only; if the value contains no
   triple-single and value does not end with `'`.
5. **Triple-double-quoted** — `"""value"""` — CIF 2.0 only; analogous.
6. **Semicolon-delimited** — `\n;value\n;` — when all inline options are exhausted.
7. **Prefixed semicolon** — `\n;>\\\n>lines…\n;` — when the value itself contains
   `\n;` (which would prematurely terminate a plain semicolon field).

### CIF 1.1 quoting

Same as CIF 2.0 but without triple-quoted string support.  Any value containing both
quote types, or any value containing a newline, falls directly to semicolon-delimited.

### Semicolon folding

When `line_limit` is set and a content line in a semicolon field exceeds the limit, the
CIF 2.0 line-folding protocol is applied: continuation lines get a `\\` fold marker
appended, and each logical line is broken at a space boundary within the budget.

Prefix and fold may be combined when both conditions apply simultaneously.

---

## Complete example

```python
import sqlite3
import pycifparse as cif

# Load dictionary and generate schema
loader = cif.DictionaryLoader()
dictionary = loader.load('cif_core.dic')
schema = cif.generate_schema(dictionary)

# Parse and ingest
conn = sqlite3.connect(':memory:')
cif.apply_schema(conn, schema)
cif.apply_fallback_schema(conn)

cif_file = cif.build(open('structure.cif').read())
cif.ingest(conn, cif_file, schema)

# Emit with default settings (ORIGINAL mode)
print(cif.emit(conn, schema))

# Emit in ONE_BLOCK mode with column ordering
plan = cif.OutputPlan(specs=[
    cif.BlockSpec(
        category_order=['symmetry', 'cell', ['atom_site', 'atom_site_aniso'], 'audit'],
        column_order={
            'atom_site': ['label', 'type_symbol', 'fract_x', 'fract_y', 'fract_z', 'u_iso_or_equiv'],
        },
    )
])
print(cif.emit(conn, schema, mode=cif.EmitMode.ONE_BLOCK, plan=plan))

# Emit in GROUPED mode with a custom block namer
def my_namer(anchor_dict):
    for key, vals in anchor_dict.items():
        if key.endswith('.id'):
            return vals[0]
    return 'block'

plan2 = cif.OutputPlan(block_namer=my_namer)
print(cif.emit(conn, schema, mode=cif.EmitMode.GROUPED, plan=plan2))

# Emit in ALL_BLOCKS mode with category ordering
plan3 = cif.OutputPlan(specs=[
    cif.BlockSpec(
        category_order=['cell', 'symmetry', 'atom_site'],
    )
])
print(cif.emit(conn, schema, mode=cif.EmitMode.ALL_BLOCKS, plan=plan3))
```

---

## `OutputPlan.specs` input for ALL_BLOCKS ordering

In ALL_BLOCKS mode only the first spec that declares a non-empty `category_order` is
consulted.  The `matches`, `single_block`, and `block_namer` fields of every spec are
ignored.  A minimal plan for ordering only:

```python
plan = OutputPlan(specs=[
    BlockSpec(category_order=['cell', 'symmetry', 'atom_site'])
])
```

All categories not listed appear after the listed ones, alphabetically.

---

## Interaction summary

| Parameter / field | ONE_BLOCK | ORIGINAL | GROUPED | ALL_BLOCKS |
|---|---|---|---|---|
| `BlockSpec.matches` | consulted | consulted | consulted | ignored |
| `BlockSpec.category_order` | applied | applied | applied | first spec only |
| `BlockSpec.column_order` | applied | applied | applied | applied |
| `BlockSpec.single_block` | applied | applied | applied | ignored |
| `BlockSpec.block_namer` | ignored (name is always 'output') | applied | applied | ignored |
| `OutputPlan.block_namer` | ignored | applied | applied | ignored |
| FK-PK suppression | off | on (Set + Loop) | on (Set only) | on (Set-key columns) |
| `_audit_dataset.id` injection | no | no | no | yes (per block) |
| Conformance tag injection | yes (if dict metadata present) | no | no | no |
| `_cif_fallback` rows | allowed | allowed | allowed | raises ValueError |
