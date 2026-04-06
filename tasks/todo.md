# pycifparse — Task Log

---

## ▶ RESUME FROM HERE

**Current position:** Stage 2 complete. Ready to begin Stage 3 (DDLm dictionary parsing).

**Test suite state:**
- 473 tests pass in ~1:47 (default run: `pytest -m "not slow"`)
- 5 additional slow tests against large real-world CIF files (`pytest -m slow`)

**Completed this session (housekeeping and corrections):**
- Renamed `CIF*` → `Cif*` throughout codebase, docs, and tests (`CifParser`, `CifVersion`, etc.)
- Deleted stale `src/pycifparse/ir/` stub (superseded by `cifmodel/`)
- Fixed `builder.on_error` — was silently discarding parser errors; now forwards to caller
- Moved empty-loop detection from builder (semantic) to parser (syntactic); refactored
  `_loop_value_count: int` → `_loop_has_values: bool` in `CifParser`
- Added duplicate block/save-frame name handling: `_id`, `_block_list`/`_save_frame_list`,
  `get_all(name)` on `CifFile` and `CifBlock`
- `debug_build()` added to `debug.py`: prints model with row-wise loop display, column-aligned
- Added `__init__.py` public exports: `pycifparse` exports `CifFile`, `CifBlock`,
  `CifSaveFrame`, `CifBuilder`, `build`; `pycifparse.parser` exports `CifParser`
- Created `prompts/API Reference.md` — public API reference for use alongside Stage 3 prompt
- Fixed spec contradiction in `prompts/CIF Parser Design Prompt.md` (line-folding layer)
- 29 tests added (duplicate names, empty-loop corrections, debug smoke tests); 5 corrected

**Open decisions / prerequisites before starting Stage 3:**
1. **Stage 3 prompt** ✓ — exists at `prompts/Stage3_Dictionary_Schema_Prompt.md`;
   pre-implementation Q&A clarifications recorded in prompt Appendix and Lesson 12.
2. **Malformed-input test gaps** — non-blocking for Stage 3; gaps listed under Step 6 below.
   Resolve against spec and the error-correcting CIF 1.1 parser paper when convenient.
3. **COMCIFS test files** — `tests/cif_files/comcifs/` not yet covered by
   `test_real_file_no_semantic_errors`; add when Stage 3 is stable.

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

## Stage 3+: Dictionary, SQLite, Output (future)

Specifications will be added to `prompts/` before each stage begins.
- Stage 3: DDLm dictionary parsing; SQLite schema generation
- Stage 4: SQLite ingestion via dictionary-defined schema
- Stage 5+: Output layer (CIF regeneration, Python/NumPy/pandas API)
