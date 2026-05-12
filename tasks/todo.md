# cifflow — Task Log

---

## ▶ RESUME FROM HERE

  ## What was done (2026-05-12, debug_grouped branch) — session 2

  Fixed seven cascading correctness bugs in GROUPED emit mode. All 1760 non-slow + 199 slow tests pass (1959 total).

  - **`_collect_grouped` orphan routing**: Replaced the `rows_for_block`-only check for `no_set_fk_tables` with a hybrid approach: tables with reverse FKs use reverse-FK reachability (handles deduplication correctly); leaf tables with no reverse FKs use direct row ownership. Fixes `atom_type` (needed by multiple structure fingerprint groups → orphan) and `space_group_symop` (leaf table → single-group inclusion).
  - **`_suppressed_fk_pk_cols`**: Removed the `suppress_all_to_set` bypass that was suppressing non-PK FK columns. Non-PK FKs (e.g. `model.structure_id`, `pd_instr_detector.instr_id`) are now never suppressed — FK propagation during re-ingest cannot recover them.
  - **`fallback_id = None`**: GROUPED mode no longer generates fresh UUIDs; only propagates existing `_audit_dataset.id` values. Fresh UUIDs caused spurious rows in re-ingest tests.
  - **`sets_with_own_block` two-pass**: Bridge-block PK-stripping only applies to Sets that have a dedicated single-anchor block elsewhere. Sets appearing exclusively in bridge blocks now keep their full data (fixes `diffrn.ambient_temperature` loss).
  - **Fix B removed**: The stub-only suppression (skip table if all columns are domain PKs) was too broad — incorrectly skipped `peak` table after FK-PK suppression left only `_peak.id`. Replaced with a simpler `'.'`-only check.
  - **`_fingerprint_anchor_fs` helper**: New function computing anchor frozenset (after child-Set stripping) for a fingerprint; used by `sets_with_own_block` precomputation.
  - **Lessons added**: 129 (hybrid orphan routing), 130 (non-PK FK never suppress), 131 (fallback_id = None), 132 (sets_with_own_block bridge stripping).

  ---

  ## What was done (2026-05-06, debug_grouped branch) — session 1

  Completely redesigned GROUPED emit mode using a Set-identity fingerprint approach. All 1827 non-slow + 73 slow tests pass (1900 total, 278s).

  - **`emit.py` `_collect_grouped`**: Replaced ~200 lines of FK-graph BFS logic with ~100 lines of fingerprint-based grouping. Each `_cifflow_block_id` receives a fingerprint: `frozenset` of `(table_name, sorted_pk_value_tuples)` drawn from BOTH winner rows AND `_tag_presence` (non-winner) entries. Blocks with identical fingerprints are merged. Set data collection uses `_fetch_rows_for_block` (not `cache.rows_for_block`) to include non-winner contributions. Block naming excludes child-Set tables (tables whose all domain PKs are FK columns pointing to other anchors in the fingerprint).
  - **Root cause fixed**: Bridge CIF blocks containing BOTH `pd_phase.id` AND `pd_diffractogram.id` had their dual Set identity destroyed by the old FK-graph single-anchor approach. The fingerprint approach preserves full multi-anchor identity; `all_of('pd_diffractogram', 'pd_phase')` now correctly matches bridge blocks.
  - **Bugfix**: `_all_cifflow_block_ids_for_tables` returns `list`, not `set`; must wrap in `set()` before using `|` operator.
  - **Test class `TestGroupedStructureSecondShortDecimated`**: Updated to reflect new multi-anchor behavior — bridge blocks now routed by `all_of('pd_diffractogram', 'pd_phase')`, phase-only blocks by `all_of('pd_phase', 'model')`, pure diffractogram by `all_of('pd_diffractogram', 'diffrn')`.
  - **Lessons added**: 125 (fingerprint needs tag_presence), 126 (child-Set naming exclusion), 127 (Set data via `_fetch_rows_for_block`), 128 (fingerprint replaces FK-graph).
  - **debug_output.py**: Updated `GROUPED_PLAN` to use new multi-anchor predicates for `second_short_decimated.cif`; output 9 blocks, 16,861 chars.

  ---

  ## What was done (2026-05-05, debug-original-output branch) — session 3

  Implemented `OutputPlan`/`BlockSpec` enhancements from `prompts/enhance outputspec.md`. All 1813 non-slow tests pass (219s).

  - **`dictionary/schema.py` `SchemaSpec.descendants(root)`**: New method; returns frozenset of `root` and all table names whose `category_parent` chain reaches `root`. Returns `frozenset()` for unknown root.
  - **`output/plan.py`**: Added `_Matcher` class (`.excluding()`, `__or__`, `__and__`), helper functions (`only`, `any_of`, `all_of`, `has`), `MatchPredicate` type alias, `str`/`set` shorthand normalisation in `BlockSpec.__post_init__`, `attach_to: MatchPredicate` field on `BlockSpec`, two-arg `OutputPlan.match(anchors, tables)`.
  - **`output/emit.py` `_sort_and_merge`**: Passes `frozenset(block.table_rows.keys())` as second arg to `plan.match`; two-pass resolution for `attach_to` blocks (first pass: normal matching; second pass: merge into target or emit standalone with warning).
  - **Exports**: `only`, `any_of`, `all_of`, `has` re-exported from `cifflow.output` and `cifflow`.
  - **Tests**: Updated all one-arg `plan.match()` calls and `lambda a:` predicates to two-arg form; added `TestMatchHelpers` (35 unit tests for helpers/combinators/shorthands), `TestAttachTo` (2 integration tests), `TestDescendants` (7 tests in `test_schema.py`).
  - **No new lessons**: Implementation followed the spec without surprises.

  Run tests: `.venv/Scripts/python -m pytest -x -q`

  ---

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

  2. **Document `OutputPlan` enhancements** — update `docs/outputspec.md` and `prompts/API Reference.md` with `_Matcher` helpers, `has()`, `attach_to`, `SchemaSpec.descendants()`, and the two-arg callable signature.

  3. **Expand tests for file-based loading** — dictionary from `.dic`, cached from `.json`,
     ingest a real `.cif` to file-backed SQLite, emit to `.cif` and re-ingest, property-based
     tests for `_BlockData` helpers.

  4. **Unify severity levels** across parser/ingest/validation — audit every `on_error` /
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

- **`_sanitize_block_name` correctness** (`emit.py:376`) — current implementation replaces
  all non-`[a-zA-Z0-9_]` characters with `_`, which is not strictly correct per CIF block
  name rules. Revisit against the CIF spec and tighten.
