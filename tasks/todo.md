# pycifparse — Task Log

---

## ▶ RESUME FROM HERE

**Current stage:** Stage 6 (output layer) — complete and stable.

**Test suite state (2026-04-12):**
- ~1083 tests pass (non-slow): `source .venv/Scripts/activate && pytest -m "not slow" --tb=short -q`
- 49 slow tests pass: `pytest -m slow`
- Total: 1132 passing, 0 xfail

**What was completed in recent sessions:**
- `quote.py`: CIF 2.0 and 1.1 quoting decision trees; 95 tests in `tests/output/test_quote.py`.
- `plan.py`: `EmitMode` (`ONE_BLOCK`, `ALL_BLOCKS`, `ORIGINAL`, `GROUPED`), `BlockSpec`, `OutputPlan`.
- `emit.py`: `emit(conn, schema, *, mode, version, plan, reconstruct_su, emit_defaults)`.
  Four mode collectors. Set/Loop/fallback renderers. SU reconstruction. GROUPED BFS anchor search.
- All symbols exported from `pycifparse.output.__init__` and `pycifparse.__init__`.
- 62 tests in `tests/output/test_emit.py` (all four modes, round-trip integration, OutputPlan,
  quoting, NULL handling, GROUPED merging, composite-key anchoring). 0 xfail.
- **FK-PK suppression** (ORIGINAL and GROUPED): Set-category FK-PK columns redundant from block
  scope are suppressed.  `_suppressed_fk_pk_cols()` in `emit.py`.
- **`_audit_dataset.id` injection** (ALL_BLOCKS, CIF 2.0 only): links blocks to one dataset UUID.
- **`example_workflow.py` Step 11**: ALL_BLOCKS emit added with round-trip parse check.
- **API Reference** updated: FK-PK suppression and ALL_BLOCKS dataset injection documented.
- **Bug fix — `_flush` slim-row column loss** (`ingest.py`): INSERT column list now uses union of
  all row keys, not just `rows[0].keys()`.  Fixes NULL columns after re-ingest of emitted CIF.
- **Bug fix — GROUPED remaining-blocks scope** (`emit.py`): remaining-blocks pass now sweeps all
  schema tables, not just `block_id_tables`.  Fixes keyed-anchor tables with NULL FK values being
  silently dropped (e.g. `diffrn_radiation_wavelength`).
- Both `test_multi_one_original` and `test_multi_one_grouped` xfail decorators removed.

**Next targets (in priority order):**
1. **Fix ALL_BLOCKS block granularity** — Set categories: one block per row; Loop categories:
   group by Set-anchor key.  Requires reworking `_collect_all_blocks` to mirror GROUPED logic.
   Revisit `_audit_dataset.id` injection once granularity is correct.
2. **`BlockSpec` merge-group syntax** — `list[str | list[str]]` inner lists emit categories as
   a single `loop_` via FULL OUTER JOIN on shared keys (see design notes below).
3. **Line ending option** — `line_ending: Literal['\n', '\r\n', '\r'] = '\n'` parameter on
   `emit()`.  Applied as a final substitution over the assembled output string before return.
   The 2048-character line-length check must operate on the content before line endings are
   applied (i.e. measure raw content length, not including the terminator).
4. **Pretty-print output** — `pretty: bool = True` flag on `emit()`.  When `True`:
   - Tag–value pairs: tag and value column-aligned across all scalar pairs in the category.
   - Loop columns: each value column width determined by the widest value in that column
     (requires a full pass over all rows before writing any output).
   - `False` skips alignment; use for large files where the per-column scan is too slow.
   - Profile on a large powder-diffraction file (tens of thousands of loop rows) to quantify
     the cost before finalising the default.
5. **Line length checks for output** — both CIF 1.1 and CIF 2.0 impose a 2048-character line
   length limit, excluding the OS line-termination character(s).  CIF 1.1 additionally limits
   data names, block codes, and frame codes to 75 characters; CIF 2.0 has no such identifier
   limit.  The emitter must:
   - Detect lines exceeding 2048 characters and either wrap them (loop data rows can be split
     across lines) or escalate to a semicolon-delimited text field where inline wrapping is not
     possible (e.g. a very long unquoted value).
   - For CIF 1.1 output, validate that all data names, block codes, and frame codes are at most
     75 characters; raise on violation.
   - Implement as a post-render validation pass, version-aware, that warns or raises on
     violations before the final string is returned from `emit()`.
6. **`convert_database(src, dst, schema)`** — copy a TEXT-storage database to a new file,
   casting each column to the SQLite type indicated by `ColumnDef.type_contents`:
   `"Integer"` → `INTEGER`, `"Real"` / `"Float"` → `REAL`, everything else stays `TEXT`.
   CIF sentinels `'.'` and `'?'` convert to `NULL`.  Failed casts produce `NULL`, a kept
   TEXT value, or raise — controlled by an `on_coercion_failure` parameter (`'null'` /
   `'keep'` / `'error'`).  Stub is already shown in `example_workflow.py` Step 12.
7. ~~**Ingest stub promotion / emit round-trip bugs**~~ — **DONE** (2026-04-12).  See Lesson 58.

**Required future work:**
- **`BlockSpec.categories` — merge groups**: allow inner lists to specify categories that should
  be emitted as a single `loop_` construct via a FULL OUTER JOIN on shared key columns.
  Proposed syntax: `categories=['audit_dataset', 'cell', ['pd_data', 'pd_meas', 'pd_proc']]`.
  Design:
  - All members of a merge group must be Loop-class categories.
  - Members are joined on their shared key columns (identical or subset PK relationship);
    if no common key can be identified, fall back to separate loops with a warning.
  - Key columns appear once in the loop header; each member's non-key columns follow in
    list order.
  - Missing rows in any member produce `NULL` in the merged result, rendered as `.`.
  - The join is performed in SQLite.  SQLite has no native FULL OUTER JOIN, so
    use a two-phase strategy:
    (1) Primary LEFT JOIN chain — `pd_meas LEFT JOIN pd_proc LEFT JOIN pd_calc`
        on shared key.  Handles the common case (identical key sets) in one pass.
    (2) Stragglers query — collect keys present in later members but absent from
        the first table and append those rows.  Avoids a full UNION ALL in the
        typical case where key sets are identical.
    **Profile before committing to this approach** — with tens of thousands of
    rows, verify that the two-phase query outperforms a Python-side merge dict
    on realistic powder-diffraction data.
  - `BlockSpec.categories` type changes from `list[str]` to `list[str | list[str]]`;
    all downstream helpers (`_ordered_categories`, `_render_block`, column ordering,
    FK-PK suppression) need to handle both element types.


- **`ALL_BLOCKS` mode — correct block granularity**: the current implementation emits one block
  per non-empty SQLite table, which is wrong for multi-row tables.  The correct behaviour is:
  - **Set categories**: one output block per row (each row is a distinct instance; rows arrive
    from different original `_block_id`s).
  - **Loop categories**: group rows by the Set-anchor key (the domain PK of the nearest Set
    ancestor in the FK chain).  Rows that share the same Set-anchor key values belong to the
    same output block.  Tables with no Set ancestor remain one block per table.
  - **Consequence for `_audit_dataset.id` injection**: the dataset UUID should be derived from
    whichever `_block_id`s contributed to the block, not just the global session UUID.  Revisit
    this logic once block granularity is correct.

**Open decisions / known limitations:**
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
