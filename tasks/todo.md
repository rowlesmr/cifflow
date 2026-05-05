# cifflow — Task Log

---

## ▶ RESUME FROM HERE

  ## What was done (2026-05-05, debug-original-output branch) — session 2

  Fixed ORIGINAL mode category ordering so that Set scalars and Loop categories interleave correctly, matching source block order. All 1779 non-slow tests pass (219s).

  - **`duckdb_ingest.py` `load_block_data`**: Added a shared `event_counter` that increments for each new Set-table first-encounter and each loop. Returns `loop_group_entries: list[tuple]` of `(block_id, table_name, loop_id, event_pos)`.
  - **`ingest.py`**: Accumulates `blk_entries` across all blocks; after `flush_table_batches`, bulk-inserts all event positions into `_loop_groups` via `executemany`.
  - **`emit.py` `_compute_original_category_order`**: Rewrote to query `_loop_groups` for all entries (both `__scalars__` and loop entries), use `min_row_id` (event positions) as the unified sort key, and only apply union-find to non-scalar loop_ids.
  - **Known gap recorded**: Shared Set rows in ORIGINAL output include extra columns from other blocks' winning contributions (e.g. `pd_phase`, `pd_diffractogram`). Fixing this requires per-column winning-block provenance in `_tag_presence`. Documented as known gap; ordering is correct.
  - **Lessons added**: 123 (`_cifflow_row_id` per-table scoping), 124 (winning-block column provenance gap).

  Run tests: `.venv/Scripts/python -m pytest -x -q`

  ---

  ## What was done (2026-05-05, debug-original-output branch) — session 1

  Resolved the ORIGINAL mode + user plan interaction: ORIGINAL mode now always ignores `OutputPlan` and emits a `UserWarning` if one is supplied. All 1779 non-slow tests pass.

  - **`emit.py`**: Added a dedicated ORIGINAL branch in `emit()` before the `_sort_and_merge` call. ORIGINAL always produces `[(b, None) for b in raw_blocks]`; passing a plan emits `UserWarning('OutputPlan is ignored in ORIGINAL mode; use GROUPED mode for custom ordering.')`.
  - **`test_integration.py`**: Added `'_loop_groups'` to the expected infrastructure table set in `TestIngestNoSchema::test_no_structured_tables_have_rows` (pre-existing gap from the previous session).
  - **`test_emit.py`**: Fixed `TestOutputPlan::test_plan_column_order_respected` → GROUPED; added `test_plan_ignored_in_original_mode_warns`; moved `TestOutputPlanCategoryOrder` tests to ONE_BLOCK; restored `TestSingleBlock`, `TestEmissionOrder`, and two `TestOutputPlanMatches` tests to GROUPED (accidentally clobbered by a too-broad `replace_all`).

  **Decision resolved — ORIGINAL + user plan:** ORIGINAL mode's contract is source fidelity; user customisation belongs in GROUPED. The `OutputPlan` API now enforces this with a warning rather than silent override.

  Run tests: `.venv/Scripts/python -m pytest -x -q`

  ---

  ## What was done (2026-05-05, rust branch)

  Fixed the ORIGINAL emit mode multi-category source loop bug: loops spanning multiple DDLm categories (e.g. `pd_data`, `pd_meas`, `pd_proc`, `pd_calc`) were being split into separate `loop_`
   blocks in ORIGINAL output instead of being reconstructed as a single unified loop. All 1722 non-slow tests pass (1719 pass + 3 new powder loop tests); the only pre-existing failure
  (`test_unknown_wildcard_emits_warning`) was also resolved.

  - **Created `_loop_groups` infrastructure table** (`duckdb_ingest.py`): added to `_create_infrastructure_tables` so it exists in every ingest database; populated in `_run_merge_tables`
  before each `_raw_*` table is dropped, storing `(block_id, table_name, loop_id, min_row_id)`.
  - **Fixed `_compute_original_category_order` to query `_loop_groups`** (`emit.py`): the function previously read `_loop_id` directly from final-table rows, but `_loop_id`/`_iter_idx` are
  never copied to final tables — only `_raw_*` staging tables have them (which are dropped during ingest). Changed signature to take `conn` + `block_id` and query `_loop_groups` instead.
  - **Fixed `_render_original_loop_group` to use positional join** (`emit.py`): replaced the dead `(_loop_id, _iter_idx)` index lookup (which always found nothing) with a positional join on
  rows sorted by `_cifflow_row_id`. Eliminated the fallback path that silently rendered tables individually.
  - **Added `suppress_pk_cols` parameter to `_render_merge_group`** (`emit.py`): allows callers to exclude FK-PK columns that point to co-emitted Set categories from the shared PK header of a
   merged loop.
  - **FK suppression for PK-compatible merge groups in ORIGINAL mode** (`emit.py`): in `_render_original_loop_group`, when all tables share the same non-synthetic PK set, compute suppressed
  Set-FK columns from the first table and pass them to `_render_merge_group` — preventing `diffractogram_id` (FK to the co-emitted `pd_diffractogram` scalar) from appearing.
  - **Fixed `preferred_category_order` overriding user plan** (`emit.py`): changed the `effective_spec` override to only apply when no user plan spec is provided (`spec is None`). Previously
  it unconditionally replaced the user's plan, which suppressed wildcard expansion and broke `test_unknown_wildcard_emits_warning`.
  - **Updated `test_no_structured_table_rows`** (`tests/ingestion/test_cifflow_files.py`): added `'_loop_groups'` to the expected infrastructure table set.
  - **Added three new slow integration tests** (`tests/output/test_emit.py`): `test_powder_loop_original_round_trip`, `test_powder_loop_original_single_loop`,
  `test_powder_loop_original_fk_suppressed` — using `powder_loop.cif` and its golden output to cover the multi-category loop case.

  **Bug fixed — multi-category source loops split in ORIGINAL output:** Root cause was two-part: (1) `_loop_id` is stripped from rows when `_raw_*` tables are dropped during ingest, so the
  union-find grouping always produced singletons; (2) the `(_loop_id, _iter_idx)`-keyed index in `_render_original_loop_group` always came up empty, triggering the individual-table fallback
  path.

  **Bug fixed — user plan ignored in ORIGINAL mode:** `preferred_category_order` unconditionally replaced the matched plan `BlockSpec` in `_render_block`, so wildcards in user-provided plans
  were never expanded and warnings were never emitted.

Run tests: `.venv/Scripts/python -m pytest -x -q`

---

## Open Decisions

  1. ~~**ORIGINAL mode + user plan interaction**~~ — **Resolved (2026-05-05):** ORIGINAL mode ignores `OutputPlan` entirely and warns. Users who want custom ordering should use GROUPED.
  1. **ONE_BLOCK fidelity mismatch** — `audit`/`audit_conform` rows appear in re-ingested output
     but not in the original. This is intentional (ONE_BLOCK auto-emits conformance data).
     Decide: teach the fidelity check to treat these as expected divergences, or exclude
     auto-emitted conformance data from the round-trip definition?

  2. **`emit_create_statements` DDL target** — currently generates SQLite DDL; ingest path uses
     DuckDB. Keep as SQLite for backward compat, or update to DuckDB DDL?

  3. **`_cifflow_block_id` / `_cifflow_row_id` rename** — pervasive rename to
     `_cifflow_cifflow_block_id` / `_cifflow_cifflow_row_id`. Large mechanical change touching
     schema, ingest, output, fidelity, inspect, all tests, all prompts. Decide timing: before or
     after rust branch merge to main?

  4. **rust branch merge to main** — rust branch has PyO3 CifFile, Arrow IR, DuckDB ingest.
     Main is still on old Python CifFile + SQLite. Decide merge timing relative to remaining
     functional work.

  5. **`_validation_result` table** — created for two UUID-regime checks; role unclear now that
     content validator uses a report-object approach. Scope: extend, retain, or remove?

  6. **Ingest optimisation** — current 12s ingest is 97× faster than original. Main remaining
     bottleneck is `ROW_NUMBER()` sort for large tables. Not in scope unless a specific use case
     requires it.


---

## What's Next (priority order)

  1. **Resolve ONE_BLOCK fidelity mismatch classification** — 2 mismatches are intentional (`audit`/`audit_conform` auto-emitted by ONE_BLOCK); update the fidelity check or its pass/fail criteria accordingly.


   3. **Expand tests for file-based loading** — dictionary from `.dic`, cached from `.json`,
     ingest a real `.cif` to file-backed SQLite, emit to `.cif` and re-ingest, property-based
     tests for `_BlockData` helpers.

  4. **Scope `OutputSpec` grouping options** — understand what flexible per-user grouping
     control could look like: which dimensions can be varied, what the API surface should be,
     interaction with schema category hierarchy and sibling groups.

  5. **Unify severity levels** across parser/ingest/validation — audit every `on_error` /
     `ParseError` site; assign `'Error' | 'Warning' | 'Info'`; standardise message phrasing;
     decide `ingest()` return type.

  6. **`CifBuilder` cross-type duplicate tag detection** — scalar-then-loop silently loses
     scalar; loop-then-scalar makes loop structurally inconsistent. Fix in `builder.py` with
     semantic errors in both cases.

  7. **`source_line`/`source_col` propagation** — add to `CifBlock`, thread through
     `on_data_block`, `builder.py`, `ingest.py` `_emit`, surface in `ValidationIssue`.

---

## Remaining Items (unscheduled)

- **`_cifflow_block_id` rename** — see Open Decision 3. Global search-and-replace when
  timing is decided.

- **`_validation_result` table** — see Open Decision 5.

- **Scope `ddl.dic` defaults** — load `ddl.dic` at schema-generation time as authoritative
  source of DDLm attribute defaults instead of ad-hoc `or 'Single'` guards.

- **Known gap: extra columns in shared Set rows (ORIGINAL mode)** — `_fetch_rows_for_block` returns owned rows fully unmasked, including columns won by other blocks. Fixing requires per-column winning-block provenance in `_tag_presence`. See Lesson 124.

- **Known gap: `diffrn_radiation` PK conflict** — `cif_img.dic` overrides
  `multi_block_core.dic` category key. Dictionary design conflict above library remit;
  document and leave until COMCIFS resolves it.

- **Duplicate tag deduplication in `CifBlock`** — identical byte-for-byte duplicates can be
  silently discarded (with a semantic error); differing values must be preserved. Decide
  whether applies to loop columns too.

- **Malformed-input test gaps** — `global_`, nested save frames, `data_` inside save frame,
  `loop_` with no tags, unterminated multiline at EOF, CIF 1.1 charset violations, duplicate
  table keys. See Stage 1 Step 6 in archive.

- **GROUPED multi-dataset blocks** — ALL_BLOCKS correctly emits multiple `_audit_dataset.id`
  as `loop_`. Open question: should GROUPED preserve all dataset IDs, or should re-ingestion
  be more tolerant (union vs intersection)?

- **Documentation** — SQLite value encoding convention in `API Reference.md`; NumPy-style
  docstring pass when public surface stabilises.

- **`CifBlock`/`CifSaveFrame` inheritance refactor** — mild LSP violation; mechanical change
  when either class is passed polymorphically.
