# pycifparse — Task Log

---

## ▶ RESUME FROM HERE

**Current position:** Stage 3 COMPLETE.

**Test suite state:**
- 611 tests pass (non-slow): `pytest -m "not slow"`
- 21 additional slow tests: `pytest -m slow`

**Completed this session (Stage 3):**
- Step 10: `DdlmItem` dataclass — 14 tests
- Step 11: `DictionaryLoader` + `DdlmDictionary` — 45 non-slow + 1 slow test;
  bug fix: import identity tags (`_definition.id`, `_definition.class`,
  `_name.*`) must never be merged from source frame (Lesson 13 candidate)
- Step 12: `generate_schema` + `emit_create_statements` — 58 tests;
  bug fix: SQL identifiers quoted with `"..."` to handle reserved keywords
  (e.g. `update`) — lesson 14 candidate
- Step 13: `apply_schema` — 9 tests;
  bug fix: Python sqlite3 auto-commits DDL; must use explicit BEGIN/COMMIT/ROLLBACK
  via `isolation_level = None` for transactional DDL (Lesson 15 candidate)
- Step 14: `ResolvedTag` + `resolve_tag` — 17 tests
- Step 15: module wiring (`dictionary/__init__.py`, top-level `__init__.py`);
  integration tests (`test_integration.py`) — 16 slow tests;
  `prompts/API Reference.md` updated with dictionary public API

**Open items (non-blocking):**
- Malformed-input test gaps — listed under Step 6; resolve against spec when convenient
- COMCIFS files not yet in `test_real_file_no_semantic_errors` — add when convenient
- Lesson entries for import identity tags, SQL quoting, transactional DDL — write to `tasks/lessons.md`

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

**Assumption:** dictionary CIF files are structurally sound — `build()` will return
a well-formed model with no structural `ParseError`s.

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
- [x] 611 non-slow + 21 slow tests pass

### Open decisions / review notes
- SQL reserved-keyword table names (e.g. `update` in `ddl.dic`) require
  double-quoting all identifiers in `emit_create_statements` and `apply_schema`.
- Python's `sqlite3` auto-commits DDL outside implicit transactions;
  `apply_schema` must set `isolation_level = None` and issue explicit
  BEGIN/COMMIT/ROLLBACK to guarantee rollback on failure.
- `ddl.dic` produces 0 FK constraints (Link items target non-schema categories);
  this is expected. FK tests use `cif_core.dic`.
- Lessons to write: import identity tags, SQL quoting, transactional DDL.

---

## Stage 4+: Ingestion, Output (future)

- Stage 4: SQLite ingestion via dictionary-defined schema
- Stage 5+: Output layer (CIF regeneration, Python/NumPy/pandas API)
