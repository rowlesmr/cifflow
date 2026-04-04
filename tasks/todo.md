# pycifparse — Task Log

---

## ▶ RESUME FROM HERE

**Current position:** Stage 1 complete, including debug tooling. Ready to begin Stage 2 (IR).

**Test suite state:**
- 218 tests pass in ~2.5 s (default run: `pytest -m "not slow"`)
- 5 additional slow tests against large real-world CIF files (`pytest -m slow`)
- Test files: `tests/lexer/test_lexer.py`, `tests/parser/test_version.py`,
  `tests/parser/test_parser.py`, `tests/parser/test_integration.py`

**Just completed (Stage 1 — Lexer + Parser + Debug):**
- Full CIF 2.0 and CIF 1.1 lexer with all string types and error recovery
- Streaming event-driven parser: data blocks, save frames, loops, lists, tables, all error paths
- Version detection (magic line, BOM, fallback)
- Debug tooling: `src/pycifparse/debug.py` — `debug_lex()`, `debug_parse()`, `DebugHandler`
  (prints token stream and/or parser events + errors to stdout; ANSI colour on ttys)

**What comes next: Stage 2 — IR**
See `prompts/CIF_Parser_Design_Prompt.md` §IR Rules for the full specification.
Key responsibilities:
- Accumulate parser events into an in-memory structure (schema-agnostic)
- Store all values as raw strings; scalars as `tag → list[str]`
- Loop row-count validation (strict mode: error + stop; pad mode: warning + pad `?`)
- Multiline text transformation pipeline (MULTILINE_STRING only):
  1. Split into physical lines
  2. Prefix detection and removal
  3. Line unfolding (fold separators after prefix removal)
  4. Reconstruct logical string
- IR maintains its own container nesting depth to count complete values
- Must not depend on dictionary availability

**Open decisions to resolve before starting Stage 2:**
1. **Malformed-input test files** — user is writing these; parser error-recovery tests
   will be added once files are available (deferred from Step 6).
2. **IR container value counting** — the spec says "value index increments only on
   complete value (scalar `add_value` OR fully closed container)". Confirm: does a
   list containing N scalars count as 1 loop-column slot or N?
3. **Multiline prefix detection** — the CIF 2.0 spec defines a prefix-stripping rule
   for text fields. Confirm whether it applies to CIF 1.1 text fields as well.
4. **IR error handler interface** — the IR emits errors (loop row count, etc.).
   Confirm: reuse `CIFParserEvents.on_error`, or a separate callback?
5. **IR public API shape** — what does the caller get back after ingestion?
   A dict-like object? A class with `.get(tag)`, `.block(name)`, `.loop(tag)` methods?
   Needs design agreement before implementation.

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

---

## Stage 2: IR (next)

See `prompts/CIF_Parser_Design_Prompt.md` §IR Rules for full specification.
Resolve open decisions 2–5 above before writing any code.

### Step 8 — IR implementation (`src/pycifparse/ir/`)
- [ ] Agree IR public API (open decision 5)
- [ ] `IRBuilder` class implementing `CIFParserEvents`
- [ ] Per-block storage: `tag → list[str]` for scalars; loop table structure
- [ ] Container nesting depth tracking for complete-value counting
- [ ] Loop row-count validation (strict and pad modes)
- [ ] Multiline text transformation pipeline
- [ ] Unit tests

### Step 9 — Parser → IR integration
- [ ] Wire `CIFParser` output into `IRBuilder`
- [ ] End-to-end tests: source string → IR query

---

## Stage 3+: Dictionary, SQLite, Output (future)

Specifications will be added to `prompts/` before each stage begins.
- Stage 3: DDLm dictionary parsing; SQLite schema generation
- Stage 4: SQLite ingestion via dictionary-defined schema
- Stage 5+: Output layer (CIF regeneration, Python/NumPy/pandas API)
