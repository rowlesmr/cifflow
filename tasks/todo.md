# cifflow — Task Log

---

## What was done (2026-06-13/14, complexity branch)

Decomposed all F-grade and all 6 reducible E-grade functions into private helpers, writing branch-coverage tests before each refactor. Test count: 1858 → 2076 (+218). No remaining F-grade or reducible E-grade; only `_resolve_fk_group` (E/33) stays as an irreducible xenon exception.

**F-grade (all resolved):**
- **`inspect_schema` (61 → 1)**: 7 helpers; 16 tests.
- **`_collect_all_blocks` (45 → B/7)**: 4 helpers; 11 tests.
- **`_render_merge_group` (54 → D/21)**: 5 helpers; 11 tests.
- **`visualise_schema` (55 → B/7)**: 5 helpers; 6 tests.
- **`_render_block` (69 → C/14)**: 5 helpers; 24 tests.
- **`generate_schema` (98 → A/2)**: 7 helpers; 19 tests.
- **`_collect_grouped` (170 → D/26)**: 12 helpers; 29 tests.

**E-grade (all reducible resolved):**
- **`propagate_fk_sql` (36 → A)**: 3 helpers; 17 tests.
- **`_run_fk_fill_pass` (39 → A)**: 3 helpers; 11 tests.
- **`_collect_structure` (32 → A)**: 2 helpers; 20 tests.
- **`_render_original_loop_group` (38 → C/15)**: extracted `_render_positional_join` D/24; 8 tests.
- **`_render_set_category` (31 → A)**: 3 helpers; 19 tests.
- **`consolidate_component_intensities` (33 → B/7)**: 2 helpers; 21 tests.

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

  4. **Unify severity levels** across parser/ingest/validation/dictionary — audit every `on_error` /
     `ParseError` site and every `warn()` call in `loader.py`; assign `'Error' | 'Warning' | 'Info'`;
     standardise message phrasing; decide `ingest()` return type.  Dictionary warnings currently span
     the full range from purely informational ("has 2 data blocks — using first") to effectively fatal
     ("contains no data blocks"), making `DdlmDictionary.is_valid` / `__bool__` meaningless as a
     single threshold.  Fix requires classifying each warning site before exposing validity on the
     public API.

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
