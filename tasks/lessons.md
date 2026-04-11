# pycifparse — Lessons Learned

## Lesson 1 — Multiline text field closing delimiter (2026-04-04)

**Context:** Lexer `_read_multiline` implementation.

**Mistake:** After consuming the closing `\n;`, added `_skip_to_eol()` to discard remaining content on the closing line. This silently dropped valid tokens (e.g. `1.0` in `simple_loops.cif`'s `; 1.0`).

**Correct rule:** Per CIF 2.0 EBNF, `text-delim = line-term, ';'`. The closing delimiter is exactly two characters (`\n` + `;`). After consuming them, the lexer returns to NORMAL state immediately. Content after the closing `;` is tokenised normally — if it's a comment it's skipped by comment handling, if it's a value it becomes the next token.

**How to apply:** Never skip anything after the closing `;`. The line boundary is not special; only the two-character delimiter matters.

---

## Lesson 2 — Sequential loops are not nested loops (2026-04-04)

**Context:** Parser `_handle_keyword` for `loop_`.

**Mistake:** Added a `if self._in_loop: halt` guard for `loop_` keywords, treating any `loop_` encountered while a loop was active as a fatal "nested loop" error. This caused `simple_loops.cif` (with three sequential loops) to halt after the first loop.

**Correct rule:** Per CLAUDE.md: "on_loop_end emitted on: EOF, new tag, new loop, new save frame, new data block, STOP_". A `loop_` keyword always terminates the current loop via `_prepare_for_keyword` and starts a fresh one. A "nested loop" in the CIF sense is not representable in the flat token stream — there is no construct that creates a structurally nested loop.

**How to apply:** `loop_` should always call `_prepare_for_keyword` (which closes any active loop/containers/tag) then `_start_loop`. Never halt on `loop_` seen while `_in_loop` is True.

---

## Lesson 4 — `@property` preferred over `cached_property` during incremental construction (2026-04-05)

**Context:** `CifSaveFrame.loops` and `CifSaveFrame.tags` in `cifmodel/model.py`.

**Decision:** Used plain `@property` (recomputed on every access) rather than `cached_property`.

**Reason:** `_loops` and `_tag_order` are mutated during construction by `CifBuilder` (via `_add_loop`, `_append_value`). `cached_property` stores the result on first access and never recomputes — so every mutation would require explicit cache invalidation (`del self.loops`), adding noise to every internal mutation method.

**How to apply:** Use `cached_property` only on data that is immutable after construction, or where the cache lifetime can be clearly defined. For properties backed by lists that grow during construction, plain `@property` is simpler and correct. Switch to `cached_property` only if profiling identifies it as a hot path.

---

## Lesson 3 — `:` is not a bare-word terminator in CIF 2.0 (2026-04-04)

**Context:** Lexer `_read_bare_word` and `tokens()` for CIF 2.0.

**Mistake:** Added `:` as a terminator inside `_read_bare_word` for CIF 2.0. This split valid unquoted values like `2007-12-18T12:16:55+02:00` into multiple tokens, generating spurious "value has no preceding tag" errors.

**Correct rule:** Per CIF 2.0 EBNF, `restrict-char = non-blank-char - ('[' | ']' | '{' | '}')`. The colon is NOT excluded from `restrict-char`, so it is a legal character inside a `wsdelim-string`. The `:` table separator only appears at the start of a new token position (directly after a quoted key, with no preceding whitespace). It is emitted as a standalone token only by the outer `tokens()` loop (when `:` is the first character seen in NORMAL state), never by `_read_bare_word`.

**How to apply:** Do not break on `:` inside `_read_bare_word`. Only the `tokens()` loop emits `:` as a standalone VALUE token.

---

## Lesson 5 — `build()` convenience function is unspecified (2026-04-05)

**Context:** Stage 2 (`cifmodel/builder.py`).

**Decision:** Added `build(source, *, mode='pad') -> tuple[CifFile, list[ParseError]]` as a convenience wrapper around `CifBuilder` + `CifParser`.

**Status:** Not mentioned in CLAUDE.md or the parser prompt. Added as a practical utility. If the spec is later updated to prescribe a different top-level API shape, this function may need to change.

**How to apply:** Treat `build()` as a convenience shortcut, not a canonical API. Do not design downstream layers to depend on it exclusively.

---

## Lesson 6 — Empty loop handling is an extension of the spec (2026-04-05)

**Context:** `CifBuilder.on_loop_end()` in Stage 2.

**Decision:** A loop with zero values (tags declared, no values before loop end) is treated as a distinct semantic error with message "no values", separate from the row-count mismatch error.

**Status:** The prompt specifies row-count validation ("validate that the number of values received is divisible by the number of loop tags") but does not explicitly address the zero-values case. Our handling is a reasonable extension — zero is not divisible by any positive tag count — but it goes beyond what is written.

**How to apply:** If the spec is later clarified to prescribe different behaviour for empty loops (e.g. silent discard, or merging with row-count mismatch), revisit `on_loop_end()`.

---

## Lesson 7 — Strict mode extended beyond its specified scope (2026-04-05)

**Context:** `CifBuilder` mode parameter in Stage 2.

**Decision:** The prompt defines strict/pad mode only for loop row-count mismatch recovery. We applied the same strict mode behaviour (stop accumulating after first semantic error) to two additional cases: empty loops and duplicate block/save frame names.

**Status:** This extension is internally consistent and conservative (strict means strict), but it is not spec-backed for these cases. The duplicate name spec says only "emit `on_error`" with no mention of strict/pad distinction.

**How to apply:** If the spec is later updated to define strict/pad behaviour for these cases differently, the `_semantic_error` helper in `CifBuilder` will need case-specific handling rather than a single `_stopped` flag.

---

## Lesson 8 — Empty save frame names are not recoverable (2026-04-05)

**Context:** Parser `_handle_keyword` for `save_`.

**Decision:** Empty save frame names are not supported, unlike empty data block names (which are handled — error emitted, name stored as `""`).

**Reason:** `save_` is syntactically unambiguous as a frame-close token. There is no token form that could mean "open a save frame with an empty name" without conflicting with the close semantics. The only available heuristic — treating `save_` outside a frame as an opener — would silently misinterpret a common error (accidental `save_` outside a frame) as an empty-named frame open.

**Practical justification:** Save frames appear almost exclusively in DDLm dictionaries, which are well-formed. An empty save frame name in a real file would indicate severe malformation; treating it as a recoverable condition adds complexity for no practical benefit.

**How to apply:** Do not attempt to recover empty save frame names. `save_` outside a save frame remains a syntactic error and is ignored. This is a deliberate deviation from the general principle of allowing empty names with an error.

---

## Lesson 9 — Use a consistent docstring style to support autogeneration (2026-04-05)

**Context:** Project-wide docstrings reviewed ahead of potential documentation autogeneration.

**Problem:** Docstrings are currently inconsistent — a mix of one-liners, Sphinx-style `*name*`
emphasis, and NumPy-style `Parameters` blocks. Public API methods (`__getitem__`, `__contains__`,
`get_all`) have no parameter or return documentation. Private helpers sometimes have more
documentation than public methods.

**How to apply:** When writing or updating docstrings, follow a single style throughout.
NumPy style is preferred (used in `debug.py`):

```python
def method(self, name: str) -> list[CifBlock]:
    """Short one-line summary.

    Longer description if needed.

    Parameters
    ----------
    name:
        Description of the parameter.

    Returns
    -------
    list[CifBlock]
        Description of what is returned.

    Raises
    ------
    KeyError
        If the name is not found.
    """
```

Public methods must always document parameters, return values, and exceptions.
Private methods (`_name`) need only a one-liner. This keeps autogeneration viable
without adding noise to internal code.

---

## Lesson 10 — `:` at the start of a bare-word value (2026-04-06)

**Context:** Lexer `tokens()` — CIF 2.0 table key/value separator handling.

**Mistake:** The `:` standalone-token path fired unconditionally whenever `:` appeared
as the first character in NORMAL state.  This split valid values like `:100.0`
(CIF enumeration range lower-bound) into a standalone `:` token followed by `100.0`,
causing the `:` to be assigned as the tag value and `100.0` to become an orphan.

**Correct rule:** `:` is only a table separator when it is directly adjacent to the
preceding token (no whitespace between them).  When preceded by whitespace it is
the start of a bare-word value and must be read by `_read_bare_word`, which does
not break on `:` — so `:100.0` becomes a single token.

**Fix:** Added `_last_was_ws: bool = True` to the lexer.  Set `True` after consuming
whitespace/newlines/comments; `False` after emitting any token.  Standalone `:` is
only emitted when `not self._last_was_ws`.

**Side effect:** `{ "key" :value }` (whitespace before `:`, no space after) now
produces value `":value"` rather than `"value"`, with a "not followed by : separator"
error instead of "whitespace between key and `:` separator".  The key is still
recovered correctly.  This is an acceptable trade-off — the ambiguity is
unresolvable once `:value` is a single token.

**How to apply:** Never break on `:` inside `_read_bare_word`.  Standalone `:` tokens
are only valid when the lexer is in a non-whitespace context (adjacent to a prior token).

---

## Lesson 11 — SU validation does not belong in the lexer (2026-04-06)

**Context:** `_check_su` function in `lexer/lexer.py`.

**Mistake:** Added a heuristic to flag bare words that look like `number(su)` but fail
the `\(\d+\)$` pattern as lexical errors.  This caused false positives on fax numbers
with area codes in parentheses (e.g. `12(34)9477334` in `cif_core.dic`) and any other
string that happens to start with a numeric pattern followed by `(`.

**Correct rule:** The CIF lexer has no concept of "numeric value with SU" distinct
from any other bare word.  Both are `ValueType.STRING` tokens.  Whether the SU
sub-expression is well-formed is a semantic question, not a lexical one.

**Fix:** Removed `_check_su`, `_NUMERIC_PREFIX_RE`, and `_VALID_SU_RE` entirely.

**How to apply:** Do not validate numeric sub-structure in the lexer.  SU format
validation belongs in the dictionary/ingestion layer where the expected type is known.

---

## Lesson 12 — Never infer category from tag name; always use `_name.category_id` (2026-04-06)

**Context:** Stage 3 import processing and all future dictionary/ingestion layers.

**Rule:** A tag's category is always the value of `_name.category_id` in its save
frame definition.  The dot-notation convention (`_category.object`) is not reliable —
`_name.category_id` can differ from the prefix of `_definition.id` (see the
`pd_instr` / `pd_meas` example in the Stage 3 prompt).

**Never** split a tag name on `.` or any other character to infer the category.
Always look up the tag's save frame and read `_name.category_id` directly.

**How to apply:** Wherever a tag's category or table name is needed — Loop category
detection, schema generation, FK resolution, ingestion routing — obtain it via
`DdlmItem.category_id` or by reading `_name.category_id` from the relevant save
frame.  String manipulation of tag names is never a substitute.

## Lesson 13 — Scope one debug_{thing} function per stage (2026-04-06)

**Context:** Stage 3 complete; considering debug utilities for new layers.

**Rule:** Each major pipeline stage that produces a non-trivial in-memory structure
should have exactly one `debug_{thing}` function scoped to its primary output:

| Stage | Primary output | Debug function |
|-------|---------------|----------------|
| Lexer | token stream | `debug_lex` |
| Parser + IR | `CifFile` | `debug_build` |
| Schema generator | `SchemaSpec` | `debug_schema` |
| Ingestion | SQLite rows | `debug_db` (future) |

The function should visualise whatever a developer needs to inspect when
something goes wrong at that stage — not a raw dataclass dump.

**What to skip:** A debug function for an intermediate structure
(`DdlmDictionary`, `TableDef`) is rarely worth the maintenance cost unless it
repeatedly comes up in practice.  A REPL with `resolve_tag` or a targeted
`print` is usually enough.  Add `debug_{thing}` only when the structure is
large, nested, or opaque enough that ad-hoc inspection is consistently painful.

**How to apply:** When starting a new stage, ask: what is the primary artifact
a developer inspects when this stage misbehaves?  Write one debug function for
that artifact.  Keep it in `debug.py` alongside existing helpers.

## Lesson 14 — Template files use save frame label as identifier, not `_definition.id` (2026-04-06)

**Context:** `_import.get` frame lookup in `DictionaryLoader._find_frame_by_definition_id`.

**Mistake:** Spec says to locate imported frames by `_definition.id` match. Implemented
exactly that. But template files (`templ_attr.cif`, `templ_enum.cif`) carry zero
`_definition.id` entries — their save frame label is their sole identifier. The import
looked up by `_definition.id`, found nothing, treated it as a miss, and aborted,
leaving `_type.contents` / `_type.purpose` unpopulated for hundreds of items.

**Correct rule:** Match by `_definition.id` when present (full dictionary frames);
fall back to save frame label when absent (template files). The `elif` is deliberate:
a frame that declares `_definition.id` is matched exclusively by that value, not
its label.

**How to apply:** Any future import resolution code must include this two-step
lookup. Never assume template files conform to the `_definition.id` convention.

## Lesson 15 — Category `_name.category_id` is the parent, not the table name (2026-04-06)

**Context:** `generate_schema` table naming and domain-item lookup.

**Mistake:** Used `cat_item.category_id` (= `_name.category_id` of the category frame)
as the SQL table name and as the filter for domain items.  In DDLm, a category
frame's `_name.category_id` is its **parent** category in the hierarchy — for
`ATOM_TYPE`, that is `ATOM`.  This produced a table named `atom` instead of
`atom_type`, with the wrong class and wrong PK.

**Correct rule:**
- Table name = `_table_name(cat_item.definition_id)` — the category's own
  canonical identifier.
- Domain items = items whose `item.category_id == cat_item.definition_id` — because
  items carry `_name.category_id` pointing to the category's `_definition.id`,
  not to the parent.
- `cat_item.category_id` is only relevant for understanding the category
  hierarchy; it plays no role in schema generation.

**How to apply:** Whenever iterating over categories to build tables, always key
on `definition_id`, never on `category_id`.

## Lesson 16 — Import identity tags must never be merged from a source frame (2026-04-06)

**Context:** `DictionaryLoader._merge_frame` — `_import.get` mode `"Contents"`.

**Mistake:** Initial merge logic treated `_definition.id`, `_definition.class`,
`_definition.scope`, and `_name.*` as ordinary tags subject to the `dupl` policy.
With `dupl=Exit` (default) these caused an abort whenever source and target shared
them.  With `dupl=Replace` they overwrote the target frame's own identity, so the
extracted `DdlmItem` carried the template's `definition_id` instead of the target's.

**Correct rule:** The set `_IMPORT_IDENTITY_TAGS` (`_definition.id`,
`_definition.scope`, `_definition.class`, `_name.category_id`, `_name.object_id`,
`_name.linked_item_id`, `_import.get`) defines the frame's own identity and must
always be skipped during merging — regardless of the `dupl` policy.  Only
attribute tags (`_type.*`, `_units.code`, `_description.text`, etc.) are merged.

**How to apply:** Any future import or merge operation must exclude identity tags
before applying conflict resolution.

## Lesson 17 — SQL identifiers must be double-quoted to handle reserved keywords (2026-04-06)

**Context:** `emit_create_statements` and `apply_schema`.

**Mistake:** Used bare table and column names in generated DDL.  `ddl.dic` contains
a category whose `definition_id` normalises to `update` — a reserved SQL keyword —
which caused a `sqlite3.OperationalError` when applying the schema.

**Correct rule:** Always wrap every SQL identifier (table name, column name, FK
reference) in double quotes in generated DDL: `"identifier"`.  Embedded double
quotes are escaped by doubling: `"it""s"`.  This is standard SQL and SQLite accepts
it unconditionally.

**How to apply:** Use a `_qi(name)` helper wherever an identifier appears in a
generated SQL string.  Never interpolate bare names directly into DDL.

## Lesson 18 — Python sqlite3 auto-commits DDL; use explicit BEGIN for transactional DDL (2026-04-06)

**Context:** `apply_schema` rollback-on-failure requirement.

**Mistake:** Used `with conn:` context manager expecting it to roll back a failed
`CREATE TABLE`.  Python's `sqlite3` module implicitly commits any pending
transaction before executing a DDL statement, so `CREATE TABLE` escapes the
context manager's rollback scope.

**Correct rule:** For transactional DDL in Python's `sqlite3`, set
`conn.isolation_level = None` (autocommit mode), issue `BEGIN` manually, execute
all DDL, then `COMMIT` or `ROLLBACK`.  Restore `isolation_level` in a `finally`
block.  This guarantees that all DDL within the block is atomic.

**How to apply:** Any function that executes DDL and must guarantee rollback on
failure should follow this pattern.  Do not rely on `with conn:` for DDL.

## Lesson 19 — CIF presence-state encoding in SQLite (2026-04-07)

**Context:** Structured table schema design; replaced status-column approach.

**Rule:** All value columns store TEXT. CIF presence states are encoded directly
in the value column using the following convention:

| Stored value | CIF meaning |
|---|---|
| `NULL` | tag absent from this data block |
| `'.'` | inapplicable (unquoted `.` — `ValueType.PLACEHOLDER`) |
| `'?'` | unknown (unquoted `?` — `ValueType.PLACEHOLDER`) |
| `'"."'` | literal `.` stored with delimiters — source `ValueType` was any of `DOUBLE_QUOTED`, `SINGLE_QUOTED`, `TRIPLE_DOUBLE_QUOTED`, `TRIPLE_SINGLE_QUOTED`, `MULTILINE_STRING` |
| `'"?"'` | literal `?` stored with delimiters — same set of source `ValueType`s |
| anything else | real value, stored as raw string |

**Why:** Status companion columns (`{col}_status`) doubled the column count and
added complexity to schema generation, ingestion, and queries. This encoding
preserves all CIF semantics in a single column. NULL means exactly one thing
(absent), which matches natural SQL semantics. `.` and `?` are the CIF
representations that any CIF user immediately recognises.

**`_cif_fallback` retains `value_type`:** The fallback table keeps its
`value_type` column because there is no schema type information to distinguish
bare-word values from quoted ones. `value_type` enables numeric coercion to
operate only on bare words, and the output layer to know which values to quote
on round-trip.

**How to apply:**
- At ingestion: inspect `ValueType`. `PLACEHOLDER` → store `'.'` or `'?'`.
  Any non-PLACEHOLDER ValueType whose raw string value is `.` or `?`
  (`DOUBLE_QUOTED`, `SINGLE_QUOTED`, `TRIPLE_DOUBLE_QUOTED`, `TRIPLE_SINGLE_QUOTED`,
  `MULTILINE_STRING`) → store `'"."'` or `'"?"'`.
  All other values → store raw string.
  Tag absent → do not insert row / leave column NULL.
- At query time: `WHERE col IS NOT NULL AND col NOT IN ('.', '?')` selects rows
  with real values.
- At output: `NULL` → omit tag. `'.'` → emit `.`. `'?'` → emit `?`.
  `'"."'` → emit `"."`. `'"?"'` → emit `"?"`. All other values → use `value_type`
  from `_cif_fallback` (or schema type) to decide quoting.

---

## Lesson 20 — `_row_id` uniqueness requires a composite constraint (2026-04-08)

**Context:** `emit_create_statements` in `schema.py`; Stage 4 schema design.

**Mistake:** Emitted `_row_id ... UNIQUE` as an inline column constraint.
At the time this was written, `_row_id` was assumed to reset to 1 at the start
of each block, so a multi-block CIF would produce duplicate `_row_id` values in
the same table. `UNIQUE` on `_row_id` alone would fire on the second block's
first row.

**Later clarification (Stage 4):** `_row_id` is in fact global — it never
resets between blocks. A composite `UNIQUE (_block_id, _row_id)` constraint
is therefore stronger than strictly necessary, but it remains correct and is
the prescribed form regardless.

**Correct rule:** For tables where `(_block_id, _row_id)` is not already the
`PRIMARY KEY` (i.e. keyed Loop tables and all Set tables), emit a table-level
`UNIQUE ("_block_id", "_row_id")` constraint. For keyless Loop tables,
`(_block_id, _row_id)` is already the PK so no extra constraint is needed.

**How to apply:** Never use `_row_id UNIQUE`. Always use the composite form.

---

## Lesson 21 — Mixed loop cross-tier join requires shared `_row_id` per iteration (2026-04-08)

**Context:** `_cif_fallback` table design; Stage 4 ingestion.

**Problem:** A loop whose tags split between a structured table and `_cif_fallback`
produces rows in both locations. If `_row_id` increments per cell in `_cif_fallback`,
there is no join key linking a fallback cell to the structured row from the same
loop iteration.

**Correct rule:** `_row_id` is scoped per table globally across the entire
`ingest()` call — it never resets between blocks. For a mixed loop, all
`_cif_fallback` cells from a given iteration share the same `_row_id` as the
corresponding structured table row — both draw from the structured table's counter.
The join key is `(_block_id, _row_id)` within that table + `_cif_fallback`.

For pure-fallback loops, `_cif_fallback` uses its own global counter,
incrementing once per iteration (not per cell).

**Consequence:** `_cif_fallback` PK is `(_block_id, _row_id, tag)` — `tag` is
needed because multiple cells (different tags) share `(_block_id, _row_id)` within
the same loop iteration.

**How to apply:** Maintain `_row_id_counters: dict[str, int]` (table name →
counter). For mixed loops, draw from the structured table's counter for both the
structured row and all fallback INSERTs for that iteration. For pure-fallback
loops, draw from `_cif_fallback`'s counter. `_row_id_counters` is initialised
once per `ingest()` call and never resets between blocks.

---

## Lesson 22 — Set category `_row_id` must be reserved at first tag encounter (2026-04-08)

**Context:** Stage 4 ingestion; scalar Set category accumulation strategy.

**Problem:** Scalar Set tags are accumulated during block traversal and INSERTed
at end of block. If `_row_id` is assigned at INSERT time, Set rows always get
higher `_row_id` values than Loop rows in the same block, regardless of their
position in the file. This breaks document order and the "scalar Set and
single-row loop are equivalent" guarantee.

**Correct rule:** When the **first scalar tag** of a Set category is encountered,
immediately reserve the current `_row_id_counter` for that category's pending row
and increment the counter. INSERT at end of block using the reserved value. This
places the Set row in document order relative to any Loop rows.

**How to apply:** Maintain `set_row_reservations: dict[str, int]` (table_name →
reserved `_row_id`) populated on first-tag-seen, drawing from that table's entry
in `_row_id_counters`. Use the reserved values when performing the end-of-block
INSERTs.

---

## Lesson 23 — Set categories can appear in loops; schema must accommodate both forms (2026-04-08)

**Context:** Stage 4 ingestion; Set table handling.

**Rule:** A DDLm Set category is *normally* represented by scalar tags (one logical
row per block), but the CIF format allows any category's tags to appear in a loop_
if the PK column is included. Both of these are valid and equivalent:

```
# scalar form
_cell.length_a 12
_cell.length_b 13

# looped form (single iteration)
loop_
_cell.length_a _cell.length_b
12 13
```

The ingestion layer must handle both. When a Set category appears in a loop, each
iteration produces a separate row with its own `_row_id`. The scalar accumulation
strategy (accumulate then INSERT at end of block) only applies to tags that arrive
outside a loop.

**How to apply:** Detect Set categories appearing inside a loop at ingestion time
and treat them as Loop-style rows (assign `_row_id` per iteration, pass through
merge algorithm). Do not defer these to end-of-block accumulation.

---

## Lesson 24 — A single logical entity may be spread across multiple CIF blocks (2026-04-08)

**Context:** Stage 4 ingestion; multi-block CIF files.

**Rule:** CIF allows a single dataset to be spread across multiple data blocks.
Tags from the same category with the same PK value across different blocks
describe the same logical row. The ingestion layer always merges such rows.

**Merge rules:**
- Rows with the same PK value (across any blocks) are merged into one row.
- First-seen block provides `_block_id` and `_row_id` for the merged row.
- First non-NULL value for each column wins; conflicts (two different non-NULL
  values for the same column) emit a semantic error and keep the first value.
- `_cif_fallback` rows are not merged; they remain block-local.

**`_row_id` implication:** `_row_id_counters` must not reset between blocks.
`_row_id` is effectively per-table globally across the whole `ingest()` call.
The counter increments once per new unique PK seen (across all blocks).

**Implementation:** Accumulate all structured rows in a `merged_rows` dict
(table → PK tuple → column dict) throughout the entire `ingest()` call. Perform
all SQL INSERTs after all blocks have been processed.

---

## Lesson 25 — `_audit_dataset.id` introduces a namespace; absence says nothing (2026-04-07)

**Context:** Stage 4 ingestion; multi-block CIF files with dataset IDs.

**Rule:** The presence of `_audit_dataset.id` in a block asserts that the block
belongs to a named dataset. The *absence* of `_audit_dataset.id` says nothing — it
does not mean the block is unrelated to other blocks.

**Two block classes:**
- **Dataset blocks** — carry one or more `_audit_dataset.id` values. Their PKs are
  unambiguous within the dataset because the dataset ID provides the namespace.
- **General blocks** — carry no `_audit_dataset.id`. May use UUIDs for uniqueness
  (high confidence) or short identifiers (assumed coherence, warn the user).

**`_audit_dataset.id` is a loop category** — a block may carry multiple dataset ID
values via a `loop_`. The set of values for each block is read from the IR before
any rows are written.

**Pre-ingestion check (fatal):** Before any database writes, `ingest()` computes
the intersection of dataset ID sets across all dataset blocks. If the intersection
is empty and at least one dataset block exists, a `ValueError` is raised and
nothing is written. General blocks (no `_audit_dataset.id`) are always included.

**`dataset_id` parameter:** Bypasses the intersection check. Only blocks whose
dataset ID set contains `dataset_id` are ingested (plus all general blocks).
Allows extracting one coherent dataset from a multi-dataset CIF file.

**Merge algorithm is unconditional.** The pre-ingestion check guarantees coherence;
there are no blocked merges. Same PK → always merge.

**`id_regime`** — recorded per ingested block in `_block_dataset_membership`:
- `'dataset'` — block carries `_audit_dataset.id`
- `'uuid'` — no dataset ID; all PK values pass UUID format check
- `'assumed'` — no dataset ID; PK values are not all UUIDs, **or** no structured-table rows exist (cannot determine UUID usage)

**Post-ingestion validation checks** (written to `_validation_result`):
- `uuid_regime` (Warning) — general block with non-UUID structured-table PKs.
- `uuid_reference_check` (Info) — general-block UUID PK not referenced by any
  dataset block as a FK value.

**Both tables** (`_block_dataset_membership`, `_validation_result`) are created by
`apply_fallback_schema()`, not `apply_schema()`.

---

## Lesson 26 — Single-iteration loops feed `fk_accumulator` (2026-04-09)

**Context:** Stage 4 ingestion; FK propagation source 2.

**Rule:** After any loop completes, if it produced exactly one iteration, write
every column value from that iteration into `fk_accumulator`. This makes the
values available for FK propagation in subsequent loops within the same block,
equivalent to a scalar. Multi-iteration loops do not feed `fk_accumulator`.

**Why:** A single-iteration loop is semantically equivalent to a set of scalar
tags. Parent-category IDs occasionally appear in a one-row loop rather than as
bare scalars; without this rule their values would be invisible to FK propagation
in later loops, forcing unnecessary UUID fallbacks.

**How to apply:** After processing each loop, check the iteration count. If
exactly 1, iterate over all column values produced (across all tables for
multi-category loops) and write them into `fk_accumulator` keyed by
`definition_id`. Do not write partial iterations — only after the loop is
confirmed to have had exactly one iteration.

## Lesson 27 — `ColumnDef.type_contents` is informational only; DDL always emits TEXT (2026-04-09)

**Context:** Stage 4 design review; `ColumnDef` field rename.

**Mistake:** `ColumnDef` originally had `sql_type: str` storing SQL type strings
(`"TEXT"`, `"INTEGER"`, `"REAL"`) for use in generated DDL. This conflicted with
the Lesson 19 decision that all value columns store TEXT for round-trip fidelity.

**Correct rule:** `ColumnDef.type_contents` stores the DDLm `_type.contents` value
(e.g. `"Text"`, `"Integer"`, `"Real"`, `"List"`) for future validation and
type-coercion use. It does not affect DDL generation. `emit_create_statements`
always emits `TEXT` for all value columns regardless of `type_contents`.

**How to apply:** Never use `type_contents` to determine the SQL column type in DDL.
Use it only in validation logic and in `convert_database` to guide coercion.

---

## Lesson 28 — `fk_accumulator` stores encoded database values, not raw `CifScalar` (2026-04-09)

**Context:** Stage 4 ingestion; FK propagation implementation.

**Rule:** Values written to `fk_accumulator` must be pre-encoded via `encode_value`
— i.e., in the exact form they will appear in the database column. FK propagation
then copies the value directly into the target column without re-encoding.

**Why:** Encoding at write-time (into the accumulator) rather than at read-time
(when propagating) keeps the propagation path simple: a straight dict lookup and
column assignment, with no type inspection at use time.

**How to apply:** Whenever a value is written to `fk_accumulator` — whether from
a scalar Set tag, a single-iteration loop, or a UUID fallback — always call
`encode_value` first. The accumulator type is `dict[str, str]`.

---

## Lesson 29 — Value encoding for quoted `.` and `?` covers all non-PLACEHOLDER ValueTypes (2026-04-09)

**Context:** Stage 4 value encoding table; extension of Lesson 19.

**Mistake:** The initial encoding table only listed `DOUBLE_QUOTED` for the
`'"."'` / `'"?"'` cases. `SINGLE_QUOTED`, `TRIPLE_DOUBLE_QUOTED`,
`TRIPLE_SINGLE_QUOTED`, and `MULTILINE_STRING` values whose content is `.` or
`?` were not covered, causing them to fall through to "raw string" and be stored
as `'.'` or `'?'` — indistinguishable from bare PLACEHOLDER values.

**Correct rule:** Any value whose raw content is `.` or `?` AND whose `ValueType`
is not `PLACEHOLDER` must be stored as `'"."'` or `'"?"'` respectively. This
applies to all five non-PLACEHOLDER ValueTypes:
`DOUBLE_QUOTED`, `SINGLE_QUOTED`, `TRIPLE_DOUBLE_QUOTED`, `TRIPLE_SINGLE_QUOTED`,
`MULTILINE_STRING`.

The detection logic at ingestion: `if raw in ('.', '?') and value_type != PLACEHOLDER`.

**How to apply:** The encoding table in Stage 4 prompt §Value encoding is the
authoritative reference. The `encode_value` function must check `value_type !=
PLACEHOLDER` (not `value_type == DOUBLE_QUOTED`) to catch all cases.

---

## Lesson 30 — Container `value_type` exists only in `_cif_fallback`; use `json_valid()` for structured tables (2026-04-09)

**Context:** Stage 4 container value handling; querying encoded container values.

**Rule:** The `value_type` column (`'list'` or `'table'`) exists only in
`_cif_fallback`. Structured tables have no `value_type` column. To detect a
container value in a structured table column at query time, use SQLite's
`json_valid(column)` function.

**Why:** Structured table columns are defined by the dictionary schema with no
metadata column. Adding a companion `{col}_type` column was rejected as it doubles
column count and was the same problem as the discarded status-column approach.

**How to apply:**
- In `_cif_fallback` queries: `WHERE value_type IN ('list', 'table')` — precise.
- In structured table queries: `WHERE json_valid(column)` — safe guard before
  calling any other JSON function. Never call `json_extract`, `json_each`, or
  `json_type` on a column without this guard (they raise on non-JSON input).

---

## Lesson 31 — `SchemaSpec` embeds alias resolution and deprecation; `ingest()` needs no dictionary reference (2026-04-10)

**Context:** Stage 4 design review; `ingest()` `dictionary` parameter.

**Mistake/Gap:** `ingest()` had a `dictionary: DdlmDictionary | None = None` parameter
used solely for alias resolution via `resolve_tag`. This forced the caller to pass
both `schema` (derived from the dictionary) and `dictionary` as separate arguments —
redundant and error-prone. It also meant `ingest()` retained an unnecessary dependency
on `pycifparse.dictionary.ddlm_parser`.

**Correct rule:** `SchemaSpec` is self-contained for routing:
- `alias_to_definition_id: dict[str, str]` — copied from `DdlmDictionary.alias_to_definition_id` by `generate_schema`; used in the tag routing loop to canonicalise aliases.
- `deprecated_ids: set[str]` — copied from `DdlmDictionary.deprecated_ids`; used to emit a non-fatal semantic warning when a deprecated tag name is encountered in a CIF file.

`ingest()` has no `dictionary` parameter. The `SchemaSpec` carries everything needed.

Deprecation warnings are non-fatal: ingestion proceeds normally, the warning is
appended to the return list, and (if provided) `on_error` is called.

**Why:** `SchemaSpec` is already the single authoritative artefact the caller
passes to `ingest()`. Embedding routing metadata there eliminates an implicit
dependency and makes the ingestion function's contract explicit.

**How to apply:**
- `generate_schema(dictionary)` must populate `alias_to_definition_id` and `deprecated_ids` from the `DdlmDictionary`.
- Tag routing (step 2): `canonical = schema.alias_to_definition_id.get(tag, tag)`.
- Deprecation check (step 3): `if canonical in schema.deprecated_ids and tag not in deprecated_warned: emit warning; deprecated_warned.add(tag)`. Use two message forms: alias case `"tag '{tag}' is deprecated (canonical: '{canonical}')"`, direct case `"tag '{tag}' is deprecated"`.
- `deprecated_warned` is a `set[str]` in per-block state; reset at the start of each block.
- Never pass `dictionary` to `ingest()` — it does not accept one.

---

## Lesson 32 — pytest must be run from the `.venv` (2026-04-10)

**Context:** Project uses a local virtual environment at `.venv/`.

**Rule:** Always run pytest as `.venv/Scripts/pytest` (Windows) — not a globally installed
`pytest`. The global interpreter will not have the project's dependencies.

**How to apply:** `.venv/Scripts/pytest -m "not slow" --tb=short -q` for the fast suite;
`.venv/Scripts/pytest -m slow` for integration tests.

---

## Lesson 33 — All public types returned by public functions must be top-level re-exports (2026-04-10)

**Context:** `CifScalar` was missing from `pycifparse/__init__.py`.

**Gap:** `CifScalar` was exported from `pycifparse.cifmodel` but not re-exported at the
top level. Any caller receiving a `CifScalar` from `block["_tag"]` could not write
type annotations, `isinstance` checks, or access `value_type` without importing from
the internal submodule path `pycifparse.cifmodel.scalar`.

**Correct rule:** Any type that appears in the return value of a public function, or
that a caller must inspect to use the API correctly, must be re-exported from the
top-level `pycifparse/__init__.py` and listed in the API Reference module layout.

**How to apply:** When adding a new public type at any stage, immediately add it to
`pycifparse/__init__.py` (import + `__all__`) and to the module layout comment in
`prompts/API Reference.md`. Do not leave public types stranded in submodule paths.

---

## Lesson 34 — `_post_validate` must run before `_flush`; validation rows are inserted in `_flush` (2026-04-10)

**Context:** `ingest.py` run order; `_validation_result` rows.

**Bug:** `_post_validate()` was called after `_flush()`. Since `_flush()` inserts
`self.validation_rows` into `_validation_result`, any rows appended by `_post_validate`
were never written to the database.

**Correct order:** `_post_validate()` → `_flush()` → `COMMIT`. Post-validation populates
`self.validation_rows`; the flush writes them.

**How to apply:** In any `run()` method that separates a validate step from a flush step,
validate first, then flush. If post-validation needs to write to the database, it must run
before the flush that writes its output table.

---

## Lesson 35 — `_apply_fk` must create stub parent rows for all FK values, not just UUID-generated ones (2026-04-10)

**Context:** `ingest.py` FK constraint satisfaction; `one_structure.cif` + `cif_core.dic` integration test.

**Problem (original):** When `_apply_fk` generated a UUID for a missing key-FK column, it
populated the child row but never created the corresponding parent row. SQLite's
`DEFERRABLE INITIALLY DEFERRED` FK constraint then fired at COMMIT with
`IntegrityError: FOREIGN KEY constraint failed`.

**Problem (broader):** The same constraint violation occurs for non-key FK columns that carry
an explicit value from CIF data (e.g. `atom_site.type_symbol = 'Se'` referencing `atom_type.symbol`)
when the parent table has no row for that value. The original fix only covered the UUID-generation
path; non-key FK columns with real data values were never checked.

**Fix:** In `_apply_fk`, after the value-assignment block, add an unconditional stub-creation
step: for any FK column that ends up with a non-NULL value (explicit, propagated, or UUID-generated),
call `_merge_into` on the parent table with a stub row containing only `_block_id` and the
FK target column set to that value. `_merge_into` is idempotent — if the parent row already
exists from real data, the stub is merged without overwriting any non-NULL values.

**How to apply:** Always pass `block_id`, `merged_rows`, and `row_id_counters` to `_apply_fk`
during schema-aware ingestion. These default to `None` (stub creation skipped) so unit tests
that call `_apply_fk` directly without a DB connection are unaffected.

---

## Lesson 36 — `_name.linked_item_id` must not be an import-identity tag (2026-04-10)

**Context:** `DictionaryLoader._resolve_imports` / `_merge_frame` in `loader.py`.

**Bug:** `_name.linked_item_id` was listed in `_IMPORT_IDENTITY_TAGS`, causing it to be
unconditionally skipped whenever a save frame merged attributes from a template via
`_import.get` (mode="Contents"). This is correct for true identity tags (`_definition.id`,
`_name.category_id`, `_name.object_id`) — you never want an import to change the frame's
own identity — but wrong for `_name.linked_item_id`, which is a *data attribute* that
templates are specifically designed to provide.

**Observed symptom:** `_geom_angle.atom_site_label_1` and `_geom_angle.atom_site_label_3`
(and similar FK-via-template items) had `type_purpose='Link'` but `linked_item_id=None`.
`generate_schema` skips items with `linked_item_id is None` during FK detection, so no FK
constraint was generated and no FK column was recognised in the schema.

**Root cause:** Both items import `[{'file':templ_attr.cif 'save':atom_site_id}]`, and the
`atom_site_id` template frame provides `_name.linked_item_id = '_atom_site.label'`. Because
`_name.linked_item_id` was in `_IMPORT_IDENTITY_TAGS`, `_merge_frame` skipped it regardless
of whether the importing frame had its own value.

**Fix:** Remove `_name.linked_item_id` from `_IMPORT_IDENTITY_TAGS`. The `dupl` policy in
`_merge_frame` already handles conflicts: if the importing frame already defines
`_name.linked_item_id`, the default `dupl='Exit'` would warn rather than silently overwrite.

**How to apply:** `_IMPORT_IDENTITY_TAGS` should only contain tags that define a frame's CIF
structural identity (definition id, scope, class, category, object). Tags that are data
attributes of the definition — even when they affect its semantic role (linked item, type
purpose, type contents) — must not be blocked from template inheritance.

---

## Lesson 37 — CIF 2.0 structural delimiters must not split tags or save frame names (2026-04-10)

**Context:** `Lexer._read_bare_word` in `lexer/lexer.py`.

**Bug:** `_read_bare_word` unconditionally broke on `[`, `]`, `{`, `}` for ALL bare words in
CIF 2.0 mode. This split tokens like `_axis.vector[1]` (tag) into `_axis.vector` + `[` + `1`
+ `]`, and `save_axis.vector[1]` (save frame name) into `save_axis.vector` + `[` + `1` + `]`.

**CIF 2.0 EBNF rule:**
- `restrict-char = non-blank-char - ( '[' | ']' | '{' | '}' )` — used by `wsdelim-string`
  (plain unquoted values). `[` terminates a plain value.
- `data-name = '_', non-blank-char, { non-blank-char }` — tags use `non-blank-char`, which
  includes `[`, `]`, `{`, `}`.
- `container-code = non-blank-char, { non-blank-char }` — save/data frame names also use
  `non-blank-char`.

So `[` terminates plain values but must NOT terminate tags or prefix keywords.

**Fix:** In the `_CIF2_DELIMITERS` break check, only break when the accumulator is empty
(delimiter starts its own standalone token) OR the accumulated word is a plain value — i.e.
it does NOT start with `_` (tag) and does NOT start with a prefix keyword (`save_`, `data_`).

**How to apply:** Whenever the CIF 2.0 EBNF distinguishes between `restrict-char` and
`non-blank-char` contexts, lexer logic must check what kind of token is being accumulated
before applying delimiter break rules.

## Lesson 38 — FK target must be the sole PK, not just any PK column (2026-04-10)

**Context:** `generate_schema` building `ForeignKeyDef` entries; `cif_pow.dic` ingestion.

**Mistake:** Initial fix checked `target_column not in primary_keys` to detect invalid FK targets.
This correctly caught columns that aren't PKs at all, but missed the case where the target column
IS listed in `primary_keys` but the PK is composite (e.g. `['id', 'variant']`). SQLite only creates
a UNIQUE index for a single-column PRIMARY KEY — a composite PK does NOT uniquely index any
individual column. So `FOREIGN KEY (x) REFERENCES t(id)` is also "foreign key mismatch" when
`t` has `PRIMARY KEY (id, variant)`.

**Correct check:** `tables[tgt_tbl].primary_keys != [target_item.object_id]` — the FK target
column must be the sole (and only) PK of the target table.

**How to apply:** Any time a FK constraint is being generated and the target table has a composite
PK, the FK is invalid unless it references ALL columns of the PK (i.e., the FK itself is composite).
Single-column FKs targeting individual columns of a composite PK must be skipped with a warning.

## Lesson 39 — Multi-category loop compatibility and PK propagation (2026-04-10)

**Context:** `_loops_compatible` and `_process_loop` in `ingest.py`; `cif_pow.dic` loops.

**Problem:** DDLm multi-category loops (e.g. `pd_data/pd_meas/pd_proc/pd_calc` sharing the
same `(point_id, diffractogram_id)` PK) were being routed to `_cif_fallback` with
"incompatible multi-category loop" because:

1. `_loops_compatible` compared FK-resolved target sets. After the composite-PK FK fix (Lesson 38),
   FKs like `pd_meas.point_id → pd_data.point_id` were correctly skipped (individual columns of
   a composite PK are not valid SQL FK targets). Without those FKs, each table's `_loop_target_set`
   resolved to a different self-reference, so the sets never matched.

2. Even if compatibility had passed, `_apply_fk` only fills columns that have an FK. Without FKs
   for `pd_meas/proc/calc.point_id` and `.diffractogram_id`, those PK columns would remain NULL.

**Fix (two parts):**
1. Changed `_loops_compatible` to compare non-synthetic PK column name sets instead of FK-resolved
   target sets. Tables with the same PK column names (e.g. all having `{point_id, diffractogram_id}`)
   are compatible. This is the authoritative DDLm signal: if categories appear in the same loop,
   they share the same key structure.
2. Added cross-table PK propagation in `_process_loop` after `_apply_fk` for all tables. For each
   iteration, collect all non-NULL PK values from all sibling rows (by column name), then fill NULL
   PK columns in sibling rows from the pool. This ensures `pd_meas.diffractogram_id` gets the same
   value as `pd_data.diffractogram_id` (which was filled by the FK-accumulator path).

**How to apply:** The two-part pattern (compatibility check + cross-propagation) is needed whenever
sibling-category tables link to each other through composite-PK columns. Never rely solely on SQL FK
constraints being present for PK fill logic.

---

## Lesson 40 — Composite FK groups with conflicting source columns (bond endpoints) (2026-04-11)

**Context:** `generate_schema` FK group loop; `_chemical_conn_bond` in `cif_core.dic`.

**Problem:** `_chemical_conn_bond.atom_1` and `.atom_2` both carry `type_purpose='Link'`
targeting `_chemical_conn_atom.number`. The FK-group loop detected `has_conflicts=True`
(multiple source columns pointing to the same target column) and skipped all FKs.

**Correct rule:** `has_conflicts=True` means multiple source columns independently reference
the same target — each reference is valid on its own. When all PK columns of the target table
are covered by the group AND there are no non-PK target columns, emit one `ForeignKeyDef` per
source column individually, rather than skipping the group.

**How to apply:** In the FK group loop, add a branch:
`if has_conflicts and not missing_pk_cols and not non_pk_tgt_cols:` — iterate over all
`(src_col, tgt_col)` pairs and emit a separate FK for each. Only skip when there is a genuine
ambiguity (missing PKs or conflicting non-PK targets).

---

## Lesson 41 — `_scalar` must not filter `.` when reading `_enumeration.default` (2026-04-11)

**Context:** `DictionaryLoader` `_scalar` helper; `_enumeration.default` in DDLm dictionaries.

**Problem:** `_scalar` filtered both `'.'` (inapplicable) and `'?'` (unknown) as CIF placeholders,
returning `default` (usually `None`) for both. `_enumeration.default = '.'` is a legitimate
dictionary value meaning "the enumeration default is the CIF inapplicable sentinel", but it was
being silently dropped, leaving `DdlmItem.enumeration_default = None`.

**Fix:** Added `keep_dot: bool = False` parameter to `_scalar`. When `True`, `'.'` is returned
as a real value. Call `_scalar(data, '_enumeration.default', keep_dot=True)`.

**How to apply:** Any `_scalar` call reading a tag where `'.'` is a semantically meaningful value
(not a missing-data placeholder) must pass `keep_dot=True`. The `'?'` filter (unknown/missing) is
always applied regardless.

---

## Lesson 42 — Propagation links use `enumeration_default` as fallback; not UUID generation (2026-04-11)

**Context:** `generate_schema` propagation links; `_diffrn_radiation.variant` and
`_diffrn_radiation_wavelength.radiation_id` in `cif_pow.dic`.

**Problem (original attempt):** PK Link columns whose FK was skipped (because the FK target had a
composite PK) were left NULL, causing NOT NULL constraint violations. A first fix attempted to
generate UUIDs as a last resort, but UUID stubs for columns like `variant` (no parent table to stub
into) were semantically wrong and caused FK violations in the parent stub.

**Correct rule:**
1. PK Link columns with skipped FKs are recorded in `propagation_links`. At ingest time, their value
   is filled from (in priority order): the current loop row's matching `definition_id`, then
   `fk_accumulator`, then `enumeration_default` from `DdlmItem`. No UUID generation.
2. These columns are marked `nullable=True` in the schema — NULL is valid when no value is available
   from any source.
3. `DdlmItem.enumeration_default` must be populated (see Lesson 41) for this to work when the CIF
   omits the tag entirely.

**How to apply:** The propagation link tuple is `(col_name, target_def_id, enumeration_default)`.
Unpack all three in `_apply_fk`. If no value is found from loop or accumulator, use `enumeration_default`
as the final fallback. If that is also `None`, leave the column NULL (which is now permitted).

---

## Lesson 43 — Use class-scoped fixtures for shared ingestion state in tests (2026-04-11)

**Context:** `tests/ingestion/test_integration.py`; `TestIngestWithSchema`, `TestIngestNoSchema`,
`TestIngestSecondShort`.

**Problem:** Each test method called `_conn_with_schema(...)` and `ingest(...)` independently.
For a class of 7 tests against `cif_core.dic`, this ran 7 full ingestions of the same CIF/schema
pair. Each ingestion is expensive (~0.5s); total wall time was proportionally wasteful.

**Correct rule:** When multiple tests in a class all query the same ingested database and none of
them mutate state (all queries are SELECT-only), use a `@pytest.fixture(scope='class')` that runs
ingestion once and shares the connection. All test methods take the fixture as a parameter.

**Caution:** Only safe when tests are read-only. If any test inserts, updates, or deletes rows,
shared connections cause cross-test pollution. Check all tests in the class before converting.

**How to apply:** Name the fixture `{descriptive}_conn` (e.g. `one_structure_conn`,
`second_short_conn`). Declare it at module level with `scope='class'`. Tests that verified the
ingest return value (e.g. `assert errors == []`) must be rewritten — the return value is discarded
by the fixture. Replace with an equivalent read assertion.
