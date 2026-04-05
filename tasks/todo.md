# pycifparse — Task Log

---

## ▶ RESUME FROM HERE

**Current position:** Stage 2 complete. Ready to begin Stage 3 (DDLm dictionary parsing).

**Test suite state:**
- 440 tests pass in ~1:43 (default run: `pytest -m "not slow"`)
- 5 additional slow tests against large real-world CIF files (`pytest -m slow`)
- New test files: `tests/cifmodel/test_textfield.py`, `tests/cifmodel/test_model.py`,
  `tests/cifmodel/test_builder.py`, `tests/cifmodel/test_integration.py`

**Just completed (Stage 2 — CIF model / IR):**
- `src/pycifparse/cifmodel/` module: `CifFile`, `CifBlock`, `CifSaveFrame`, `CifBuilder`, `build()`
- Multiline text transformation pipeline (`textfield.py`): prefix detection, line folding
- Loop row-count validation (strict and pad modes); empty loop detection
- Container nesting depth tracking; closed container counts as 1 loop slot
- `build(source, *, mode='pad')` convenience function: returns `(CifFile, list[ParseError])`
- 106 new tests: 30 builder, 20 model, 30 textfield, 26 integration

**Open decisions to resolve before starting Stage 2:**
1. **Malformed-input test files** ✓ — complete; tests added to `tests/parser/test_malformed.py`.
2. **IR container value counting** ✓ — a properly closed container (list or table) counts
   as 1 loop-column slot regardless of nesting depth or number of inner values.
3. **Multiline prefix detection** ✓ — prefix stripping and line folding apply to both
   CIF 1.1 and CIF 2.0 text fields.
4. **IR error handler interface** ✓ — `CifBuilder` implements `CIFParserEvents` and
   accepts `on_error: Callable[[ParseError], None]` as a constructor argument.
   Caller passes `handler.on_error`; no rename of `CIFParserEvents` needed.
5. **IR public API shape** ✓ — confirmed:
   - Classes: `CifFile`, `CifBlock`, `CifSaveFrame`, `CifBuilder`
   - `CifFile.blocks` → `list[str]` block names in file order
   - `CifFile["blockname"]` → `CifBlock`
   - `CifBlock["_tag"]` → `list[str]` all values (scalars and loop columns alike)
   - `CifBlock.tags` → `list[str]` all tag names in the block
   - `CifBlock.loops` → `list[list[str]]` each inner list is one loop's tags
   - `CifBlock.save_frames` → `list[str]` save frame names in order
   - `CifBlock["save_name"]` → `CifSaveFrame` (same interface as `CifBlock`)
   - Missing tag or block raises `KeyError`
   - Duplicate tag values preserved; all values returned as `list[str]` including scalars

---

## Stage 1: CIF 2.0 Parser (then CIF 1.1) ✓ COMPLETE

### Step 1 — Project scaffolding ✓
- [x] Directory structure, `pyproject.toml`, stub `__init__.py` files, `tasks/lessons.md`

### Step 2 — Shared types (`src/pycifparse/types.py`) ✓
- [x] `ValueType`, `TokenType`, `ParseError`, `CIFVersion`, `CIFParserEvents`

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
- [x] `CIFParser`; 88 tests
- [x] Data blocks, save frames, loops (sequential and `stop_`-terminated),
      lists, tables, orphan values, `global_` (fatal), all error-recovery paths
- [x] Table key adjacency check: whitespace before `:` accepted with syntactic error
- Key lesson: Lesson 2 (sequential loops are not nested loops)

### Step 6 — Integration tests ✓
- [x] All non-comcifs files parse without errors
- [x] Large files (≥1 MB) marked `@pytest.mark.slow`; run with `pytest -m slow`
- [x] Timestamp values (`2007-12-18T12:16:55+02:00`) confirmed as single STRING tokens
- [ ] Malformed-input file tests — deferred (user to provide files)

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
- [x] `CifBuilder` class implementing `CIFParserEvents`
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

## Stage 3+: Dictionary, SQLite, Output (future)

Specifications will be added to `prompts/` before each stage begins.
- Stage 3: DDLm dictionary parsing; SQLite schema generation
- Stage 4: SQLite ingestion via dictionary-defined schema
- Stage 5+: Output layer (CIF regeneration, Python/NumPy/pandas API)
