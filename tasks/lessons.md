# cifflow — Lessons Learned

## Lesson 118 — ALL_BLOCKS re-ingest duplication: propagation links must resolve transitively across block boundaries (2026-05-03)

**Context:** Re-ingesting `output_all_blocks.cif` caused `pd_data` to accumulate 25252 rows (4×6313) instead of 6313. The ALL_BLOCKS emit mode writes one CIF block per category per Set-key, so `data_pd_meas_degaussa_raw_01`, `data_pd_proc_degaussa_raw_01`, etc. are separate blocks with different `_cifflow_block_id` values.

**Bug chain:**
1. `_raw_pd_meas` rows have `_cifflow_block_id = 'pd_meas_degaussa_raw_01'`. Their propagation link says `diffractogram_id ← _pd_data.diffractogram_id`, so the fill queries `_raw_pd_data` filtered by `_cifflow_block_id = 'pd_meas_degaussa_raw_01'` — but `_raw_pd_data` only has rows from `'pd_data_degaussa_raw_01'`. Fill fails.
2. `has_single_fk = False` for `diffractogram_id` (only composite FK) → UUID generation fires → each pd_meas row gets a unique random UUID.
3. Composite FK stub creation: pd_meas's (point_id, diffractogram_id) → pd_data: for each UUID-diffractogram_id not found in `_raw_pd_data`, inserts a stub. 6313 stubs × 3 tables = 18939 spurious rows added to `_raw_pd_data`.
4. `_merge_keyed` sees 25252 distinct PKs and inserts all of them.

**Root cause:** `_run_fk_fill_pass` resolved propagation links only one level deep. The full chain `pd_meas.diffractogram_id → pd_data.diffractogram_id → pd_diffractogram.id` was not followed. Each ALL_BLOCKS block emits the Set-key scalar (`_pd_diffractogram.id degaussa_raw_01`), so `_raw_pd_diffractogram` has a row with `_cifflow_block_id = 'pd_meas_degaussa_raw_01'` and `id = 'degaussa_raw_01'` — exactly what pd_meas needs — but the code never looked there.

**Fix:** Modified the propagation link resolution in `_run_fk_fill_pass` to follow the chain transitively (up to 8 levels). For each level, it adds three subqueries (same-loop/iter, scalars-loop, any-row-in-block) to the COALESCE. The direct level is tried first; deeper levels are fallbacks. With `diffractogram_id` correctly filled, composite FK stub insertion finds existing matches and inserts nothing.

**Rule:** Propagation links can form chains (A → B → C). A block-scoped FK fill that only resolves one level silently fails when the direct parent's staging rows live in a different block. Always follow the full transitive chain and include all reachable ancestors in the COALESCE so that any level sharing the current `_cifflow_block_id` can satisfy the lookup.

**Diagnostic approach:** Added `_pd_diag` checkpoints at 4 points in `propagate_fk_sql` (before/after each FK fill pass and after stubs) and a `[DIAG]`/`[POST-MERGE]` block in `create_final_tables`. The FK-DIAG showed `_raw_pd_data` at 6313 rows through all 4 checkpoints, confirming the spurious rows appeared in the composite FK stub phase that runs after those checkpoints. This narrowed the bug to lines 775–808 within minutes.

---

## Lesson 117 — DuckDB migration audit: most modules were already DuckDB; only test_check_fidelity.py needed changes (2026-05-01)

**Context:** A post-migration audit swept every non-ingest file for SQLite patterns. Files checked: `fidelity/check.py`, `database/compact.py`, `database/__init__.py`, `inspect/_ingest.py`, `validation/_db_validate.py`, `validation/_validate.py`, `tests/fidelity/test_check_fidelity.py`, `tests/dictionary/test_schema.py`.

**Finding:** All source files were already fully DuckDB. Only `test_check_fidelity.py` retained SQLite: 6 test helpers used `sqlite3.connect(':memory:')` as a duck-typed substitute for DuckDB connections, and two mock `side_effect` used `sqlite3.OperationalError`.

**Fix:** In `test_check_fidelity.py`: replaced `sqlite3.connect(':memory:')` with `duckdb.connect()`, removed `row_factory` lines (unused by DuckDB cursor), replaced `sqlite3.OperationalError` mock side-effects with plain `Exception`, and changed `REAL` column type to `DOUBLE`. Import changed from `sqlite3` to `duckdb`. `test_schema.py` kept as-is — its `_execute_schema` helper correctly uses SQLite to validate `emit_create_statements` SQLite DDL (a public API function, not the ingest path).

**Rule:** When using a DB connection in a test purely as a duck-typed fixture (no DB-specific API), prefer the actual target DB type. Duck typing works until a function adds a DB-specific API call, at which point every test using the wrong type silently breaks.

---

## Lesson 116 — Pre-fetch all rows at the start of an emit pass to eliminate N+1 DuckDB queries (2026-05-01)

**Context:** `emit.py` collection functions (`_collect_original`, `_collect_grouped`, `_collect_one_block`) previously called `_fetch_rows(conn, table_name, '"_cifflow_block_id" = ?', ...)` once per block per table — an N+1 pattern. For `second.cif` (156 blocks × ~125 populated tables) this produced 19,500 individual DuckDB queries.

**Fix:** Added `_EmitCache` class that pre-fetches all rows from all schema tables once at the start of each collection pass (using `_fetch_rows(conn, tbl_name)` per table). Rows are indexed three ways: by block_id (`_by_block`), by PK tuple (`_by_pk`), and as a flat list (`_all`). `_tag_presence` and `_cif_fallback` are also pre-fetched. All subsequent lookups in `_fetch_rows_for_block` and per-bid loops are pure in-memory dict operations. Emit dropped from 82s → 12s on `second.cif`.

**Rule:** Any emit/collection pass that loops (blocks × tables) and queries DuckDB per iteration is O(blocks × tables) queries. Pre-fetch all tables once and serve lookups from an in-memory index. The memory cost (loading 600K+ rows into Python dicts) is acceptable for files that fit in RAM; the time savings are typically an order of magnitude.

---

## Lesson 115 — On Windows, each DuckDB query triggers Python import machinery, adding AV-scanning overhead (2026-05-01)

**Context:** cProfile of the emit phase showed 156,278 calls to `_find_and_load` (Python importlib) tracing back exclusively to `_fetch_rows`. Each of the 19,500 `_fetch_rows` calls triggered ~8 import lookups; each lookup triggered ~7 `FileFinder.find_spec` calls each doing `nt.stat()` (Windows file-system stat). Total overhead from import machinery: ~45s of the 82s emit time.

**Root cause:** DuckDB's Python layer triggers Python import lookups for type-conversion modules on each query execution (likely lazy-loading per result-set schema). On Windows, each `sys.path` search is intercepted by antivirus, making each `nt.stat()` call ~60μs instead of < 10μs.

**Fix:** Eliminating 19,500 → 125 queries (Lesson 116) reduced the import overhead proportionally — from ~45s to near-zero.

**Rule:** On Windows, minimise the number of DuckDB `execute()` calls in hot loops. The Windows AV overhead per query is 200–500μs regardless of query complexity. The same optimisation that removes N+1 query patterns also removes the Windows import overhead.

---

## Lesson 114 — `_merge_keyed_fast` (no GROUP BY) was no faster than GROUP BY for large tables (2026-05-01)

**Context:** After the `_active_data_cols` optimization, the remaining merge bottleneck for large tables (pd_meas, pd_calc_component: 100K–240K rows) was the `ROW_NUMBER() OVER (ORDER BY _cifflow_block_idx, _loop_id, _iter_idx)` window sort. A "fast path" `_merge_keyed_fast` was added that skips GROUP BY for tables where all PKs are unique, using a direct INSERT instead.

**Benchmark result:** Direct INSERT without GROUP BY was identical in speed to `_merge_keyed` with GROUP BY:
```
pd_calc_component: group_by=0.836s  fast=0.879s
pd_proc:           group_by=0.521s  fast=0.539s
```
Both paths include `ROW_NUMBER() OVER (ORDER BY ...)` — the window sort is the bottleneck in both, not the GROUP BY hash aggregation.

**Conclusion:** The fast path added code complexity with no benefit. It was removed. `ROW_NUMBER() OVER (ORDER BY ...)` on 100K+ rows costs ~0.4–0.8s per table; eliminating it would require pre-computing a sort key during `flush_table_batches` (before the staging table is finalized).

**Rule:** Before adding an alternative implementation, benchmark it against the original. If the bottleneck is in a shared sub-expression (like the window sort), alternative paths that still include it will show no improvement.

---

## Lesson 113 — DuckDB raw fetch: `fetchall()` creates Python tuples; for 500K+ rows use `fetch_arrow_table()` (2026-04-27)

**Context:** `extract_merged_rows` calls `db.execute(...).fetchall()` to retrieve all raw staging rows for a table before the Python-side merge loop. For `pd_meas` in `second.cif` (~126K rows × 34 columns), this materialises ~4M Python strings as a list of tuples — significant allocation pressure before the merge loop even begins.

**Rule:** When fetching large result sets from DuckDB for Python-side processing, prefer `fetch_arrow_table()` (returns a columnar `pa.Table`) over `fetchall()`. Column access is zero-copy from Arrow memory; iteration is either vectorised or at least avoids per-row tuple construction. Only fall back to `fetchall()` when the result is small (< ~10K rows) or column-at-a-time access would be awkward.

---

## Lesson 112 — DuckDB `FIRST(col ORDER BY ...) FILTER (WHERE col IS NOT NULL)` aggregate is catastrophically slow for wide tables (2026-04-27)

**Context:** `extract_merged_rows` originally used a GROUP BY query with `FIRST(col ORDER BY _cifflow_block_idx, _iter_idx) FILTER (WHERE col IS NOT NULL)` for every data column. For `cell` (3 rows, 60 columns), this query alone took 3067ms — despite only 3 rows. DuckDB's ordered-aggregate with filter has O(n_cols) per-execute overhead independent of row count.

**Fix:** Eliminated GROUP BY entirely. A single `SELECT ... ORDER BY _cifflow_block_idx, _loop_id, _iter_idx` plus a Python dict (`winner_blocks`, `winners_map`) replaces the aggregate. Python tracks the first occurrence (fast path: `list(vals)` + `continue`) and fills nulls from subsequent rows. No DuckDB aggregate needed.

**Rule:** Never use DuckDB's `FIRST(col ORDER BY ...) FILTER (WHERE col IS NOT NULL)` in a wide table (> ~10 columns). Replace with a sorted fetch + Python-side winner tracking.

---

## Lesson 111 — First-occurrence rows in a merge loop: `list(vals)` + `continue` avoids millions of allocations (2026-04-27)

**Context:** The merge loop for unique-PK tables (e.g. `pd_meas`, 126K rows) initialised every row as `w = [None]*n_cols` then ran a 34-iteration inner loop and allocated `[set() for _ in data_cols]` for `seen_losers` — even for rows that were first occurrences and could never have conflicts. For 126K unique-PK rows this created 4.3M Python object allocations before any conflict was possible.

**Fix:** For first occurrences (`pk_key not in winner_blocks`): store `list(vals)` (a C-level tuple→list copy) and `continue`. The inner null-fill loop and `seen_losers` initialisation are skipped entirely. Deferred `seen_losers` to `setdefault` on the first actual conflict.

**Rule:** In a merge loop where most rows are first occurrences, guard with `if pk_key not in winner_dict: ... continue` before any conflict bookkeeping. C-level `list(x)` is orders of magnitude cheaper than a Python-loop initialiser for the fast path.

---

## Lesson 110 — `tag_presence_rows` must be populated for non-winning blocks in DuckDB merge (2026-04-27)

**Context:** ORIGINAL-mode emit uses the `_tag_presence` SQLite table to find which blocks contributed data to shared Set rows (e.g. `_audit_dataset.id` shared across block1/block2/block3 via a common UUID). The DuckDB `extract_merged_rows` path never populated `tag_presence_rows`, so non-winning blocks had no entries and appeared to have no data — causing `_audit_dataset.id` to be absent from block2 and block3 in ORIGINAL mode.

**Fix:** Added `tag_presence_rows` parameter to `extract_merged_rows`. For every non-first occurrence of a `pk_key` from a different `block_id`, append `(block_id, tbl_name, col, pk_json)` for all PK columns and non-null data columns. Updated `_run_schema_path` to pass `tag_presence_rows=self.tag_presence_rows`.

**Rule:** Any code path that produces merged rows must also populate `tag_presence_rows` for every non-winning block contribution. Omitting this breaks ORIGINAL-mode emit for all shared Set rows. Add an explicit test for each emit mode after implementing a new merge path.

---

## Lesson 109 — `_id_regime` O(blocks × rows) scan: precompute with one pass (2026-04-27)

**Context:** `_record_membership` called `_id_regime(block_id)` once per block (156 calls for `second.cif`). Each call iterated all ~126K merged rows filtering by `_cifflow_block_id`. Total cost: 9.81s.

**Fix:** `_compute_id_regimes()` does one O(n_rows) pass over all merged rows, building a `dict[block_id, regime]`. `_record_membership` does a single dict lookup. Cost: 0.27s.

**Rule:** Any per-block function that iterates all merged rows is O(blocks × rows) — quadratic. Detect the pattern by looking for `for block in blocks: for row in all_rows: if row['_cifflow_block_id'] == block:`. Replace with a single precompute pass.

---

## Lesson 108 — Arrow bulk insert in DuckDB: one `register/execute/unregister` per table, not per block (2026-04-27)

**Context:** The initial Arrow-insert path in `_load_loop` did one `db.register('__batch__', arrow_batch) + INSERT + db.unregister` per block per table. For `second.cif` (156 blocks × N tables), this meant 156 register/execute/unregister cycles per table. The DuckDB overhead per cycle is non-trivial regardless of batch size.

**Rule:** Accumulate all blocks' rows per table first, then do a single Arrow insert per table (one `pa.record_batch` covering all blocks). This reduces the register/execute/unregister overhead from O(blocks × tables) to O(tables). The tradeoff is higher peak memory (all blocks' rows held before insert), which is acceptable for files that fit in RAM.

---

## Lesson 107 — `arrow-rs` `pyarrow` feature pyo3 version coupling: use arrow v54+ (2026-04-26)

**Context:** Enabling `arrow = { version = "53", features = ["pyarrow"] }` pulls in `pyo3 v0.22.4` as a transitive dependency. Our crate already depends on `pyo3 = "^0.23"`. Both crates link to `python` via `pyo3-ffi`, so cargo rejects the graph with a `links` conflict.

**Root cause:** Arrow 53's `pyarrow` feature declares `pyo3 = "^0.22"`. Arrow 54 bumped this to `pyo3 = "^0.23"`.

**Rule:** When enabling `arrow`'s `pyarrow` feature alongside `pyo3 = "^0.23"`, use `arrow = { version = "54", ... }` or later. Mixing arrow 53 + pyo3 0.23 + pyarrow feature is a hard compile-time conflict.

---

## Lesson 106 — PyO3 types that replace Python classes must expose mutable internal state as Python objects, not Rust-typed fields (2026-04-26)

**Context:** Phase B.3 replaced `CifSaveFrame`/`CifBlock`/`CifFile` Python classes with PyO3 `#[pyclass]` types. `writer.py` and `clean.py` directly mutate `._tags`, `._loops`, `._tag_order` etc. as Python dicts/lists — `ns._tags[tag] = val`, `ns._loops[loop_idx].append(x)`, `del ns._tags[tag]`.

**Design decision:** Store `_tags`, `_tag_order`, `_loops`, `_save_frames`, `_save_frame_list`, `_blocks`, `_block_list` as `PyObject = Py<PyAny>` fields with `#[pyo3(get, set)]`. The `#[pyo3(get)]` getter returns the same Python object (not a copy), so mutations from Python propagate back into the Rust struct automatically.

**Rule:** When replacing a Python class with a PyO3 type and the class has attributes that are mutated in place by external Python code, store those attributes as `PyObject` (live Python objects) rather than Rust-native types. Rust-native types require explicit getters/setters and cannot be mutated in-place from Python.

---

## Lesson 105 — `build()` optimisation: `parse_cif` eliminates the dict-unpacking pass (2026-04-26)

**Context:** `build()` previously called `parse_raw()` (returns a Python dict) then iterated the dict to construct `CifBlock`/`CifSaveFrame` Python objects — two full passes over all data. With PyO3 types, `parse_cif()` constructs the `PyCifFile`/`PyCifBlock`/`PyCifSaveFrame` objects directly in Rust in one pass and returns them to Python.

**Rule:** When the target model types are PyO3 classes, the parsing Rust function should construct and return those classes directly rather than going via a Python dict intermediary. The dict path exists only for legacy compatibility; new code should use the direct-construction path.

---

## Lesson 104 — Arrow IPC is the correct Rust→Python transport for RecordBatches; avoid the `pyarrow` crate feature (2026-04-26)

**Context:** Phase B.2 needed to return Arrow `RecordBatch` objects from Rust to Python. Two approaches exist in `arrow-rs`: the `pyarrow` feature on the `arrow` crate, which lets you hand a `RecordBatch` directly across the PyO3 boundary, and the IPC route, which serializes each batch to bytes in Rust and deserializes with `pyarrow.ipc` in Python.

**Decision:** Use IPC bytes (`arrow::ipc::writer::FileWriter` → `Cursor<Vec<u8>>` → `PyBytes`). The `pyarrow` feature pins `arrow-rs` to a specific `pyo3` version — if it diverges from the `pyo3` version declared elsewhere in `Cargo.toml`, compilation fails. IPC carries no such coupling.

**Rule:** When crossing the Rust/Python boundary with Arrow data, default to IPC bytes unless you have confirmed version alignment between `arrow`'s `pyarrow` feature and your `pyo3` dependency. One `FileWriter` per batch; `pyarrow.ipc.open_file(io.BytesIO(data)).get_batch(0)` on the Python side.

---

## Lesson 103 — Parquet and Arrow IPC both require a single schema per file; per-loop schemas are in-memory only (2026-04-26)

**Context:** The compiled_path.md spec says each loop gets its own `RecordBatch` with only its own tag columns. This is valid as an in-memory `Vec<RecordBatch>` (each batch has its own schema). But writing to a single Parquet file or a single Arrow IPC file requires a unified schema across all row-groups/batches. The two representations are not equivalent on disk.

**Rule:** Never conflate "per-batch schema" (in-memory Arrow) with "file schema" (Parquet/IPC). If you need to inspect batches from disk, either write one file per batch (as `debug_parquet.py` now does) or use a union schema with NULL padding. The per-loop schema lives only in memory.

---

## Lesson 102 — `_id_regime` quadratic scan: maintain a per-block index during merge (2026-04-23)

**Context:** `_id_regime` iterated over all rows in `merged_rows` for every block, filtering by `_cifflow_block_id`. With 156 blocks and ~1.98 M total rows, this is O(blocks × rows) — 12.9 s cumulative in profiling.

**Fix:** Added `_block_pk_values: dict[str, list[str]]` to `_Ingester`, populated inside `_merge_into` on new-row insertion (non-synthetic PK columns only). Threaded through `_apply_fk` so stub rows are also indexed. The set-buffer inline merge path required a separate update since it bypasses `_merge_into`. `_id_regime` now does a single `dict.get` lookup.

**Rule:** Any O(total_rows) scan inside a per-block loop is quadratic. Use a per-block accumulator built incrementally during the merge pass instead.

---

## Lesson 101 — Regex tokenizer: three correctness pitfalls (2026-04-22)

**Context:** Replacing the generator-based lexer with a `re.finditer` tokenizer introduced three non-obvious bugs.

1. **Unterminated triple-quoted strings.** Lazy `[\s\S]*?` fails silently if no closing `'''`/`"""` exists — the regex engine then tries shorter patterns and misidentifies the content (e.g. `''` matches as an empty single-quoted string). Fix: add greedy `TDQ_UNT`/`TSQ_UNT` fallback patterns (`"{3}[\s\S]*`) immediately after the terminated counterparts so they swallow to EOF.

2. **`\n;` inside triple-quoted strings.** A plain multiline pre-scan regex fires on `\n;` even when it's inside `'''...'''`, which is legal in CIF 2.0. Fix: the pre-scan must also find triple-quote openers and track a `skip_until` offset; any multiline match before `skip_until` is ignored.

3. **`:` inside bare words.** Making `:` a standalone delimiter token splits values like `16:00` into three tokens. In the original lexer, `:` is checked at the top of the main loop but `_read_bare_word` does not stop at it, so bare words consume `:` greedily. Fix: use a lookbehind `(?<=[\"'\]\}]):` — `:` is only structural when the immediately preceding matched character is a structural close (quote or bracket).

**Rule:** Before writing a regex tokenizer, audit the reference implementation for every character that has context-dependent meaning. Unterminated-string fallbacks and multiline-in-triple-quoted interactions are the two most common pitfalls.

---

## Lesson 100 — ALL_BLOCKS `dataset_id` must come from `_block_dataset_membership`, not a single upfront UUID (2026-04-21)

**Context:** ALL_BLOCKS mode injected one `uuid.uuid4()` for all emitted blocks. The fidelity checker then found `_audit_dataset.id` values differing between the original file and re-emitted file.

**Fix:** `_resolve_dataset_id()` queries `_block_dataset_membership` for the originating `_cifflow_block_id` values of each row group. Returns the existing ID (str), a sorted list when multiple IDs (emitted as a `loop_`), or a fresh UUID only when no membership data exists.

**Rule:** In ALL_BLOCKS mode, `dataset_id` must be resolved per block from the database, not generated once globally. `_BlockData.dataset_id` may be `str | list[str] | None`.

---

## Lesson 99 — `_sort_and_merge` re-sorts ALL_BLOCKS output alphabetically, discarding plan ordering (2026-04-21)

**Context:** `_ordered_tables_all_blocks` orders table processing by `plan.category_order`, but ALL_BLOCKS blocks all have empty `anchor_frozenset`, so they all match the catch-all spec and are then sorted alphabetically by `_sort_and_merge`, undoing the plan order.

**Fix:** In `emit()`, ALL_BLOCKS skips `_sort_and_merge` entirely: `ordered = [(b, None) for b in raw_blocks]`. The ordering is already baked into the list from `_collect_all_blocks`.

**Rule:** `_sort_and_merge` is designed for GROUPED/ORIGINAL anchor-key matching. For ALL_BLOCKS, bypass it and preserve the collector's output order directly.

---

## Lesson 98 — `plan.specs` not `plan.blocks` (2026-04-21)

**Context:** `_ordered_tables_all_blocks` referenced `plan.blocks` — a non-existent attribute. `OutputPlan` uses `specs`.

**Rule:** `OutputPlan.specs` is the list of `BlockSpec` objects. There is no `.blocks` attribute.

---

## Lesson 97 — `_classify_pk_cols` returns 5-tuples; all unpack sites must match (2026-04-21)

**Context:** The Loop branch of `_collect_all_blocks` still unpacked 3-tuples `(col, tag)` after `_classify_pk_cols` was extended to return `(col, is_set, tag, set_table, set_col)`. This caused `ValueError: not enough values to unpack` and left the old `set_fk_map` approach in place.

**Fix:** Updated unpacking to `(col, tag, st, sc)` for the filtered list and `(col, _, _, _)` for the grouping key. Removed the now-redundant `set_fk_map` construction.

**Rule:** When changing a return tuple's arity, grep for every unpack site before closing. The Set branch and Loop branch of the same function can diverge silently.

---

## Lesson 96 — Test callbacks that accept a two-arg signature break when a third kwarg is added (2026-04-20)

**Context:** `on_error` callback in `ingest()` extended from `(msg, block_id)` to `(msg, block_id, *, table, column, key_values)`.

**Mistake:** Existing test lambdas `lambda msg, blk=None: ...` and `list.append` were passed as the callback. When the ingester called them with keyword arguments, Python raised `TypeError` (unexpected keyword argument / too many arguments).

**Fix:** Update all test callbacks to accept `**kw`: `lambda msg, blk=None, **kw: ...`.

**Rule:** Any time a callback signature gains optional keyword arguments, grep for every call site that passes a bare lambda or method (e.g. `list.append`) as the callback and update them. Direct method references like `list.append` can never accept kwargs.

---

## Lesson 95 — Structured data in callbacks: use keyword-only kwargs, not positional extension (2026-04-20)

**Context:** Adding `table`, `column`, `key_values` to the `on_error` callback to give merge conflicts structured metadata.

**Decision:** Extended the signature as keyword-only args with defaults (`*, table=None, column=None, key_values=None`) rather than adding positional args.

**Why:** Positional extension is a hard breaking change — every caller must update their signature to match arity. Keyword-only args with defaults are backward-compatible: callers that don't declare them will break only if the callee actually passes them (which it does), so callers need `**kwargs` to absorb extras. This is still a change, but a more targeted one.

**Rule:** When extending a callback signature with optional metadata, prefer keyword-only args. Document the full signature in the docstring; use `Callable[..., None]` as the type annotation and explain the actual contract in prose.

---

## Lesson 94 — Test message-content assertions can break when repr() is used in format strings (2026-04-19)

**Context:** `test_table_unquotable_key_gives_error` in `tests/validation/test_db_validate.py`.

**Mistake:** Test asserted `bad_key in r.message` where `bad_key = "''' and \"\"\""`. The message was formatted with `{key!r}`, so the literal string never appeared verbatim in the message (Python repr escapes the inner quotes differently).

**Fix:** Assert on `r.value == bad_key` instead — the `value` field carries the raw string, not the repr.

**Rule:** When checking that a specific string appears in an error message, prefer asserting on a structured field (`value`, `tag`, etc.) rather than parsing the human-readable `message`. If you must check `message`, use the exact formatted form including any `repr()` escaping.

---

## Lesson 93 — Patching the wrong function when the real trigger is never called (2026-04-19)

**Context:** `TestInternalError` in `tests/validation/test_db_validate.py`.

**Mistake:** Patched `_check_keyless_cardinality` to raise, but the test table had PKs (`_cifflow_block_id`, `_cifflow_row_id`), so the code path that calls `_check_keyless_cardinality` (keyless Set tables only) was never reached. The patch was never triggered; the test passed vacuously.

**Fix:** Patch `_run_validation` directly to raise, which is always called regardless of table shape.

**Rule:** Before patching a function to simulate an error, verify that the test setup actually exercises the code path that calls it. A vacuously-passing test gives false confidence.

---

## Lesson 92 — Parametrize None separately when a downstream default converts it (2026-04-19)

**Context:** `TestTypeMapping.test_type_contents_stored_as_is` in `tests/dictionary/test_schema.py`.

**Mistake:** `None` was included in the parametrize list for `type_contents`. After adding `item.type_contents or 'Text'` in `generate_schema()`, the `None` case now produces `'Text'`, so `assert col.type_contents == None` fails.

**Fix:** Remove `None` from the parametrize list. Add a dedicated `test_type_contents_none_defaults_to_text` test that asserts `col.type_contents == 'Text'`.

**Rule:** When a function converts a sentinel input value (e.g. `None → 'Text'`), do not test the sentinel alongside the pass-through values in a single parametrize block. Give it its own test that asserts the converted output.

---

## Lesson 91 — Define helpers before the dict/mapping that references them (2026-04-19)

**Context:** `_db_checks.py`; `_TYPE_CONTENTS_RULES` dict referencing `_valid_datetime`, `_valid_real`, `_valid_range`.

**Mistake:** The initial file draft placed `_TYPE_CONTENTS_RULES` before the helper functions it referenced. Python raises `NameError` at module import time.

**Fix:** Move all helper functions above the dict that uses them.

**Rule:** Module-level dicts/mappings that reference functions must appear after those functions in the source file. Unlike class bodies, module-level expressions are evaluated top-to-bottom at import time.

---

## Lesson 90 — strip_loop_padding k=0 unless ALL columns have trailing PLACEHOLDERs (2026-04-18)

**Context:** `_strip_padding_in_ns` in `cifmodel/clean.py`; test for `strip_loop_padding`.

**Situation:** Initial test used a real-parsed padded loop (3 tags, 5 values, 1 padded). The algorithm computes `k = min(trailing PLACEHOLDER count per column)`. Because the parser only pads the missing columns in the incomplete final row, the non-padded columns have `k=0`, so `min=0` — nothing is stripped. The test expected 1 row but got 2.

**Fix:** Test directly constructs a `CifFile` with a loop where ALL columns' last values are `CifScalar('?', PLACEHOLDER)`, which is the condition that makes `k > 0`.

**Rule:** `strip_loop_padding` only fires when every column in the loop simultaneously has trailing PLACEHOLDERs. Real-world padding from the parser will only produce this condition if the user's last complete row also happens to end with `?` values. Tests for this step must construct the model state directly, not rely on the parser producing a specific pattern.

---

## Lesson 89 — Python chained comparison `a in b == c` is not `(a in b) == c` (2026-04-18)

**Context:** `test_copy_true_original_unmodified` in `tests/cifmodel/test_clean.py`.

**Mistake:** Wrote `assert "_error_value" in cif["b"]._tags == original_has_tag`. Python evaluates this as `("_error_value" in cif["b"]._tags) and (cif["b"]._tags == original_has_tag)` — a chained comparison. The dict is never equal to a bool, so the assertion failed with a confusing dict-vs-bool message.

**Fix:** Split into two separate assertions.

**Rule:** Never write `a in b == c`. Always use `(a in b) == c` with explicit parentheses, or two separate assertions.

---

## Lesson 88 — A single-column loop reassigned to a different length is still consistent (2026-04-18)

**Context:** `test_reassign_loop_column_different_length_ok` in `tests/cifmodel/test_writer.py`.

**Mistake:** Test asserted that `build()` raises `ValueError` when a single-column loop's column is reassigned to a shorter list. It doesn't — with only one column, all column lengths are trivially equal regardless of length. The build-time inconsistency check only fires when two columns in the same loop have different lengths.

**Fix:** Test uses a two-column loop, reassigns only one column to a different length, and then verifies `build()` raises.

**Rule:** Loop column-length validation requires at least two columns to detect a mismatch. Single-column loops are always structurally valid regardless of length (as long as length ≥ 1 for the zero-row check).

---

## Lesson 87 — Implement without permission after spec review causes wasted work (2026-04-18)

**Context:** Start of `CifWriter` + `clean` implementation session.

**Mistake:** After the previous session ended with "spec is ready", the assistant immediately began implementing (`model.py`, `builder.py`, `writer.py`, `clean.py`) without being asked to. The user had to revert all changes and start over.

**Rule:** A complete spec does not authorise implementation. Wait for explicit instruction ("implement", "go ahead", etc.) before writing any code. "Spec is ready" means the design work is done — nothing more.

---

## Lesson 86 — When multiple bridge chains resolve, check them all for agreement (2026-04-16)

**Context:** `_fill_bridge_columns` in `ingestion/ingest.py`.

**Situation:** After adding fallback chains, the code tried chains in order and stopped at the first non-None result.  This silently ignores disagreements: if two chains both resolve but give different values, the fallback value is used without any indication that the data is inconsistent.

**Fix:** All chains are now evaluated; non-None results are collected and compared.  If they all agree, the common value is used silently.  If they disagree, a warning is emitted (via the `emit` callback) naming all resolved values and the row, and the first resolved value is still used.

**Rule:** When multiple independent paths can provide the same derived value, always check them all and warn on disagreement.  Stopping early at the first non-None result hides data quality issues that would otherwise go undetected.

---

## Lesson 83 — A partial FK is only safe when the dictionary is wrong, not when the data happens to be sparse (2026-04-16)

**Context:** Fix A — emitting a partial `FOREIGN KEY` when a composite-PK target has one component with `enumeration_default = '.'`.

**Mistake:** The code tried to infer from `enumeration_default = '.'` that a column would always be inapplicable, and on that basis added a `UNIQUE` constraint to the target table and emitted a partial FK.  The user correctly identified that `enumeration_default` is only the value used when the item is *absent* — it does not prevent a CIF file from supplying a real value.  A file that legitimately populates that column would violate the synthetic `UNIQUE` constraint and be rejected by SQLite.

**Fix:** Reverted Fix A entirely.  A partial FK targeting a composite-PK table is a dictionary-level authoring problem: the referring category is simply missing a Link item for the extra PK column.  The correct resolution is to fix the dictionary, not to paper over it in code.

**Rule:** Do not add database constraints that encode assumptions about what values CIF files *will* supply.  Only encode what the dictionary says they *must* supply.

---

## Lesson 84 — BFS for transitive bridges must return all shortest paths, not just the first (2026-04-16)

**Context:** `_find_transitive_bridge` in `dictionary/schema.py`; `_fill_bridge_columns` in `ingestion/ingest.py`.

**Mistake:** The BFS correctly collected all paths at the minimum depth but then returned only `results[0]`.  In the cif_pow case, two equally-short paths exist for `pd_peak.radiation_id`: one through `diffrn` and one through `pd_instr`.  Different CIF authors populate different paths — files that omit `_diffrn.diffrn_radiation_id` but provide `_pd_instr.radiation_id` silently produced `NULL` for the bridge column.

**Fix:** `_find_transitive_bridge` now returns the full `results` list.  `BridgeColumnDef` carries `fallback_chains` for the alternatives.  `_fill_bridge_columns` tries each chain in order per row and uses the first non-NULL result.

**Rule:** When multiple bridge paths of equal length exist, all of them are valid — different data files will use different paths.  Always preserve and attempt all candidates at ingest time rather than picking one arbitrarily at schema-generation time.

---

## Lesson 85 — Bridge column lookups must not be keyed by `_cifflow_block_id` (2026-04-16)

**Context:** `_build_chain_lookups` and `_resolve_chain` in `ingestion/ingest.py`.

**Mistake:** Bridge lookups were keyed by `(block_id, pk_val)` where `block_id` was the `_cifflow_block_id` of the *source* row.  In a multi-block dataset (e.g. `data_peak`, `data_powder_1`, `data_wavelength_1` all sharing one `_audit_dataset.id`), the source table (`pd_peak`) and the bridge table (`pd_diffractogram`) come from different CIF data blocks and therefore have different `_cifflow_block_id` values.  The lookup always missed, leaving the derived column NULL.

**Fix:** Changed lookup key to just `pk_val`.  `merged_rows` is already scoped to a single dataset ingest call, so cross-dataset contamination cannot occur — the `_cifflow_block_id` discrimination was unnecessary and actively harmful.

**Rule:** Bridge lookups operate within `merged_rows` which is already dataset-scoped.  Do not add `_cifflow_block_id` as a discriminator inside that lookup — it will break any multi-block dataset where source and target rows originate from different CIF data blocks.

---

## Lesson 77 — In sparse column display, apply the qualification check before skipping synthetics (2026-04-15)

**Context:** `_column_rows` in `dictionary/visualise.py`.

**Mistake:** Synthetic columns were skipped with an early `continue` before the
bridge-qualification check ran.  A synthetic column that is `bc.column_name` (the
derived composite-FK column) should appear in `show_columns='sparse'` — the spec
says "synthetic columns are excluded **unless** they qualify via bridge rules".
Skipping them unconditionally meant the derived column was silently absent from
sparse display even though it is structurally significant.

**Fix:** Removed the synthetic-specific `continue` in the `sparse` branch entirely.
The single combined check
`not (col.is_primary_key or col.name in fk_source_cols or col.name in bridge_cols)`
now covers all columns uniformly — synthetic columns that qualify via `bridge_cols`
pass through; others are excluded.

**Rule:** When column display has multiple exclusion criteria, evaluate qualification
(which columns should be shown) as a single predicate rather than applying independent
early-exit guards that may shadow later qualification checks.

## Lesson 76 — Non-Single container columns store JSON; coerce leaves, not the whole value (2026-04-15)

**Context:** `convert_database` in `database/compact.py`; `ColumnDef` in `dictionary/schema.py`.

**Mistake:** `convert_database` treated every `INTEGER`-typed column the same way regardless of
`_type.container`.  A column like `_pd_pref_orient_march_dollase.hkl` has `type_contents="Integer"`
but `type_container="Matrix"`, so its stored value is a JSON array `["1","2","3"]`.  Trying to cast
that string to `int` fails (coercion-failure warning), and the JSON is lost.

**Fix:**
1. Added `type_container: str | None = None` to `ColumnDef` (optional field, default `None`, placed
   last to avoid breaking existing callers).
2. `generate_schema` populates it from `DdlmItem.type_container` for all domain columns.
3. `_sql_type_for(col)` returns `"TEXT"` whenever `type_container` is set and is not `"Single"`.
4. `_cast_value` detects a JSON array/object (raw starts with `[` or `{`), decodes it, recurses into
   leaves with `_cast_json_leaves`, casts each string leaf to the leaf type (from `type_contents`
   alone), then re-serialises with `json.dumps`.

**Rule:** Any column whose `type_container` is not `"Single"` stores structured data as JSON.
Always check `type_container` before deciding the column's storage affinity or cast strategy.

## Lesson 75 — Rows fetched from SQLite may already be typed, not TEXT (2026-04-15)

**Context:** `_cast_value` in `database/compact.py`.

**Mistake:** `_cifflow_row_id` is declared `INTEGER` in the source schema, so SQLite returns it as
a Python `int`.  Calling `re.sub(pattern, repl, raw)` on an `int` raises `TypeError`.

**Fix:** Guard at the top of `_cast_value`:
```python
if not isinstance(raw, str):
    return raw  # already typed
```
This handles any non-string value that comes back from SQLite without trying to cast it.

## Lesson 74 — Test decimal-alignment with line-level dot position, not token-internal (2026-04-15)

**Context:** `TestDecimalAlign` in `tests/output/test_emit.py`.

**Mistake:** Tests used `ln.split()[N].index('.')` (position of dot inside the stripped token).
Because `ln.split()` removes leading spaces, the dot position varies per token when integer
parts differ in width (`0.1234` → index 1, `10.5` → index 2).

**Fix:** Use `ln.index('.')` (position of dot in the full line).  Decimal alignment is a
line-level property — the dot should land at the same character column in every row.

---

## Lesson 73 — Column ordering in loop emit may differ from source CIF order (2026-04-15)

**Context:** `TestDecimalAlign.test_loop_real_column_dot_aligned` and sibling tests.

**Observation:** The loop renderer outputs columns in schema/table key order, not source-CIF
insertion order.  Key columns (PK) appear first.  Tests that hard-code a column index
(e.g. `parts[2]`) are fragile.

**Fix:** Look up the value by searching the line for the expected substring, or use the
line-level position (`ln.index('.')`) rather than assuming a particular column index.

## Lesson 72 — `_fold_content_lines` breaks before the space, keeping it in the next segment (2026-04-15)

**Context:** `_fold_content_lines` in `output/quote.py`.

**Decision:** When breaking at a whitespace character at position `break_at`, the first segment
is `line[:break_at]` (space not included) and the next segment starts at `line[break_at:]`
(space is the first character of the continuation).  Fold reconstruction removes `\<newline>`
— nothing is inserted — so the space is preserved in the reconstructed string.  This is
correct for round-trip fidelity.

**Alternative considered:** Including the space in the first segment (`line[:break_at+1]`)
would also be correct, but the chosen form is slightly more natural (break point = where
content splits, not where whitespace ends up).

---

## Lesson 71 — `make_text_field` dispatches all four semicolon formats (2026-04-15)

**Context:** `output/quote.py`; `emit.py` callers.

The four formats share a single public entry point `make_text_field(s, line_limit=None)`.
The dispatch logic is:

| `'\n;' in s` | content line > limit? | format                    |
|--------------|-----------------------|---------------------------|
| No           | No                    | plain `_make_semicolon`   |
| Yes          | No                    | `_make_prefixed_semicolon`|
| No           | Yes                   | `_make_folded_semicolon`  |
| Yes          | Yes                   | `_make_prefixed_folded_semicolon` |

`needs_fold` threshold differs by format: prefix case checks `len(line) > line_limit -
len(_PREFIX)` (physical line = `>{content}`); fold-only case checks `len(line) > line_limit`
(physical line = `{content}`).  This asymmetry is intentional and correct.

**Rule:** Always call `make_text_field` from the emit layer rather than `_make_semicolon`
or `_make_prefixed_semicolon` directly when `line_limit` is in play.

---

## Lesson 70 — Set-category re-quoting requires two passes over the tag–value pairs (2026-04-15)

**Context:** `_render_set_category` in `output/emit.py`.

**Problem:** `tag_width` (used for alignment) is computed from inline tokens.  If re-quoting
converts some inline tokens to multiline (because `tag + sep + token > line_limit`), then
`tag_width` was computed with those tags included — it may be wider than needed.

**Fix:** Two-pass approach:
1. Build `(tag, value, token)` triples; apply folding to any already-multiline tokens.
2. Compute `tag_width` from inline tokens.
3. Re-quote inline tokens where `len(f'{tag:<{tag_width}}  {token}') > line_limit`.
4. **Recompute** `tag_width` from the remaining inline tokens.

Step 4 is necessary: without it, some tags would be padded to a width driven by a tag
that now has a multiline value (and thus doesn't participate in inline alignment).

**Rule:** Always recompute `tag_width` after any step that may convert inline → multiline.

---

## Lesson 69 — ALL_BLOCKS delegates to GROUPED and strips audit_dataset for UUID consistency (2026-04-14)

**Context:** `_collect_all_blocks` in `output/emit.py`.

**Problem 1 — wrong block granularity:** The old ALL_BLOCKS put all rows from one table into
one block (one block per non-empty table).  Correct: one block per Set-anchor key combination.

**Fix:** `_collect_all_blocks` now calls `_collect_grouped`, then wraps each result with
`suppress_fk_pk=False` and a fresh session-scoped `dataset_id` UUID (CIF 2.0 only).

**Problem 2 — inconsistent `_audit_dataset.id`:** The `_render_block` injection skips blocks
that already have `audit_dataset` in `table_rows` (emitting the stored UUID instead).  When
GROUPED gives one block an `audit_dataset` row and others nothing, the block with the row
gets its stored UUID while the rest get the fresh emission UUID — causing a mismatch on
re-ingest ("no common _audit_dataset.id").

**Fix:** `_collect_all_blocks` strips `'audit_dataset'` from each block's `table_rows`
before building `_BlockData`.  This guarantees the injection always fires, all blocks
receive the same emission UUID, and the re-ingested CIF is treated as one coherent dataset.

**Rule:** ALL_BLOCKS and GROUPED share block-partitioning.  Differences: (a) no FK-PK
suppression; (b) `audit_dataset` stripped → emission UUID injected consistently.

---

## Lesson 68 — GROUPED block names changed from _cifflow_block_id to anchor-key-derived names (2026-04-14)

**Context:** `_collect_grouped` in `output/emit.py`; `_default_block_name`.

**Change:** GROUPED mode previously used the first anchor row's `_cifflow_block_id` as the output block
name.  The spec requires names to be derived from the anchor key tuple
(`{object_id}_{key_value}` joined with underscores, then sanitised).  Block names are now
built from the anchor key dict (e.g. `expt.id=['myexp']` → `id_myexp`).

**Impact on tests:** The existing GROUPED tests did not check specific block names (they always
accessed blocks via `cif2.blocks[0]` or `cif2.blocks` index), so no test changes were needed
for the naming switch.  New `TestDefaultBlockName` tests explicitly assert the new naming form.

**Rule:** When adding new GROUPED tests, access blocks by index or by iterating `cif2.blocks`,
not by a hardcoded `_cifflow_block_id` string — the block name is now the anchor key value, not the
source block's `data_` header.

---

## Lesson 67 — collect-then-sort architecture is required for spec-matched emission ordering (2026-04-14)

**Context:** `emit()` and `_sort_and_merge()` in `output/emit.py`.

**Problem:** The original emit.py rendered blocks during collection (each mode collector called
`_render_block` directly and accumulated rendered strings).  This made it impossible to reorder
blocks by spec index after the fact, because rendering happened before spec matching.

**Fix:** Changed all mode collectors to return `list[_BlockData]` (raw data structures, not
rendered strings).  `emit()` then passes the full list to `_sort_and_merge()`, which does spec
matching, `single_block` merging, and alphabetical sorting, before finally rendering each block.

**Rule:** Any future feature that requires post-collection reordering or grouping of blocks must
operate on `_BlockData` objects, not rendered strings.  Do not render until the final emission
order is known.

---

## Lesson 66 — Merge group key-compatibility check uses non-synthetic PK column sets (2026-04-14)

**Context:** `_render_merge_group()` in `output/emit.py`.

**Rule:** Two categories are key-compatible for merge group purposes when they share the
*same frozenset of non-synthetic primary key column names*.  Synthetic columns (`_cifflow_block_id`,
`_cifflow_row_id`, `_cifflow_id`) are excluded from the comparison — every table has `_cifflow_block_id`
and `_cifflow_row_id`, so including them would make all tables appear compatible.

**Fallback:** When categories are not key-compatible (different non-synthetic PK sets), emit
them as plain loops in the listed order — no warning, no error.

**FULL OUTER JOIN implementation:** Done in Python (not SQL) by indexing each table's rows by
PK tuple, collecting all unique PK tuples in encounter order, then iterating the union and
looking up each table's row for that PK (substituting `'.'` for missing rows).  SQLite has no
native FULL OUTER JOIN, and the Python approach avoids query complexity.

---

## Lesson 65 — category_parent self-references must be excluded (2026-04-14)

**Context:** `generate_schema` in `dictionary/schema.py`; `SchemaSpec.category_parent`.

**Problem:** In DDLm, top-level categories often have `_name.category_id` pointing to themselves
(e.g. `CELL` has `_name.category_id = cell`).  Without a self-reference guard, `category_parent`
would map `'cell' → 'cell'`, making `cell` appear as its own child.  Wildcard BFS would still
terminate (the `found` set prevents revisiting), but the `children` map would contain
`{'cell': ['cell', ...]}`, which is semantically wrong and misleading.

**Fix:** Added `parent_tbl != tbl_name` guard:
```python
category_parent[tbl_name] = (
    parent_tbl if parent_tbl in tables and parent_tbl != tbl_name else None
)
```

**Rule:** When building a parent-child hierarchy from DDLm `_name.category_id`, always exclude
self-references.  Top-level categories (with no parent in the schema) should have
`category_parent[tbl] = None`.

---

## Lesson 62 — CIF placeholder '.' and '?' must be treated as NULL in structured-table fidelity comparison (2026-04-13)

**Context:** `_normalised_rows()` and `_fingerprint_uuid()` in `fidelity/check.py`.

**Problem:** SQL `NULL` (tag absent from block) and the CIF placeholder string `'.'` (tag explicitly
inapplicable) are semantically equivalent for structured tables, but the fidelity checker treated them
differently.  A block with an absent `_diffrn.ambient_temperature` (stored as `NULL`) would not match
a block where `_diffrn.ambient_temperature .` was written explicitly (stored as `'.'`), producing
spurious `row_content` mismatches.

**Fix:** In both `_normalised_rows` and `_fingerprint_uuid`, skip values that are `'.'` or `'?'`
immediately after the `is None` guard:
```python
str_val = str(val)
if str_val in ('.', '?'):
    continue
```

**Rule:** For structured table comparison, `NULL`, `'.'`, and `'?'` all mean "no data here".
Treat them identically.  This does not apply to `_cif_fallback`, where `ValueType.PLACEHOLDER`
is stored and the distinction between absent and explicit placeholder is meaningful.

---

## Lesson 63 — Row diff hints must use row-relative (+/-) not absolute (A+/B+) labels (2026-04-13)

**Context:** `_row_diff_hint()` in `fidelity/check.py`.

**Problem:** The diff hint function computed `B+col=val` when the best-match row had a value the
surplus row didn't, and `A+col=val` when the surplus row had an extra value.  These labels were
hardcoded but the `row` parameter can come from either surplus_a or surplus_b.  For surplus_b rows
this produced contradictory output: both sides claimed the other had the extra value.

**Fix:** Use row-relative labels:
- `+col=val` — this row has it, closest match doesn't (the surplus row carries the extra value).
- `-col=val` — closest match has it, this row doesn't (the surplus row is missing the value).

**Rule:** Diff hints must be described relative to the row being reported, not relative to absolute
source labels.  The caller (which knows which side the row came from) supplies context via the
description string; the hint just describes the delta.

---

## Lesson 64 — DictionaryLoader needs a separate path_resolver to record full paths in source_files (2026-04-13)

**Context:** `DictionaryLoader` in `dictionary/loader.py`; `DdlmDictionary.source_files`;
`SchemaSpec.source_files`.

**Problem:** The `SourceResolver` callable returns file *content* (a string), not a path.  When
building the `source_files` list for `DdlmDictionary`, only the URI (bare filename) was available,
not the full absolute path.  The fidelity report header needed full paths to be unambiguous.

**Fix:** Added an optional `path_resolver: Callable[[str], str | None]` parameter to
`DictionaryLoader.__init__`.  Added `directory_path_resolver(path)` as a companion to
`directory_resolver(path)`.  In `_load_constituent`, the path resolver is called to resolve the
URI to a full path; if absent, the URI is stored as-is.  The base URI is resolved the same way in
`load()`.

**Rule:** Source and path resolution are separate concerns.  The source resolver fetches content;
the path resolver maps URIs to filesystem paths for human-readable output.  Keep them separate
rather than trying to make the source resolver do double duty.

---

## Lesson 57 — Emit: FK-PK columns pointing to a co-emitted Set are implicit from block scope (2026-04-11)

**Context:** `_suppressed_fk_pk_cols()` and `_render_block()` in `output/emit.py`.

**Problem:** In ORIGINAL and GROUPED modes, a table's domain PK column is often also a FK to a
Set-class category in the same block (e.g. `_cell.diffrn_id` FK → `_diffrn.id`).  Emitting both
the Set's own PK tag (`_diffrn.id`) and the referencing table's FK-PK column (`_cell.diffrn_id`)
is redundant: CIF block scope already implies the relationship.  Emitting the FK-PK also causes
problems on re-ingestion if the values differ (e.g. after UUID rotation).

**Fix:** Before rendering each table in `_render_block`, compute a set of suppressible columns
via `_suppressed_fk_pk_cols(table_def, rows, table_rows, schema)`:
1. The column must be in the table's domain PK (non-synthetic).
2. It must be part of a FK that targets a Set-class table.
3. That Set table must appear in `table_rows` for the same block with exactly one row.
4. Every row in the current table must carry the same FK value, equal to the target Set's PK value.

If all four conditions hold, the column is removed from the active column list before rendering.

**Scope:** ORIGINAL and GROUPED modes only (where related categories share a block).  ALL_BLOCKS
emits one block per table — categories are in separate blocks, so their FK values are not implicit
and must be preserved.  ONE_BLOCK is a special case where this could also apply, but is currently
excluded for simplicity.

**Rule:** If a category's PK FK target is a Set category in the same block with a consistent
value, the FK-PK column is redundant in the output.  Suppress it to keep the CIF clean; the
reader derives the value from the target Set's own PK tag.

## Lesson 58 — Two root causes of NULL columns after emit → re-ingest (2026-04-11)

**Context:** `_flush` in `ingestion/ingest.py`; `_collect_grouped` in `output/emit.py`.

**Bug 1: `_flush` uses only `rows[0].keys()` as the INSERT column list.**

Rows in `merged_rows` are Python dicts whose key sets can differ.  A stub created by
`_apply_fk` starts with only `{_cifflow_block_id, id}`.  When later merged with real data, the stub
dict grows in-place — but only for the one row that actually received the merge.  Other rows
for the same table (created from other stubs that never received real data) keep a smaller
key set.  If such a "slim" row happens to be `rows[0]`, `_flush` builds its INSERT column
list from only those keys, silently omitting columns present in later rows.  Chrome_dome's
`2theta_monochr_pre` was absent from the INSERT because `copper_top` (inserted first, slim)
was `rows[0]`.

**Fix:** Compute the union of all row keys:
```python
seen: dict[str, None] = {}
for r in rows:
    seen.update(dict.fromkeys(r.keys()))
cols = list(seen)
```

**Bug 2: GROUPED "remaining blocks" only iterated `block_id_tables`, missing keyed-anchor tables.**

`diffrn_radiation_wavelength` is in the `pd_phase` keyed-anchor group.  Its rows have
`phase_id = NULL` (no FK link to any phase), so `_fetch_rows_via_fk_path` returns nothing and
the second-pass `_cifflow_block_id` filter covers `pd_phase` block_ids — not the wavelength block_ids.
`exptl_crystal` has no rows → its entire FK group falls back to `block_id_tables`, causing the
wavelength block_ids to appear in `remaining_cifflow_block_ids`.  But only `block_id_tables` were swept
in the remaining-blocks pass, so `diffrn_radiation_wavelength` (a keyed-anchor table) was never
emitted.

**Fix:** Sweep all schema tables in the remaining-blocks pass.  It is safe because
`remaining_cifflow_block_ids` is filtered to block_ids not in `absorbed_all` — those rows were never
emitted by any keyed-anchor group.

**Rule:** When rows have NULL FK columns they cannot be found via FK-path joins.  Any table
whose rows may have NULL links to the anchor must also be swept by block_id in the fallback.
Always compute the full column-key union in `_flush`; never assume that all rows share the
same dict shape.

## Lesson 56 — GROUPED mode: empty root-anchor table silently drops its entire FK group (2026-04-11)

**Context:** `_collect_grouped` in `output/emit.py`.

**Problem:** The GROUPED emitter finds the root Set anchor for each table by BFS along FK chains.  For `cif_core.dic`, tables like `cell` and `diffrn` chain through `diffrn.crystal_id → exptl_crystal.id`, making `exptl_crystal` the root anchor.  When a CIF file does not contain any `_exptl_crystal.*` data (and no stub was created because `diffrn.crystal_id` is NULL in the database), `exptl_crystal` has zero rows.  With no anchor rows there are no PK groups to iterate over, so the entire group — `diffrn`, `cell`, `cell_measurement` — is silently dropped from the output.

**Fix:** After fetching `anchor_rows`, check for empty:

```python
if not anchor_rows:
    block_id_tables.extend(keyed_anchor_to_tables[anchor_name])
    continue
```

Tables whose root anchor is unpopulated are promoted to `block_id_tables`, which uses `_cifflow_block_id` as the grouping key.  They are then emitted in the remaining-blocks sweep, preserving all data.

**Rule:** A keyed anchor with no rows is indistinguishable from a keyless Set for emission purposes — fall back to `_cifflow_block_id` grouping.  Never silently discard tables because their anchor is empty.

## Lesson 55 — Emit round-trip tests: NULL vs '.' normalisation and the Set-PK stub conflict (2026-04-11)

**Context:** `tests/output/test_emit.py` — `_assert_same_data`, `TestDatabaseRoundTrip`, `TestEmitRoundTripIntegration`.

**Design:** Round-trip tests work by emitting a populated database, re-parsing the emitted CIF, re-ingesting into a fresh database, then comparing the two databases column-by-column.  `_cifflow_block_id` and `_cifflow_row_id` are excluded from comparison (they are administrative, not CIF data).  Synthetic columns (`is_synthetic=True`) are also excluded.

**Problem 1 — NULL → '.' transformation:** Loop emission cannot omit columns mid-row; it emits SQL NULL as the CIF placeholder `'.'`.  After re-ingestion `'.'` is stored as the string `'.'`, not NULL.  Naively comparing tuples then fails for every loop column that was absent in the original.

**Fix 1:** Normalise both sides before comparison: `None → None` and `'.' → None` (both mean "absent/not applicable" at the loop level).  `'?'` is kept distinct (it means "unknown", not "not applicable").

**Problem 2 — domain-key PK conflict:** Every structured SQLite table (Set and Loop alike) has `PRIMARY KEY (domain_key_cols)` — `_cifflow_block_id` is NOT included.  The `_cifflow_block_id` column appears only in a secondary `UNIQUE (_cifflow_block_id, _cifflow_row_id)` constraint.  This means only one row per domain-key value can exist across all blocks.  When the emitted CIF is re-ingested and a dependent block (e.g. a `preferred_orientation` block that FKs into `pd_instr`) is processed before the source block (e.g. `some_characters_instrument`), the ingester creates a stub row for `pd_instr.id = 'chrome_dome'`.  The subsequent insert of real data from the instrument block then fails silently on the PK constraint and the real values are lost.

**Root cause:** Block emission order is alphabetical, which can differ from the original ingestion order.  The ingest layer's merge logic keeps the first occurrence and ignores later arrivals for the same PK.  Stubs created by FK scaffolding therefore block real data.

**Disposition (original):** Three integration tests were marked `xfail(strict=True)`.  Two (`test_multi_one_original`, `test_multi_one_grouped`) were fixed in Lesson 58.  The actual root causes were in `_flush` (slim-row key union) and GROUPED remaining-blocks sweep (keyed-anchor tables excluded), not in the merge logic itself.  The xfail decorators have been removed.

**Rule:** Any time a new emit mode or block-ordering strategy is added, check whether its output can be faithfully re-ingested regardless of block order.  If not, the ingest merge logic needs to handle stub → real-data promotion.

## Lesson 54 — Emit round-trip tests: which modes can safely collapse multiple Set rows (2026-04-11)

**Context:** `tests/output/test_emit.py` — `TestDatabaseRoundTrip`.

**Problem:** A test (`test_multiblock_set_one_block`) tried to round-trip a CIF with two blocks each carrying a Set-category row (keyless `cell`) through ONE_BLOCK mode.  Every structured table has `PRIMARY KEY (domain_key_cols)` without `_cifflow_block_id`, so only one row per domain-key value can exist.  `cell`'s domain key is keyless (no explicit `_category_key`), making rows from different blocks indistinguishable — merging them into one block raises `IngestionError: merge conflict`.

**Rule:** ONE_BLOCK mode is only round-trip-safe when every row across all source blocks has a distinct domain key.  Categories where rows are only distinguished by `_cifflow_block_id` (keyless Sets) cannot be merged into a single block without conflict.  Do not write round-trip tests that attempt to collapse such rows into ONE_BLOCK.

## Lesson 53 — ONE_BLOCK: Set categories with multiple rows must render as loops; transitive bridge columns must not be emitted (2026-04-11)

**Context:** `_render_block` in `output/emit.py`; `generate_schema` in `dictionary/schema.py`.

**Problem 1 — data loss:** `_render_block` always called `_render_set_category(rows[0], ...)` for
Set-class categories, emitting only the first row as scalar tag-value pairs and silently dropping
all subsequent rows.  In ORIGINAL and GROUPED modes each block only ever contains one row per Set
category (one original CIF block → one Set row), so this went unnoticed.  In ONE_BLOCK mode every
Set category accumulates N rows (one per original block) and only row 0 was emitted.

**Fix 1:** Change the dispatch condition to `category_class == 'Set' and len(rows) == 1`.  When a
Set category has more than one row, fall back to `_render_loop_category`.

**Problem 2 — spurious columns:** Transitive bridge columns added by `generate_schema` to enable
composite FK joins (e.g. `geom_angle.structure_id`, populated from a bridge table at ingest time)
had `definition_id=''` and `is_synthetic=False`.  They had no real CIF tag, but `_active_cols`
passed them through (only `is_synthetic=True` columns are suppressed), causing `_col_tag` to
synthesise a fake tag name and emit the column.

**Fix 2:** Mark bridge columns `is_synthetic=True` at the point they are appended to the table in
`generate_schema`.  `_active_cols` then filters them automatically.

**Rule:** Any column that exists only for internal FK machinery and has no DDLm `definition_id`
must be `is_synthetic=True`.  Whenever a new infrastructure column is added to the schema, confirm
it carries the synthetic flag if it has no corresponding CIF tag.

## Lesson 52 — GROUPED mode: anchor groups that are FK-targets of other groups need block_id fallback (2026-04-11)

**Context:** `_collect_grouped` in `output/emit.py`.

**Problem:** Sets like `space_group` are "exclusive-target" anchors: they have no FK of their own
to any other keyed anchor, and are directly FK-referenced from exactly one other anchor group (e.g.
`structure` in the `pd_phase` group references `space_group`). With a naive independent anchor loop,
each exclusive-target anchor generates its own output blocks (one per domain PK value), duplicating
block names already produced by the referencing anchor group.

**Compounding issue:** The primary anchor row's `_cifflow_block_id` may differ from the FK-chained rows'
`_cifflow_block_id` (e.g. `pd_phase._cifflow_block_id = Selenium_0_some_chars` vs `structure._cifflow_block_id = Selenium_0`
vs `space_group._cifflow_block_id = Selenium_0`). A simple "skip if block_id already absorbed" check using
the primary block_id is correct here — using the extended covered_cifflow_block_ids for the skip check
would falsely skip legitimate anchor groups (e.g. `pd_phase` would be skipped because pd_instr's
FK chain covers its primary block_id).

**Fix (three-part):**
1. **Identify exclusive-target anchors** (referenced from exactly one other anchor group AND anchor
   table itself has no FK to any other keyed anchor) and move them to `block_id_tables`.
2. **Split absorbed tracking:** `absorbed_primary` (anchor-row block_ids, used for the skip check)
   vs `absorbed_all` (all swept block_ids including FK-extended, used to suppress remaining blocks).
3. **block_id_tables sweep uses extended covered_cifflow_block_ids**, so exclusive-target anchor rows (e.g.
   `space_group`) are picked up via the block_ids discovered through FK-chain joins (e.g. structure
   rows bring in `Selenium_0`, and `space_group._cifflow_block_id=Selenium_0` is then absorbed).

**Rule:** When an anchor Set is exclusively referenced from one other anchor group and has no FK
out, treat it as a `block_id_table`. Use separate "primary claimed" and "all swept" tracking to
prevent both false skips and duplicate emission.

## Lesson 51 — GROUPED mode: covered_cifflow_block_ids must be expanded from FK-chained rows, not just the anchor table (2026-04-11)

**Context:** `_collect_grouped` in `output/emit.py`.

**Problem:** When a Set category has a domain PK (e.g. `expt.id`), two input blocks with the same
key value conflict at ingestion — only the first block's anchor row survives. `covered_cifflow_block_ids`
was seeded only from anchor table rows, so the second block's `_cifflow_block_id` was never recorded.
Loop descendants from the second block (which stored their rows without conflict, since their own
PKs differ) were fetched correctly by the FK JOIN, but the no-anchor tables from that block were
not absorbed — they produced an orphan standalone block.

**Fix:** Seed `covered_cifflow_block_ids` from anchor rows as before, then extend it after each FK-chained
table fetch by scanning the returned rows' `_cifflow_block_id` values. Only after all FK-chained tables
are processed are no-anchor tables fetched (using the now-complete `covered_cifflow_block_ids`) and
`absorbed_cifflow_block_ids` updated. A second pass handles tables where the FK path was `None`
(block-id fallback), using the expanded `covered_cifflow_block_ids`.

**Rule:** In GROUPED mode, `covered_cifflow_block_ids` is the union of all `_cifflow_block_id` values present in
any row belonging to this anchor group — not just those in the anchor table itself.

## Lesson 50 — GROUPED mode: Set-anchor BFS must explore all FK targets, not just the first (2026-04-11)

**Context:** `_find_set_anchor` in `output/emit.py`.

**Problem:** The original implementation followed only the first FK target at each hop (depth-first,
single path). A table with composite keys may have multiple FKs: some to Loop tables (no Set
ancestor) and others directly to a Set. If the Loop FK appeared first in the `foreign_keys` list,
the Set was never found and the table fell through to `_cifflow_block_id` fallback grouping instead of
being anchored to the Set.

**Example:** A table like ATOM_SITE_ANISO with FK to ATOM_SITE (Loop) and FK to STRUCTURE (Set).
With depth-first traversal and ATOM_SITE first, `_find_set_anchor` would follow ATOM_SITE, find no
Set there, and return `None` — incorrectly treating ATOM_SITE_ANISO as a no-anchor table.

**Fix:** Replace the depth-first single-path walk with BFS over all FK targets at each level.  The
first Set-class table reached (closest by FK hop count) is returned as the anchor.

**Rule:** When searching for a Set ancestor through FK links, always use BFS across all FK
targets — never assume one path is sufficient.

## Lesson 49 — Check whether ' and " are legal mid-word in CIF 2.0 unquoted strings (2026-04-11) JediTerm/PyCharm has a column-tracking bug with ANSI codes and line wrapping (2026-04-11)

**Context:** `inspect/` package output in PyCharm Community with terminal emulation enabled.

**Symptom:** Any line that contains ANSI SGR escape codes and is long enough to wrap displays the
continuation text at a large column offset instead of column 1. Removing ANSI codes from the
specific wrapping line does not fix it — the bug corrupts the terminal's column counter for all
subsequent lines once any ANSI codes have been processed.

**Root cause:** JediTerm (PyCharm's terminal emulator) miscounts ANSI escape byte sequences as
visible characters when computing column positions for line wrapping. This is a known JediTerm bug,
not a problem in our output.

**Non-fix:** Splitting the coloured prefix and the long text into separate `print()` calls did not
help. Removing ANSI codes entirely from the output did not help either (the terminal state was
already corrupted by earlier lines). Widening the terminal panel past the longest line avoids the
wrap and therefore avoids the symptom.

**Rule:** Do not attempt to work around JediTerm line-wrap rendering bugs in library code. Accept
that output may look odd in narrow PyCharm terminals; document it as a known limitation.

## Lesson 48b — Semicolon-delimited text fields: content starts on the same line as the opening `;` (2026-04-11)

**Context:** `_make_semicolon` in `output/quote.py`.

**Mistake:** Initial implementation used `f'\n;\n{s}\n;'`, placing an extra blank line between the
opening delimiter and the content. The CIF specification requires the content to begin immediately
after the opening `;` on the same line — `\n;content\nhere\n;`. The extra `\n` would cause the
round-tripped value to gain a leading newline.

**Fix:** `f'\n;{s}\n;'`.

**Rule:** In a semicolon-delimited text field, the opening `;` and the first character of content
are on the same line. The closing `;` is on a line by itself (column 1).

## Lesson 49 — Check whether ' and " are legal mid-word in CIF 2.0 unquoted strings (2026-04-11)

**Context:** In `quote.py` Rule 2 (bare word), we defensively excluded values containing `'` or `"`
from being emitted unquoted, because our lexer re-enters SINGLE_QUOTED / DOUBLE_QUOTED state when
it encounters those characters mid-token.

**Suspicion:** CIF 2.0 may legally permit `'` and `"` as non-first characters in an unquoted
string (bare word). If so, the lexer is wrong, not the spec.

**Action required:** Check the CIF 2.0 EBNF (`references/CIF2-ENBF.txt`) for the definition of
an unquoted data value. If `'` and `"` are allowed mid-word, fix the lexer to not re-enter a
quoted-string state when already mid-token, and relax the Rule 2 guard in `quote.py` accordingly.

## Lesson 47 — Composite FK column fill requires transitive single-column FK lookup (2026-04-11)

**Context:** `_apply_fk` composite FK branch in `ingestion/ingest.py`.

**Mistake:** When filling a missing source column of a composite FK, the lookup searched `fk_accumulator` using `column_to_tag(target_table, target_col)`. This is correct when the target column is a natural PK given directly in the CIF data. But if the target column is itself a single-column FK (e.g. `pd_data.diffractogram_id → pd_diffractogram.id`), the fk_accumulator holds the value under the *ultimate* tag (`_pd_diffractogram.id`), not under the intermediate one (`_pd_data.diffractogram_id`). The one-level lookup found nothing, leaving the column NULL.

**Consequence:** The UUID fill pass then assigned a UUID to the NULL PK column. The composite FK stub section created parent stubs with that UUID. Later, when the real loop produced rows with the correct value (e.g. `'degaussa_raw_01'`), they got a different PK and were inserted as separate rows — doubling the row count.

**Fix:** After the direct `fk_accumulator` lookup fails, walk the single-column FK chain from the target column up to 15 levels. At each step, look up the current `(table, col)` in `column_to_tag` and try `fk_accumulator`. Stop as soon as a value is found. Emit a warning if the depth limit is reached (possible FK cycle).

**Rule:** Composite FK column fill must be transitively FK-aware. A column whose value originates two or more FK hops away is still resolvable — follow the chain rather than assuming one hop is sufficient.

## Lesson 46 — Loop-class scalar tags must be buffered per-block, not merged immediately (2026-04-11)

**Context:** `_process_scalar` in `ingestion/ingest.py`.

**Mistake:** Loop-class tags given as scalars (outside any `loop_`) were processed one at a time: each tag created a row containing only that column and was immediately passed to `_merge_into`. Because the PK column (`_pd_instr_detector.id = CD-detc`) and non-PK columns (`_pd_instr_detector.instr_id`, `_pd_instr.soller_ax_spec_detc`) were processed in separate calls, the non-PK rows had PK = `(None,)`. All `(None,)` rows from different blocks then merged together and produced false value conflicts (e.g. `keeping 'chrome_dome', ignoring 'copper_top'`).

**Why it matters:** A CIF block may give a single Loop-class entity entirely as scalars — this is a real pattern in multi-block powder diffraction files. The intent is one logical row; all column values from that block describe the same row.

**Fix:** Accumulate Loop-class scalar tags in a `loop_scalar_buffers` dict (parallel to `set_buffers`) and write to `fk_accumulator` immediately. Flush the complete, fully-populated row through `_apply_fk` + `_merge_into` at the end of the block, after all tags have been seen.

**Rule:** Whether a table's `category_class` is `Set` or `Loop`, scalar tags within a single block always describe one row. Merge only when the full row is assembled.

**Symptom to watch for:** False merge conflicts on non-PK columns where PK = `(None,)` for rows that should have distinct natural keys.

## Lesson 45 — UUID-per-row for keyless loops requires a post-_apply_fk fill pass (2026-04-11)

**Context:** `_process_loop` and `_apply_fk` in `ingestion/ingest.py`.

**Mistake:** UUID generation for missing PK columns was only wired inside `_apply_fk` Source 3, which fires for *single-column key-FKs* only. Two categories were silently skipped:
1. **Pure-key PKs** (no FK at all, e.g. `atom_site.label`) — `_apply_fk` never visits them.
2. **Composite-key-FK components** (e.g. `pd_meas.point_id` in the `(point_id, diffractogram_id)` composite PK) — the composite path explicitly excluded UUID generation.
Both produced `NULL` for the key column on every iteration, so all rows collapsed to one via `_merge_into`.

Additionally, for single-column key-FKs, the generated UUID was written to `fk_accumulator`, so iteration 1 found it via Source 2 and reused the same UUID — same result.

**Correct rule:**
- Source 3 (`_apply_fk`): only persist the UUID to `fk_accumulator` when `loop_row_by_defid is None` (scalar context). In loop context leave the accumulator untouched so each iteration regenerates.
- After all `_apply_fk` calls for the iteration, run a **UUID fill pass** over sibling rows: for each NULL non-synthetic PK column, generate one UUID per column name and apply it to every sibling table that shares that column name. This handles pure-key and composite-key cases uniformly.
- For composite FKs now fully specified after the fill, call `_apply_fk` on the created parent stub *before* `_merge_into`, so that grandparent stubs are also inserted first — preserving the topological order that SQLite deferred FK constraints require.

**How to apply:** Any time a loop may lack its key column, the fill pass in `_process_loop` handles it automatically. `_apply_fk` alone is not sufficient for composite or pure-key scenarios.

**Note on deferred FKs:** `DEFERRABLE INITIALLY DEFERRED` constraints are still enforced at the end of each `executemany` in autocommit mode. Parents must precede children in `merged_rows` insertion order, or the constraint fires before the parent row exists.

---

## Lesson 44 — SU values must be scaled, not stored raw (2026-04-11)

**Context:** `split_su` in `ingestion/ingest.py`.

**Mistake:** `split_su('3.992(4)')` returned `('3.992', '4')` — storing the raw parenthetical digits rather than the actual uncertainty. The `_su` column would hold `'4'` while an explicitly supplied `_cell.length_a_su 0.004` would hold `'0.004'`, making the two representations inconsistent.

**Correct rule:** The SU digit(s) represent units in the last decimal place of the measurand. Scale by `10^(exponent - decimal_places)`:
- `'3.992(4)'` → `('3.992', '0.004')` (4 × 10⁻³)
- `'1234(5)'`  → `('1234',  '5')`      (5 × 10⁰)
- `'12.34(56)'` → `('12.34', '0.56')` (56 × 10⁻²)
- `'1.23e-4(5)'` → `('1.23e-4', '0.000005')` (5 × 10⁻⁶)

**Fix:** Replaced the one-liner `split_su` with scaling logic that counts decimal places in the mantissa and the exponent separately. Updated `TestSplitSu` and `TestSUIngestion` to assert scaled values.

## Lesson 1 — Multiline text field closing delimiter (2026-04-04)

**Context:** Lexer `_read_multiline` implementation.

**Mistake:** After consuming the closing `\n;`, added `_skip_to_eol()` to discard remaining content on the closing line. This silently dropped valid tokens (e.g. `1.0` in `simple_loops.cif`'s `; 1.0`).

**Correct rule:** Per CIF 2.0 EBNF, `text-delim = line-term, ';'`. The closing delimiter is exactly two characters (`\n` + `;`). After consuming them, the lexer returns to NORMAL state immediately. Content after the closing `;` is tokenised normally — if it's a comment it's skipped by comment handling, if it's a value it becomes the next token.

**How to apply:** Never skip anything after the closing `;`. The line boundary is not special; only the two-character delimiter matters.

---

## Lesson 2 — Sequential loops are not nested loops (2026-04-04)

**Context:** Parser `_handle_keyword` for `loop_`.

**Mistake:** Added a `if self._in_loop: halt` guard for `loop_` keywords, treating any `loop_` encountered while a loop was active as a fatal "nested loop" error. This caused `simple_loops.cif` (with three sequential loops) to halt after the first loop.

**Correct rule:** Per CLAUDE.md: "on_loop_end emitted on: EOF, new tag, new loop, new save frame, new data block, STOP_". A `loop_` keyword always terminates the current loop via `_prepare_for_keyword` and starts a fresh one. A "nested loop" in the CIF sense is not representable in the flat token stream — there is no construct that creates a structurally nested loop.

**How to apply:** `loop_` should always call `_prepare_for_keyword` (which closes any active loop/containers/tag) then `_start_loop`. Never halt on `loop_` seen while `_in_loop` is True.

---

## Lesson 4 — `@property` preferred over `cached_property` during incremental construction (2026-04-05)

**Context:** `CifSaveFrame.loops` and `CifSaveFrame.tags` in `cifmodel/model.py`.

**Decision:** Used plain `@property` (recomputed on every access) rather than `cached_property`.

**Reason:** `_loops` and `_tag_order` are mutated during construction by `CifBuilder` (via `_add_loop`, `_append_value`). `cached_property` stores the result on first access and never recomputes — so every mutation would require explicit cache invalidation (`del self.loops`), adding noise to every internal mutation method.

**How to apply:** Use `cached_property` only on data that is immutable after construction, or where the cache lifetime can be clearly defined. For properties backed by lists that grow during construction, plain `@property` is simpler and correct. Switch to `cached_property` only if profiling identifies it as a hot path.

---

## Lesson 3 — `:` is not a bare-word terminator in CIF 2.0 (2026-04-04)

**Context:** Lexer `_read_bare_word` and `tokens()` for CIF 2.0.

**Mistake:** Added `:` as a terminator inside `_read_bare_word` for CIF 2.0. This split valid unquoted values like `2007-12-18T12:16:55+02:00` into multiple tokens, generating spurious "value has no preceding tag" errors.

**Correct rule:** Per CIF 2.0 EBNF, `restrict-char = non-blank-char - ('[' | ']' | '{' | '}')`. The colon is NOT excluded from `restrict-char`, so it is a legal character inside a `wsdelim-string`. The `:` table separator only appears at the start of a new token position (directly after a quoted key, with no preceding whitespace). It is emitted as a standalone token only by the outer `tokens()` loop (when `:` is the first character seen in NORMAL state), never by `_read_bare_word`.

**How to apply:** Do not break on `:` inside `_read_bare_word`. Only the `tokens()` loop emits `:` as a standalone VALUE token.

---

## Lesson 5 — `build()` convenience function is unspecified (2026-04-05)

**Context:** Stage 2 (`cifmodel/builder.py`).

**Decision:** Added `build(source, *, mode='pad') -> tuple[CifFile, list[ParseError]]` as a convenience wrapper around `CifBuilder` + `CifParser`.

**Status:** Not mentioned in CLAUDE.md or the parser prompt. Added as a practical utility. If the spec is later updated to prescribe a different top-level API shape, this function may need to change.

**How to apply:** Treat `build()` as a convenience shortcut, not a canonical API. Do not design downstream layers to depend on it exclusively.

---

## Lesson 6 — Empty loop handling is an extension of the spec (2026-04-05)

**Context:** `CifBuilder.on_loop_end()` in Stage 2.

**Decision:** A loop with zero values (tags declared, no values before loop end) is treated as a distinct semantic error with message "no values", separate from the row-count mismatch error.

**Status:** The prompt specifies row-count validation ("validate that the number of values received is divisible by the number of loop tags") but does not explicitly address the zero-values case. Our handling is a reasonable extension — zero is not divisible by any positive tag count — but it goes beyond what is written.

**How to apply:** If the spec is later clarified to prescribe different behaviour for empty loops (e.g. silent discard, or merging with row-count mismatch), revisit `on_loop_end()`.

---

## Lesson 7 — Strict mode extended beyond its specified scope (2026-04-05)

**Context:** `CifBuilder` mode parameter in Stage 2.

**Decision:** The prompt defines strict/pad mode only for loop row-count mismatch recovery. We applied the same strict mode behaviour (stop accumulating after first semantic error) to two additional cases: empty loops and duplicate block/save frame names.

**Status:** This extension is internally consistent and conservative (strict means strict), but it is not spec-backed for these cases. The duplicate name spec says only "emit `on_error`" with no mention of strict/pad distinction.

**How to apply:** If the spec is later updated to define strict/pad behaviour for these cases differently, the `_semantic_error` helper in `CifBuilder` will need case-specific handling rather than a single `_stopped` flag.

---

## Lesson 8 — Empty save frame names are not recoverable (2026-04-05)

**Context:** Parser `_handle_keyword` for `save_`.

**Decision:** Empty save frame names are not supported, unlike empty data block names (which are handled — error emitted, name stored as `""`).

**Reason:** `save_` is syntactically unambiguous as a frame-close token. There is no token form that could mean "open a save frame with an empty name" without conflicting with the close semantics. The only available heuristic — treating `save_` outside a frame as an opener — would silently misinterpret a common error (accidental `save_` outside a frame) as an empty-named frame open.

**Practical justification:** Save frames appear almost exclusively in DDLm dictionaries, which are well-formed. An empty save frame name in a real file would indicate severe malformation; treating it as a recoverable condition adds complexity for no practical benefit.

**How to apply:** Do not attempt to recover empty save frame names. `save_` outside a save frame remains a syntactic error and is ignored. This is a deliberate deviation from the general principle of allowing empty names with an error.

---

## Lesson 9 — Use a consistent docstring style to support autogeneration (2026-04-05)

**Context:** Project-wide docstrings reviewed ahead of potential documentation autogeneration.

**Problem:** Docstrings are currently inconsistent — a mix of one-liners, Sphinx-style `*name*`
emphasis, and NumPy-style `Parameters` blocks. Public API methods (`__getitem__`, `__contains__`,
`get_all`) have no parameter or return documentation. Private helpers sometimes have more
documentation than public methods.

**How to apply:** When writing or updating docstrings, follow a single style throughout.
NumPy style is preferred (used in `debug.py`):

```python
def method(self, name: str) -> list[CifBlock]:
    """Short one-line summary.

    Longer description if needed.

    Parameters
    ----------
    name:
        Description of the parameter.

    Returns
    -------
    list[CifBlock]
        Description of what is returned.

    Raises
    ------
    KeyError
        If the name is not found.
    """
```

Public methods must always document parameters, return values, and exceptions.
Private methods (`_name`) need only a one-liner. This keeps autogeneration viable
without adding noise to internal code.

---

## Lesson 10 — `:` at the start of a bare-word value (2026-04-06)

**Context:** Lexer `tokens()` — CIF 2.0 table key/value separator handling.

**Mistake:** The `:` standalone-token path fired unconditionally whenever `:` appeared
as the first character in NORMAL state.  This split valid values like `:100.0`
(CIF enumeration range lower-bound) into a standalone `:` token followed by `100.0`,
causing the `:` to be assigned as the tag value and `100.0` to become an orphan.

**Correct rule:** `:` is only a table separator when it is directly adjacent to the
preceding token (no whitespace between them).  When preceded by whitespace it is
the start of a bare-word value and must be read by `_read_bare_word`, which does
not break on `:` — so `:100.0` becomes a single token.

**Fix:** Added `_last_was_ws: bool = True` to the lexer.  Set `True` after consuming
whitespace/newlines/comments; `False` after emitting any token.  Standalone `:` is
only emitted when `not self._last_was_ws`.

**Side effect:** `{ "key" :value }` (whitespace before `:`, no space after) now
produces value `":value"` rather than `"value"`, with a "not followed by : separator"
error instead of "whitespace between key and `:` separator".  The key is still
recovered correctly.  This is an acceptable trade-off — the ambiguity is
unresolvable once `:value` is a single token.

**How to apply:** Never break on `:` inside `_read_bare_word`.  Standalone `:` tokens
are only valid when the lexer is in a non-whitespace context (adjacent to a prior token).

---

## Lesson 11 — SU validation does not belong in the lexer (2026-04-06)

**Context:** `_check_su` function in `lexer/lexer.py`.

**Mistake:** Added a heuristic to flag bare words that look like `number(su)` but fail
the `\(\d+\)$` pattern as lexical errors.  This caused false positives on fax numbers
with area codes in parentheses (e.g. `12(34)9477334` in `cif_core.dic`) and any other
string that happens to start with a numeric pattern followed by `(`.

**Correct rule:** The CIF lexer has no concept of "numeric value with SU" distinct
from any other bare word.  Both are `ValueType.STRING` tokens.  Whether the SU
sub-expression is well-formed is a semantic question, not a lexical one.

**Fix:** Removed `_check_su`, `_NUMERIC_PREFIX_RE`, and `_VALID_SU_RE` entirely.

**How to apply:** Do not validate numeric sub-structure in the lexer.  SU format
validation belongs in the dictionary/ingestion layer where the expected type is known.

---

## Lesson 12 — Never infer category from tag name; always use `_name.category_id` (2026-04-06)

**Context:** Stage 3 import processing and all future dictionary/ingestion layers.

**Rule:** A tag's category is always the value of `_name.category_id` in its save
frame definition.  The dot-notation convention (`_category.object`) is not reliable —
`_name.category_id` can differ from the prefix of `_definition.id` (see the
`pd_instr` / `pd_meas` example in the Stage 3 prompt).

**Never** split a tag name on `.` or any other character to infer the category.
Always look up the tag's save frame and read `_name.category_id` directly.

**How to apply:** Wherever a tag's category or table name is needed — Loop category
detection, schema generation, FK resolution, ingestion routing — obtain it via
`DdlmItem.category_id` or by reading `_name.category_id` from the relevant save
frame.  String manipulation of tag names is never a substitute.

## Lesson 13 — Scope one debug_{thing} function per stage (2026-04-06)

**Context:** Stage 3 complete; considering debug utilities for new layers.

**Rule:** Each major pipeline stage that produces a non-trivial in-memory structure
should have exactly one `debug_{thing}` function scoped to its primary output:

| Stage | Primary output | Debug function |
|-------|---------------|----------------|
| Lexer | token stream | `debug_lex` |
| Parser + IR | `CifFile` | `debug_build` |
| Schema generator | `SchemaSpec` | `debug_schema` |
| Ingestion | SQLite rows | `debug_db` (future) |

The function should visualise whatever a developer needs to inspect when
something goes wrong at that stage — not a raw dataclass dump.

**What to skip:** A debug function for an intermediate structure
(`DdlmDictionary`, `TableDef`) is rarely worth the maintenance cost unless it
repeatedly comes up in practice.  A REPL with `resolve_tag` or a targeted
`print` is usually enough.  Add `debug_{thing}` only when the structure is
large, nested, or opaque enough that ad-hoc inspection is consistently painful.

**How to apply:** When starting a new stage, ask: what is the primary artifact
a developer inspects when this stage misbehaves?  Write one debug function for
that artifact.  Keep it in `debug.py` alongside existing helpers.

## Lesson 14 — Template files use save frame label as identifier, not `_definition.id` (2026-04-06)

**Context:** `_import.get` frame lookup in `DictionaryLoader._find_frame_by_definition_id`.

**Mistake:** Spec says to locate imported frames by `_definition.id` match. Implemented
exactly that. But template files (`templ_attr.cif`, `templ_enum.cif`) carry zero
`_definition.id` entries — their save frame label is their sole identifier. The import
looked up by `_definition.id`, found nothing, treated it as a miss, and aborted,
leaving `_type.contents` / `_type.purpose` unpopulated for hundreds of items.

**Correct rule:** Match by `_definition.id` when present (full dictionary frames);
fall back to save frame label when absent (template files). The `elif` is deliberate:
a frame that declares `_definition.id` is matched exclusively by that value, not
its label.

**How to apply:** Any future import resolution code must include this two-step
lookup. Never assume template files conform to the `_definition.id` convention.

## Lesson 15 — Category `_name.category_id` is the parent, not the table name (2026-04-06)

**Context:** `generate_schema` table naming and domain-item lookup.

**Mistake:** Used `cat_item.category_id` (= `_name.category_id` of the category frame)
as the SQL table name and as the filter for domain items.  In DDLm, a category
frame's `_name.category_id` is its **parent** category in the hierarchy — for
`ATOM_TYPE`, that is `ATOM`.  This produced a table named `atom` instead of
`atom_type`, with the wrong class and wrong PK.

**Correct rule:**
- Table name = `_table_name(cat_item.definition_id)` — the category's own
  canonical identifier.
- Domain items = items whose `item.category_id == cat_item.definition_id` — because
  items carry `_name.category_id` pointing to the category's `_definition.id`,
  not to the parent.
- `cat_item.category_id` is only relevant for understanding the category
  hierarchy; it plays no role in schema generation.

**How to apply:** Whenever iterating over categories to build tables, always key
on `definition_id`, never on `category_id`.

## Lesson 16 — Import identity tags must never be merged from a source frame (2026-04-06)

**Context:** `DictionaryLoader._merge_frame` — `_import.get` mode `"Contents"`.

**Mistake:** Initial merge logic treated `_definition.id`, `_definition.class`,
`_definition.scope`, and `_name.*` as ordinary tags subject to the `dupl` policy.
With `dupl=Exit` (default) these caused an abort whenever source and target shared
them.  With `dupl=Replace` they overwrote the target frame's own identity, so the
extracted `DdlmItem` carried the template's `definition_id` instead of the target's.

**Correct rule:** The set `_IMPORT_IDENTITY_TAGS` (`_definition.id`,
`_definition.scope`, `_definition.class`, `_name.category_id`, `_name.object_id`,
`_name.linked_item_id`, `_import.get`) defines the frame's own identity and must
always be skipped during merging — regardless of the `dupl` policy.  Only
attribute tags (`_type.*`, `_units.code`, `_description.text`, etc.) are merged.

**How to apply:** Any future import or merge operation must exclude identity tags
before applying conflict resolution.

## Lesson 17 — SQL identifiers must be double-quoted to handle reserved keywords (2026-04-06)

**Context:** `emit_create_statements` and `apply_schema`.

**Mistake:** Used bare table and column names in generated DDL.  `ddl.dic` contains
a category whose `definition_id` normalises to `update` — a reserved SQL keyword —
which caused a `sqlite3.OperationalError` when applying the schema.

**Correct rule:** Always wrap every SQL identifier (table name, column name, FK
reference) in double quotes in generated DDL: `"identifier"`.  Embedded double
quotes are escaped by doubling: `"it""s"`.  This is standard SQL and SQLite accepts
it unconditionally.

**How to apply:** Use a `_qi(name)` helper wherever an identifier appears in a
generated SQL string.  Never interpolate bare names directly into DDL.

## Lesson 18 — Python sqlite3 auto-commits DDL; use explicit BEGIN for transactional DDL (2026-04-06)

**Context:** `apply_schema` rollback-on-failure requirement.

**Mistake:** Used `with conn:` context manager expecting it to roll back a failed
`CREATE TABLE`.  Python's `sqlite3` module implicitly commits any pending
transaction before executing a DDL statement, so `CREATE TABLE` escapes the
context manager's rollback scope.

**Correct rule:** For transactional DDL in Python's `sqlite3`, set
`conn.isolation_level = None` (autocommit mode), issue `BEGIN` manually, execute
all DDL, then `COMMIT` or `ROLLBACK`.  Restore `isolation_level` in a `finally`
block.  This guarantees that all DDL within the block is atomic.

**How to apply:** Any function that executes DDL and must guarantee rollback on
failure should follow this pattern.  Do not rely on `with conn:` for DDL.

## Lesson 19 — CIF presence-state encoding in SQLite (2026-04-07)

**Context:** Structured table schema design; replaced status-column approach.

**Rule:** All value columns store TEXT. CIF presence states are encoded directly
in the value column using the following convention:

| Stored value | CIF meaning |
|---|---|
| `NULL` | tag absent from this data block |
| `'.'` | inapplicable (unquoted `.` — `ValueType.PLACEHOLDER`) |
| `'?'` | unknown (unquoted `?` — `ValueType.PLACEHOLDER`) |
| `'"."'` | literal `.` stored with delimiters — source `ValueType` was any of `DOUBLE_QUOTED`, `SINGLE_QUOTED`, `TRIPLE_DOUBLE_QUOTED`, `TRIPLE_SINGLE_QUOTED`, `MULTILINE_STRING` |
| `'"?"'` | literal `?` stored with delimiters — same set of source `ValueType`s |
| anything else | real value, stored as raw string |

**Why:** Status companion columns (`{col}_status`) doubled the column count and
added complexity to schema generation, ingestion, and queries. This encoding
preserves all CIF semantics in a single column. NULL means exactly one thing
(absent), which matches natural SQL semantics. `.` and `?` are the CIF
representations that any CIF user immediately recognises.

**`_cif_fallback` retains `value_type`:** The fallback table keeps its
`value_type` column because there is no schema type information to distinguish
bare-word values from quoted ones. `value_type` enables numeric coercion to
operate only on bare words, and the output layer to know which values to quote
on round-trip.

**How to apply:**
- At ingestion: inspect `ValueType`. `PLACEHOLDER` → store `'.'` or `'?'`.
  Any non-PLACEHOLDER ValueType whose raw string value is `.` or `?`
  (`DOUBLE_QUOTED`, `SINGLE_QUOTED`, `TRIPLE_DOUBLE_QUOTED`, `TRIPLE_SINGLE_QUOTED`,
  `MULTILINE_STRING`) → store `'"."'` or `'"?"'`.
  All other values → store raw string.
  Tag absent → do not insert row / leave column NULL.
- At query time: `WHERE col IS NOT NULL AND col NOT IN ('.', '?')` selects rows
  with real values.
- At output: `NULL` → omit tag. `'.'` → emit `.`. `'?'` → emit `?`.
  `'"."'` → emit `"."`. `'"?"'` → emit `"?"`. All other values → use `value_type`
  from `_cif_fallback` (or schema type) to decide quoting.

---

## Lesson 20 — `_cifflow_row_id` uniqueness requires a composite constraint (2026-04-08)

**Context:** `emit_create_statements` in `schema.py`; Stage 4 schema design.

**Mistake:** Emitted `_cifflow_row_id ... UNIQUE` as an inline column constraint.
At the time this was written, `_cifflow_row_id` was assumed to reset to 1 at the start
of each block, so a multi-block CIF would produce duplicate `_cifflow_row_id` values in
the same table. `UNIQUE` on `_cifflow_row_id` alone would fire on the second block's
first row.

**Later clarification (Stage 4):** `_cifflow_row_id` is in fact global — it never
resets between blocks. A composite `UNIQUE (_cifflow_block_id, _cifflow_row_id)` constraint
is therefore stronger than strictly necessary, but it remains correct and is
the prescribed form regardless.

**Correct rule:** For tables where `(_cifflow_block_id, _cifflow_row_id)` is not already the
`PRIMARY KEY` (i.e. keyed Loop tables and all Set tables), emit a table-level
`UNIQUE ("_cifflow_block_id", "_cifflow_row_id")` constraint. For keyless Loop tables,
`(_cifflow_block_id, _cifflow_row_id)` is already the PK so no extra constraint is needed.

**How to apply:** Never use `_cifflow_row_id UNIQUE`. Always use the composite form.

---

## Lesson 21 — Mixed loop cross-tier join requires shared `_cifflow_row_id` per iteration (2026-04-08)

**Context:** `_cif_fallback` table design; Stage 4 ingestion.

**Problem:** A loop whose tags split between a structured table and `_cif_fallback`
produces rows in both locations. If `_cifflow_row_id` increments per cell in `_cif_fallback`,
there is no join key linking a fallback cell to the structured row from the same
loop iteration.

**Correct rule:** `_cifflow_row_id` is scoped per table globally across the entire
`ingest()` call — it never resets between blocks. For a mixed loop, all
`_cif_fallback` cells from a given iteration share the same `_cifflow_row_id` as the
corresponding structured table row — both draw from the structured table's counter.
The join key is `(_cifflow_block_id, _cifflow_row_id)` within that table + `_cif_fallback`.

For pure-fallback loops, `_cif_fallback` uses its own global counter,
incrementing once per iteration (not per cell).

**Consequence:** `_cif_fallback` PK is `(_cifflow_block_id, _cifflow_row_id, tag)` — `tag` is
needed because multiple cells (different tags) share `(_cifflow_block_id, _cifflow_row_id)` within
the same loop iteration.

**How to apply:** Maintain `_cifflow_row_id_counters: dict[str, int]` (table name →
counter). For mixed loops, draw from the structured table's counter for both the
structured row and all fallback INSERTs for that iteration. For pure-fallback
loops, draw from `_cif_fallback`'s counter. `_cifflow_row_id_counters` is initialised
once per `ingest()` call and never resets between blocks.

---

## Lesson 22 — Set category `_cifflow_row_id` must be reserved at first tag encounter (2026-04-08)

**Context:** Stage 4 ingestion; scalar Set category accumulation strategy.

**Problem:** Scalar Set tags are accumulated during block traversal and INSERTed
at end of block. If `_cifflow_row_id` is assigned at INSERT time, Set rows always get
higher `_cifflow_row_id` values than Loop rows in the same block, regardless of their
position in the file. This breaks document order and the "scalar Set and
single-row loop are equivalent" guarantee.

**Correct rule:** When the **first scalar tag** of a Set category is encountered,
immediately reserve the current `_cifflow_row_id_counter` for that category's pending row
and increment the counter. INSERT at end of block using the reserved value. This
places the Set row in document order relative to any Loop rows.

**How to apply:** Maintain `set_row_reservations: dict[str, int]` (table_name →
reserved `_cifflow_row_id`) populated on first-tag-seen, drawing from that table's entry
in `_cifflow_row_id_counters`. Use the reserved values when performing the end-of-block
INSERTs.

---

## Lesson 23 — Set categories can appear in loops; schema must accommodate both forms (2026-04-08)

**Context:** Stage 4 ingestion; Set table handling.

**Rule:** A DDLm Set category is *normally* represented by scalar tags (one logical
row per block), but the CIF format allows any category's tags to appear in a loop_
if the PK column is included. Both of these are valid and equivalent:

```
# scalar form
_cell.length_a 12
_cell.length_b 13

# looped form (single iteration)
loop_
_cell.length_a _cell.length_b
12 13
```

The ingestion layer must handle both. When a Set category appears in a loop, each
iteration produces a separate row with its own `_cifflow_row_id`. The scalar accumulation
strategy (accumulate then INSERT at end of block) only applies to tags that arrive
outside a loop.

**How to apply:** Detect Set categories appearing inside a loop at ingestion time
and treat them as Loop-style rows (assign `_cifflow_row_id` per iteration, pass through
merge algorithm). Do not defer these to end-of-block accumulation.

---

## Lesson 24 — A single logical entity may be spread across multiple CIF blocks (2026-04-08)

**Context:** Stage 4 ingestion; multi-block CIF files.

**Rule:** CIF allows a single dataset to be spread across multiple data blocks.
Tags from the same category with the same PK value across different blocks
describe the same logical row. The ingestion layer always merges such rows.

**Merge rules:**
- Rows with the same PK value (across any blocks) are merged into one row.
- First-seen block provides `_cifflow_block_id` and `_cifflow_row_id` for the merged row.
- First non-NULL value for each column wins; conflicts (two different non-NULL
  values for the same column) emit a semantic error and keep the first value.
- `_cif_fallback` rows are not merged; they remain block-local.

**`_cifflow_row_id` implication:** `_cifflow_row_id_counters` must not reset between blocks.
`_cifflow_row_id` is effectively per-table globally across the whole `ingest()` call.
The counter increments once per new unique PK seen (across all blocks).

**Implementation:** Accumulate all structured rows in a `merged_rows` dict
(table → PK tuple → column dict) throughout the entire `ingest()` call. Perform
all SQL INSERTs after all blocks have been processed.

---

## Lesson 25 — `_audit_dataset.id` introduces a namespace; absence says nothing (2026-04-07)

**Context:** Stage 4 ingestion; multi-block CIF files with dataset IDs.

**Rule:** The presence of `_audit_dataset.id` in a block asserts that the block
belongs to a named dataset. The *absence* of `_audit_dataset.id` says nothing — it
does not mean the block is unrelated to other blocks.

**Two block classes:**
- **Dataset blocks** — carry one or more `_audit_dataset.id` values. Their PKs are
  unambiguous within the dataset because the dataset ID provides the namespace.
- **General blocks** — carry no `_audit_dataset.id`. May use UUIDs for uniqueness
  (high confidence) or short identifiers (assumed coherence, warn the user).

**`_audit_dataset.id` is a loop category** — a block may carry multiple dataset ID
values via a `loop_`. The set of values for each block is read from the IR before
any rows are written.

**Pre-ingestion check (fatal):** Before any database writes, `ingest()` computes
the intersection of dataset ID sets across all dataset blocks. If the intersection
is empty and at least one dataset block exists, a `ValueError` is raised and
nothing is written. General blocks (no `_audit_dataset.id`) are always included.

**`dataset_id` parameter:** Bypasses the intersection check. Only blocks whose
dataset ID set contains `dataset_id` are ingested (plus all general blocks).
Allows extracting one coherent dataset from a multi-dataset CIF file.

**Merge algorithm is unconditional.** The pre-ingestion check guarantees coherence;
there are no blocked merges. Same PK → always merge.

**`id_regime`** — recorded per ingested block in `_block_dataset_membership`:
- `'dataset'` — block carries `_audit_dataset.id`
- `'uuid'` — no dataset ID; all PK values pass UUID format check
- `'assumed'` — no dataset ID; PK values are not all UUIDs, **or** no structured-table rows exist (cannot determine UUID usage)

**Post-ingestion validation checks** (written to `_validation_result`):
- `uuid_regime` (Warning) — general block with non-UUID structured-table PKs.
- `uuid_reference_check` (Info) — general-block UUID PK not referenced by any
  dataset block as a FK value.

**Both tables** (`_block_dataset_membership`, `_validation_result`) are created by
`apply_fallback_schema()`, not `apply_schema()`.

---

## Lesson 26 — Single-iteration loops feed `fk_accumulator` (2026-04-09)

**Context:** Stage 4 ingestion; FK propagation source 2.

**Rule:** After any loop completes, if it produced exactly one iteration, write
every column value from that iteration into `fk_accumulator`. This makes the
values available for FK propagation in subsequent loops within the same block,
equivalent to a scalar. Multi-iteration loops do not feed `fk_accumulator`.

**Why:** A single-iteration loop is semantically equivalent to a set of scalar
tags. Parent-category IDs occasionally appear in a one-row loop rather than as
bare scalars; without this rule their values would be invisible to FK propagation
in later loops, forcing unnecessary UUID fallbacks.

**How to apply:** After processing each loop, check the iteration count. If
exactly 1, iterate over all column values produced (across all tables for
multi-category loops) and write them into `fk_accumulator` keyed by
`definition_id`. Do not write partial iterations — only after the loop is
confirmed to have had exactly one iteration.

## Lesson 27 — `ColumnDef.type_contents` is informational only; DDL always emits TEXT (2026-04-09)

**Context:** Stage 4 design review; `ColumnDef` field rename.

**Mistake:** `ColumnDef` originally had `sql_type: str` storing SQL type strings
(`"TEXT"`, `"INTEGER"`, `"REAL"`) for use in generated DDL. This conflicted with
the Lesson 19 decision that all value columns store TEXT for round-trip fidelity.

**Correct rule:** `ColumnDef.type_contents` stores the DDLm `_type.contents` value
(e.g. `"Text"`, `"Integer"`, `"Real"`, `"List"`) for future validation and
type-coercion use. It does not affect DDL generation. `emit_create_statements`
always emits `TEXT` for all value columns regardless of `type_contents`.

**How to apply:** Never use `type_contents` to determine the SQL column type in DDL.
Use it only in validation logic and in `convert_database` to guide coercion.

---

## Lesson 28 — `fk_accumulator` stores encoded database values, not raw `CifScalar` (2026-04-09)

**Context:** Stage 4 ingestion; FK propagation implementation.

**Rule:** Values written to `fk_accumulator` must be pre-encoded via `encode_value`
— i.e., in the exact form they will appear in the database column. FK propagation
then copies the value directly into the target column without re-encoding.

**Why:** Encoding at write-time (into the accumulator) rather than at read-time
(when propagating) keeps the propagation path simple: a straight dict lookup and
column assignment, with no type inspection at use time.

**How to apply:** Whenever a value is written to `fk_accumulator` — whether from
a scalar Set tag, a single-iteration loop, or a UUID fallback — always call
`encode_value` first. The accumulator type is `dict[str, str]`.

---

## Lesson 29 — Value encoding for quoted `.` and `?` covers all non-PLACEHOLDER ValueTypes (2026-04-09)

**Context:** Stage 4 value encoding table; extension of Lesson 19.

**Mistake:** The initial encoding table only listed `DOUBLE_QUOTED` for the
`'"."'` / `'"?"'` cases. `SINGLE_QUOTED`, `TRIPLE_DOUBLE_QUOTED`,
`TRIPLE_SINGLE_QUOTED`, and `MULTILINE_STRING` values whose content is `.` or
`?` were not covered, causing them to fall through to "raw string" and be stored
as `'.'` or `'?'` — indistinguishable from bare PLACEHOLDER values.

**Correct rule:** Any value whose raw content is `.` or `?` AND whose `ValueType`
is not `PLACEHOLDER` must be stored as `'"."'` or `'"?"'` respectively. This
applies to all five non-PLACEHOLDER ValueTypes:
`DOUBLE_QUOTED`, `SINGLE_QUOTED`, `TRIPLE_DOUBLE_QUOTED`, `TRIPLE_SINGLE_QUOTED`,
`MULTILINE_STRING`.

The detection logic at ingestion: `if raw in ('.', '?') and value_type != PLACEHOLDER`.

**How to apply:** The encoding table in Stage 4 prompt §Value encoding is the
authoritative reference. The `encode_value` function must check `value_type !=
PLACEHOLDER` (not `value_type == DOUBLE_QUOTED`) to catch all cases.

---

## Lesson 30 — Container `value_type` exists only in `_cif_fallback`; use `json_valid()` for structured tables (2026-04-09)

**Context:** Stage 4 container value handling; querying encoded container values.

**Rule:** The `value_type` column (`'list'` or `'table'`) exists only in
`_cif_fallback`. Structured tables have no `value_type` column. To detect a
container value in a structured table column at query time, use SQLite's
`json_valid(column)` function.

**Why:** Structured table columns are defined by the dictionary schema with no
metadata column. Adding a companion `{col}_type` column was rejected as it doubles
column count and was the same problem as the discarded status-column approach.

**How to apply:**
- In `_cif_fallback` queries: `WHERE value_type IN ('list', 'table')` — precise.
- In structured table queries: `WHERE json_valid(column)` — safe guard before
  calling any other JSON function. Never call `json_extract`, `json_each`, or
  `json_type` on a column without this guard (they raise on non-JSON input).

---

## Lesson 31 — `SchemaSpec` embeds alias resolution and deprecation; `ingest()` needs no dictionary reference (2026-04-10)

**Context:** Stage 4 design review; `ingest()` `dictionary` parameter.

**Mistake/Gap:** `ingest()` had a `dictionary: DdlmDictionary | None = None` parameter
used solely for alias resolution via `resolve_tag`. This forced the caller to pass
both `schema` (derived from the dictionary) and `dictionary` as separate arguments —
redundant and error-prone. It also meant `ingest()` retained an unnecessary dependency
on `cifflow.dictionary.ddlm_parser`.

**Correct rule:** `SchemaSpec` is self-contained for routing:
- `alias_to_definition_id: dict[str, str]` — copied from `DdlmDictionary.alias_to_definition_id` by `generate_schema`; used in the tag routing loop to canonicalise aliases.
- `deprecated_ids: set[str]` — copied from `DdlmDictionary.deprecated_ids`; used to emit a non-fatal semantic warning when a deprecated tag name is encountered in a CIF file.

`ingest()` has no `dictionary` parameter. The `SchemaSpec` carries everything needed.

Deprecation warnings are non-fatal: ingestion proceeds normally, the warning is
appended to the return list, and (if provided) `on_error` is called.

**Why:** `SchemaSpec` is already the single authoritative artefact the caller
passes to `ingest()`. Embedding routing metadata there eliminates an implicit
dependency and makes the ingestion function's contract explicit.

**How to apply:**
- `generate_schema(dictionary)` must populate `alias_to_definition_id` and `deprecated_ids` from the `DdlmDictionary`.
- Tag routing (step 2): `canonical = schema.alias_to_definition_id.get(tag, tag)`.
- Deprecation check (step 3): `if canonical in schema.deprecated_ids and tag not in deprecated_warned: emit warning; deprecated_warned.add(tag)`. Use two message forms: alias case `"tag '{tag}' is deprecated (canonical: '{canonical}')"`, direct case `"tag '{tag}' is deprecated"`.
- `deprecated_warned` is a `set[str]` in per-block state; reset at the start of each block.
- Never pass `dictionary` to `ingest()` — it does not accept one.

---

## Lesson 32 — pytest must be run from the `.venv` (2026-04-10)

**Context:** Project uses a local virtual environment at `.venv/`.

**Rule:** Always run pytest as `.venv/Scripts/pytest` (Windows) — not a globally installed
`pytest`. The global interpreter will not have the project's dependencies.

**How to apply:** `.venv/Scripts/pytest -m "not slow" --tb=short -q` for the fast suite;
`.venv/Scripts/pytest -m slow` for integration tests.

---

## Lesson 33 — All public types returned by public functions must be top-level re-exports (2026-04-10)

**Context:** `CifScalar` was missing from `cifflow/__init__.py`.

**Gap:** `CifScalar` was exported from `cifflow.cifmodel` but not re-exported at the
top level. Any caller receiving a `CifScalar` from `block["_tag"]` could not write
type annotations, `isinstance` checks, or access `value_type` without importing from
the internal submodule path `cifflow.cifmodel.scalar`.

**Correct rule:** Any type that appears in the return value of a public function, or
that a caller must inspect to use the API correctly, must be re-exported from the
top-level `cifflow/__init__.py` and listed in the API Reference module layout.

**How to apply:** When adding a new public type at any stage, immediately add it to
`cifflow/__init__.py` (import + `__all__`) and to the module layout comment in
`prompts/API Reference.md`. Do not leave public types stranded in submodule paths.

---

## Lesson 34 — `_post_validate` must run before `_flush`; validation rows are inserted in `_flush` (2026-04-10)

**Context:** `ingest.py` run order; `_validation_result` rows.

**Bug:** `_post_validate()` was called after `_flush()`. Since `_flush()` inserts
`self.validation_rows` into `_validation_result`, any rows appended by `_post_validate`
were never written to the database.

**Correct order:** `_post_validate()` → `_flush()` → `COMMIT`. Post-validation populates
`self.validation_rows`; the flush writes them.

**How to apply:** In any `run()` method that separates a validate step from a flush step,
validate first, then flush. If post-validation needs to write to the database, it must run
before the flush that writes its output table.

---

## Lesson 35 — `_apply_fk` must create stub parent rows for all FK values, not just UUID-generated ones (2026-04-10)

**Context:** `ingest.py` FK constraint satisfaction; `one_structure.cif` + `cif_core.dic` integration test.

**Problem (original):** When `_apply_fk` generated a UUID for a missing key-FK column, it
populated the child row but never created the corresponding parent row. SQLite's
`DEFERRABLE INITIALLY DEFERRED` FK constraint then fired at COMMIT with
`IntegrityError: FOREIGN KEY constraint failed`.

**Problem (broader):** The same constraint violation occurs for non-key FK columns that carry
an explicit value from CIF data (e.g. `atom_site.type_symbol = 'Se'` referencing `atom_type.symbol`)
when the parent table has no row for that value. The original fix only covered the UUID-generation
path; non-key FK columns with real data values were never checked.

**Fix:** In `_apply_fk`, after the value-assignment block, add an unconditional stub-creation
step: for any FK column that ends up with a non-NULL value (explicit, propagated, or UUID-generated),
call `_merge_into` on the parent table with a stub row containing only `_cifflow_block_id` and the
FK target column set to that value. `_merge_into` is idempotent — if the parent row already
exists from real data, the stub is merged without overwriting any non-NULL values.

**How to apply:** Always pass `block_id`, `merged_rows`, and `row_id_counters` to `_apply_fk`
during schema-aware ingestion. These default to `None` (stub creation skipped) so unit tests
that call `_apply_fk` directly without a DB connection are unaffected.

---

## Lesson 36 — `_name.linked_item_id` must not be an import-identity tag (2026-04-10)

**Context:** `DictionaryLoader._resolve_imports` / `_merge_frame` in `loader.py`.

**Bug:** `_name.linked_item_id` was listed in `_IMPORT_IDENTITY_TAGS`, causing it to be
unconditionally skipped whenever a save frame merged attributes from a template via
`_import.get` (mode="Contents"). This is correct for true identity tags (`_definition.id`,
`_name.category_id`, `_name.object_id`) — you never want an import to change the frame's
own identity — but wrong for `_name.linked_item_id`, which is a *data attribute* that
templates are specifically designed to provide.

**Observed symptom:** `_geom_angle.atom_site_label_1` and `_geom_angle.atom_site_label_3`
(and similar FK-via-template items) had `type_purpose='Link'` but `linked_item_id=None`.
`generate_schema` skips items with `linked_item_id is None` during FK detection, so no FK
constraint was generated and no FK column was recognised in the schema.

**Root cause:** Both items import `[{'file':templ_attr.cif 'save':atom_site_id}]`, and the
`atom_site_id` template frame provides `_name.linked_item_id = '_atom_site.label'`. Because
`_name.linked_item_id` was in `_IMPORT_IDENTITY_TAGS`, `_merge_frame` skipped it regardless
of whether the importing frame had its own value.

**Fix:** Remove `_name.linked_item_id` from `_IMPORT_IDENTITY_TAGS`. The `dupl` policy in
`_merge_frame` already handles conflicts: if the importing frame already defines
`_name.linked_item_id`, the default `dupl='Exit'` would warn rather than silently overwrite.

**How to apply:** `_IMPORT_IDENTITY_TAGS` should only contain tags that define a frame's CIF
structural identity (definition id, scope, class, category, object). Tags that are data
attributes of the definition — even when they affect its semantic role (linked item, type
purpose, type contents) — must not be blocked from template inheritance.

---

## Lesson 37 — CIF 2.0 structural delimiters must not split tags or save frame names (2026-04-10)

**Context:** `Lexer._read_bare_word` in `lexer/lexer.py`.

**Bug:** `_read_bare_word` unconditionally broke on `[`, `]`, `{`, `}` for ALL bare words in
CIF 2.0 mode. This split tokens like `_axis.vector[1]` (tag) into `_axis.vector` + `[` + `1`
+ `]`, and `save_axis.vector[1]` (save frame name) into `save_axis.vector` + `[` + `1` + `]`.

**CIF 2.0 EBNF rule:**
- `restrict-char = non-blank-char - ( '[' | ']' | '{' | '}' )` — used by `wsdelim-string`
  (plain unquoted values). `[` terminates a plain value.
- `data-name = '_', non-blank-char, { non-blank-char }` — tags use `non-blank-char`, which
  includes `[`, `]`, `{`, `}`.
- `container-code = non-blank-char, { non-blank-char }` — save/data frame names also use
  `non-blank-char`.

So `[` terminates plain values but must NOT terminate tags or prefix keywords.

**Fix:** In the `_CIF2_DELIMITERS` break check, only break when the accumulator is empty
(delimiter starts its own standalone token) OR the accumulated word is a plain value — i.e.
it does NOT start with `_` (tag) and does NOT start with a prefix keyword (`save_`, `data_`).

**How to apply:** Whenever the CIF 2.0 EBNF distinguishes between `restrict-char` and
`non-blank-char` contexts, lexer logic must check what kind of token is being accumulated
before applying delimiter break rules.

## Lesson 38 — FK target must be the sole PK, not just any PK column (2026-04-10)

**Context:** `generate_schema` building `ForeignKeyDef` entries; `cif_pow.dic` ingestion.

**Mistake:** Initial fix checked `target_column not in primary_keys` to detect invalid FK targets.
This correctly caught columns that aren't PKs at all, but missed the case where the target column
IS listed in `primary_keys` but the PK is composite (e.g. `['id', 'variant']`). SQLite only creates
a UNIQUE index for a single-column PRIMARY KEY — a composite PK does NOT uniquely index any
individual column. So `FOREIGN KEY (x) REFERENCES t(id)` is also "foreign key mismatch" when
`t` has `PRIMARY KEY (id, variant)`.

**Correct check:** `tables[tgt_tbl].primary_keys != [target_item.object_id]` — the FK target
column must be the sole (and only) PK of the target table.

**How to apply:** Any time a FK constraint is being generated and the target table has a composite
PK, the FK is invalid unless it references ALL columns of the PK (i.e., the FK itself is composite).
Single-column FKs targeting individual columns of a composite PK must be skipped with a warning.

## Lesson 39 — Multi-category loop compatibility and PK propagation (2026-04-10)

**Context:** `_loops_compatible` and `_process_loop` in `ingest.py`; `cif_pow.dic` loops.

**Problem:** DDLm multi-category loops (e.g. `pd_data/pd_meas/pd_proc/pd_calc` sharing the
same `(point_id, diffractogram_id)` PK) were being routed to `_cif_fallback` with
"incompatible multi-category loop" because:

1. `_loops_compatible` compared FK-resolved target sets. After the composite-PK FK fix (Lesson 38),
   FKs like `pd_meas.point_id → pd_data.point_id` were correctly skipped (individual columns of
   a composite PK are not valid SQL FK targets). Without those FKs, each table's `_loop_target_set`
   resolved to a different self-reference, so the sets never matched.

2. Even if compatibility had passed, `_apply_fk` only fills columns that have an FK. Without FKs
   for `pd_meas/proc/calc.point_id` and `.diffractogram_id`, those PK columns would remain NULL.

**Fix (two parts):**
1. Changed `_loops_compatible` to compare non-synthetic PK column name sets instead of FK-resolved
   target sets. Tables with the same PK column names (e.g. all having `{point_id, diffractogram_id}`)
   are compatible. This is the authoritative DDLm signal: if categories appear in the same loop,
   they share the same key structure.
2. Added cross-table PK propagation in `_process_loop` after `_apply_fk` for all tables. For each
   iteration, collect all non-NULL PK values from all sibling rows (by column name), then fill NULL
   PK columns in sibling rows from the pool. This ensures `pd_meas.diffractogram_id` gets the same
   value as `pd_data.diffractogram_id` (which was filled by the FK-accumulator path).

**How to apply:** The two-part pattern (compatibility check + cross-propagation) is needed whenever
sibling-category tables link to each other through composite-PK columns. Never rely solely on SQL FK
constraints being present for PK fill logic.

---

## Lesson 40 — Composite FK groups with conflicting source columns (bond endpoints) (2026-04-11)

**Context:** `generate_schema` FK group loop; `_chemical_conn_bond` in `cif_core.dic`.

**Problem:** `_chemical_conn_bond.atom_1` and `.atom_2` both carry `type_purpose='Link'`
targeting `_chemical_conn_atom.number`. The FK-group loop detected `has_conflicts=True`
(multiple source columns pointing to the same target column) and skipped all FKs.

**Correct rule:** `has_conflicts=True` means multiple source columns independently reference
the same target — each reference is valid on its own. When all PK columns of the target table
are covered by the group AND there are no non-PK target columns, emit one `ForeignKeyDef` per
source column individually, rather than skipping the group.

**How to apply:** In the FK group loop, add a branch:
`if has_conflicts and not missing_pk_cols and not non_pk_tgt_cols:` — iterate over all
`(src_col, tgt_col)` pairs and emit a separate FK for each. Only skip when there is a genuine
ambiguity (missing PKs or conflicting non-PK targets).

---

## Lesson 41 — `_scalar` must not filter `.` when reading `_enumeration.default` (2026-04-11)

**Context:** `DictionaryLoader` `_scalar` helper; `_enumeration.default` in DDLm dictionaries.

**Problem:** `_scalar` filtered both `'.'` (inapplicable) and `'?'` (unknown) as CIF placeholders,
returning `default` (usually `None`) for both. `_enumeration.default = '.'` is a legitimate
dictionary value meaning "the enumeration default is the CIF inapplicable sentinel", but it was
being silently dropped, leaving `DdlmItem.enumeration_default = None`.

**Fix:** Added `keep_dot: bool = False` parameter to `_scalar`. When `True`, `'.'` is returned
as a real value. Call `_scalar(data, '_enumeration.default', keep_dot=True)`.

**How to apply:** Any `_scalar` call reading a tag where `'.'` is a semantically meaningful value
(not a missing-data placeholder) must pass `keep_dot=True`. The `'?'` filter (unknown/missing) is
always applied regardless.

---

## Lesson 42 — Propagation links use `enumeration_default` as fallback; not UUID generation (2026-04-11)

**Context:** `generate_schema` propagation links; `_diffrn_radiation.variant` and
`_diffrn_radiation_wavelength.radiation_id` in `cif_pow.dic`.

**Problem (original attempt):** PK Link columns whose FK was skipped (because the FK target had a
composite PK) were left NULL, causing NOT NULL constraint violations. A first fix attempted to
generate UUIDs as a last resort, but UUID stubs for columns like `variant` (no parent table to stub
into) were semantically wrong and caused FK violations in the parent stub.

**Correct rule:**
1. PK Link columns with skipped FKs are recorded in `propagation_links`. At ingest time, their value
   is filled from (in priority order): the current loop row's matching `definition_id`, then
   `fk_accumulator`, then `enumeration_default` from `DdlmItem`. No UUID generation.
2. These columns are marked `nullable=True` in the schema — NULL is valid when no value is available
   from any source.
3. `DdlmItem.enumeration_default` must be populated (see Lesson 41) for this to work when the CIF
   omits the tag entirely.

**How to apply:** The propagation link tuple is `(col_name, target_def_id, enumeration_default)`.
Unpack all three in `_apply_fk`. If no value is found from loop or accumulator, use `enumeration_default`
as the final fallback. If that is also `None`, leave the column NULL (which is now permitted).

---

## Lesson 43 — Use class-scoped fixtures for shared ingestion state in tests (2026-04-11)

**Context:** `tests/ingestion/test_integration.py`; `TestIngestWithSchema`, `TestIngestNoSchema`,
`TestIngestSecondShort`.

**Problem:** Each test method called `_conn_with_schema(...)` and `ingest(...)` independently.
For a class of 7 tests against `cif_core.dic`, this ran 7 full ingestions of the same CIF/schema
pair. Each ingestion is expensive (~0.5s); total wall time was proportionally wasteful.

**Correct rule:** When multiple tests in a class all query the same ingested database and none of
them mutate state (all queries are SELECT-only), use a `@pytest.fixture(scope='class')` that runs
ingestion once and shares the connection. All test methods take the fixture as a parameter.

**Caution:** Only safe when tests are read-only. If any test inserts, updates, or deletes rows,
shared connections cause cross-test pollution. Check all tests in the class before converting.

**How to apply:** Name the fixture `{descriptive}_conn` (e.g. `one_structure_conn`,
`second_short_conn`). Declare it at module level with `scope='class'`. Tests that verified the
ingest return value (e.g. `assert errors == []`) must be rewritten — the return value is discarded
by the fixture. Replace with an equivalent read assertion.

## Lesson 59 — Real value comparison must preserve significant figures, not just numeric equality (2026-04-12)

**Context:** `check_fidelity` row normalisation for `Real`-typed columns.

**Problem:** Naïve `float` comparison collapses `1.2` and `1.20` to the same value, but in
crystallography these are different measurements with different precision. Significant figures are
meaningful and must be preserved in fidelity checks.

**Rule:** Two Real values are equal for fidelity purposes iff they represent the same number
*with the same number of significant digits*. Scientific notation and fixed-point notation are
interchangeable representations of the same value: `1.200e2 == 120.0` (both 4 sig figs),
but `1.2 != 1.20` (2 vs 3 sig figs).

**Fix:** Normalise Real values to canonical fixed-point form using Python's `decimal` module:
```python
from decimal import Decimal
canonical = format(Decimal(value), 'f')
```
`format(..., 'f')` converts scientific notation to decimal while preserving trailing zeros
(significant figures). Compare canonical strings, not floats.

**How to apply:** Strip any SU suffix before constructing the `Decimal`. Integer columns do not
need this treatment — string comparison is sufficient. Do not use `float()` for equality checks
on Real CIF values.

## Lesson 61 — Triple-quoted strings must not end with a bare quote of the same type (2026-04-12)

**Context:** `_quote_cif2` in `output/quote.py`.

**Problem:** A string ending with `'` wrapped in `'''...'''` produces `''''` at the close.
A CIF reader sees `'''` (closing delimiter) then a stray `'` — the value is truncated and the
next token is malformed.  Same applies to `"` and `"""`.

**Fix:** Before choosing a triple-quoted delimiter, check whether the value ends with the
same quote character.  `has_ending_single` and `has_ending_double` are computed alongside
`has_triple_single` / `has_triple_double` and used as additional guards in every branch that
would produce `'''...'''` or `"""..."""`.  If the preferred triple delimiter would create an
ambiguous closing sequence, fall through to the next rule (use the other triple type, or
semicolon).

**How to apply:** Any time a triple-quoted string is chosen, verify
`not has_ending_{quote_type}` before committing to that delimiter.

## Lesson 60 — Validation is an observation layer; it never gates further processing (2026-04-12)

**Context:** `validate()` in `src/cifflow/validation/`.

**Rule:** The validation layer reports semantic violations in a `ValidationReport` but never
prevents ingestion, emission, or any other processing step. If a user ignores validation errors
and downstream processing fails as a result, that is the user's responsibility. `ValidationMode`
controls only the severity label and `passed` flag in the report — it does not cause the function
to raise or block.

**How to apply:** `ingest()` must never call `validate()` internally. Any caller that wants
validation must call it explicitly before ingestion and decide what to do with the report.

---

## Lesson 65 — Dead code in the lexer cannot be covered; identify it rather than chasing it (2026-04-14)

**Context:** `lexer/lexer.py` — `_check_su` (lines 69-78), `_read_bare_word` CIF2-delimiter guard (lines 257-265), single-quoted CIF1.x illegal-char guard (line 307).

**Problem:** These lines appear reachable by reading the code but are structurally unreachable:
- `_check_su` is defined but never called.
- The CIF2 delimiter guard in `_read_bare_word` (lines 257-265) is never reached because CIF2 delimiters are consumed as separate tokens before `_read_bare_word` is entered.
- Line 307 (`errors.append(err)` for the delimiter character): the delimiter is always `'` or `"` (ASCII 39/34), which `_check_cif1_char` always accepts — so `err` is always `None`.

**How to apply:** Before writing tests to cover a "missing" line, verify the line is reachable by tracing the actual call paths. If the line is structurally unreachable (dead code), note it and move on rather than trying to contort inputs to hit it. Accept the residual gap.

---

## Lesson 66 — `_cif_fallback` column names must be verified before hand-crafting INSERT statements in tests (2026-04-14)

**Context:** `tests/fidelity/test_check_fidelity.py` — `_compare_schema_mismatch` tests.

**Problem:** Hand-written INSERT into `_cif_fallback` used `block_id` (wrong) instead of `_cifflow_block_id` (correct). The actual schema uses underscore-prefixed names (`_cifflow_block_id`, `_cifflow_row_id`) for all synthetic columns. The error only surfaced at test runtime.

**How to apply:** Before writing raw SQL INSERTs into framework-managed tables in tests, read `emit_fallback_create_statements()` (or the relevant DDL emitter) to confirm exact column names. Never guess — the underscore prefix convention is easy to miss.

---

## Lesson 67 — `CifSaveFrame.__getitem__` and `__contains__` are shadowed by `CifBlock`; test both classes separately (2026-04-14)

**Context:** `cifmodel/model.py` — lines 42-43 (CifSaveFrame.__getitem__ KeyError), line 46 (__contains__).

**Problem:** Both `CifBlock` and `CifSaveFrame` define `__getitem__` and `__contains__`. All existing tests used `CifBlock` instances, which hit the `CifBlock` overrides. The `CifSaveFrame` base-class implementations (lines 39-46) were never exercised because `CifBlock` short-circuits to its own versions.

**How to apply:** When a base class defines methods that subclasses override, coverage of the base-class versions requires explicit tests using direct base-class instances, not instances of any subclass.

---

## Lesson 68 — Lines 389-394 in lexer.py are in `_read_triple_cif1x`, not the CIF 2.0 handler (2026-04-14)

**Context:** `lexer/lexer.py` — `_read_triple_cif1x` (the CIF 1.x triple-quote handler).

**Problem:** The unterminated triple-quote path (lines 389-394) was assumed to be the CIF 2.0 handler and tests were written with `version=CIF2`. They failed to cover the lines because the CIF 2.0 handler is a separate function. Lines 389-394 live in `_read_triple_cif1x`, which is only entered for CIF 1.x input.

**How to apply:** When targeting a specific line range for coverage, confirm which function it actually lives in (not just what the surrounding code looks like) before writing the test. Use `grep -n "def " file.py` to map line ranges to function names.
