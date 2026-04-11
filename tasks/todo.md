# pycifparse — Task Log

---

## ▶ RESUME FROM HERE

**Current stage:** Stage 5 complete. 969 tests passing. Ready for Stage 6 (output layer).

## Stage 5: Inspect Package — COMPLETE ✓

### Steps
- [x] Write plan to todo.md
- [x] Create `src/pycifparse/inspect/` package
  - [x] `_common.py` — shared ANSI/colour utilities, `resolve_source`, `fmt_value`
  - [x] `_lexer.py` — `inspect_lexer` (rename of `debug_lex`)
  - [x] `_parser.py` — `inspect_parse` + `ParseHandler` (rename of `debug_parse`/`DebugHandler`)
  - [x] `_model.py` — `inspect_model` (rename of `debug_build`)
  - [x] `_schema.py` — `inspect_schema` (rename of `debug_schema`; now also accepts `DdlmDictionary`)
  - [x] `_ingest.py` — `inspect_ingest` + `TraceEvent` (new)
  - [x] `__init__.py` — exports all public symbols
- [x] Delete `src/pycifparse/debug.py`
- [x] Rename `tests/test_debug.py` → `tests/test_inspect.py`, update all imports/references
- [x] Update `src/pycifparse/__init__.py` to export inspect symbols
- [x] Verify test suite still passes: **969 tests** (all passing)

**Open decisions carried forward:**
- `inspect_ingest` currently traces warnings/errors and FK violations. Routing events (tag → table)
  require hooks into `_Ingester` internals; deferred to when a `filter=` parameter is added.
- `filter=` parameter on `inspect_ingest` — unfiltered trace first; leave open for later.
- SQLite trace output for `inspect_ingest` — out of scope; leave open.

---

**Previous stage:** Stage 4 (SQLite ingestion) — complete.

**Test suite state (2026-04-11):**
- 936 tests pass (non-slow): `.venv/Scripts/pytest -m "not slow" --tb=short -q`
- 27 slow tests pass: `.venv/Scripts/pytest -m slow`
- Total: 963 passing

**What was just completed (this session):**
- **UUID-per-row for keyless loops** (`_process_loop`): added a post-`_apply_fk` UUID fill pass
  that handles all three keyless-PK scenarios uniformly — single-column key-FK, pure-key (no FK),
  and composite-key-FK components. Uses `pk_uuid_pool` to share UUIDs across sibling tables in a
  multi-category loop iteration. Topological stub ordering ensures grandparent rows exist before
  parent stubs are inserted (required for `DEFERRABLE INITIALLY DEFERRED` FK checks). Lessons 44–45.
- **`IngestionError`** (`ingestion/ingest.py`, `ingestion/__init__.py`, `__init__.py`): new exception
  class with `.errors: list[str]`. All blocks are processed first to collect all conflicts, then
  `IngestionError` is raised after the block loop. The existing `except Exception` handler in
  `run()` triggers `ROLLBACK`. Cross-block value conflicts are semantic errors (not warnings) —
  feeding multiple blocks implies they belong together; a value conflict means the blocks are
  incompatible.
- **Loop-class scalar buffering** (`_process_scalar`): Loop-class scalar tags are now accumulated
  into `loop_scalar_buffers` (parallel to `set_buffers`) and flushed as a complete row at end of
  block. Previously they were merged one tag at a time, so the PK column was absent from non-PK
  rows, causing all blocks to collide on PK = `(None,)`. Fixed false merge conflicts in
  `multi_one.cif` `pd_instr_detector` rows. Lesson 46.
- **`TestCoreRepeatedLoopKey`**: new test class covering the `core_repeated_loop_key.cif` fixture —
  exact duplicate silently dropped, value conflicts raise `IngestionError`, transaction rolled back.
- **`TestCoreMultipleBlocks`** updated: Block C's F1/Na1 conflicts with earlier blocks now raise
  `IngestionError` (6 errors); 0 rows after rollback.
- **`TestIngestMultiBlock`** corrected: was using `core_schema` for a powder diffraction file;
  switched to `pow_schema`. Assertion updated to check `pd_instr` and `pd_instr_detector` row
  counts instead of `_cif_fallback` (which is 0 when a schema is present).

**What comes next (Stage 5):**
- Consult `prompts/Stage5_Ingest_Debug_Prompt.md` before starting
- Implement `inspect_*` family in a new `inspect` module (exact module layout TBD — see open decisions)
- `inspect_lexer` and `inspect_parse` will wrap / replace `debug.py` functionality

**Open decisions / known limitations:**
- **Synthetic Set key scope — block-scoped vs block-category-scoped `_pycifparse_id`:**
  Cross-block UUIDs are always distinct (agreed). The question is within a single block: should all
  keyless Set tables share one UUID (block-scoped), or should each keyless Set table get its own
  UUID (block-category-scoped, current behaviour)?

  *Block-scoped:* one UUID `X` per block; every keyless Set table in that block gets `_pycifparse_id = X`.
  *Block-category-scoped (current):* each keyless Set table in a block independently generates its
  own UUID4, so `cell._pycifparse_id ≠ diffrn._pycifparse_id` even within the same block.

  *Arguments for block-scoped:*
  - A block describes one experiment/structure. All its keyless Set entities are facets of the same
    thing; a shared identifier reflects that unity.
  - FK chains between keyless Set tables would resolve trivially — if `cell.diffrn_id` is an FK to
    `diffrn._pycifparse_id` and both use the same UUID, no propagation is needed.
  - Simpler to generate: one `uuid4()` call per block rather than one per keyless Set table.

  *Arguments for block-category-scoped (current):*
  - Each Set category is a distinct entity; sharing a UUID across unrelated categories is
    semantically misleading and would cause spurious JOIN matches on `_pycifparse_id`.
  - FK propagation already handles cross-table wiring correctly via `fk_accumulator` keyed on
    named CIF tags (e.g. `_diffrn.id`), not on `_pycifparse_id` directly. The UUID values do
    not need to match across tables for FK resolution to work.
  - Block-scoped only helps FK resolution when the FK column IS `_pycifparse_id` in the target —
    which cannot happen for real dictionaries (they link via named items like `_diffrn.id`).

  *Current position:* block-category-scoped. The FK propagation machinery makes the block-scoped
  shortcut unnecessary, and distinct UUIDs per category are semantically cleaner.

- **Stage 5 module layout**: single `inspect.py` vs `inspect/` package with one module per layer.
- **`inspect_ingest` API shape**: context-manager collector vs flag gating a `TraceEvent` list.
- **`inspect_ingest` granularity**: pre-flush vs post-FK-resolution row snapshots.
- **`inspect_schema` input**: accept raw dictionary source string (parse internally) or require a
  pre-loaded `DdlmDictionary`?
- `uuid_reference_check` is a stub — no rows written in Stage 4. Implement in a later stage.
- Looped keyless Set: error is supposed to be emitted and UUID assigned per row, but this path is
  not explicitly tested. Covered implicitly by the `_pycifparse_id` test but no error-emission test.
- `_process_scalar` for the no-schema path uses `_row_id=1` for all scalars. In a block with
  duplicate scalar tags, the fallback PK (`_block_id, _row_id, tag`) will cause a DB-level error
  on the second occurrence. The spec says duplicate tags are undefined behaviour — caller must
  consolidate before `ingest()`. Documented in the Assumptions section of Stage4 prompt.

---

## Stage 4: SQLite Ingestion — Implementation Plan

### Step 1 — Module scaffolding ✓
- [x] Create `src/pycifparse/ingestion/__init__.py` (exports `ingest`)
- [x] Create `src/pycifparse/ingestion/ingest.py` (stub raising `NotImplementedError`)
- [x] Export `ingest` from `pycifparse/__init__.py`
- [x] Create `tests/ingestion/__init__.py`, `test_ingest.py`, `test_integration.py`
- [x] Confirm import works: `from pycifparse import ingest`

### Steps 2–10 ✓ COMPLETE
All implemented in `src/pycifparse/ingestion/ingest.py` and unit-tested in `tests/ingestion/test_ingest.py` (92 tests).

### Step 11 — Integration tests (`@pytest.mark.slow`) ✓
- [x] Ingest a real CIF file against `cif_core.dic` schema; spot-check known tag values in structured tables
- [x] No-schema ingest of the same file; verify all tags appear in `_cif_fallback`
- [x] Multi-block real CIF; verify cross-block merge produces correct row counts

**Open items (non-blocking):**
- Malformed-input test gaps — listed under Stage 1 Step 6; resolve against spec when convenient
- COMCIFS files not yet in `test_real_file_no_semantic_errors` — add when convenient

---

## Stage 1: CIF 2.0 Parser (then CIF 1.1) ✓ COMPLETE

### Step 1 — Project scaffolding ✓
- [x] Directory structure, `pyproject.toml`, stub `__init__.py` files, `tasks/lessons.md`

### Step 2 — Shared types (`src/pycifparse/types.py`) ✓
- [x] `ValueType`, `TokenType`, `ParseError`, `CifVersion`, `CifParserEvents`

### Step 3 — Version detection ✓
- [x] `detect_version`; 15 tests

### Step 4 — Lexer (`src/pycifparse/lexer/`) ✓
- [x] Hand-written state machine; 76 tests
- [x] All string types: bare word, single/double quoted, triple quoted (CIF 2.0),
      multiline text field, CIF 1.1 embedded-quote rule
- [x] All three line-ending styles (`\n`, `\r\n`, `\r`), including mixed in one file
- [x] CIF 1.1 character set validation (non-ASCII and VT/FF → LexerError)
- Key lessons: Lesson 1 (multiline closing delimiter), Lesson 3 (`:` not a bare-word terminator)

### Step 5 — Parser (`src/pycifparse/parser/`) ✓
- [x] `CifParser`; 88 tests
- [x] Data blocks, save frames, loops (sequential and `stop_`-terminated),
      lists, tables, orphan values, `global_` (fatal), all error-recovery paths
- [x] Table key adjacency check: whitespace before `:` accepted with syntactic error
- Key lesson: Lesson 2 (sequential loops are not nested loops)

### Step 6 — Integration tests ✓
- [x] All non-comcifs files parse without errors
- [x] Large files (≥1 MB) marked `@pytest.mark.slow`; run with `pytest -m slow`
- [x] Timestamp values (`2007-12-18T12:16:55+02:00`) confirmed as single STRING tokens
- [~] Malformed-input file tests — partially complete; 5 malformed CIF files with tests in
      `tests/parser/test_malformed.py` covering loops, containers, strings (CIF 1.1 and 2.0),
      and multiline fields
  - Known gaps (to be addressed against spec before closing):
    - `global_` keyword (fatal — stop parsing immediately)
    - `save_` outside a save frame; nested save frames; `data_` inside a save frame; EOF inside open save frame
    - `loop_` with no tag names
    - Keyword (`loop_`, `save_`, `data_`) appearing in value position
    - Tag with no value at EOF; consecutive tags (tag with no value before next tag)
    - Orphan bare-word values not triggered by container close
    - Unterminated multiline text field at EOF (opening `;`, no closing `;` before EOF)
    - CIF 1.1 character set violations (non-ASCII, VT/FF) — check `test_lexer.py` first for overlap
    - Duplicate table keys; empty `{}` and `[]`

### Step 7 — CIF 1.1 paths ✓
- [x] Character set validation in lexer
- [x] `[`, `]`, `{`, `}`, `:` inert in CIF 1.1 bare words
- [x] CIF 1.1 quoting rules tested against `cif1_quoting.cif`, `cif11_unquoted.cif`,
      `cif1_invalid.cif`

### Debug tooling (`src/pycifparse/debug.py`) ✓
- [x] `debug_lex(source)` — prints full token stream with positions and lexer errors
- [x] `DebugHandler(inner)` — wraps any handler; prints all events indented by nesting depth
- [x] `debug_parse(source)` — convenience wrapper: tokens then events in one call
- [x] ANSI colour on ttys; plain ASCII fallback on non-tty / Windows console
- [x] All three entry points accept `str | pathlib.Path | IO[str]`; `__main__` block accepts CLI path arg
- [x] 29 smoke tests in `tests/test_debug.py`

---

## Stage 2: CIF Model (IR) ✓ COMPLETE

### Step 8 — CIF model implementation (`src/pycifparse/cifmodel/`) ✓
- [x] `CifFile`, `CifBlock`, `CifSaveFrame` data structures
- [x] `CifBuilder` class implementing `CifParserEvents`
- [x] Per-block storage: `tag → list[str]` for scalars; loop table structure
- [x] Container nesting depth tracking for complete-value counting
- [x] Loop row-count validation (strict and pad modes)
- [x] Empty loop detection (semantic error)
- [x] Multiline text transformation pipeline (`textfield.py`)
- [x] Unit tests (106 total across 4 test files)

### Step 9 — Parser → IR integration ✓
- [x] `build(source, *, mode='pad')` convenience function
- [x] End-to-end tests: source string → IR query
- [x] Real CIF files parse cleanly through full pipeline

---

## Stage 3: Dictionary Parsing and SQLite Schema Generation ✓ COMPLETE

Prompt: `prompts/Stage3_Dictionary_Schema_Prompt.md`
Data files: `data/dictionaries/`
Tests: `tests/dictionary/`
Module: `src/pycifparse/dictionary/`
API Reference: `prompts/API Reference.md`

### Step 10 — `DdlmItem` (`dictionary/ddlm_item.py`) ✓
- [x] Dataclass with all fields and defaults as specified
- [x] Unit tests: field defaults, independent list fields, `is_deprecated` default

### Step 11 — `DictionaryLoader` + `DdlmDictionary` (`dictionary/loader.py`, `dictionary/ddlm_parser.py`) ✓
- [x] Phase A — no-import parsing: all frame types, lookup tables, alias collision,
      `_name.category_id` always authoritative
- [x] Phase B — `_import.get` resolution: `mode="Contents"`, `if_dupl` ×3, `if_miss` ×2,
      `mode="Full"` skip, ordering, caching, `directory_resolver`
- [x] `@pytest.mark.slow` test: `cif_core.dic` loads with 0 errors; aliases resolve;
      `deprecated_ids` non-empty
- [x] Bug: import identity tags (`_definition.id`, `_definition.class`, `_name.*`)
      must be excluded from `_import.get` merge — see lessons.md

### Step 12 — Schema generator (`dictionary/schema.py`) ✓
- [x] `ForeignKeyDef`, `ColumnDef`, `TableDef`, `SchemaSpec` dataclasses
- [x] `generate_schema`: Set/Loop → tables; Head silently skipped; other → warn;
      synthetic columns; PK from category_keys (5 fallback cases); FK detection;
      `column_to_tag` reverse mapping; all SQL identifiers double-quoted
- [x] `emit_create_statements`: valid SQLite DDL; `DEFERRABLE INITIALLY DEFERRED`;
      `_row_id UNIQUE`
- [x] 58 unit tests including PRAGMA verification

### Step 13 — Schema application (`dictionary/schema_apply.py`) ✓
- [x] `apply_schema`: `PRAGMA foreign_keys = ON`, WAL mode, explicit
      BEGIN/COMMIT/ROLLBACK via `isolation_level = None` for transactional DDL
- [x] 9 unit tests: pragmas, FK registration, `drop_existing`, rollback

### Step 14 — Tag resolver (`dictionary/resolver.py`) ✓
- [x] `ResolvedTag` dataclass
- [x] `resolve_tag`: case-insensitive; `was_alias`, `is_deprecated`; `None` for unknown
- [x] 17 unit tests

### Step 15 — Module wiring and integration ✓
- [x] `dictionary/__init__.py` with all specified exports
- [x] Updated `pycifparse/__init__.py` to re-export dictionary API
- [x] Integration tests: `ddl.dic` + `cif_core.dic` → load → schema → apply;
      table count; synthetic columns; FK via PRAGMA; `column_to_tag` round-trip;
      `_row_id UNIQUE` via `PRAGMA index_list`
- [x] `prompts/API Reference.md` updated with full dictionary public API

### Review notes
- SQL reserved-keyword table names (e.g. `update` in `ddl.dic`) require
  double-quoting all identifiers — Lesson 17.
- Python's `sqlite3` auto-commits DDL outside implicit transactions;
  `apply_schema` must use explicit BEGIN/COMMIT/ROLLBACK — Lesson 18.
- `ddl.dic` produces 0 FK constraints (Link items target non-schema categories); expected.
- Three post-completion bugs found via `debug_schema` on real dictionaries — Lessons 14, 15,
  and Functions silent-skip.

---

## Stage 3D: Schema-less Fallback Tier ✓ COMPLETE

Prompt: `prompts/Stage3D_fallbakc_schema.md`
Tests: `tests/dictionary/test_fallback_schema.py`

- [x] `emit_fallback_create_statements()` — fixed DDL for `_cif_fallback` table + index
- [x] `apply_fallback_schema(conn, *, drop_existing=False)` — transactional DDL application
- [x] Both exported from `dictionary/__init__.py`
- [x] `CLAUDE.md` constraint 7 updated to permit no-dictionary ingestion via fallback tier
- [x] 22 unit tests: DDL structure, column nullability, PK, index, idempotency,
      `drop_existing`, coexistence with structured schema

---

## Future work

### Planned features

- **Duplicate tag deduplication in `CifBlock`** — if a duplicate tag value is byte-for-byte
  identical to the already-stored value, discard the duplicate silently rather than appending it.
  Only true duplicates (same raw string, same `ValueType`) are discarded; differing values are
  still preserved per the non-negotiable constraint (no silent data loss). Emit a semantic error
  either way. Affects `CifBuilder` (Stage 2 layer). Decide whether deduplication applies to loop
  columns as well, or only to scalar tags.

- **`convert_database(src, dst, schema, *, on_coercion_failure='null') -> list[str]`** —
  copies a TEXT-storage database to a new connection with value columns cast to the
  type indicated by `ColumnDef.type_contents`. Round-trip fidelity is explicitly
  sacrificed. Rules:
  - Always a copy; original is never modified.
  - SU values are already split at ingestion (measurand column holds bare numeric).
  - CIF sentinels `'.'` and `'?'` → `NULL` silently (not a coercion failure).
  - `'"."'` and `'"?"'` (quoted strings) → subject to `on_coercion_failure` if the
    column is numeric.
  - `_cif_fallback`: best-effort CAST on `value` guided by `value_type`; `NULL` on
    failure per `on_coercion_failure`.
  - `on_coercion_failure`: `'null'` (default) — failed cast → NULL; `'keep'` — leave
    TEXT value; `'error'` — raise.
  - Returns list of warnings (one per coercion failure in `'null'`/`'keep'` modes).
  - Stage 5+ output layer.

- **Programmatic `CifFile` construction** — user-facing builder API accepting native Python
  types (str, int, float), converting to strings with correct `ValueType` assignment.
  Stage 5+, tightly coupled to CIF emission.

- **`CifFile` editing API** — mutation methods on `CifBlock` and `CifSaveFrame` allowing
  the user to modify a parsed `CifFile` in place rather than re-parsing an edited source
  string. Avoids a full parse/re-emit round-trip for small programmatic edits. Proposed
  operations:
  - `block.set(tag, value)` — set or replace a scalar tag value; accepts `str | CifScalar`;
    assigns appropriate `ValueType` if given a plain `str`.
  - `block.set_loop_value(loop_index, tag, row_index, value)` — replace one cell in a loop.
  - `block.delete(tag)` — remove a scalar tag or all values for a loop column.
  - `block.add_loop(tags, rows)` — append a new loop.
  - `block.rename_tag(old, new)` — rename a tag in scalars or loops (for alias resolution
    or deprecation fixes before ingestion).
  - Save-frame equivalents for the above.
  - All mutations must preserve the non-negotiable constraints (no silent data loss, file
    order of untouched tags preserved, `ValueType` provenance maintained).
  - Stage 5+, design in detail before implementing.

### Documentation

- **SQLite value encoding convention** — document the presence-state encoding
  (Lesson 19) in `prompts/API Reference.md` and any future user-facing docs before
  Stage 4 is complete. Consumers querying the database directly must know that
  `NULL` = absent, `'.'` = inapplicable, `'?'` = unknown, `'"."'`/`'"?"'` = literal
  quoted dot/question-mark, and that `_cif_fallback.value_type` drives quoting on
  round-trip.

- **Docstring pass for autogeneration** — all public methods and classes need consistent
  NumPy-style `Parameters`/`Returns`/`Raises` sections (see Lesson 9). Do when the public
  surface has stabilised (after Stage 4+).

### Refactors

- **`CifBlock`/`CifSaveFrame` inheritance** — `CifBlock extends CifSaveFrame` is a mild LSP
  violation. Refactor to a private `_CifNamespace` base with both as siblings if either class
  is ever passed polymorphically. Mechanical change; all tests pass unchanged.
