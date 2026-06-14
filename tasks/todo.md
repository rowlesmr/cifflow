# cifflow — Task Log

---

## What was done (2026-06-13/14, complexity branch) — F-grade and most E-grade complete

Decomposed all F-grade and all 6 reducible E-grade functions into private helpers, writing branch-coverage tests before each refactor. Test count: 1858 → 2076 (218 new tests added). No remaining F-grade or reducible E-grade functions.

**F-grade (all resolved):**
- **`inspect_schema` (CC 61 → 1)**: 7 helpers; 16 tests.
- **`_collect_all_blocks` (CC 45 → B/7)**: 4 helpers; 11 tests.
- **`_render_merge_group` (CC 54 → D/21)**: 5 helpers; 11 tests.
- **`visualise_schema` (CC 55 → B/7)**: 5 helpers; 6 tests.
- **`_render_block` (CC 69 → C/14)**: 5 helpers; 24 tests.
- **`generate_schema` (CC 98 → A/2)**: 7 helpers; 19 tests.
- **`_collect_grouped` (CC 170 → D/26)**: 12 helpers; 29 tests.

**E-grade (all 6 reducible resolved):**
- **`propagate_fk_sql` (E/36 → A)**: 3 helpers; 17 tests.
- **`_run_fk_fill_pass` (E/39 → A)**: 3 helpers; 11 tests.
- **`_collect_structure` (E/32 → A)**: 2 helpers; 20 tests.
- **`_render_original_loop_group` (E/38 → C/15)**: 1 helper (`_render_positional_join` D/24); 8 tests.
- **`_render_set_category` (E/31 → A)**: 3 helpers; 19 tests.
- **`consolidate_component_intensities` (E/33 → B/7)**: 2 helpers (`_consolidate_within_transaction` C/19, `_drop_all_dot_columns` B/9); 21 tests.

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

Radon grades (empirical — confirmed from output): A ≤5, B 6–10, C 11–20, D 21–30, E 31–50, F 51+.
(Note: CLAUDE.md documents "D 16–25" but radon's actual boundary is D=21–30, E=31+.)
Xenon `--max-absolute D` flags grade E and above (CC ≥ 31).
Run: `.venv/Scripts/python -m radon cc src/ --show-complexity --min C`
Enforce: `.venv/Scripts/xenon src/ --max-average B --max-modules C --max-absolute D`

**Rule: write function-level tests locking down current behaviour before refactoring any function below.**

### F-grade (must fix) — ALL RESOLVED ✅

| CC | Location | Notes |
|----|----------|-------|
| ~~170~~ → **D/26** | ~~`output/emit.py:540 _collect_grouped`~~ ✅ | Decomposed into 12 module-level helpers; 29 branch-coverage tests added (`test_collect_grouped.py`, suite: 1951 → 1980) |
| ~~98~~ → **A/2** | ~~`dictionary/schema.py:485 generate_schema`~~ ✅ | Decomposed into 7 private helpers; 19 branch-coverage tests added (suite: 1927 → 1951) |
| ~~69~~ → **C/14** | ~~`output/emit.py:1671 _render_block`~~ ✅ | Decomposed into 5 private helpers; 24 branch-coverage tests added (suite: 1903 → 1927) |
| ~~61~~ → **A/1** | ~~`inspect/_schema.py:12 inspect_schema`~~ ✅ | Decomposed into 7 private helpers; 16 branch-coverage tests added (suite: 1858 → 1874) |
| ~~55~~ → **B/7** | ~~`dictionary/visualise.py:449 visualise_schema`~~ ✅ | Decomposed into 5 private helpers; 6 branch-coverage tests added (suite: 1896 → 1903) |
| ~~54~~ → **D/21** | ~~`output/emit.py:2076 _render_merge_group`~~ ✅ | Decomposed into 5 private helpers; 11 branch-coverage tests added (suite: 1885 → 1896) |
| ~~45~~ → **B/7** | ~~`output/emit.py:1504 _collect_all_blocks`~~ ✅ | Decomposed into 4 private helpers; 11 branch-coverage tests added (suite: 1874 → 1885) |

### E-grade (xenon violations — should fix)

| CC | Location | Status |
|----|----------|--------|
| ~~39~~ → **A** | ~~`ingestion/duckdb_ingest.py:538 _run_fk_fill_pass`~~ ✅ | Extracted `_fill_single_fk`, `_fill_composite_fk`, `_fill_propagation_links`; 11 tests (suite: 2008 → 2019) |
| ~~38~~ → **C/15** | ~~`output/emit.py:2468 _render_original_loop_group`~~ ✅ | Extracted `_render_positional_join` D/24; 8 tests (suite: 2025 → 2033) |
| ~~36~~ → **A** | ~~`ingestion/duckdb_ingest.py:750 propagate_fk_sql`~~ ✅ | Extracted `_generate_uuid_pks`, `_create_composite_fk_stub_parents`, `_create_single_fk_stub_parents`; 17 tests (suite: 1980 → 1997) |
| ~~32~~ → **A** | ~~`output/emit.py:1269 _collect_structure`~~ ✅ | Extracted `_segregate_structure_blocks` C/15, `_collect_satellite_merges` C/13; 20 tests (suite: 2005 → 2025) |
| ~~31~~ → **A** | ~~`output/emit.py:2591 _render_set_category`~~ ✅ | Extracted `_build_set_quads`, `_requote_set_quads`, `_decimal_align_set_quads`; 19 tests (suite: 2036 → 2055) |
| ~~33~~ → **C/19** | ~~`database/component_intensities.py:41 consolidate_component_intensities`~~ ✅ | Extracted `_consolidate_within_transaction` C/19, `_drop_all_dot_columns` B/9; 21 tests (suite: 2055 → 2076) |
| 33 | `dictionary/schema.py:674 _resolve_fk_group` — irreducible FK resolution core; see Lesson 157 | leave |

### D-grade (document or fix)

`dictionary/loader.py`: `_extract_item` (30), `_load_recursive` (27), `_resolve_imports` (25)
`dictionary/schema.py`: `_find_transitive_bridge` (28)
`dictionary/visualise.py`: `_emit_clustered_nodes` (25), `_classify_tables` (21), `_column_rows` (21)
`output/emit.py`: `_render_fallback` (29), `_compute_fp_anchor` (28)†, `_collect_fp_table_rows` (27)†, `_collect_grouped` (26)†, `_ordered_categories` (25), `_compute_original_category_order` (24), `_render_positional_join` (24)†, `_bfs_collect_child_sets` (22)†, `_render_loop_category` (22), `_suppressed_fk_pk_cols` (22), `_find_set_anchor` (22), `_build_grouped_state` (21)†, `_render_merge_group` (21), `_render_pure_fallback_loop` (21)
`inspect/_schema.py`: `inspect_fk_path` (26)
`inspect/_model.py`: `_print_namespace` (25)
`database/compact.py`: `convert_database` (28)
`database/defaults.py`: `_make_keyed_op` (22)
`validation/_validate.py`: `validate` (22)

† Helpers extracted during E/F-grade reduction; D-grade by nature of the logic they encapsulate.

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
