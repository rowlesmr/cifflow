# pycifparse — Task Log

---

## ▶ RESUME FROM HERE

**Current stage:** Stage 6 complete. `CifWriter` + `clean` API implemented.

**Test suite state (2026-04-18):**
- ~1555 tests pass (non-slow): `source .venv/Scripts/activate && pytest -m "not slow" --tb=short -q`
- 58 slow tests pass: `pytest -m slow`
- Total: ~1613 passing, 0 xfail
- 2 pre-existing failures: missing `ideal_condensed.cif` test file (unrelated)

---

### Active task: pending items from `CifWriter` + `clean` implementation

#### Rename `_error_value` → `_pycifparse_error_value`

The synthetic tag `_error_value` (used by the parser to store orphan values with no preceding
tag) could clash with a legitimately defined tag in a real CIF file. It should be renamed to
`_pycifparse_error_value` to use a clearly library-specific namespace.

Action required:
- Rename `'_error_value'` → `'_pycifparse_error_value'` everywhere it appears:
  `src/pycifparse/cifmodel/builder.py`, any tests that reference it, and
  `prompts/construct_cif.md`.
- Rename `'_block_id'` → `'_pycifparse_block_id'` and `'_row_id'` → `'_pycifparse_row_id'`
  everywhere they appear: schema generation, ingestion, output, compactification, fidelity,
  inspect layers, all tests, all prompts, and `docs/api.md`. This is a pervasive rename —
  do it in one pass with a global search-and-replace, then verify no plain `_block_id` or
  `_row_id` strings remain (grep for both before closing).
- `_pycifparse_id` is already correctly named; no change needed there.
- Register all four `_pycifparse_*` synthetic tags (`_pycifparse_block_id`,
  `_pycifparse_row_id`, `_pycifparse_id`, `_pycifparse_error_value`) with the IUCr so the
  namespace is formally reserved and cannot be assigned to a real dictionary item.

---

#### Known gap to fix in `CifBuilder` (before or after implementing writer/clean)

**Cross-type duplicate tags: scalar vs loop column in the same namespace.**

`CifBuilder` does not detect the case where a tag appears both as a scalar and as a loop column
in the same namespace. Two failure modes:

- **Scalar first, then loop**: `_add_loop` (model.py) unconditionally overwrites `_tags[tag]`
  with loop values. The scalar value is silently lost — violates the "no silent data loss"
  constraint. No error is emitted.

- **Loop first, then scalar**: `_append_value` appends the scalar to the loop column's value
  list, leaving that column one value longer than all other columns in the loop. No error
  is emitted. The loop is structurally inconsistent.

Fix required in `CifBuilder` (`src/pycifparse/cifmodel/builder.py`):
- In `on_loop_start`: check if any incoming loop tag already exists as a scalar in the current
  namespace (`tag in ns._tags and tag not in any existing loop`). If so, emit a semantic error.
- In `add_tag`: check if the tag already exists as a loop column in the current namespace
  (`tag in ns._tags and tag in any loop in ns._loops`). If so, emit a semantic error.

In both cases the builder should continue (consistent with its error-tolerant design) but the
error must be recorded. Recovery action for scalar-then-loop: the scalar value is lost (note
this in the error). Recovery for loop-then-scalar: the extra value is appended (the inconsistent
loop will be visible in the model).

---

### Remaining items

#### Update `docs/api.md`

- Add `cifmodel/writer.py` and `cifmodel/clean.py` to the module layout table.
- Add API sections for `CifWriter`, `BlockWriter`, `SaveFrameWriter`, `CifInput`, `clean`, `CleanWarning`.
- Mark `CifVersion` as now exported from top-level `pycifparse`.

---

## Previously completed (2026-04-15 to 2026-04-18)

- **`CifWriter` + `clean` API**: `writer.py`, `clean.py`, model prerequisites (`version`,
  `deepcopy()`), builder version-stamping, `__init__.py` exports. 134 tests. Lessons 87–90.
- **`visualise_schema` / `visualise_schema_html`**: two-pass BFS connectivity, ghost nodes,
  three-tier badge system, `highlight_components`, `show_columns`, self-contained HTML with
  bundled viz.js + svg-pan-zoom. 25 tests. Lesson 77.
- **`prompts/propose_keys.md`**: complete DDLm FK/PK proposal prompt. See file.
- **`prompts/proposed_keys.output`**: mechanical analysis (33 Set + 74 Part B + 8 semantic
  isolated-deprecated categories). All 9 components connected after proposals.

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

- **Validation layer** (`src/pycifparse/validation/`) — spec: `prompts/Stage6_Validation_Prompt.md`.
  Operates on `CifFile` before ingestion. Checks `type_container`, `type_dimension`, `ValueType`
  consistency, `type_contents` format, `enumeration_states` membership, `enumeration_range` bounds.
  Returns `ValidationReport`; never blocks processing.
  **Prerequisites:** extend `DdlmItem` + `loader.py` with `enumeration_range` and `type_dimension`;
  extend `ColumnDef` with `type_container`, `type_dimension`, `enumeration_states`, `enumeration_range`.

- ~~**`check_fidelity`**~~ — **DONE** (2026-04-13). See Lessons 62–64.

- **Duplicate tag deduplication in `CifBlock`** — if a duplicate tag value is byte-for-byte
  identical to the already-stored value, discard the duplicate silently rather than appending it.
  Only true duplicates (same raw string, same `ValueType`) are discarded; differing values are
  still preserved per the non-negotiable constraint (no silent data loss). Emit a semantic error
  either way. Affects `CifBuilder` (Stage 2 layer). Decide whether deduplication applies to loop
  columns as well, or only to scalar tags.

- ~~**Programmatic `CifFile` construction**~~ — **IN PROGRESS** (`prompts/construct_cif.md`).
  `CifWriter` + `clean` API. See "Active task" section above.

- ~~**`CifFile` editing API**~~ — **SUPERSEDED** by `CifWriter` mutation methods.
  `CifWriter` provides `reassign_tag`, `delete_tag`, `remove_loop_tag`, `deconstruct_loop`,
  `rename_block`, `rename_save_frame`. No separate editing layer needed.

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

### Planned features (inspect layer)

- ~~**`visualise_schema(schema) -> str`**~~ — **DONE** (2026-04-15).
  `src/pycifparse/dictionary/visualise.py`, exported from `pycifparse.dictionary` and
  `pycifparse`.  Spec: `prompts/stage 6 visualise schema.md`.  25 tests.

### Refactors

- **`CifBlock`/`CifSaveFrame` inheritance** — `CifBlock extends CifSaveFrame` is a mild LSP
  violation. Refactor to a private `_CifNamespace` base with both as siblings if either class
  is ever passed polymorphically. Mechanical change; all tests pass unchanged.

### Open decisions / known limitations
- **`inspect_ingest` routing trace**: currently captures warnings, errors, FK violations only.
  Full per-tag routing events (tag → table.column) would require hooks into `_Ingester` internals;
  deferred until a `filter=` parameter is added.
- **`inspect_ingest` filter parameter**: unfiltered trace first; leave open for later.
- **SQLite trace output for `inspect_ingest`**: out of scope; leave open.
- **`_pycifparse_id` scoping**: block-category-scoped (current). Revisit with real-world evidence.
- `uuid_reference_check` is a stub — no rows written in Stage 4. Implement in a later stage.
- Looped keyless Set: error is supposed to be emitted and UUID assigned per row, but this path is
  not explicitly tested. Covered implicitly by the `_pycifparse_id` test but no error-emission test.
- `_process_scalar` for the no-schema path uses `_row_id=1` for all scalars. In a block with
  duplicate scalar tags, the fallback PK (`_block_id, _row_id, tag`) will cause a DB-level error
  on the second occurrence. The spec says duplicate tags are undefined behaviour — caller must
  consolidate before `ingest()`. Documented in the Assumptions section of Stage4 prompt.
- **`emit_defaults` flag**: accepted but has no effect. Suppressing default-fill values requires
  per-value provenance tracking not yet implemented.
- **CIF 2.0 bare-word `'`/`"` legality** (Lesson 49): Rule 2 in `quote.py` defensively excludes
  values containing `'` or `"` from bare-word emission. Check `references/CIF2-ENBF.txt`; if they
  are legal mid-word, fix the lexer and relax the guard.
