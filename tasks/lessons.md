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
| `'"."'` | literal string `"."` (quoted dot — any quoted `ValueType`) |
| `'"?"'` | literal string `"?"` (quoted question mark — any quoted `ValueType`) |
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
  Quoted `.` or `?` → store `'"."'` or `'"?"'`. All other values → store raw string.
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
