# pycifparse — Task Log

---

## ▶ RESUME FROM HERE

**Current position:** Stage 4 prompt blocked on container value handling. See ⚠ BLOCKING item below before proceeding.

**Test suite state:**
- 704 tests pass (non-slow): `pytest -m "not slow"`
- ~27 additional slow tests: `pytest -m slow`

**What was just completed (Stage 4 prompt + schema fixes):**
- `prompts/Stage4_Ingestion_Prompt.md` — fully designed and agreed, including dataset namespace section
- Schema generator (`schema.py`) revised:
  - `_row_id NOT NULL` added to **all** tables (Set and Loop)
  - Keyless Set tables: synthetic `_pycifparse_id TEXT NOT NULL` column as PK (UUID assigned at ingestion); `_block_id` is informational only
  - `_row_id UNIQUE` replaced with composite `UNIQUE (_block_id, _row_id)` on tables where `_row_id` is not already a PK column
  - `_cif_fallback` PK changed from `(_block_id, _row_id)` to `(_block_id, _row_id, tag)`
  - `emit_fallback_create_statements` now also emits DDL for `_block_dataset_membership` and `_validation_result`
- `apply_fallback_schema` updated to drop/create the two new metadata tables with `drop_existing`
- Tests updated throughout: `test_schema.py`, `test_schema_apply.py`, `test_fallback_schema.py` (36 tests)

**Key Stage 4 design decisions (see prompt for full spec):**
- `_row_id` scoped **per table** globally (never resets between blocks); `_block_id` records first contributing block
- Cross-block merging always on: rows with the same PK across blocks are merged (first-seen wins; conflict → error)
- Mixed loop cross-tier join: fallback cells share `_row_id` with their structured row (same iteration); both draw from the structured table's counter
- Set table `_row_id` reserved at first scalar tag encounter (preserves document order)
- Keyless Set `_pycifparse_id` is always a UUID; looped keyless Set emits a semantic error
- Key-FK always propagated; non-key FK only with `propagate_fk=True`
- Propagation source order: within-loop first, then block-scoped accumulator
- UUID fallback for missing PK with no propagation source; stored in accumulator for later use
- Dataset namespace: hybrid `_audit_dataset.id` approach; `id_regime` per-block in `_block_dataset_membership`; two post-ingestion validation checks in `_validation_result` (`uuid_regime`, `uuid_reference_check`)
- Namespace: pre-ingestion check computes intersection of dataset ID sets; raises `ValueError` if no common ID and no `dataset_id` param; merge algorithm is unconditional; `dataset_id: str | None` param selects one dataset from a multi-dataset CIF; `_audit_dataset.id` is a loop category (multiple values per block possible)

**What comes next: Stage 4 — SQLite ingestion implementation**
- Module: `src/pycifparse/ingestion/`
- Tests: `tests/ingestion/test_ingest.py` (unit) + `tests/ingestion/test_integration.py` (slow)

---

## Stage 4: SQLite Ingestion — Implementation Plan

### Step 1 — Module scaffolding ✓
- [x] Create `src/pycifparse/ingestion/__init__.py` (exports `ingest`)
- [x] Create `src/pycifparse/ingestion/ingest.py` (stub raising `NotImplementedError`)
- [x] Export `ingest` from `pycifparse/__init__.py`
- [x] Create `tests/ingestion/__init__.py`, `test_ingest.py`, `test_integration.py`
- [x] Confirm import works: `from pycifparse import ingest`

### ⚠ BLOCKING — Container value handling (resolve before Step 2)

CIF 2.0 lists (`[1 2 3]`) and tables (`{"key": val}`) are stored in the IR as
plain Python `list` / `dict` with no `ValueType`. The ingestion layer has no
defined behaviour for them. Must be resolved before building `encode_value` or
the `_cif_fallback` writer. See Stage 4 prompt §Container value handling.

Options under consideration:
- **JSON serialisation** — store as JSON TEXT; `value_type = 'list'` / `'table'`
- **CIF notation** — reconstruct CIF syntax as TEXT; same `value_type` extension
- **Reject** — emit error, store NULL (violates no-silent-data-loss constraint)

Implications extend to downstream users querying the database directly.

### Step 2 — Building-block helpers
- [ ] Value encoding: `encode_value(scalar: CifScalar) -> str | None` (Lesson 19)
- [ ] SU detection + splitting: `split_su(raw: str) -> tuple[str, str] | None`
- [ ] SU reverse map: build `measurand_def_id → su_column_name` from `SchemaSpec`
- [ ] Tag routing map: invert `schema.column_to_tag`; integrate `resolve_tag`
- [ ] Unit tests for all helpers in isolation

### Step 3 — `_cif_fallback` ingestion
- [ ] Initialise `_row_id_counters` and `loop_id_counter`
- [ ] Write scalar fallback rows (no `loop_id`, no `col_index`)
- [ ] Write pure-fallback loop rows (`loop_id`, `col_index`, shared `_row_id` per iteration)
- [ ] No-schema mode: route all tags to fallback
- [ ] Unit tests: unmapped tags, no-schema mode, `loop_id`, `col_index`, `_row_id` per iteration

### Step 4 — Set table ingestion (scalar form) + transaction model
- [ ] Per-ingest `merged_rows` accumulator
- [ ] `set_buffers` and `set_row_reservations` per block
- [ ] Accumulate scalar Set tags; flush to `merged_rows` at end of block
- [ ] Deferred INSERT from `merged_rows` after all blocks processed
- [ ] Transaction model: explicit `BEGIN` / `COMMIT` / `ROLLBACK`; restore `isolation_level`
- [ ] Unit tests: scalar Set row, `_row_id` reservation preserves document order relative to Loop rows, `_pycifparse_id` is a UUID for keyless Set, `_block_id` populated

### Step 5 — Loop table ingestion + cross-block merging
- [ ] Per-iteration row building from `CifBlock` loop data
- [ ] Merge algorithm: new PK → add to `merged_rows`; existing PK → column-level merge
- [ ] Conflict detection: non-NULL vs non-NULL mismatch → keep first, emit error
- [ ] `_row_id_counters` for structured tables (never resets between blocks)
- [ ] Unit tests: loop rows, `_row_id` increments, multi-block merge, `_block_id` from first block, conflict error

### Step 6 — FK propagation
- [ ] `fk_accumulator` per block; populate from scalar/Set values as encountered, and from single-iteration loop values after that loop completes
- [ ] Key-FK propagation: within-loop source first, then `fk_accumulator`
- [ ] Non-key FK propagation: only when `propagate_fk=True`
- [ ] UUID fallback: generate UUID when key-FK absent and no source found; store in `fk_accumulator`; emit error
- [ ] FK target in fallback tier: leave `NULL`, emit error
- [ ] Unit tests: all propagation cases from prompt test list

### Step 7 — Set looped form + Loop-class tags as scalars
- [ ] Detect Set category appearing inside a `loop_`; treat as Loop-style (bypass `set_buffers`)
- [ ] Single-tag scalar for a Loop-class tag: insert as single-row loop
- [ ] Looped keyless Set: assign UUID per row, emit error
- [ ] Unit tests: looped Set with PK, keyless looped Set error, scalar Loop-class tag

### Step 8 — Multi-category loops + mixed loops
- [ ] FK-target resolution compatibility check (per prompt spec)
- [ ] Compatible multi-category loop: route each tag to its own table; unknown tags to fallback with shared `_row_id`
- [ ] Incompatible multi-category loop: entire loop to fallback; emit error
- [ ] Mixed single-category loop: split routing; fallback cells share `_row_id` with structured row
- [ ] Unit tests: all four cases from prompt test list

### Step 9 — Pre-ingestion namespace check
- [ ] Read `_audit_dataset.id` from each block via IR; build `block → set[dataset_ids]`
- [ ] Compute intersection of dataset blocks; raise `ValueError` if empty
- [ ] `dataset_id` parameter: filter to matching blocks + general blocks
- [ ] Populate `_block_dataset_membership` for all ingested blocks
- [ ] `id_regime` determination (dataset / uuid / assumed)
- [ ] Unit tests: all namespace scenarios from prompt test list

### Step 10 — Post-ingestion validation checks
- [ ] `uuid_regime`: for each general block, check PK values of its structured rows against UUID format
- [ ] `uuid_reference_check`: **stub only** — not implemented in Stage 4; no rows written
- [ ] Write results to `_validation_result`
- [ ] Unit tests: `uuid_regime` warning, `uuid_reference_check` stub (assert no rows written), no false positives

### Step 11 — Integration tests (`@pytest.mark.slow`)
- [ ] Ingest a real CIF file against `cif_core.dic` schema; spot-check known tag values in structured tables
- [ ] No-schema ingest of the same file; verify all tags appear in `_cif_fallback`
- [ ] Multi-block real CIF; verify cross-block merge produces correct row counts

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

- **`convert_database(src, dst, schema, *, on_coercion_failure='null') -> list[str]`** —
  copies a TEXT-storage database to a new connection with numeric columns cast to their
  schema-declared types (INTEGER/REAL). Round-trip fidelity is explicitly sacrificed.
  Rules:
  - Always a copy; original is never modified.
  - SU values are already split at ingestion (measurand column holds bare numeric).
  - PLACEHOLDERs already handled via status columns; no special treatment needed.
  - `_cif_fallback`: best-effort CAST on `value`; populate `numeric_value REAL` column
    (NULL on failure).
  - `on_coercion_failure`: `'null'` (default) — failed cast → NULL; `'keep'` — leave
    TEXT value; `'error'` — raise.
  - Returns list of warnings (one per coercion failure in `'null'`/`'keep'` modes).
  - Stage 5+ output layer.

- **Programmatic `CifFile` construction** — user-facing builder API accepting native Python
  types (str, int, float), converting to strings with correct `ValueType` assignment.
  Stage 5+, tightly coupled to CIF emission.

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
