# cifflow — Task Log

---

## ▶ RESUME FROM HERE

## What was done (2026-06-05/06)

**`inspect_fk_path` partial links (Lesson 151):**
- Added `PartialLinkDef` dataclass to `schema.py`; `generate_schema` now records every DDLm Link item skipped due to incomplete PK coverage, non-PK target, or ambiguity
- `inspect_fk_path` displays these as `~>` partial connections (with covered/missing PK columns and reason) when no complete FK or bridge path is found
- `PartialLinkDef` exported from `cifflow.dictionary`

**`parser/__init__.py` restored (Lesson 152):**
- Deleting it caused CI docs build failure (`cifflow.parser.version` unreachable)
- Restored as a stub; `detect_version` remains available for `inspect_lexer` and `test_version.py`

**Loader bug fix — spurious alias collision warnings (Lesson 153):**
- `_load_recursive` built `all_items = list(pool.values()) + primary_items` without deduplicating by `definition_id`. When a constituent and the current file both define the same item (e.g. `_exptl_crystal.id` in both `multi_block_core.dic` and imported `cif_core.dic`), `_build_lookup_tables` received two copies and fired a false alias collision warning for the second.
- Fixed by deduplicating via a dict merge (primary overwrites constituent) before calling `_build_lookup_tables`

Test count: 1858 (all passing).

---

## What was done (2026-06-03/04, multiple branches)

**Dictionary / schema additions:**
- `DdlmItem.source_file` — each item now records the file path it came from; serialises into JSON cache automatically (old caches load fine with `None` default)
- `merge_dictionaries(*dicts, dupl='Ignore'|'Replace'|'Exit')` — public API to combine multiple loaded dictionaries; `Exit` raises `ValueError` listing all duplicate definition IDs
- `DictionaryLoader(block_constituent_imports=True)` — skips `mode="Full"` Head-target imports (whole-dictionary constituent pulls) while allowing frame-level imports to proceed

**Inspect additions:**
- `inspect_fk_path(schema, source, target)` — prints all direct FK edge chains and bridge-column chains between two tables; annotates synthetic source columns with their bridge derivation and fallback chains

**Lexer bug fix (both Python and Rust):**
- Mid-word quote characters no longer terminate bare words (Lesson 149). `hello"world` and `here"` are now single tokens. Updated 8 parser tests whose old assertions relied on the broken behaviour.

**Code cleanup — Python lexer/parser removed:**
- `inspect_lexer`, `inspect_parse`, `inspect_model`, `test_lexer.py`, `test_parser.py` all migrated to `cifflow_core.lex_cif` / `cifflow_core.parse` (Rust)
- Deleted `lexer/lexer.py`, `lexer/tokens.py`, `parser/parser.py`, their `__init__.py` files
- Kept `parser/version.py` (`detect_version`) for `inspect_lexer` version-error display and `test_version.py`
- Added `lex_cif(source, mode)` to `cifflow_core` Rust extension (Lesson 150)

Test count: 1858 (all passing). Suite time dropped from ~5 min to ~2.5 min.

---

## What was done (2026-06-02, consolidate-calc-component + duplicate-spacegroup-block-bug branches)

Completed `consolidate_component_intensities` (post-processing: assembles `pd_calc_component` rows into `pd_calc.component_intensities_net/total` and `pd_calc_overall.component_presentation_order`). Fixed four bugs found during integration testing:
- Step 2 INSERT missing `_cifflow_block_id` → `sorted()` crash in `_all_cifflow_block_ids_for_tables` (Lesson 145)
- `_all_cifflow_block_ids_for_tables` / `_all_cifflow_block_ids` now skip NULL `_cifflow_block_id` values defensively
- `clear_source=True` simplified to `DELETE FROM pd_calc_component` (was NULL-column approach with NOT NULL / synthetic-column pitfalls)
- GROUPED duplicate space_group block: FK propagation deposits Set rows into sibling blocks → incidental blocks emit empty PK-only duplicates; fixed by skipping incidental blocks for leaf-Set primary anchors (Lesson 146)

Also fixed `release_patch.bat` quoting bug (Lesson 147) and released v0.1.12. All 1850 tests pass.

---

## What was done (2026-06-01, debug-output-using-topas-source branch) — STRUCTURE mode fixes

Fixed three bugs in `EmitMode.STRUCTURE`, plus the specificity-ranked routing system in `OutputPlan`. All 1850 tests pass.

- **Bug 1 — Bridge blocks absorbed as structure targets**: `_collect_structure` used `if 'structure' in afs` to identify pure structure blocks, which also caught refln bridge blocks with anchor = {pd_diffractogram, pd_phase, structure}. Fixed to `if afs == frozenset({'structure'})`.
- **Bug 2 — `space_group_symop` (and child tables) silently dropped**: `space_group_blocks[sg_id] = block` overwrote the first (richest) block with later blocks that lacked symop rows. GROUPED emits one block per source-block-id per fingerprint; only the original source block has the loop rows. Fixed by merging (`_merge_blocks_into`) instead of overwriting for both `space_group_blocks` and `pd_phase_blocks`.
- **Bug 3 — `_replace_anchor` preserves structure block identity after satellite absorption**: `_merge_blocks_into` unions anchor_frozenset; structure blocks would gain anchor = {structure, pd_phase, space_group, model} after absorption, breaking `only('structure')`. `_replace_anchor` helper (added last session) restores the original anchor.
- **Specificity system**: `_Matcher.specificity` attribute added; `only` = 10000+len, `all_of` = len, `any_of`/`has` = 1. `plan.match` now picks highest-specificity match regardless of plan order. Plan ORDER controls output emission order; specificity controls routing.
- Lessons updated/added: 139 (updated rule), 140 (exact-anchor check), 141 (merge not overwrite in satellite accumulation).

---

## What was done (2026-06-01, debug-output-using-topas-source branch) — per-pkreach-group fingerprinting

Fixed fundamental GROUPED emit correctness bug where co-located but independently-anchored Set tables (e.g. `atom_site`→structure, `geom_angle`→model, `space_group_symop`→space_group in a single source block) were merged into one output block with union anchor `{structure,model,space_group}`, causing `only("structure")` to match nothing. All 1850 tests pass.

- **Root cause**: `_block_fingerprint` unioned all PK-FK-reachable Set tables across all loop tables into a single fingerprint. Fixed by computing one fingerprint per *distinct* non-empty pkreach frozenset among loop tables — co-located independent Sets become separate output blocks; bridge blocks (one loop table's PK spans multiple Sets simultaneously) remain correctly multi-anchor.
- **Supporting fixes**: Updated `table_to_needed_by` to filter reverse-FK children by pkreach subset (atom_type correctly assigned to structure group only); updated `sets_with_own_block` to include incidental tables (pd_phase stripped to PK-only in bridge blocks); fixed edge case where loop tables with pkreach=∅ (core_schema) now route to pure_loop_block_ids preserving all data.
- **Test updated**: `test_all_of_structure_and_model_routes_structure_blocks` → `test_only_structure_routes_structure_blocks` (verifies structure blocks exist and do NOT contain model data).
- Lessons added: 138 (GROUPED fingerprints must be per-distinct-pkreach-group, not unioned).

---

## What was done (2026-06-01, debug-output-using-topas-source branch) — reconstruct_su + merge-group fixes

Fixed two bugs in GROUPED emit with `reconstruct_su=True`, discovered while testing against a real TOPAS powder diffraction CIF via `scripts/topas/pdcif2.py`. All 1850 tests pass.

- **Bug 1 — FK-PK columns suppressed when `reconstruct_su=True`**: `_active_cols` used `col.linked_item_id is not None` to identify SU columns, but FK-PK Link columns (e.g. `pd_meas.point_id`) also carry `linked_item_id` and were incorrectly suppressed. Fixed by replacing the check with `set(_su_col_map(table_def).values())`, which only returns genuine within-table SU columns. Added 3 new tests in `TestReconstructSU`.
- **Bug 2 — Merge group `['pd_data', 'pd_meas', 'pd_proc', 'pd_calc']` not combining**: `_render_merge_group` PK-compatibility check used raw schema PKs (`{point_id, diffractogram_id}`), leaving FK-PK columns in the join key even though they are suppressed in GROUPED output. Fixed by pre-computing `effective_suppressed` per table before the compatibility check, so effective PKs (`{point_id}`) are used for both compatibility and join-key selection.
- Lessons added: 136 (`_active_cols` must use `_su_col_map`), 137 (`_render_merge_group` PK-compatibility must account for FK-PK suppression).
## What was done (2026-05-30, main branch) — STRUCTURE mode + release pipeline

Implemented `EmitMode.STRUCTURE` (absorbs `pd_phase`, `space_group`, single-model `model` blocks into their parent `structure` block), wrote 14 tests covering merge / orphan / multi-model cases (1847 tests pass), and updated `docs/outputspec.md` with full STRUCTURE documentation including the `any_of('structure')` anchor-frozenset caveat. Also overhauled the release pipeline: `release_patch.bat` now creates a `release/vX.Y.Z` branch and opens a PR instead of pushing directly to `main`; `release.yml` now triggers on `push: branches:[main] + paths:[pyproject.toml]` instead of on tag push (so PyPI publish only fires after CI passes); `[skip ci]` removed from `pyproject.toml` commit_message (was silently suppressing the release workflow).

**Immediate actions needed on restart:**
1. `git tag -d v0.1.8 v0.1.9 v0.1.10` — delete dangling local tags that point to orphaned amended commits
2. Commit the `pyproject.toml` `[skip ci]` removal (currently an unstaged working-tree change)
3. Create `release/v0.1.10` branch from current local `main` (already has the bump commit), push and open PR manually (`gh` not installed yet — do it on GitHub or install with `winget install GitHub.cli`)
4. After PR merges, verify `release.yml` fires and publishes v0.1.10 to PyPI

**Current local state:**
- `pyproject.toml` version = 0.1.10 (bump commit 8afb598 already on local main, not yet pushed to origin)
- `pyproject.toml` commit_message `[skip ci]` removed in working tree (unstaged)
- `release.yml`, `release_patch.bat`, `release_patch_dry.bat` all updated and already on origin/main via PRs #58/#59

---

## What was done (2026-05-13, main branch) — auto-generated docs

Completed the full MkDocs + mkdocstrings documentation pipeline (Phases 1–5 of `prompts/autogenerate docs.md`). All docstrings across `src/cifflow/` converted to NumPy style, `ruff check src/` and `pydoclint src/` pass clean, and `mkdocs build --strict` succeeds. All 1835 tests pass.

- Converted docstrings in all modules (`dictionary/`, `ingestion/`, `output/`, `validation/`, `fidelity/`, `inspect/`, `database/`, `cifmodel/`, `lexer/`, `parser/`, root `__init__.py`). Fixed ruff D-rules and pydoclint DOC-rules file by file.
- Created `docs/api/` pages for all modules using mkdocstrings `:::` directives; filled in `parser.md` and `model.md` stubs.
- Added CI `docs` job (build + deploy) and `release.yml` `docs` job; fixed maturin venv requirement and pydoclint console-script invocation.
- Completed Phase 5: created `CONTRIBUTING.md`, deleted `docs/api.md`, updated cross-references, added GitHub Pages links to `README.md` and `docs/index.md`.
- Audited all public functions for transitive raises; updated `Raises` sections in `writer.py` and `plan.py`; documented `KeyError` from `_find_loop_index` in parameter descriptions (pydoclint DOC502 workaround).
- Added complete `OutputPlan` example to `docs/outputspec.md`; moved non-example root scripts to `scripts/`.
- Lessons added: 133 (pydoclint console script), 134 (transitive raises in parameter descriptions), 135 (maturin CI venv).

---

## Previous work (summary)

- **2026-05-12, debug_grouped branch**: Fixed seven cascading GROUPED emit correctness bugs — hybrid orphan routing for no-FK-to-Set tables, non-PK FK suppression removed, `fallback_id = None`, bridge-block PK-stripping restricted to sets with own blocks. 1959 tests passing.
- **2026-05-06, debug_grouped branch**: Redesigned GROUPED mode with Set-identity fingerprint approach replacing FK-graph BFS; `all_of` multi-anchor matching now works correctly. 1900 tests passing.
- **2026-05-05, debug-original-output branch**: Implemented `OutputPlan`/`BlockSpec` enhancements (`only`, `any_of`, `all_of`, `has`, `attach_to`, `SchemaSpec.descendants`); fixed ORIGINAL mode category ordering with `_loop_groups` event positions; ORIGINAL mode now ignores `OutputPlan` with a warning. 1813 tests passing.

---

## Open Decisions

  1. **ONE_BLOCK fidelity mismatch** — `audit`/`audit_conform` rows appear in re-ingested output
     but not in the original. This is intentional (ONE_BLOCK auto-emits conformance data).
     Decide: teach the fidelity check to treat these as expected divergences, or exclude
     auto-emitted conformance data from the round-trip definition?

  5. **`_validation_result` table** — created for two UUID-regime checks; role unclear now that
     content validator uses a report-object approach. Scope: extend, retain, or remove?

  6. **Ingest optimisation** — current 12s ingest is 97× faster than original. Main remaining
     bottleneck is `ROW_NUMBER()` sort for large tables. Not in scope unless a specific use case
     requires it.


---

## What's Next (priority order)

  1. **Resolve ONE_BLOCK fidelity mismatch classification** — 2 mismatches are intentional (`audit`/`audit_conform` auto-emitted by ONE_BLOCK); update the fidelity check or its pass/fail criteria accordingly.

  2. **Expand tests for file-based loading** — dictionary from `.dic`, cached from `.json`,
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


- **`_validation_result` table** — see Open Decision 2.

- **Scope `ddl.dic` defaults** — load `ddl.dic` at schema-generation time as authoritative
  source of DDLm attribute defaults instead of ad-hoc `or 'Single'` guards.

- **Versioned DDLm handling** — DDLm itself evolves; `ddl.dic` defines deprecations and
  semantic changes to DDLm structural tags (e.g. `_enumeration_default.index` →
  `_enumeration_defaults.index`). Full fix requires knowing which DDLm version a domain
  dictionary targets and applying appropriate reading rules. For now, hardcode known
  mappings in `DictionaryLoader`.

- **Known gap: extra columns in shared Set rows (ORIGINAL mode)** — `_fetch_rows_for_block` returns owned rows fully unmasked, including columns won by other blocks. Fixing requires per-column winning-block provenance in `_tag_presence`. See Lesson 124.

- **Duplicate tag deduplication in `CifBlock`** — identical byte-for-byte duplicates can be
  silently discarded (with a semantic error); differing values must be preserved. Decide
  whether applies to loop columns too.

- **Malformed-input test gaps** — `global_`, nested save frames, `data_` inside save frame,
  `loop_` with no tags, unterminated multiline at EOF, CIF 1.1 charset violations, duplicate
  table keys. See Stage 1 Step 6 in archive.

- **GROUPED multi-dataset blocks** — ALL_BLOCKS correctly emits multiple `_audit_dataset.id`
  as `loop_`. Open question: should GROUPED preserve all dataset IDs, or should re-ingestion
  be more tolerant (union vs intersection)?

- **`CifBlock`/`CifSaveFrame` inheritance refactor** — mild LSP violation; mechanical change
  when either class is passed polymorphically.

- **`_sanitize_block_name` correctness** (`emit.py:376`) — current implementation replaces
  all non-`[a-zA-Z0-9_]` characters with `_`, which is not strictly correct per CIF block
  name rules. Revisit against the CIF spec and tighten.
