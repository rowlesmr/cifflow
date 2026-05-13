# cifflow — Task Log

---

## ▶ RESUME FROM HERE

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
