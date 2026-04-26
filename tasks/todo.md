# pycifparse — Task Log

---

## ▶ RESUME FROM HERE

**Current state:** Phase B.2 complete — Arrow IR pipeline implemented. `build_arrow()` returns `list[pa.RecordBatch]` from Rust via Arrow IPC bytes; each batch has per-loop schema (only its own tag columns). `debug_parquet.py` writes one Parquet file per batch. All 1836 tests pass. Next: Phase B.3 — PyO3-exposed `CifFile`/`CifBlock`/`CifSaveFrame` backed by Arrow RecordBatches.

**Test suite state (2026-04-26):**
- 1836 tests pass (full suite)
- Run: `.venv/Scripts/python -m pytest -m "not slow" --tb=short -q`

---

### Compiled Path Phase B — Arrow IR + Rust-backed CifFile

**Goal:** Replace Python `CifFile`/`CifBlock`/`CifSaveFrame` with PyO3-exposed Rust structs backed by Arrow RecordBatches. Same public Python API. Enables Phase C (DuckDB merge).

**Reference:** `prompts/compiled_path.md`

#### Design decisions (confirmed)

**No `CifScalar`, no `ValueType` in the Python API.**

Plain Python strings throughout. Encoding conventions carry the semantics:

| Meaning | Stored string | Emit behaviour |
|---------|--------------|----------------|
| PLACEHOLDER `.` or `?` | `.` or `?` (1 char) | bare, unquoted |
| Quoted sentinel `"."` or `"?"` | `"."` or `"?"` (3 chars, with quotes) | emit with quotes |
| CIF container (list/table) | `\x00[...]` / `\x00{...}` (JSON, `\x00` prefix) | decode JSON |
| Everything else | raw string | re-quote based on content |

`block["_tag"]` → `list[str]`  
`CifScalar` deleted. `ValueType` no longer in Python API. ~20 tests updated.  
Emit layer re-quotes by content analysis. Ingest checks string value directly.

**Arrow schema (per compiled_path.md)**

Scalar tags → one RecordBatch per block, one row, one column per tag:
```
_block_idx:  Int32
_block_name: Utf8
_frame_idx:  Int32  (NULL for block-level)
_frame_name: Utf8   (NULL for block-level)
_loop_id:    Utf8   "__scalars__"
<tag_1>:     Utf8
<tag_2>:     Utf8
...
```

Loop → one RecordBatch per loop, N rows, one column per tag:
```
_block_idx:  Int32
_block_name: Utf8
_loop_id:    Utf8   "__loop_0__", "__loop_1__", ...
<tag_1>:     Utf8
<tag_2>:     Utf8
...
```

**Python API preserved (unchanged):**
```python
cif["block"]              # → CifBlock
block["_tag"]             # → list[str | list | dict]
block["save_name"]        # → CifSaveFrame
"_tag" in block           # → bool
block.tags                # → list[str]
block.loops               # → list[list[str]]
block.save_frames         # → list[str]
block.get_all("save")     # → list[CifSaveFrame]
cif.blocks                # → list[str]
cif.get_all("block")      # → list[CifBlock]
cif.version               # → CifVersion
cif.deepcopy()            # → CifFile
```

#### Phase B.1 — Drop CifScalar + plain string encoding ✓ COMPLETE (2026-04-26)

- [x] `CifScalar` removed from all public exports (`__init__.py`, `cifmodel/__init__.py`)
- [x] `CifValue = Union[str, list, dict]` (was `Union[CifScalar, list, dict]`)
- [x] `raw_builder.rs`: `RawValue::Str(String)` (was `RawValue::Str(String, ValueType)`); `add_value` applies encoding conventions
- [x] `builder.py` `add_value`: applies encoding conventions (multiline transform, `"."` / `"?"` sentinel)
- [x] `clean.py`: `_trailing_placeholder_count` uses `v == '?'`
- [x] `writer.py`: `_infer` returns plain strings; `CifInput` no longer includes `CifScalar`
- [x] `ingest.py`: `encode_value` checks string value directly (no `.value_type`); `_maybe_split_su` simplified
- [x] 36 tests updated; 1836 passing

#### Phase B.2 — Arrow IR pipeline ✓ COMPLETE (2026-04-26)

- [x] `arrow = { version = "53", features = ["ipc"] }` added to `pycifparse_core/Cargo.toml`
- [x] `raw_builder.rs`: `ParsedCif::to_ipc_batches()` — scalar batch + one batch per loop per block/save-frame; each batch carries only its own tag columns; serialised via `arrow::ipc::writer::FileWriter` → `Vec<u8>`
- [x] `lib.rs`: `parse_arrow(source, mode)` added; returns `(list[bytes], list[error_dicts])`; registered in module
- [x] `builder.py`: `build_arrow(source, *, mode)` added; deserializes IPC bytes via `pyarrow.ipc.open_file`
- [x] `__init__.py`: `build_arrow` exported
- [x] `debug_parquet.py`: rewritten to use `build_arrow`; writes one Parquet file per batch (per-loop schema, no union/NULL padding)
- [x] 1836 tests pass; Lessons 103–104

#### Phase B.3 — PyO3-exposed CifFile backed by Arrow RecordBatches — next

#### Implementation steps

- [ ] **Step 1** — `cif_model.rs`: PyO3 `#[pyclass]` types
  - `PyCifFile` — holds `Vec<PyCifBlock>` + version
  - `PyCifBlock` — holds scalar RecordBatch + loop RecordBatches + save frames
  - `PyCifSaveFrame` — same as PyCifBlock minus save frames
  - All `#[pymethods]` implementing the preserved API
- [ ] **Step 2** — Update `lib.rs`:
  - `parse_raw` returns `PyCifFile` directly (not a dict)
  - Keep `parse` (callback path) unchanged
- [ ] **Step 3** — Update `builder.py`:
  - `build()` delegates to `pycifparse_core.build(source, mode)`
  - Remove dict-unpacking code
- [ ] **Step 4** — Update `model.py`:
  - `CifFile`, `CifBlock`, `CifSaveFrame` become thin Python wrappers or are removed
- [ ] **Step 5** — Update `.pyi` stubs for new PyO3 types
- [ ] **Step 6** — Run full test suite; fix failures
- [ ] **Step 7** — Benchmark: verify parse still ≤ 1s

#### Risk areas

- `deepcopy()` on Arrow-backed types: must clone the underlying RecordBatches
- Container values (CIF lists/tables) are not columnar — store as JSON strings in Arrow or as a separate side-channel
- Save frame access from `CifBlock` — save frames nested inside blocks need to be accessible via `block["save_name"]`
- `CifScalar` is removed from the public API; downstream consumers use plain `str` (resolved in B.1)

---

### Performance optimisation — Phase 1 (partial, feature branch only)

Profiling was done against `second.cif` (18 MB, 156 blocks, ~378k lines) with `cif_pow.dic`.
Profiler: `profile_pipeline.py --input second --profile`.

#### Baseline (before optimisation)

| Phase  | Time   |
|--------|--------|
| Parse  | 55.8 s |
| Ingest | 71.9 s |
| Total  | 133 s  |

#### Phase 1.1 — Regex tokenizer (`lexer/_tokenize_re.py`) ✓ (feature branch)

Replaced the generator-based `Lexer` with a two-pass regex tokenizer returning a flat `list[Token]`.
Also replaced `_PeekableTokens` in `parser.py` with direct list indexing.

**Approach:**
- Pre-scan (`_PRESCAN_RE`) finds triple-quoted regions and semicolon multiline spans. Triple-quoted content is skipped so `\n;` inside `'''...'''` is not misidentified as a multiline delimiter.
- Main regex (`_CIF2_RE` / `_CIF1_RE`) runs `re.finditer` over non-multiline segments.
- CIF 1.x per-character charset validation in `_match_to_token` for DQ/SQ tokens.
- Unterminated triple-quoted strings require greedy `TDQ_UNT`/`TSQ_UNT` fallback patterns; without them, the lazy `[\s\S]*?` fails and falls through to wrong patterns.
- `:` is structural only immediately after a closing quote/bracket — replicated via lookbehind `(?<=[\"'\]\}]):`. Bare words consume `:` greedily (e.g. `16:00` is one token).

**Result:** Parse 55.8 s → 20.5 s (~2.7×). Tokenize: 4.2 s, `_match_to_token`: 6.9 s, `_classify_bare`: 2.4 s.

#### Phase 1.2 — `_id_regime` O(1) index ✓ (feature branch)

`_id_regime` previously scanned all rows in `merged_rows` filtering by `_block_id` — O(blocks × total_rows) quadratic.

**Fix:** Added `_block_pk_values: dict[str, list[str]]` to `_Ingester`. Populated during `_merge_into` (new `block_pk_values` parameter, also threaded through `_apply_fk`). Also updated the inline set-buffer merge path. `_id_regime` now does a single dict lookup.

**Result:** Ingest 69.8 s → 48.6 s (~1.4×). Actual saving ~21 s (predicted ~13 s).

#### After Phase 1.1 + 1.2

| Phase      | Before | After  |
|------------|--------|--------|
| Parse      | 55.8 s | 19.8 s |
| Ingest     | 69.8 s | 48.6 s |
| Compactify |  5.2 s |  5.2 s |
| Emit       |    —   | 35.2 s |
| **Total**  | 133 s  | 109 s  |

#### Remaining phases (not yet implemented)

From `prompts/performance enhancement.md`:

| Phase | Description | Estimated saving |
|-------|-------------|-----------------|
| 1.3 | SQLite write pragmas during ingest (`synchronous=OFF`, `journal_mode=MEMORY`) | 3–5 s |
| 1.4 | Short-circuit `_apply_fk` when all FK columns already present | 8–12 s |
| 1.5 | Replace `_pk_tuple` genexpr with `operator.itemgetter` | 2–3 s |
| 1.6 | Streaming UPSERT (highest risk/reward) | TBD |

Current ingest hot spots (from post-1.2 profile): `_apply_fk` 17.7 s (752 K calls), `dict.get` 6.1 s (35.6 M calls), `_merge_into` 10.0 s (1.98 M calls), `_pk_tuple` 5.4 s (2.74 M calls), `executemany` 5.6 s.

#### Open decisions

- **Branch merge**: performance work lives on a feature branch. Decide whether to merge to main before continuing with functional work, or keep separate.
- **Emit optimisation**: emit now takes 35.2 s (32% of total). `quote()` + `_illegal_start` account for ~13 s combined (1.9 M calls). `_apply_decimal_align` is 4 s. Not yet in scope.
- **Re-profile threshold**: re-profile after each of 1.3–1.5 before committing to 1.6.

---

### Completed task: ALL_BLOCKS mode (2026-04-21) ✓

- `_classify_pk_cols` extended to 5-tuple `(col, is_set, tag, set_table, set_col)` — handles multi-column FKs and one-hop Loop intermediates
- Loop branch updated to use 5-tuple unpacking; removed `set_fk_map`
- `_BlockData.preferred_category_order` — parent tables before child in block output
- `_ordered_tables_all_blocks` — controls table iteration order from plan's `category_order`
- `_collect_all_blocks` — guards (fallback rows, keyless Sets), per-table block generation, synthetic parent row injection
- `_resolve_dataset_id` — per-block lookup via `_block_dataset_membership`; preserves original `_audit_dataset.id`; returns `str | list[str] | None`; `_BlockData.dataset_id` type widened accordingly; `_render_block` emits multi-ID as `loop_`
- `_sort_and_merge` bypassed for ALL_BLOCKS (plan ordering already baked in)
- `plan.blocks` typo fixed to `plan.specs`
- Lessons 97–100

---

### Completed task: unified validation layer (2026-04-19) ✓

Spec: `prompts/unified_validate.md`

#### What was implemented

- `DdlmItem`: added `enumeration_range` and `type_dimension` fields
- `loader.py`: populates both new fields from `_enumeration.range` / `_type.dimension`
- `ColumnDef`: added `type_container`, `enumeration_states`, `enumeration_range`, `type_dimension`
- `generate_schema()`: propagates all four new fields; `type_contents` defaults to `'Text'` when absent
- `quote.py`: added `is_table_key_quotable()` helper
- `src/pycifparse/validation/`: new package with `_db_checks.py`, `_db_validate.py`, `_validate.py`, `__init__.py`
- `pycifparse/__init__.py`: exports `validate`, `ValidationReport`, `ValidationIssue`
- `tests/validation/`: `test_validate.py` (42 tests) + `test_db_validate.py` (121 tests) = 163 tests

#### Lessons: 91–94

---

### Remaining items

#### Expand tests to cover file-based loading

Most tests construct schemas, CIF models, and databases entirely in memory using
inline strings and `sqlite3.connect(':memory:')`.  Real-world usage loads
dictionaries from `.dic` files (via `DictionaryLoader` or cache), ingests `.cif`
files from disk, and writes output files.  Gaps include:

- Loading a dictionary from a `.dic` file and verifying the resulting schema matches
  expectations (title, version, uri, table count, FK structure).
- Loading a cached dictionary from a `.json` file and confirming round-trip fidelity
  with the live-loaded version (all fields including `uri`).
- Ingesting a real `.cif` file from disk into a file-backed SQLite database (not
  `:memory:`), closing the connection, reopening it, and emitting — exercises the
  full persistence path.
- Emitting to a `.cif` file on disk and re-ingesting from that file.
- `_replace_name` and other `_BlockData` helpers: property-based or table-driven
  tests verifying that every field is preserved after round-trips through helper
  functions (lesson from the `conformance_tags` omission bug).

---

#### Unify severity levels and message style across all pipeline stages

Each pipeline stage currently uses its own severity vocabulary and message conventions:

- **Parser/builder**: `ParseError.error_type` is `'lexical' | 'syntactic' | 'semantic'` —
  a category, not a severity. All parse errors are treated as errors by consumers, but
  some (e.g. unknown tag routed to fallback) are arguably warnings.
- **Ingestion**: `ingest()` returns plain `list[str]`; the `on_error` callback now carries
  `severity='Warning' | 'Info'`, but callers who use the return value have no severity at all.
  The distinction between what is an error vs. a warning is implicit (strings in
  `IngestionError.errors` are errors; everything else is a warning).
- **Validation**: `ValidationIssue.severity` is `'Error' | 'Warning' | 'Info'` — the most
  complete model; use this as the reference.

The goal is consistent severity semantics and message phrasing across all three stages,
so that a caller can filter by severity without needing to know which layer raised the issue.

Work to scope before implementing:

- Audit every `on_error` / `ParseError` emission site and assign it a severity from
  `'Error' | 'Warning' | 'Info'` using the definitions already established in `ValidationIssue`.
- Decide whether `ParseError.error_type` (`lexical`, `syntactic`, `semantic`) maps to
  severity or remains a separate classification field alongside severity.
- Standardise message phrasing: tense, quoting style, and level of detail should be
  consistent regardless of which layer emits the message.
- `ingest()` return value (`list[str]`) carries no severity — decide whether to change it
  to `list[tuple[str, str]]` or leave it as-is and route all severity information through
  the `on_error` callback only.

---

#### Scope `_validation_result` table purpose

The `_validation_result` table was created during the ingestion layer for two UUID-regime
checks (`uuid_regime`, `uuid_reference_check`). Now that the content validator (above) uses
a report-object approach (Option A) and does not write to the database, the table's ongoing
role is unclear.

Questions to resolve before writing the validator spec:
- Are the two existing ingestion checks (`uuid_regime`, `uuid_reference_check`) still
  the right things to store in the database, or should they also move to a report object?
- If the table is retained, should it be extended with columns for table/column/tag/value
  to support future DB-write validation results?
- If neither ingestion check nor future validation writes to it, should the table be removed?

---

#### Scope: read `ddl.dic` to provide DDLm attribute defaults

DDLm attribute defaults (e.g. `_type.container` defaults to `Single`,
`_type.contents` may have a default, etc.) are defined in `ddl.dic` itself,
not hardcoded in pycifparse. Currently defaults are either `None` or
approximated by ad-hoc `or 'Single'` guards in `generate_schema()`.

Scope what it would mean to load `ddl.dic` at schema-generation time and use
it as the authoritative source of DDLm attribute defaults, so that `DdlmItem`
fields reflect true DDLm defaults rather than Python `None`.

Questions to resolve:
- Which DDLm attributes have declared defaults in `ddl.dic`, and what are they?
- Where in the pipeline should the defaults be applied — in `loader.py` when
  populating `DdlmItem`, or in `generate_schema()` when building `ColumnDef`?
- Does loading `ddl.dic` impose a runtime cost or dependency that conflicts
  with the "no runtime dependencies" design goal?
- Are there attributes where `None` is semantically meaningful (i.e. "not
  declared") distinct from the DDLm default — and if so, how are they
  distinguished?

---

#### Known gap: `CifBuilder` cross-type duplicate tags

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

#### Add `source_line`/`source_col` to `CifBlock` and surface in `ValidationIssue`

Ingest-stage `ValidationIssue` objects currently populate `block` (block name) but leave
`line` and `col` as `None`. The `data_` token's position is available at parse time but not
stored on `CifBlock`.

Required changes (do in one pass):
1. `CifBlock.__init__`: add `source_line: int = 0, source_col: int = 0`
2. `CifParserEvents.on_data_block` (`types.py`): add `line: int = 0, col: int = 0` params
3. `parser.py`: pass `tok.line, tok.column` when emitting `on_data_block`
4. `builder.py`: accept and store them on the block
5. `inspect/_parser.py`: accept and forward them
6. `ingest.py` `_emit`/`_emit_error`: look up `block.source_line/col`; extend `on_error`
   callback to `(msg, block_id, line, col)`
7. `_validate.py`: populate `ValidationIssue.line/col` from the callback
8. `inspect/_ingest.py`: update `_on_error` to accept `(msg, block_id, line, col)`
9. Test mock handlers (`test_parser.py`, `test_malformed.py`): add `line=0, col=0` defaults

---

#### Rename `_block_id` → `_pycifparse_block_id`, `_row_id` → `_pycifparse_row_id`

Pervasive rename across schema generation, ingestion, output, compactification, fidelity,
inspect layers, all tests, all prompts, and `docs/api.md`. Do in one pass with global
search-and-replace; grep for both before closing. `_pycifparse_id` and
`_pycifparse_error_value` are already correctly named.

---

#### Instrument parse/ingest/database phases for performance profiling

The full pipeline (dictionary load → schema generation → CIF parse → ingest → emit) has
not been profiled against large or complex files.  Before optimising anything, identify
where time actually goes.

Suggested approach:
- Write a dedicated profiling script (not inside `scripts/`, which is the AI review
  toolchain) that drives the full pipeline against a large real-world input (e.g. a
  multi-block powder CIF with `cif_pow.dic`).
- Use `cProfile` / `pstats` or `py-spy` from outside the library — do not embed
  timing code in library modules.
- Add coarse `time.perf_counter()` brackets in the profiling script around each phase
  call so wall-clock cost is visible without a full profiler run.
- Key suspects to measure: dictionary `_load_recursive` (import resolution),
  `generate_schema` (BFS/FK derivation), `_Ingester.run` (per-block merge loops),
  `_fill_bridge_columns`, and `emit` (alignment passes for large loops).
- Record findings in `tasks/lessons.md` before making any changes.

---

#### Known gap: `diffrn_radiation` PK overridden by `cif_img.dic`

`multi_block_core.dic` defines `_diffrn_radiation.id` as the category key for
`DIFFRN_RADIATION`, giving it PK `['id']`. `cif_img.dic` (also imported by `cif_pow.dic`)
redefines the category key as `['diffrn_id', 'variant']`, which overwrites the correct key
during dictionary merging. As a result, the schema generated from `cif_pow.dic` has the
wrong PK for `diffrn_radiation`, and the FK from
`diffrn_radiation_wavelength.radiation_id → diffrn_radiation.id` is not captured.

This is a dictionary design conflict above the library's remit — `cif_img.dic` and
`multi_block_core.dic` disagree on the canonical key for the same category. Resolution
requires the dictionary authors to align the two constituent dictionaries.

Consequence: `_diffrn_radiation_wavelength.radiation_id` cannot be suppressed from
ORIGINAL-mode output (the FK is absent from the schema) until the dictionary conflict
is resolved or a workaround is introduced.

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

- **Investigate multi-dataset blocks (GROUPED)**: ALL_BLOCKS now correctly emits multiple `_audit_dataset.id` values as a `loop_` when a row group spans more than one original dataset. The equivalent question for GROUPED mode remains open: should GROUPED output preserve all dataset IDs per block, or should re-ingestion be more tolerant (union rather than intersection)?


- ~~**Validation layer**~~ — **DONE** (2026-04-19). `src/pycifparse/validation/`. Spec: `prompts/unified_validate.md`. 163 tests. Lessons 91–94.

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
