# cifflow — Task Log

---

## What was done (2026-06-13/14, complexity branch) — F-grade complexity reduction complete

Decomposed all F-grade functions into private helpers, writing branch-coverage tests before each refactor. Test count: 1858 → 1980 (122 new tests added). No remaining F-grade functions.

- **`inspect_schema` (CC 61 → 1)**: Decomposed into 7 private helpers; 16 tests added (`test_inspect_schema.py`).
- **`_collect_all_blocks` (CC 45 → B/7)**: Decomposed into 4 private helpers; 11 tests added.
- **`_render_merge_group` (CC 54 → D/21)**: Decomposed into 5 private helpers; 11 tests added.
- **`visualise_schema` (CC 55 → B/7)**: Decomposed into 5 private helpers; 6 tests added.
- **`_render_block` (CC 69 → C/14)**: Decomposed into 5 private helpers; 24 tests added (`test_emit_render_block.py`).
- **`generate_schema` (CC 98 → A/2)**: Decomposed into 7 private helpers; 19 tests added (`test_schema.py`).
- **`_collect_grouped` (CC 170 → D/26)**: Decomposed into 12 module-level helpers (`_collect_set_pk_vals`, `_fp_entries_for_expanded`, `_drop_incidental_child_sets`, `_compute_block_fingerprint`, `_compute_no_set_fk_routing`, `_collect_fp_table_rows`, `_compute_fp_anchor`, `_compute_primary_fp_anchors`, `_collect_incidental_block_rows`, `_bfs_collect_child_sets`, `_build_grouped_state`, `_emit_pure_loop_blocks`); 29 branch-coverage tests added (`test_collect_grouped.py`).

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

## Complexity Reduction (radon/xenon analysis, 2026-06-12)

Radon grades: A ≤5, B 6–10, C 11–15, D 16–25, E 26–50, F 51+.
Run: `.venv/Scripts/python -m radon cc src/ --show-complexity --min C`
Enforce: `.venv/Scripts/xenon src/ --max-average B --max-modules C --max-absolute D`

**Rule: write function-level tests locking down current behaviour before refactoring any function below.**

### F-grade (must fix)

| CC | Location | Notes |
|----|----------|-------|
| ~~170~~ → **D/26** | ~~`output/emit.py:540 _collect_grouped`~~ ✅ | Decomposed into 12 module-level helpers; 29 branch-coverage tests added (`test_collect_grouped.py`, suite: 1951 → 1980) |
| ~~98~~ → **A/2** | ~~`dictionary/schema.py:485 generate_schema`~~ ✅ | Decomposed into 7 private helpers (`_determine_primary_keys`, `_build_table_columns`, `_build_tables`, `_resolve_fk_group`, `_build_foreign_keys`, `_build_propagation_links`, `_build_category_parent`, `_build_tag_metadata`); 19 branch-coverage tests added (suite: 1927 → 1951) |
| ~~69~~ → **C/14** | ~~`output/emit.py:1671 _render_block`~~ ✅ | Decomposed into 5 private helpers (`_render_merge_group_item`, `_render_single_table_item`, `_collect_remnant_rows`, `_organise_fallback_rows`, `_inject_header_items`); 24 branch-coverage tests added (suite: 1903 → 1927) |
| ~~61~~ → **1** | ~~`inspect/_schema.py:12 inspect_schema`~~ ✅ | Decomposed into 7 private helpers; 16 branch-coverage tests added (suite: 1858 → 1874) |
| ~~55~~ → **B/7** | ~~`dictionary/visualise.py:449 visualise_schema`~~ ✅ | Decomposed into 5 private helpers (`_build_vis_context`, `_emit_clustered_nodes`, `_emit_fk_edges`, `_emit_bridge_edges`, `_emit_parent_edges`); 6 branch-coverage tests added (suite: 1896 → 1903) |
| ~~54~~ → **D/21** | ~~`output/emit.py:2076 _render_merge_group`~~ ✅ | Decomposed into 5 private helpers; 11 branch-coverage tests added (suite: 1885 → 1896) |
| ~~45~~ → **B/7** | ~~`output/emit.py:1504 _collect_all_blocks`~~ ✅ | Decomposed into 4 private helpers (`_validate_all_blocks_preconditions`, `_inject_set_parents`, `_collect_set_table_blocks`, `_collect_loop_table_blocks`); 11 branch-coverage tests added (suite: 1874 → 1885) |

### E-grade (should fix)

| CC | Location |
|----|----------|
| 33 | `dictionary/schema.py:674 _resolve_fk_group` — irreducible FK resolution core; see Lesson 157 |
| 39 | `ingestion/duckdb_ingest.py:538 _run_fk_fill_pass` |
| 36 | `ingestion/duckdb_ingest.py:750 propagate_fk_sql` |
| 33 | `database/component_intensities.py:41 consolidate_component_intensities` |
| 32 | `output/emit.py:1211 _collect_structure` |
| 31 | `output/emit.py:2351 _render_set_category` |

### D-grade (document or fix)

`dictionary/loader.py`: `_extract_item` (30), `_load_recursive` (27), `_resolve_imports` (25)
`dictionary/schema.py`: `_find_transitive_bridge` (28)
`output/emit.py`: `_render_original_loop_group` (38 — E, already above), `_ordered_categories` (25), `_compute_original_category_order` (24), `_render_loop_category` (22), `_suppressed_fk_pk_cols` (22), `_find_set_anchor` (22), `_render_pure_fallback_loop` (21)
`inspect/_schema.py`: `inspect_fk_path` (26)
`inspect/_model.py`: `_print_namespace` (25)
`database/compact.py`: `convert_database` (28)
`database/defaults.py`: `_make_keyed_op` (22)
`validation/_validate.py`: `validate` (22)

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
