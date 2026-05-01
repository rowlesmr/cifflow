# pycifparse — Task Log

---

## ▶ RESUME FROM HERE

**Current state (2026-05-01, rust branch):** DuckDB migration fully complete and audited. All 1749 tests pass. Lessons 114–117.

### What was done this session (2026-05-01)

**DuckDB migration audit** — swept every non-ingest source file and test for remaining SQLite patterns:

| File | Result |
|------|--------|
| `fidelity/check.py` | Already DuckDB — no changes |
| `database/compact.py` | Already DuckDB — no changes |
| `inspect/_ingest.py` | Already DuckDB — no changes |
| `validation/_db_validate.py` | Already DuckDB — no changes |
| `validation/_validate.py` | Already DuckDB — no changes |
| `tests/fidelity/test_check_fidelity.py` | **Changed** — replaced 6× `sqlite3.connect` with `duckdb.connect()`, 2× `sqlite3.OperationalError` mock with `Exception`, removed `row_factory`, `REAL` → `DOUBLE` |
| `tests/dictionary/test_schema.py` | Left as-is — correctly uses SQLite to validate `emit_create_statements` SQLite DDL output |

**Ingest dead code** — confirmed stale entries in the previous RESUME section removed (`_process_loop`, `_process_scalar`, `_apply_fk`, `_merge_into` were already deleted in an earlier session). `_loops_compatible` is live.

**Emit optimization** — `_EmitCache` added to `emit.py`; eliminated 19,500 → ~125 DuckDB queries per emit pass. Emit: 82s → 12s. Lesson 116.

**`_merge_keyed_fast` removed** — dead code; benchmarks showed no improvement over GROUP BY path (both bottleneck on `ROW_NUMBER() OVER (ORDER BY ...)`). Lesson 114.

### Performance summary (`second.cif`, 18 MB, 156 blocks, `cif_pow.dic`, 163 tables)

| Phase | Time |
|-------|------|
| CIF parse | ~0.7s |
| Ingest (DuckDB) | ~12s |
| Emit | ~12s |
| **Total** | **~25s** |

**Ingest bottlenecks (hard to optimize further without architectural change):**
- `create_final_tables` ~5.6s — `ROW_NUMBER() OVER (ORDER BY ...)` sort in `_merge_keyed` for large tables (pd_meas, pd_calc_component: 100K–240K rows). Sort is inherent to assigning sequential `_row_id` values.
- `propagate_fk_sql` ~2.4s — FK fill passes.
- `flush_table_batches` ~0.9s, `load_block_data` ~1.2s.

### Test suite state

- **1749 tests pass** (full suite, rust branch, 2026-05-01)
- Run: `.venv/Scripts/python -m pytest -x -q`
- Profiler: `.venv/Scripts/python profile_pipeline.py --input second`

### Open decisions

1. **`emit_create_statements` DDL target** — this function generates SQLite DDL (`TEXT`, `INTEGER`, `DEFERRABLE INITIALLY DEFERRED`) and is still exported as public API. The ingest path no longer uses it (DuckDB ingest has its own DDL). Decision needed: should `emit_create_statements` be updated to generate DuckDB DDL, or kept as SQLite DDL for backward compatibility? Currently tested with SQLite in `test_schema.py`.

2. **`_block_id`/`_row_id` rename** — todo.md documents a pervasive rename to `_pycifparse_block_id`/`_pycifparse_row_id`. This is a large mechanical change touching schema generation, ingest, output, compactification, fidelity, inspect, all tests, and prompts. Decide when/whether to do this before or after the rust branch merges to main.

3. **rust branch merge to main** — the rust branch contains: PyO3-backed `CifFile` (Phase B), Arrow IR pipeline, DuckDB ingest (Phase C), and all associated performance work. Main branch is still on the old Python CifFile + SQLite path. Decide merge timing relative to remaining functional work (severity unification, `_cif_synthetic` table, `CifBuilder` cross-type duplicates).

4. **Ingest further optimization** — current 12s ingest is already 97× faster than the original Python path. The main remaining bottleneck (`ROW_NUMBER()` sort for 100K+ row tables) would require pre-computing a sort key during `flush_table_batches`. Not in scope unless a specific use case requires it.

5. **`_validation_result` table** — created for two UUID-regime checks but its ongoing role is unclear now that the content validator uses a report-object approach. Scope whether to extend, retain, or remove it before adding further validation.

### What's next

The DuckDB migration and performance optimization work is complete. Remaining work in `tasks/todo.md` (see "Remaining items" section):
- Expand tests for file-based loading
- Unify severity levels across parser/ingest/validation
- `CifBuilder` cross-type duplicate tag detection
- `_cif_synthetic` scoping and `_validation_result` table decision
- `source_line`/`source_col` propagation to `ValidationIssue`

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

#### Phase B.4 — Direct Arrow handoff + Rust file I/O ✓ COMPLETE (2026-04-26)

- [x] `arrow` upgraded from v53 → v54 (v54 uses pyo3 ^0.23; v53 uses ^0.22 — conflict)
- [x] `raw_builder.rs`: `ParsedCif::to_py_batches()` using `arrow::pyarrow::ToPyArrow`; `to_ipc_batches`, `batch_to_ipc`, IPC imports removed
- [x] `lib.rs`: `parse_arrow` uses `to_py_batches()` — returns `list[pa.RecordBatch]` directly; `parse_arrow_file(path, mode)` added (Rust `std::fs::read_to_string`); both registered in module
- [x] `builder.py`: `build_arrow()` drops IPC deserialization; `build_arrow_file(path, *, mode)` added
- [x] `__init__.py`: `build_arrow_file` exported
- [x] `pycifparse_core.pyi`: `parse_arrow` return type updated; `parse_arrow_file` stub added
- [x] 1836 tests pass; Lesson 107

#### Phase B.3 — PyO3-exposed CifFile ✓ COMPLETE (2026-04-26)

- [x] `cif_model.rs` (new): `PyCifSaveFrame`, `PyCifBlock`, `PyCifFile` `#[pyclass]` types
  - Internal data stored as live Python objects (`Py<PyAny>`) so `writer.py`/`clean.py` mutation works unchanged
  - Full public API: `__getitem__`, `__contains__`, `tags`, `loops`, `save_frames`, `get_all`, `deepcopy`
  - Mutation methods: `_append_value`, `_add_loop`, `_add_save_frame`, `_add_block`
  - `build_py_cif(ParsedCif, py)` converts in one pass — no dict intermediary
- [x] `lib.rs`: `parse_cif(source, mode)` added; returns `(PyCifFile, list[error_dicts])` directly
- [x] `builder.py`: `build()` calls `parse_cif` — dict-unpacking code removed
- [x] `model.py`: replaced Python class definitions with PyO3 re-exports (`CifFile = _core.CifFile` etc.)
- [x] `pycifparse_core.pyi`: full stubs for all three types + `parse_cif`
- [x] 1836 tests pass; Lessons 105–106

#### Risk areas

- `deepcopy()` on Arrow-backed types: must clone the underlying RecordBatches
- Container values (CIF lists/tables) are not columnar — store as JSON strings in Arrow or as a separate side-channel
- Save frame access from `CifBlock` — save frames nested inside blocks need to be accessible via `block["save_name"]`
- `CifScalar` is removed from the public API; downstream consumers use plain `str` (resolved in B.1)

---

### Compiled Path Phase C — DuckDB merge + validate (replaces Python ingest hot path)

**Goal:** Replace `_process_loop`, `_apply_fk`, and `_merge_into` in `ingest.py` with DuckDB SQL.
Arrow RecordBatches from the Rust parser flow straight into DuckDB; Python touches only schema
metadata and conflict flag columns, never row data. `ValidationReport` is unchanged.

**Reference:** `prompts/compiled_path.md` § Component 3

**Ordering requirement (must not be violated):**
```
1. Per-block implicit key resolution  (FK propagation within each block's namespace)
2. Cross-block merge                  (needs complete key values to match rows)
3. Validation                         (operates on fully-merged, key-complete data)
4. Final SQLite push                  (only if no blocking failures)
```

#### Invariants that must be preserved

- `ValidationReport` / `ValidationIssue` public API: unchanged
- `ingest()` public signature: unchanged
- SQLite schema (DDL, FK constraints, indices): unchanged — output identical to today
- `_cif_fallback`, `_tag_presence`, `_block_order`, `_block_dataset_membership`: still written
- `IngestionError` raised on blocking failures; advisory failures still push to SQLite

#### What stays in Python (do NOT move)

| Kept in Python | Reason |
|---------------|--------|
| `_select_blocks()` / `dataset_id` routing | Block selection logic, not a hot path |
| `_fill_bridge_columns()` | Complex multi-hop lookup; SQL JOIN chains are harder to generate dynamically and this is not a bottleneck |
| `_block_dataset_membership`, `_block_order` inserts | Metadata only; trivial to write from Python |
| `encode_value()`, `split_su()`, `build_su_map()`, `build_tag_to_column()` | Still used by helpers and fallback |
| `validate()` public function, `ValidationReport`, `ValidationIssue` | Pure data objects; public API |
| `_db_validate.py` check logic (leaf-level type/range/state checks) | Re-used, not replaced |

#### What gets deleted

- `_process_loop()` and `_process_scalar()` in `_Ingester`
- `_apply_fk()` (entire function, ~160 lines)
- `_merge_into()` (entire function, ~50 lines)
- `_loops_compatible()` — subsumed by DuckDB tag routing
- Per-row UUID generation loop in `_process_loop`
- `set_buffers`, `loop_scalar_buffers`, `all_iter_rows` accumulator patterns

#### Phase C.1 — DuckDB setup + raw table loading from Arrow IR

**New file: `src/pycifparse/ingestion/duckdb_ingest.py`**

- `pip install duckdb` (add to project deps)
- `build_duckdb(batches, schema) -> duckdb.DuckDBPyConnection`:
  - Open `duckdb.connect(':memory:')`
  - Register each `pa.RecordBatch` via `db.register(f'_ir_{batch_id}', batch)`
  - For each schema table: `CREATE TABLE <tbl> AS SELECT ...` routing Arrow columns
    to schema columns via `tag → (table, col)` mapping from `SchemaSpec`
  - SU splitting expressed as SQL string manipulation (regex extract of `numeric(su)` form)
  - Fallback routing: tags not in schema land in `_cif_fallback` DuckDB table
  - Save frames: filter `_frame_idx IS NOT NULL`; routed to schema tables or fallback like blocks

**Risk:** Tag routing — each Arrow batch has a different column set; the SQL must UNION
the right columns across batches. The Python `tag_to_column` dict drives column selection;
the generated SQL is structurally similar to `_process_loop` but expressed as `SELECT`.

#### Phase C.2 — Per-block FK propagation in SQL

All FK propagation runs against DuckDB tables that were just built in C.1.
Python iterates the FK graph from `SchemaSpec` and generates SQL; DuckDB executes it.

**Single-column key-FK fill (replaces primary use of `_apply_fk`):**
```sql
UPDATE child_table c
SET fk_col = (
    SELECT p.pk_col FROM parent_table p
    WHERE p._block_id = c._block_id LIMIT 1
)
WHERE c.fk_col IS NULL
  AND c._block_id IN (SELECT DISTINCT _block_id FROM parent_table);
```
This is executed once per FK edge in the graph, in topological order.

**UUID generation for missing key-FKs (replaces `str(uuid.uuid4())` per row):**
```sql
UPDATE child_table
SET fk_col = gen_random_uuid()::text
WHERE fk_col IS NULL;
```
Multi-category loop UUID sharing (same UUID across sibling tables per iteration)
requires a two-pass approach: assign UUIDs to one canonical table first, then
propagate via the shared PK column name to sibling tables.

**Composite FK propagation:** Expressed as multi-column UPDATE with correlated subquery
or JOIN. Transitive lookup (current Python: up to 15-hop chain) is expressed as a
sequence of JOIN steps generated from the FK graph.

**Propagation links** (non-FK DDLm Link items): additional UPDATE statements generated
from `schema.propagation_links`.

**Stub row insertion** (parent row guaranteed to exist for deferred FK check):
expressed as `INSERT INTO parent ... SELECT ... FROM child WHERE NOT EXISTS (SELECT 1 FROM parent ...)`.

**Risk:** The current Python `_apply_fk` accumulates state across loop iterations via
`fk_accumulator` (a dict keyed by `def_id`). SQL equivalents must express this as
set-oriented operations. The accumulator pattern works because scalars are processed
before loops within a block — the SQL ordering must reflect this (process scalar
RecordBatches first, then loop batches, or use a CTE that selects scalar values).

#### Phase C.3 — Cross-block merge in SQL

After all blocks have been FK-propagated, merge rows across blocks by PK.

**Merge rule (per table):**
```sql
-- Identify conflicts: same PK, different non-NULL non-PK value for same column
SELECT pk_cols, col, MIN(value), MAX(value)
FROM all_block_rows
GROUP BY pk_cols, col
HAVING COUNT(DISTINCT value) > 1 AND COUNT(value) > 1
```

**Merge result:**
```sql
-- Merged table: FIRST non-NULL wins per (pk, col)
SELECT pk_cols, FIRST(col IGNORE NULLS ORDER BY _block_idx) AS col, ...
FROM all_block_rows
GROUP BY pk_cols
```

Conflicts are collected into the audit log / `ValidationIssue` list. Blocking conflicts
raise `IngestionError` and abort the SQLite push (audit rows still committed).

**Risk:** `_merge_into` tracks `_row_id` counters to give every row a stable integer ID.
DuckDB uses `ROW_NUMBER() OVER (ORDER BY ...)` to assign `_row_id` equivalents.

#### Phase C.4 — Validation in DuckDB

Re-express `validate_database()` (`_db_validate.py`) as DuckDB queries against the merged
tables, before the final SQLite push.

- **Mandatory tags**: `SELECT ... WHERE col IS NULL AND ...` per required column
- **Enumeration states**: `SELECT ... WHERE col NOT IN (...)` per constrained column  
- **Enumeration range**: `SELECT ... WHERE TRY_CAST(col AS DOUBLE) NOT BETWEEN lo AND hi`
- **Type checks**: DuckDB's `TRY_CAST` for real/integer validation; regex for datetime
- **FK integrity**: `LEFT JOIN parent ... WHERE parent.pk IS NULL`

Leaf-level check functions in `_db_checks.py` (type parsing, range parsing) are still called
from Python to build the SQL WHERE clauses — they are not replaced, just called differently.

Results are collected into `DbValidationResult` objects exactly as today, then converted to
`ValidationIssue` via the existing `_db_result_to_issue()`. `ValidationReport` is unchanged.

#### Phase C.5 — Final SQLite push from DuckDB

```python
for table_name in schema.tables:
    arrow_table = db.execute(f'SELECT * FROM "{table_name}"').arrow()
    # convert Arrow → rows, INSERT OR REPLACE into SQLite
    cols = arrow_table.schema.names
    placeholders = ', '.join('?' for _ in cols)
    cur.executemany(
        f'INSERT OR REPLACE INTO "{table_name}" ({", ".join(cols)}) VALUES ({placeholders})',
        zip(*[col.to_pylist() for col in arrow_table.columns]),
    )
```

Metadata tables (`_block_order`, `_block_dataset_membership`, `_tag_presence`,
`_validation_result`, `_cif_fallback`) are still written from Python as today — these
are not hot-path tables and their current implementation is correct.

`_pycifparse_audit` table (from spec): deferred — write from Python for now,
using the same `on_error` callback mechanism.

#### Phase C.6 — Hot path deletion + verification

**Delete:**
- `_Ingester._process_loop`, `_Ingester._process_scalar`
- `_apply_fk`, `_merge_into`, `_loops_compatible`

**Keep (still used by C.1–C.5 or by other code):**
- `_Ingester._process_block_no_schema` (fallback path, no DuckDB needed)
- `encode_value`, `split_su`, `build_su_map`, `build_tag_to_column`
- `_select_blocks`, `_read_dataset_ids`, `_id_regime`, `_record_membership`
- `_fill_bridge_columns`

**Verification:** Run both pipelines against the same test CIF files; dump both SQLite
databases to sorted text and diff. All structured tables must match. `_cif_fallback`
must match. `_tag_presence` and `_block_order` must match.

#### Implementation order

```
C.1  →  C.2  →  C.3  →  C.4  →  C.5  →  C.6
```

Implement and test each step before advancing. After C.1, existing tests still pass
(old Python path still runs). After C.6, the old path is deleted and tests validate
the new path exclusively.

#### Open questions (resolve before C.2)

1. **FK graph ordering:** Does `SchemaSpec` expose the FK edges in topological order,
   or must Phase C derive it? Check `schema.tables` and `TableDef.foreign_keys`.
2. **fk_accumulator equivalent:** Scalar values that set FK context for later loops —
   must the SQL use a CTE that selects the scalar RecordBatch first and joins to loop rows?
   Or is the existing set-oriented merge sufficient?
3. **Multi-category loop UUID sharing:** The Python shares one UUID per (col_name, iter_idx)
   across sibling tables. SQL equivalent: generate UUID once in a CTE, JOIN to all tables.
4. **`duckdb` version:** Confirm `gen_random_uuid()` is available (added in DuckDB 0.8).
   Use `uuid()` as fallback if needed.

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

### Phase C — DuckDB ingest hot path: performance work (2026-04-27)

#### What was done this session

Phase C (DuckDB integration) is functionally complete and all 1836 tests pass.
This session focused entirely on performance after profiling revealed ingest dominated runtime.

**Optimisations implemented:**

| Optimisation | Component | Before | After |
|---|---|---|---|
| Arrow bulk insert (`_load_loop`) | `duckdb_ingest.py` | `executemany` per row | `pa.record_batch` → `db.register` + INSERT per table-per-block |
| Eliminate GROUP BY + Python-side merge | `extract_merged_rows` | `FIRST(col ORDER BY ...) FILTER` × 60 cols | single `ORDER BY` fetch + Python winner dict |
| First-occurrence fast path | `extract_merged_rows` | `[None]*n_cols` + 34-iter loop per row | `list(vals)` + `continue` |
| Deferred `seen_losers` | `extract_merged_rows` | `[set()]*n_cols` per row | `setdefault` on first conflict only |
| `_compute_id_regimes()` one-pass precompute | `ingest.py` | O(blocks × rows) per-block scan | O(rows) single pass → dict |
| `tag_presence_rows` population (bug fix) | `extract_merged_rows` | never populated | populated for non-winning blocks |
| Explicit `db.close(); del db` | `_run_schema_path` | deferred GC | controlled release |

**Measured speedups (`multi_one.cif`, 41KB, 25 blocks):**
- Ingest: 18.452s → 1.541s (12×)

**Measured speedups (`second.cif`, 17MB, 156 blocks):**
- Ingest: ~2680s (original Python) → 27.553s DuckDB (97× vs original)
- `second.cif` clean run breakdown: Load ~10s, Merge ~5s, Flush ~8.5s, Propagate ~3s

**Bug fixed:** `TestOriginalModeSharedSet::test_all_blocks_have_audit_dataset_id` — block2/block3 were missing `_audit_dataset.id` in ORIGINAL-mode output because `tag_presence_rows` was never populated by the DuckDB path. Now fixed (lessons 108–113).

#### Current state

- All 1836 tests pass
- `second.cif` ingest: ~27.5s (target: ~2.7s for another 10×)
- `multi_one.cif` ingest: ~1.5s (41KB — acceptable)
- Phase C.6 (delete old Python hot path) still deferred — `_process_loop`, `_process_scalar`, `_apply_fk`, `_merge_into`, `_loops_compatible` still exist in `ingest.py`

#### Next: another 10× improvement on `second.cif` ingest

Target: 27.5s → ~2.7s. Remaining bottlenecks and candidate approaches:

| Bottleneck | Current cost | Approach |
|---|---|---|
| Arrow inserts (Load phase) | ~10s — 156 register/execute/unregister per table | Batch all blocks per table into one Arrow insert (O(tables) not O(blocks × tables)) |
| `fetchall()` in merge (Merge phase) | ~5s — 500K+ Python tuples created | `fetch_arrow_table()` → columnar access; avoids Python tuple construction |
| SQLite `executemany` flush | ~8.5s — 126K+ rows per large table, row-by-row | Arrow → SQLite via ADBC or column-oriented `executemany(zip(*cols))` |
| FK propagation UPDATEs | ~3s — one UPDATE per FK edge per block | Batch all blocks into a single UPDATE (remove `AND _block_id = ?` filter) |
| SQLite pragmas during flush | free | `PRAGMA synchronous=OFF; PRAGMA journal_mode=MEMORY` inside ingest transaction |

**Highest leverage:** batching Arrow inserts (single `pa.concat_tables` across all blocks per table, then one register/INSERT/unregister) and replacing `fetchall` with `fetch_arrow_table()`.

#### Open decisions

1. **SQLite ADBC:** `adbc_driver_sqlite` allows Arrow → SQLite without Python intermediary. Worth the new dependency if executemany flush becomes the bottleneck after other fixes.
2. **FK propagation scope:** Current `propagate_fk_sql` emits one UPDATE per `(fk_edge, block)`. Removing the `_block_id` filter makes it one UPDATE per FK edge total — valid only if the JOIN is block-scoped anyway (it is, via parent/child sharing `_block_id`). Verify before changing.

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
