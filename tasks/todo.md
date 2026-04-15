# pycifparse — Task Log

---

## ▶ RESUME FROM HERE

**Current stage:** Stage 6 — OutputPlan fully implemented. Output layer complete and stable.

**Test suite state (2026-04-15):**
- ~1334 tests pass (non-slow): `source .venv/Scripts/activate && pytest -m "not slow" --tb=short -q`
- 58 slow tests pass: `pytest -m slow`
- Total: ~1392 passing, 0 xfail

**What was completed in recent sessions:**
- `quote.py`: CIF 2.0 and 1.1 quoting decision trees; 95 tests in `tests/output/test_quote.py`.
- `plan.py`: `EmitMode` (`ONE_BLOCK`, `ALL_BLOCKS`, `ORIGINAL`, `GROUPED`), `BlockSpec`, `OutputPlan`.
- `emit.py`: `emit(conn, schema, *, mode, version, plan, reconstruct_su, emit_defaults)`.
  Four mode collectors. Set/Loop/fallback renderers. SU reconstruction. GROUPED BFS anchor search.
- All symbols exported from `pycifparse.output.__init__` and `pycifparse.__init__`.
- **FK-PK suppression** (ORIGINAL and GROUPED): Set-category FK-PK columns redundant from block
  scope are suppressed.  `_suppressed_fk_pk_cols()` in `emit.py`.
- **`_audit_dataset.id` injection** (ALL_BLOCKS, CIF 2.0 only): links blocks to one dataset UUID.
- **`example_workflow.py` Step 11**: ALL_BLOCKS emit added with round-trip parse check.
- **Bug fix — `_flush` slim-row column loss** (`ingest.py`): INSERT column list now uses union of
  all row keys, not just `rows[0].keys()`.  Fixes NULL columns after re-ingest of emitted CIF.
- **Bug fix — GROUPED remaining-blocks scope** (`emit.py`): remaining-blocks pass now sweeps all
  schema tables, not just `block_id_tables`.  Fixes keyed-anchor tables with NULL FK values being
  silently dropped (e.g. `diffrn_radiation_wavelength`).
- Both `test_multi_one_original` and `test_multi_one_grouped` xfail decorators removed.
- **`check_fidelity`** (`src/pycifparse/fidelity/`): complete. 21 tests. See below.
- **`directory_path_resolver`**: new companion to `directory_resolver`; passes full paths into
  `DdlmDictionary.source_files` and therefore `SchemaSpec.source_files` for report use.
- **`DdlmDictionary.source_files`** / **`SchemaSpec.source_files`** / **`SchemaSpec.dictionary_name`**:
  populated during loading; serialised in JSON cache.
- **OutputPlan full spec implemented** (2026-04-14):
  - `BlockSpec`: `matches` predicate, `category_order` (with wildcard `*` and merge groups),
    `single_block`, `block_namer`.
  - `OutputPlan`: `specs` (renamed from `blocks`), `block_namer`, `match()`.
  - `emit.py` refactored: collectors return `list[_BlockData]`; `_sort_and_merge()` does
    first-match spec assignment, `single_block` merging, and emission ordering.
  - `_expand_wildcard()`: BFS over `SchemaSpec.category_parent` children map.
  - `_render_merge_group()`: key-compatible → FULL OUTER JOIN in Python → single `loop_`;
    incompatible → plain loops in listed order.
  - GROUPED block names now derived from anchor key dict (e.g. `id_myexp`), not `_block_id`.
  - `SchemaSpec.category_parent` added to `schema.py`; built in `generate_schema`.
  - 19 new tests in `test_emit.py`. API Reference updated. Lessons 65–68 added.

**What was completed in recent sessions (continued):**
- **ALL_BLOCKS block granularity fixed** (2026-04-14): `_collect_all_blocks` now delegates to
  `_collect_grouped` (mirrors GROUPED logic: one block per Set-anchor key combination).
  `dataset_id` is a fresh UUID per `emit()` call (CIF 2.0 only), shared across all output blocks.
  FK-PK suppression disabled (`suppress_fk_pk=False`).  `_block_dataset_membership` lookup
  removed (dataset UUID now always fresh).  `audit_dataset` stripped from GROUPED block
  table_rows so emission UUID is injected consistently into every block.  6 new tests.
- **`example_workflow.py` updated**: `BlockSpec.categories` → `category_order`;
  `OutputPlan.blocks` → `specs`; Step 11 comment corrected; Step 13 added (fidelity checks
  for all four emit modes).  Validated on `multi_one.cif` + `cif_pow.dic`: all four modes
  pass fidelity (0 mismatches).

**Open questions / things to revisit:**
- ~~**ONE_BLOCK block naming**~~ — **FIXED**.  `_collect_one_block` now constructs
  `_BlockData` directly with `anchor_key_dict={}`, so `_resolve_block_name` falls
  through to the `fallback` string (`'output'`) instead of calling `_default_block_name`
  and concatenating every anchor key value from the entire database into one monster name.

- **Line ending option** (2026-04-14): `line_ending: str = '\n'` parameter added to `emit()`.
  `_render_block` changed to return `list[str]` (was `str`); all lines collected flat in
  `emit()` and joined once with `line_ending`.  Multiline text fields handled correctly
  because `_render_set_category` / `_format_row` already split tokens on `\n` before
  extending the lines list.  6 new tests in `TestLineEnding`.

**Next targets (in priority order):**
1. ~~**Pretty-print output**~~ — **DONE** (2026-04-14).  `pretty: bool = True` flag on `emit()`.
   - Set categories: tag names padded to the longest tag in the category (f-string `:<width>`).
   - Loop categories: token matrix built first (one `quote()` call per cell), column widths
     computed via `_col_widths()`, then `_format_row(tokens, col_widths)` pads each token.
   - Columns containing any multiline token are excluded from padding (width 0).
   - Fallback scalar tags aligned the same way as Set categories.
   - `pretty=False` skips all alignment (compact two-space separator mode).
   - 9 new tests in `TestPretty`.  Default is `True`; profile on large files if needed.
2. ~~**Line-length enforcement and folding**~~ — **DONE** (2026-04-15).
   - `line_limit: int | None = 2048` added to `emit()`.
   - `quote.py`: new `_fold_content_lines`, `_make_folded_semicolon`, `_make_prefixed_folded_semicolon`,
     and public `make_text_field(s, line_limit)` covering all four format combinations (plain /
     prefix-only / fold-only / prefix+fold).
   - `emit.py`: `_apply_line_limit(value, token, line_limit)` re-quotes inline tokens that are
     too long and re-folds existing multiline tokens whose content lines exceed the limit.
     `_pack_tokens(padded, line_limit)` greedy-packs loop data tokens across physical lines.
     `_format_row` accepts `line_limit` and delegates to `_pack_tokens` when set.
   - Set and fallback renderers: re-quote inline tokens whose full `tag + sep + token` line
     exceeds `line_limit`; recompute `tag_width` after re-quoting.
   - CIF 1.1: block code > 75 chars → `ValueError` in `_render_block`.
   - 15 new tests in `TestLineLimit`.
3. **Decimal-aligned pretty-print** — for loop columns (and Set scalar values) whose
   `ColumnDef.type_contents` is `"Real"` or `"Float"`, align values on the decimal point
   rather than left-justifying.  Rules:
   - Determine integer-part width = max digits before `.` across all values in the column.
   - Determine fractional-part width = max digits after `.` (0 if no value has a `.`).
   - Right-pad each value with spaces after the last digit so all decimal points line up.
   - Values without a decimal point are treated as having zero fractional digits and are
     right-padded to the fractional-part width.
   - SU suffixes (e.g. `0.1234(5)`) count as part of the fractional field; align on `.` first,
     then right-pad the remainder.
   - Non-numeric tokens in a nominally-Real column (e.g. `.`, `?`, quoted strings) fall back
     to plain left-justify for that value; do not break the column alignment.
   - Gated by `pretty=True`; requires `SchemaSpec` column type information to be threaded
     into `_col_widths()` or a new `_numeric_col_widths()` variant.
   - Only applies to unquoted bare-word tokens (ValueType STRING); quoted strings and
     placeholders are always left-justified regardless of column type.
4. **`convert_database(src, dst, schema)`** — copy a TEXT-storage database to a new file,
   casting each column to the SQLite type indicated by `ColumnDef.type_contents`:
   `"Integer"` → `INTEGER`, `"Real"` / `"Float"` → `REAL`, everything else stays `TEXT`.
   CIF sentinels `'.'` and `'?'` convert to `NULL`.  Failed casts produce `NULL`, a kept
   TEXT value, or raise — controlled by an `on_coercion_failure` parameter (`'null'` /
   `'keep'` / `'error'`).  Stub is already shown in `example_workflow.py` Step 12.
5. ~~**Ingest stub promotion / emit round-trip bugs**~~ — **DONE** (2026-04-12).  See Lesson 58.
6. ~~**OutputPlan full spec**~~ — **DONE** (2026-04-14).  See Lessons 65–68.

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

### Planned features (inspect layer)

- **`visualise_schema(schema) -> str`** — Graphviz DOT output alongside `inspect_schema`.
  Lives in `src/pycifparse/inspect/` (new file `_schema_viz.py`), exported from
  `pycifparse.inspect` and `pycifparse`.  Should show:
  - **Parent–child category relationships** (from `SchemaSpec.category_parent`): directed
    edges from parent to child, visually grouping the category hierarchy.
  - **Set vs Loop class**: node shape or fill distinguishes Set categories (e.g. box) from
    Loop categories (e.g. ellipse).
  - **PK/FK relationships**: directed FK edges labelled with the source and target column
    names; highlight whether the FK target is a terminal Set category that carries a
    `_category_key.name` keyword (i.e. a keyed anchor) vs a keyless Set (PK is
    `_pycifparse_id`).
  - **Non-key linked data names**: edges or annotations for columns whose `linked_item_id` 
    points to another key column in a different table (eg `_model.structure_id`). SU links are excluded.
  - Highlight orphaned categories.
  - Show category connectivity: it should be possible to travel from one category to any other
    via PK/FK relations, or by using a non-key linked data name.
  - Output is a plain DOT string; caller renders with Graphviz or pastes into an online
    viewer. 
     - Alternatively: output is a self-contained HTML file generated from the dictionary 
                      would be ideal: no installation, no server, just open in a browser. 
                      Graphviz can export SVG which embeds directly into HTML, and you can 
                      layer interactivity on top with plain JavaScript. Clickable nodes 
                      showing full category definition: description, data names, types, units...
                      Highlight FK chains: click a FK and highlight the PK it resolves to.

### Refactors

- **`CifBlock`/`CifSaveFrame` inheritance** — `CifBlock extends CifSaveFrame` is a mild LSP
  violation. Refactor to a private `_CifNamespace` base with both as siblings if either class
  is ever passed polymorphically. Mechanical change; all tests pass unchanged.
