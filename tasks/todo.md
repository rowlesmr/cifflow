# pycifparse — Task Log

---

## ▶ RESUME FROM HERE

**Current position:** Stage 3 in progress. Step 10 is next.

**Test suite state:**
- 473 tests pass in ~1:47 (default run: `pytest -m "not slow"`)
- 5 additional slow tests against large real-world CIF files (`pytest -m slow`)

**Completed this session (Stage 2 housekeeping + Stage 1 bug fixes):**
- Renamed `CIF*` → `Cif*` throughout codebase, docs, and tests
- Deleted stale `src/pycifparse/ir/` stub
- Fixed `builder.on_error` silently discarding parser errors
- Moved empty-loop detection to parser; `_loop_value_count` → `_loop_has_values`
- Added duplicate block/save-frame name handling with `get_all(name)`
- Added `debug_build()` with row-wise loop display
- Added `__init__.py` public exports for `pycifparse` and `pycifparse.parser`
- Created `prompts/API Reference.md`
- Fixed lexer bug: `:value` at start of bare word (Lesson 10); `_last_was_ws` flag
- Removed SU validation from lexer (Lesson 11); `cif_core.dic` now parses with 0 errors
- Stage 3 prompt reviewed; pre-implementation Q&A recorded in prompt Appendix and Lesson 12
- Implementation plan written to Stage 3 section below

**Open items (non-blocking for Stage 3):**
- Malformed-input test gaps — listed under Step 6; resolve against spec when convenient
- COMCIFS files not yet in `test_real_file_no_semantic_errors` — add when Stage 3 stable

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
- [~] Malformed-input file tests — partially complete; 5 malformed CIF files with tests in `tests/parser/test_malformed.py` covering loops, containers, strings (CIF 1.1 and 2.0), and multiline fields
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

## Future features to consider

- **Programmatic `CifFile` construction** — a user-facing builder API for constructing a
  `CifFile` without parsing. Accepts native Python types (str, int, float) and converts to
  strings with correct `ValueType` assignment. Friendlier loop API than `CifBuilder`.
  Belongs in the output layer (Stage 5+) alongside CIF emission, as the two are tightly
  coupled (construction → validation → serialisation).

---

## Future documentation tasks

- **Docstring pass for autogeneration** — all public methods and classes need
  consistent `Args`, `Returns`, and `Raises` sections before an autogeneration
  tool (pdoc, Sphinx, MkDocs) would produce useful output. Current docstrings
  are readable in-source but inconsistent in style and sparse on public API.
  Do after Stage 3 when the public surface has stabilised further.

---

## Future refactors to consider

- **`CifBlock`/`CifSaveFrame` inheritance** — currently `CifBlock extends CifSaveFrame`, which
  is convenient but a mild LSP violation (a `CifBlock` is wider than a `CifSaveFrame`).
  If either class is ever passed polymorphically, refactor to a private shared base
  `_CifNamespace` with `CifSaveFrame(_CifNamespace)` and `CifBlock(_CifNamespace)` as siblings.
  Mechanical change; all tests pass unchanged; only observable difference is
  `isinstance(block, CifSaveFrame)` becomes `False`.

---

## Stage 3: Dictionary Parsing and SQLite Schema Generation

Prompt: `prompts/Stage3_Dictionary_Schema_Prompt.md`
Data files: `data/dictionaries/`
Tests: `tests/dictionary/`
Module: `src/pycifparse/dictionary/`
API Reference: `prompts/API Reference.md`

### Step 10 — `DdlmItem` (`dictionary/ddlm_item.py`)
- [ ] Dataclass with all fields and defaults as specified
- [ ] Unit tests: field defaults, independent list fields, `is_deprecated` default

### Step 11 — `DictionaryLoader` + `DdlmDictionary` (`dictionary/loader.py`, `dictionary/ddlm_parser.py`)

**Assumption:** dictionary CIF files are structurally sound — `build()` will return
a well-formed model with no structural `ParseError`s. Semantic errors (e.g. missing
tags, unexpected values) may be discovered once the schema is produced and will be
handled as warnings. No defensive code paths are needed for malformed CIF structure.

**Phase A — no-import parsing:**
- [ ] Parse a DDLm CIF via `build()`; locate first `data_` block
- [ ] Extract `_dictionary.title`, `_dictionary.version`, `_dictionary.uri`
- [ ] Iterate save frames; read all relevant tags into working dict per frame
- [ ] Extract `DdlmItem` from working dict (scope, class, category/object IDs,
      type fields, aliases, deprecations, category keys, enumeration states)
- [ ] Skip `"Dictionary"`-scope frames silently; warn on unknown scope
- [ ] Skip frames missing `_definition.id` or (for items) `_name.category_id`
- [ ] Build `DdlmDictionary` lookup tables:
      `categories`, `items`, `tag_to_item`, `alias_to_definition_id`, `deprecated_ids`
- [ ] Unit tests: all frame types, alias collision warning, `_name.category_id`
      always used (mismatched dot-notation case), duplicate handling

**Phase B — `_import.get` resolution:**
- [ ] Parse `frame["_import.get"][0]` as list of directive dicts
- [ ] `directory_resolver(path)` factory
- [ ] `_get_source(uri)` / `_get_parsed(uri)` with source + parse caching
- [ ] `_resolve_imports()`: sort by `order`, apply each directive:
      - `mode != "Contents"` → warn and skip
      - Resolve source file; apply `if_miss` policy on failure
      - Locate named frame by `_definition.id` match (not by frame label)
      - Merge tags per `if_dupl` policy (`Ignore` / `Replace` / `Exit`)
      - `Replace` + Loop category: look up tag's `_name.category_id` via source
        frame; check source category `_definition.class == "Loop"`; if so remove
        all tags with that `category_id` from working dict before inserting
- [ ] Unit tests: Contents/no-conflict, `if_dupl` ×3, `if_miss` ×2, `mode="Full"`
      skip, multi-directive ordering, caching, `directory_resolver`
- [ ] `@pytest.mark.slow` integration test: load `cif_core.dic` via
      `directory_resolver("data/dictionaries")`; 0 errors; `_type.*` populated;
      aliases resolve; `deprecated_ids` non-empty

### Step 12 — Schema generator (`dictionary/schema.py`)
- [ ] `ForeignKeyDef`, `ColumnDef`, `TableDef`, `SchemaSpec` dataclasses
- [ ] `generate_schema(dictionary)`:
      - `Set` and `Loop` categories → tables; `Head` and other → skip (warn)
      - Table name from `_name.category_id` (strip leading `_`, replace `.` with `_`)
      - Synthetic columns: `_block_id` (all), `_row_id` NOT NULL UNIQUE (Loop only)
      - Domain columns: one per item; SQL type from `type_contents`
      - PK from `_category_key.name`; fallback `_block_id` (Set) or
        `_block_id` + `_row_id` (Loop) with appropriate warnings
      - Column ordering: `_block_id`, `_row_id`, natural PKs, remaining alpha
      - FK detection: `Link` items → `ForeignKeyDef`; `SU` items → `ColumnDef.linked_item_id` only
      - `column_to_tag` reverse mapping (non-synthetic columns only)
- [ ] `emit_create_statements(schema)` → valid SQLite DDL with `DEFERRABLE INITIALLY DEFERRED`
- [ ] Unit tests: all PK cases (5), FK cases (5 per spec), synthetic columns,
      type mapping, column ordering, `column_to_tag`, `Head` skipped,
      `emit_create_statements` executes against in-memory SQLite,
      `_row_id UNIQUE` confirmed via `PRAGMA index_list(...)`

### Step 13 — Schema application (`dictionary/schema_apply.py`)
- [ ] `apply_schema(conn, schema, *, drop_existing=False)`:
      `PRAGMA foreign_keys = ON`, WAL mode, execute DDL in transaction,
      rollback on failure
- [ ] Unit tests: pragmas set, FK constraints registered, `drop_existing=True`,
      rollback on failure

### Step 14 — Tag resolver (`dictionary/resolver.py`)
- [ ] `ResolvedTag` dataclass
- [ ] `resolve_tag(tag, dictionary)`: case-insensitive lookup in `tag_to_item`;
      set `was_alias`, `is_deprecated`; return `None` if unknown
- [ ] Unit tests: current tag, alias, deprecated, unknown, case-insensitive

### Step 15 — Module wiring and integration
- [ ] `dictionary/__init__.py` with all specified exports
- [ ] Update `pycifparse/__init__.py` to re-export dictionary API
- [ ] Integration tests (`tests/dictionary/test_integration.py`):
      `ddl.dic` → load → schema → `apply_schema`; table count; synthetic columns;
      FK via `PRAGMA foreign_key_list(...)`; `column_to_tag` round-trip
- [ ] Update `prompts/API Reference.md` with dictionary public API
- [ ] All 473+ existing tests still pass

### Open decisions
- None currently. Record any deviations in `tasks/lessons.md` per prompt §Allowed Deviations.

---

## Stage 4+: Ingestion, Output (future)

- Stage 4: SQLite ingestion via dictionary-defined schema
- Stage 5+: Output layer (CIF regeneration, Python/NumPy/pandas API)
