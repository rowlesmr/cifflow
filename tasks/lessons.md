# cifflow — Lessons Learned

## Index (by topic)

- **Arrow / PyO3 / Rust:** 103, 104, 105, 106, 107
- **CIF model / builder:** 5, 6, 7, 8, 88, 89, 90
- **DuckDB ingest:** 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 123
- **Dictionary / schema:** 12, 14, 15, 16, 17, 27, 31, 36, 38, 40, 41, 42, 64
- **Emit / output:** 48b, 50, 51, 52, 53, 54, 55, 56, 57, 58, 61, 66, 67, 68, 69, 70, 71, 72, 73, 74, 120, 121, 122, 124
- **Known gaps:** 124
- **Fidelity:** 59, 60, 62, 63, 77
- **FK propagation / ingest:** 21, 22, 23, 24, 25, 26, 28, 29, 30, 32, 34, 35, 37, 39, 43, 44, 45, 46, 47, 83, 84, 85, 86
- **Lexer / parser:** 1, 2, 3, 4, 10, 11, 37, 49b, 65, 68L
- **Performance:** 102, 109, 111, 112, 113, 114, 115, 116
- **SQLite:** 18, 19, 20, 75, 76
- **Testing:** 32, 33, 34, 43, 60, 66L, 67L, 68L, 87, 88, 89, 91, 92, 93, 94, 95, 96
- **Working practices:** 9, 13, 87

---

## Lesson 1 — Multiline text field closing delimiter (2026-04-04)

**Context:** Lexer `_read_multiline`.

**Mistake:** After consuming the closing `\n;`, called `_skip_to_eol()`, silently dropping valid tokens after the delimiter (e.g. `1.0` in `; 1.0`).

**Rule:** Per CIF 2.0 EBNF, `text-delim = line-term, ';'` — exactly two characters. After consuming them, return to NORMAL state immediately. Never skip content after the closing `;`.

---

## Lesson 2 — Sequential loops are not nested loops (2026-04-04)

**Context:** Parser `_handle_keyword` for `loop_`.

**Mistake:** Added `if self._in_loop: halt`, treating any `loop_` during an active loop as a fatal error. This halted on the second loop of `simple_loops.cif`.

**Rule:** `loop_` always terminates the current loop via `_prepare_for_keyword` and starts a fresh one. Never halt on `loop_` while `_in_loop` is True.

---

## Lesson 3 — `:` is not a bare-word terminator in CIF 2.0 (2026-04-04)

**Context:** Lexer `_read_bare_word` for CIF 2.0.

**Mistake:** Added `:` as a terminator, splitting `2007-12-18T12:16:55+02:00` into multiple tokens.

**Rule:** Per CIF 2.0 EBNF, `:` is legal inside a `wsdelim-string`. Only the outer `tokens()` loop emits `:` as a standalone token when it appears with no preceding whitespace after a closing quote or bracket. Never break on `:` inside `_read_bare_word`.

---

## Lesson 4 — `@property` preferred over `cached_property` during incremental construction (2026-04-05)

**Context:** `CifSaveFrame.loops` and `.tags` in `cifmodel/model.py`.

**Rule:** Use `cached_property` only on data that is immutable after construction. For properties backed by lists that grow during construction (mutated via `_add_loop`, `_append_value`), use plain `@property` — `cached_property` requires explicit cache invalidation on every mutation.

---

## Lesson 5 — `build()` is a convenience shortcut, not a canonical API (2026-04-05)

**Context:** `cifmodel/builder.py`.

**Rule:** `build(source, *, mode='pad')` was added as a practical utility, not specified in the prompt. Do not design downstream layers to depend on it exclusively.

---

## Lesson 6 — Empty loop handling is an extension of the spec (2026-04-05)

**Context:** `CifBuilder.on_loop_end()`.

**Rule:** Treating a loop with zero values as a distinct semantic error ("no values") is a reasonable extension — the spec only specifies row-count validation. Revisit if the spec is later clarified.

---

## Lesson 7 — Strict mode was extended beyond its specified scope (2026-04-05)

**Context:** `CifBuilder` mode parameter.

**Rule:** The prompt only defines strict/pad for loop row-count mismatch. Applying `_stopped` to empty loops and duplicate block names is internally consistent but not spec-backed. Update `_semantic_error` if the spec later defines case-specific behaviour.

---

## Lesson 8 — Empty save frame names are not recoverable (2026-04-05)

**Context:** Parser `_handle_keyword` for `save_`.

**Rule:** `save_` is unambiguously the frame-close token. Do not attempt to recover empty save frame names — `save_` outside a frame remains a syntactic error. This is a deliberate deviation from the general empty-name recovery principle.

---

## Lesson 9 — Use NumPy-style docstrings throughout (2026-04-05)

**Context:** Project-wide.

**Rule:** All public methods must document parameters, return values, and exceptions using NumPy style. Private methods need only a one-liner. Apply consistently when the public surface has stabilised.

---

## Lesson 10 — `:` at the start of a bare-word value is not a delimiter (2026-04-06)

**Context:** Lexer `tokens()` — CIF 2.0 table key/value separator.

**Mistake:** The standalone-`:` path fired unconditionally when `:` appeared first in NORMAL state, splitting `:100.0` (enumeration range bound) into two tokens.

**Fix:** Added `_last_was_ws: bool = True`. Standalone `:` is only emitted when `not self._last_was_ws` (adjacent to a prior token with no whitespace).

**Rule:** `:` is structural only when directly adjacent to the preceding token. When preceded by whitespace, it begins a bare-word value.

---

## Lesson 11 — SU validation does not belong in the lexer (2026-04-06)

**Context:** `_check_su` in `lexer/lexer.py`.

**Mistake:** Heuristic flagging of `number(su)` patterns caused false positives on fax numbers with area codes in `cif_core.dic`.

**Fix:** Removed `_check_su` entirely.

**Rule:** The lexer has no concept of "SU value" distinct from any other bare word. SU format validation belongs in the dictionary/ingestion layer.

---

## Lesson 12 — Never infer category from tag name; always use `_name.category_id` (2026-04-06)

**Context:** All dictionary and ingestion layers.

**Rule:** `_name.category_id` can differ from the dot-notation prefix of `_definition.id`. Never split a tag on `.` to infer its category. Always use `DdlmItem.category_id` or read `_name.category_id` from the save frame directly.

---

## Lesson 13 — One `debug_{thing}` function per pipeline stage (2026-04-06)

**Context:** Debug utility design.

**Rule:** Each major stage gets one debug function scoped to its primary output: `debug_lex` (token stream), `debug_build` (CifFile), `debug_schema` (SchemaSpec), `debug_db` (future). Add one only when the structure is large or opaque enough that ad-hoc inspection is consistently painful.

---

## Lesson 14 — Template files use save frame label as identifier, not `_definition.id` (2026-04-06)

**Context:** `DictionaryLoader._find_frame_by_definition_id`; `_import.get` frame lookup.

**Mistake:** Template files (`templ_attr.cif`, `templ_enum.cif`) carry no `_definition.id` entries. Looking up by `_definition.id` always missed, leaving `_type.contents`/`_type.purpose` unpopulated.

**Fix:** Match by `_definition.id` when present; fall back to save frame label when absent.

**Rule:** Any import resolution code must include this two-step lookup. Never assume template files conform to the `_definition.id` convention.

---

## Lesson 15 — Category `_name.category_id` is the parent, not the table name (2026-04-06)

**Context:** `generate_schema` table naming.

**Mistake:** Used `cat_item.category_id` as the SQL table name. For `ATOM_TYPE`, `category_id` is `atom` (the parent), producing a table named `atom` instead of `atom_type`.

**Rule:** Table name = `_table_name(cat_item.definition_id)`. Domain items = items whose `item.category_id == cat_item.definition_id`. `cat_item.category_id` is only for understanding hierarchy; it plays no role in schema generation.

---

## Lesson 16 — Import identity tags must never be merged from a source frame (2026-04-06)

**Context:** `DictionaryLoader._merge_frame` — `_import.get` mode `"Contents"`.

**Mistake:** Treating `_definition.id`, `_definition.class`, `_definition.scope`, and `_name.*` as ordinary tags subject to the `dupl` policy caused either aborts (Exit) or identity overwrites (Replace).

**Rule:** `_IMPORT_IDENTITY_TAGS` = {`_definition.id`, `_definition.scope`, `_definition.class`, `_name.category_id`, `_name.object_id`, `_import.get`}. Always skip these during merging regardless of `dupl` policy. Note: `_name.linked_item_id` must NOT be in this set — see Lesson 36.

---

## Lesson 17 — SQL identifiers must be double-quoted (2026-04-06)

**Context:** `emit_create_statements` and `apply_schema`.

**Mistake:** Bare table/column names in DDL caused `sqlite3.OperationalError` when `ddl.dic` produced a category normalising to `update` (a reserved keyword).

**Rule:** Always wrap every SQL identifier in double quotes using a `_qi(name)` helper. Embedded double quotes are escaped by doubling.

---

## Lesson 18 — Python `sqlite3` auto-commits DDL; use explicit `BEGIN` for transactional DDL (2026-04-06)

**Context:** `apply_schema` rollback-on-failure requirement.

**Mistake:** Used `with conn:` expecting DDL rollback. Python's `sqlite3` implicitly commits before DDL statements.

**Rule:** Set `conn.isolation_level = None`, issue `BEGIN` manually, execute all DDL, then `COMMIT` or `ROLLBACK` in a `finally` block. Never rely on `with conn:` for DDL.

---

## Lesson 19 — CIF presence-state encoding in SQLite (2026-04-07)

**Context:** Structured table schema design.

**Rule:** All value columns store TEXT. Encoding convention:

| Stored value | CIF meaning |
|---|---|
| `NULL` | tag absent |
| `'.'` | inapplicable (bare PLACEHOLDER) |
| `'?'` | unknown (bare PLACEHOLDER) |
| `'"."'` | literal `.` in any quoted form |
| `'"?"'` | literal `?` in any quoted form |
| anything else | real value, raw string |

`_cif_fallback` retains a `value_type` column because there is no schema type to distinguish bare-word from quoted values.

---

## Lesson 20 — `_cifflow_row_id` uniqueness requires a composite constraint (2026-04-08)

**Context:** `emit_create_statements`; Stage 4 schema design.

**Rule:** For tables where `(_cifflow_block_id, _cifflow_row_id)` is not already the `PRIMARY KEY`, emit a table-level `UNIQUE ("_cifflow_block_id", "_cifflow_row_id")`. Never use `_cifflow_row_id UNIQUE` alone.

---

## Lesson 21 — Mixed loop cross-tier join requires shared `_cifflow_row_id` per iteration (2026-04-08)

**Context:** `_cif_fallback` table design.

**Rule:** For a loop whose tags split between a structured table and `_cif_fallback`, all fallback cells for a given iteration share the `_cifflow_row_id` drawn from the structured table's counter. `_cif_fallback` PK is `(_cifflow_block_id, _cifflow_row_id, tag)`. For pure-fallback loops, `_cif_fallback` uses its own global counter (once per iteration, not per cell).

---

## Lesson 22 — Set category `_cifflow_row_id` must be reserved at first tag encounter (2026-04-08)

**Context:** Stage 4 ingestion; scalar Set accumulation.

**Rule:** When the first scalar tag of a Set category is encountered, immediately reserve the current `_cifflow_row_id_counter` for that category's pending row and increment. INSERT at end of block using the reserved value to preserve document order.

---

## Lesson 23 — Set categories can appear in loops (2026-04-08)

**Context:** Stage 4 ingestion.

**Rule:** A DDLm Set category may be given in a `loop_` with its PK column included. When a Set category appears in a loop, treat each iteration as a Loop-style row (assign `_cifflow_row_id` per iteration, pass through merge). Do not defer to end-of-block accumulation.

---

## Lesson 24 — A single logical entity may be spread across multiple CIF blocks (2026-04-08)

**Context:** Stage 4 ingestion.

**Rule:** Tags from the same category with the same PK value across different blocks describe the same logical row and are always merged. First-seen block wins `_cifflow_block_id` and `_cifflow_row_id`. First non-NULL column value wins; conflicts emit a semantic error. `_cif_fallback` rows are not merged.

---

## Lesson 25 — `_audit_dataset.id` introduces a namespace; absence says nothing (2026-04-07)

**Context:** Stage 4 ingestion; multi-block CIF files.

**Rule:** Presence of `_audit_dataset.id` asserts block membership in a named dataset; absence does not mean the block is unrelated. Pre-ingestion check: if the intersection of dataset ID sets across all dataset blocks is empty and at least one dataset block exists, raise `ValueError` and write nothing.

---

## Lesson 26 — Single-iteration loops feed `fk_accumulator` (2026-04-09)

**Context:** Stage 4 ingestion; FK propagation.

**Rule:** After any loop with exactly one iteration completes, write all column values into `fk_accumulator` (keyed by `definition_id`). Multi-iteration loops do not. This makes single-row loops equivalent to scalars for FK propagation purposes.

---

## Lesson 27 — `ColumnDef.type_contents` is informational only; DDL always emits TEXT (2026-04-09)

**Context:** Stage 4 design; `ColumnDef` field.

**Rule:** `type_contents` stores the DDLm `_type.contents` value for validation and coercion use only. It never affects DDL generation — all value columns are emitted as `TEXT`. See also Lesson 76 (JSON containers).

---

## Lesson 28 — `fk_accumulator` stores encoded database values, not raw `CifScalar` (2026-04-09)

**Context:** Stage 4 ingestion.

**Rule:** Always call `encode_value` before writing to `fk_accumulator`. The accumulator type is `dict[str, str]`. FK propagation copies values directly without re-encoding.

---

## Lesson 29 — Quoted `.` and `?` encoding covers all non-PLACEHOLDER ValueTypes (2026-04-09)

**Context:** Stage 4 value encoding; extension of Lesson 19.

**Mistake:** Initial table only listed `DOUBLE_QUOTED` for the `'"."'` / `'"?"'` cases. Single/triple/multiline quoted values whose content is `.` or `?` fell through to `'.'` / `'?'` — indistinguishable from bare PLACEHOLDER.

**Rule:** Detection: `if raw in ('.', '?') and value_type != PLACEHOLDER` → store `'"."'` or `'"?"'`. Applies to all five non-PLACEHOLDER ValueTypes.

---

## Lesson 30 — Container values in structured tables: use `json_valid()`, not `value_type` (2026-04-09)

**Context:** Stage 4 container value handling.

**Rule:** `value_type` (storing `'list'`/`'table'`) exists only in `_cif_fallback`. To detect a container value in a structured table column at query time, use `json_valid(column)` as a guard before any JSON function. Never call `json_extract`/`json_each`/`json_type` without it.

---

## Lesson 31 — `SchemaSpec` is self-contained for routing; `ingest()` needs no dictionary reference (2026-04-10)

**Context:** Stage 4 design; `ingest()` parameter.

**Rule:** `SchemaSpec` carries `alias_to_definition_id` and `deprecated_ids` (populated by `generate_schema`). `ingest()` has no `dictionary` parameter. Deprecation warnings are non-fatal; ingestion proceeds normally.

---

## Lesson 32 — Run pytest from `.venv` (2026-04-10)

**Context:** Project setup.

**Rule:** Always run `.venv/Scripts/pytest` (Windows), not a global `pytest`. The global interpreter lacks project dependencies.

---

## Lesson 33 — All public types returned by public functions must be top-level re-exports (2026-04-10)

**Context:** `cifflow/__init__.py`.

**Rule:** Any type that appears in the return value of a public function must be re-exported from `cifflow/__init__.py` and listed in `API Reference.md`. Never leave public types stranded in submodule paths.

---

## Lesson 34 — `_post_validate` must run before `_flush` (2026-04-10)

**Context:** `ingest.py` run order.

**Mistake:** `_post_validate()` was called after `_flush()`, so its appended `validation_rows` were never written to `_validation_result`.

**Rule:** `_post_validate()` → `_flush()` → `COMMIT`. Validate first, then flush.

---

## Lesson 35 — `_apply_fk` must create stub parent rows for all FK values, not just UUID-generated ones (2026-04-10)

**Context:** `ingest.py` FK constraint satisfaction.

**Problem:** Stub creation only covered the UUID-generation path. Non-key FK columns with real CIF data values (e.g. `atom_site.type_symbol = 'Se'`) also needed stubs to satisfy `DEFERRABLE INITIALLY DEFERRED`.

**Rule:** After any FK column ends up with a non-NULL value (explicit, propagated, or UUID-generated), call `_merge_into` on the parent table with a stub row. `_merge_into` is idempotent — existing rows are merged without overwriting non-NULL values.

---

## Lesson 36 — `_name.linked_item_id` must NOT be an import-identity tag (2026-04-10)

**Context:** `DictionaryLoader._merge_frame`.

**Mistake:** Including `_name.linked_item_id` in `_IMPORT_IDENTITY_TAGS` caused it to be skipped during `_import.get` merge. Templates that supply `_name.linked_item_id` (e.g. `atom_site_id` template → `'_atom_site.label'`) never populated the field, so FK detection silently missed the link.

**Rule:** `_IMPORT_IDENTITY_TAGS` contains only structural identity tags (`_definition.id`, `_name.category_id`, etc.). `_name.linked_item_id` is a data attribute of the definition and must be inheritable from templates.

---

## Lesson 37 — CIF 2.0 structural delimiters must not split tags or save frame names (2026-04-10)

**Context:** `Lexer._read_bare_word` for CIF 2.0.

**Mistake:** Unconditionally breaking on `[`, `]`, `{`, `}` split `_axis.vector[1]` (tag) and `save_axis.vector[1]` (save frame name) into fragments.

**Fix:** Only break on these delimiters when the accumulated word does NOT start with `_` (tag) or a prefix keyword (`save_`, `data_`).

**Rule:** `[/{/]` etc. terminate plain unquoted values (`restrict-char` context) but not tags or frame names (`non-blank-char` context). Check what token type is being accumulated before applying delimiter break rules.

---

## Lesson 38 — FK target must be the sole PK, not just any PK column (2026-04-10)

**Context:** `generate_schema` building `ForeignKeyDef`.

**Mistake:** Checked `target_column not in primary_keys`, which passed for a column that IS a PK but part of a composite PK. SQLite doesn't create a UNIQUE index for individual columns of a composite PK, causing "foreign key mismatch" errors.

**Rule:** The FK target column must satisfy `tables[tgt_tbl].primary_keys == [target_item.object_id]` — it must be the sole PK of the target table. Single-column FKs targeting individual columns of a composite PK must be skipped with a warning.

---

## Lesson 39 — Multi-category loop compatibility and PK propagation (2026-04-10)

**Context:** `_loops_compatible` and `_process_loop` in `ingest.py`.

**Problem:** After the composite-PK FK fix (Lesson 38), individual-column FKs were correctly skipped, but `_loops_compatible` compared FK-resolved target sets — which now differed per table, routing everything to `_cif_fallback`.

**Fix (two parts):**
1. `_loops_compatible` now compares non-synthetic PK column name sets. Tables with the same PK column names are compatible.
2. Added cross-table PK propagation in `_process_loop`: after `_apply_fk`, collect all non-NULL PK values by column name from all sibling rows and fill NULLs in siblings from the pool.

**Rule:** Never rely solely on SQL FK constraints for PK fill logic across sibling-category tables.

---

## Lesson 40 — Composite FK groups with conflicting source columns (bond endpoints) (2026-04-11)

**Context:** `generate_schema` FK group loop; `_chemical_conn_bond`.

**Mistake:** When `atom_1` and `atom_2` both referenced `_chemical_conn_atom.number`, `has_conflicts=True` caused all FKs to be skipped.

**Rule:** When `has_conflicts=True` but all PK columns of the target are covered and there are no non-PK targets, emit one `ForeignKeyDef` per source column individually. Only skip when there is genuine ambiguity.

---

## Lesson 41 — `_scalar` must not filter `.` when reading `_enumeration.default` (2026-04-11)

**Context:** `DictionaryLoader._scalar`; `_enumeration.default` in DDLm dictionaries.

**Mistake:** `_scalar` filtered `'.'` as a CIF placeholder, silently dropping `enumeration_default = '.'` — a legitimate "default is the inapplicable sentinel" value.

**Fix:** Added `keep_dot: bool = False` to `_scalar`. Call with `keep_dot=True` for `_enumeration.default`.

**Rule:** Only apply the `'.'` filter for tags where it is genuinely a missing-data placeholder. Use `keep_dot=True` wherever `.` is a semantically meaningful value.

---

## Lesson 42 — Propagation links use `enumeration_default` as fallback; not UUID generation (2026-04-11)

**Context:** `generate_schema` propagation links; `_diffrn_radiation.variant`.

**Rule:** PK Link columns with skipped FKs are recorded in `propagation_links` as `(col_name, target_def_id, enumeration_default)`. Fill order: loop row → `fk_accumulator` → `enumeration_default`. If all are `None`, leave the column NULL (marked `nullable=True`). No UUID generation for these columns.

---

## Lesson 43 — Use class-scoped fixtures for shared ingestion state in tests (2026-04-11)

**Context:** `tests/ingestion/test_integration.py`.

**Rule:** When multiple tests in a class all query the same ingested database and none mutate state, use `@pytest.fixture(scope='class')` to run ingestion once. Only safe when tests are read-only. Tests that verified the return value must be rewritten as read assertions.

---

## Lesson 44 — SU values must be scaled, not stored raw (2026-04-11)

**Context:** `split_su` in `ingestion/ingest.py`.

**Mistake:** `split_su('3.992(4)')` returned `('3.992', '4')` — raw digits rather than `'0.004'`. Inconsistent with an explicitly supplied `_cell.length_a_su 0.004`.

**Rule:** Scale by `10^(exponent − decimal_places)`: `'3.992(4)'` → `'0.004'`, `'1234(5)'` → `'5'`, `'1.23e-4(5)'` → `'0.000005'`.

---

## Lesson 45 — UUID-per-row for keyless loops requires a post-`_apply_fk` fill pass (2026-04-11)

**Context:** `_process_loop` and `_apply_fk`.

**Mistake:** UUID generation was only wired into `_apply_fk` Source 3 (single-column key-FKs). Pure-key PKs and composite-key-FK components were skipped, collapsing all iterations to one row.

**Rule:** After all `_apply_fk` calls for an iteration, run a UUID fill pass: for each NULL non-synthetic PK column, generate one UUID per column name and apply to every sibling table sharing that name. In loop context, do not persist to `fk_accumulator` (each iteration must regenerate).

---

## Lesson 46 — Loop-class scalar tags must be buffered per-block, not merged immediately (2026-04-11)

**Context:** `_process_scalar` in `ingest.py`.

**Mistake:** Scalar Loop-class tags were merged one-at-a-time. Non-PK tags had PK = `(None,)`, so rows from different blocks merged together producing false conflicts.

**Fix:** Accumulate in `loop_scalar_buffers` dict. Write to `fk_accumulator` immediately. Flush the complete row at end-of-block after all tags have been seen.

**Rule:** Whether `category_class` is `Set` or `Loop`, scalar tags within a single block always describe one row. Merge only when the full row is assembled.

---

## Lesson 47 — Composite FK column fill requires transitive single-column FK lookup (2026-04-11)

**Context:** `_apply_fk` composite FK branch.

**Mistake:** The lookup searched `fk_accumulator` by the intermediate column tag (`_pd_data.diffractogram_id`), but the accumulator holds the value under the ultimate tag (`_pd_diffractogram.id`) — the lookup always missed.

**Fix:** When the direct lookup fails, walk the single-column FK chain from the target column up to 15 levels. Emit a warning if the depth limit is reached (possible cycle).

**Rule:** Composite FK column fill must be transitively FK-aware. Follow the chain rather than assuming one hop is sufficient.

---

## Lesson 48b — Semicolon-delimited text fields: content starts on the same line as the opening `;` (2026-04-11)

**Context:** `_make_semicolon` in `output/quote.py`.

**Mistake:** Used `f'\n;\n{s}\n;'` — added an extra blank line, causing round-tripped values to gain a leading newline.

**Rule:** `f'\n;{s}\n;'`. The opening `;` and the first content character are on the same line. The closing `;` is on a line by itself at column 1.

---

## Lesson 49 — JediTerm/PyCharm has a column-tracking bug with ANSI codes and line wrapping (2026-04-11)

**Context:** `inspect/` package output in PyCharm with terminal emulation.

**Symptom:** Lines with ANSI SGR codes that wrap display continuation text at a large column offset.

**Root cause:** JediTerm miscounts ANSI escape bytes as visible characters when computing column positions. Known JediTerm bug, not a library issue.

**Rule:** Do not work around JediTerm line-wrap rendering bugs in library code. Widen the terminal panel past the longest line to avoid the symptom.

---

## Lesson 49b — Check whether `'` and `"` are legal mid-word in CIF 2.0 unquoted strings (2026-04-11)

**Context:** `quote.py` Rule 2 (bare word).

**Status:** Rule 2 defensively excludes values containing `'` or `"` from bare-word emission because the lexer re-enters quoted-string state mid-token. This may be over-cautious — CIF 2.0 EBNF may permit these mid-word. Check `references/CIF2-ENBF.txt`; if allowed, fix the lexer and relax the guard.

---

## Lesson 50 — GROUPED mode: Set-anchor BFS must explore all FK targets, not just the first (2026-04-11)

**Context:** `_find_set_anchor` in `output/emit.py`.

**Mistake:** Depth-first single-path walk. If the first FK at a hop led to a Loop (with no Set ancestor), the Set reached via another FK at the same hop was never found.

**Rule:** Use BFS over all FK targets at each level. Return the first Set-class table reached (closest by hop count).

---

## Lesson 51 — GROUPED mode: `covered_cifflow_block_ids` must be expanded from FK-chained rows (2026-04-11)

**Context:** `_collect_grouped`.

**Mistake:** Seeded only from anchor table rows. When a Set has a domain PK and two blocks share the same key, only the first block's `_cifflow_block_id` was recorded, leaving the second block's Loop descendants un-absorbed.

**Rule:** After each FK-chained table fetch, extend `covered_cifflow_block_ids` from the returned rows' `_cifflow_block_id` values. `covered_cifflow_block_ids` is the union of all `_cifflow_block_id` values in any row belonging to the anchor group.

---

## Lesson 52 — GROUPED mode: exclusive-target anchor groups need block_id fallback (2026-04-11)

**Context:** `_collect_grouped`.

**Problem:** Sets like `space_group` (exclusively referenced from one other anchor, no FK out) generated their own duplicate output blocks when processed as independent anchors.

**Fix (three-part):** Identify exclusive-target anchors and move them to `block_id_tables`. Split absorbed tracking: `absorbed_primary` (anchor-row block_ids, for skip check) vs `absorbed_all` (all swept block_ids, for suppression). The `block_id_tables` sweep uses extended `covered_cifflow_block_ids`.

---

## Lesson 53 — ONE_BLOCK: Set categories with >1 row must render as loops; bridge columns must be synthetic (2026-04-11)

**Context:** `_render_block` in `output/emit.py`; `generate_schema`.

**Mistake 1:** `_render_block` always used `_render_set_category(rows[0], ...)`, dropping all rows beyond the first in ONE_BLOCK mode.
**Fix 1:** Dispatch on `category_class == 'Set' and len(rows) == 1`; otherwise fall back to `_render_loop_category`.

**Mistake 2:** Transitive bridge columns had `is_synthetic=False`, causing `_active_cols` to pass them through and emit fake tag names.
**Fix 2:** Mark all bridge columns `is_synthetic=True` at point of creation.

**Rule:** Any column with no DDLm `definition_id` must be `is_synthetic=True`.

---

## Lesson 54 — ONE_BLOCK round-trip is only safe when all rows have distinct domain keys (2026-04-11)

**Context:** `tests/output/test_emit.py`.

**Rule:** Keyless Set categories (rows distinguished only by `_cifflow_block_id`) cannot be merged into ONE_BLOCK without a PK conflict. Do not write round-trip tests attempting to collapse such rows.

---

## Lesson 55 — Emit round-trip tests: NULL vs `'.'` normalisation (2026-04-11)

**Context:** `tests/output/test_emit.py`.

**Rule:** Loop emission cannot omit mid-row columns; it emits SQL NULL as `'.'`. After re-ingestion, `'.'` is stored as a string. Normalise both sides before comparison: `None → None`, `'.' → None`. `'?'` remains distinct.

---

## Lesson 56 — GROUPED mode: empty root-anchor table silently drops its entire FK group (2026-04-11)

**Context:** `_collect_grouped`.

**Mistake:** When the root Set anchor has zero rows, the entire FK group was silently dropped.

**Fix:** `if not anchor_rows: block_id_tables.extend(keyed_anchor_to_tables[anchor_name]); continue`.

**Rule:** A keyed anchor with no rows is indistinguishable from a keyless Set for emission. Fall back to `_cifflow_block_id` grouping; never silently discard tables.

---

## Lesson 57 — Emit: FK-PK columns pointing to a co-emitted Set are implicit from block scope (2026-04-11)

**Context:** `_suppressed_fk_pk_cols()` in `output/emit.py`.

**Rule:** If a table's domain PK column is also a FK to a Set in the same block, and all rows carry the same value equal to the Set's PK, suppress the column from output (the value is implied by block scope). Applies to ORIGINAL and GROUPED modes only; not ALL_BLOCKS or ONE_BLOCK.

---

## Lesson 58 — Two root causes of NULL columns after emit → re-ingest (2026-04-11)

**Context:** `_flush` in `ingest.py`; `_collect_grouped` in `output/emit.py`.

**Bug 1:** `_flush` used only `rows[0].keys()` as the INSERT column list. Slim stub rows (with fewer keys) as row 0 silently omitted columns present in later rows.
**Fix:** Compute the union of all row keys: `seen = {}; for r in rows: seen.update(dict.fromkeys(r.keys())); cols = list(seen)`.

**Bug 2:** The remaining-blocks pass in GROUPED swept only `block_id_tables`, missing keyed-anchor tables whose rows had NULL FK columns.
**Fix:** Sweep all schema tables in the remaining-blocks pass.

**Rule:** Always compute the full column-key union in `_flush`. Any table with potentially NULL FK columns must also be swept by block_id.

---

## Lesson 59 — Real value comparison must preserve significant figures (2026-04-12)

**Context:** `check_fidelity` row normalisation for `Real`-typed columns.

**Rule:** Two Real values are equal for fidelity iff they represent the same number with the same significant digits. Use `format(Decimal(value), 'f')` for canonical fixed-point form — `Decimal` preserves trailing zeros. `1.2 ≠ 1.20`. Strip SU suffix before constructing the `Decimal`.

---

## Lesson 60 — Validation is an observation layer; it never gates processing (2026-04-12)

**Context:** `validate()` in `src/cifflow/validation/`.

**Rule:** `validate()` reports violations but never raises or blocks. `ingest()` must never call `validate()` internally. Callers call it explicitly and decide what to do with the report.

---

## Lesson 61 — Triple-quoted strings must not end with a bare quote of the same type (2026-04-12)

**Context:** `_quote_cif2` in `output/quote.py`.

**Mistake:** A value ending with `'` wrapped in `'''...'''` produces `''''` — the reader sees a closing `'''` then a stray `'`, truncating the value.

**Fix:** Compute `has_ending_single` and `has_ending_double` alongside the triple-quote guards. If the preferred triple delimiter would create an ambiguous closing sequence, fall through to the next quoting rule.

---

## Lesson 62 — CIF placeholder `'.'` and `'?'` must be treated as NULL in fidelity comparison (2026-04-13)

**Context:** `_normalised_rows()` and `_fingerprint_uuid()` in `fidelity/check.py`.

**Rule:** For structured table comparison, `NULL`, `'.'`, and `'?'` all mean "no data here" — treat them identically. This does not apply to `_cif_fallback`, where the distinction between absent and explicit placeholder is meaningful.

---

## Lesson 63 — Row diff hints must use row-relative (+/-) labels, not absolute (A+/B+) (2026-04-13)

**Context:** `_row_diff_hint()` in `fidelity/check.py`.

**Rule:** Use `+col=val` (this row has it, match doesn't) and `-col=val` (match has it, this row doesn't). The caller supplies context about which side the row came from.

---

## Lesson 64 — `DictionaryLoader` needs a separate `path_resolver` for `source_files` (2026-04-13)

**Context:** `DictionaryLoader` in `dictionary/loader.py`.

**Rule:** The `SourceResolver` returns file content; the `path_resolver` maps URIs to filesystem paths. Keep them separate — the source resolver cannot serve double duty.

---

## Lesson 65 — Dead code cannot be covered; identify it rather than chasing it (2026-04-14)

**Context:** `lexer/lexer.py` — `_check_su`, CIF2 delimiter guard, single-quoted CIF1.x illegal-char guard.

**Rule:** Before writing tests to cover a "missing" line, trace the actual call paths to verify reachability. If structurally unreachable (dead code), note it and accept the residual gap.

---

## Lesson 66L — `_cif_fallback` column names must be verified before hand-crafting INSERTs in tests (2026-04-14)

**Context:** `tests/fidelity/test_check_fidelity.py`.

**Mistake:** Used `block_id` instead of `_cifflow_block_id` in a hand-written INSERT. The underscore prefix is easy to miss.

**Rule:** Before writing raw SQL INSERTs into framework-managed tables, read the relevant DDL emitter (e.g. `emit_fallback_create_statements()`) to confirm exact column names.

---

## Lesson 67L — `CifSaveFrame.__getitem__` and `__contains__` are shadowed by `CifBlock`; test both classes separately (2026-04-14)

**Context:** `cifmodel/model.py`.

**Rule:** When a base class defines methods that subclasses override, coverage of the base-class versions requires direct base-class instances in tests, not subclass instances.

---

## Lesson 68L — Identify which function a line lives in before writing targeted tests (2026-04-14)

**Context:** `lexer/lexer.py` — `_read_triple_cif1x`.

**Mistake:** Assumed the unterminated-triple-quote path lived in the CIF 2.0 handler; wrote tests with `version=CIF2`. The lines are in `_read_triple_cif1x` (CIF 1.x only).

**Rule:** Use `grep -n "def " file.py` to map line ranges to function names before writing targeted coverage tests.

---

## Lesson 69 — ALL_BLOCKS delegates to GROUPED and strips `audit_dataset` for UUID consistency (2026-04-14)

**Context:** `_collect_all_blocks` in `output/emit.py`.

**Problem 1:** Old ALL_BLOCKS emitted one block per non-empty table, not one block per Set-anchor key combination.
**Problem 2:** Inconsistent `_audit_dataset.id` — blocks with stored UUIDs got the stored value, others got the emission UUID, causing re-ingest mismatch.

**Fix:** Call `_collect_grouped`, then strip `'audit_dataset'` from each block's `table_rows` before building `_BlockData`. This guarantees the injection always fires and all blocks receive the same emission UUID.

---

## Lesson 70 — Set-category re-quoting requires two passes to recompute `tag_width` (2026-04-15)

**Context:** `_render_set_category` in `output/emit.py`.

**Rule:** After re-quoting converts any inline token to multiline, recompute `tag_width` from the remaining inline tokens. Without step 4, some tags are padded to a width driven by a tag that no longer participates in inline alignment.

---

## Lesson 71 — `make_text_field` dispatches all four semicolon formats (2026-04-15)

**Context:** `output/quote.py`.

**Rule:** Always call `make_text_field` from the emit layer rather than `_make_semicolon` directly when `line_limit` is in play. The dispatch is: `'\n;' in s` × `content > limit` → 4 combinations.

---

## Lesson 72 — `_fold_content_lines` breaks before the space (2026-04-15)

**Context:** `_fold_content_lines` in `output/quote.py`.

**Rule:** When breaking at whitespace position `break_at`: first segment is `line[:break_at]` (space not included), continuation starts at `line[break_at:]` (space is first character). The space is preserved in the reconstructed string.

---

## Lesson 73 — Column ordering in loop emit may differ from source CIF order (2026-04-15)

**Context:** `TestDecimalAlign` in `tests/output/test_emit.py`.

**Rule:** The loop renderer outputs columns in schema/key order, not source-CIF order. Tests that hard-code a column index are fragile. Use `ln.index('.')` (line-level position) rather than `ln.split()[N].index('.')`.

---

## Lesson 74 — Test decimal alignment with line-level dot position, not token-internal (2026-04-15)

**Context:** `TestDecimalAlign` in `tests/output/test_emit.py`.

**Rule:** Use `ln.index('.')` — position of dot in the full line. Decimal alignment is a line-level property; the dot should land at the same character column in every row.

---

## Lesson 75 — Rows fetched from SQLite may already be typed, not TEXT (2026-04-15)

**Context:** `_cast_value` in `database/compact.py`.

**Mistake:** Called `re.sub` on `_cifflow_row_id`, which SQLite returns as a Python `int`, raising `TypeError`.

**Fix:** Guard at top of `_cast_value`: `if not isinstance(raw, str): return raw`.

---

## Lesson 76 — Non-Single container columns store JSON; coerce leaves, not the whole value (2026-04-15)

**Context:** `convert_database` in `database/compact.py`; `ColumnDef`.

**Mistake:** Tried to cast JSON array `["1","2","3"]` to `int`, discarding the JSON.

**Rule:** Any column whose `type_container` is not `"Single"` stores JSON. Use `_sql_type_for(col)` → `"TEXT"` for non-Single. Detect JSON (`raw.startswith('[') or raw.startswith('{')`), decode, recurse into leaves with `_cast_json_leaves`, re-serialise.

---

## Lesson 77 — In sparse column display, apply the qualification check before skipping synthetics (2026-04-15)

**Context:** `_column_rows` in `dictionary/visualise.py`.

**Mistake:** Synthetic columns were skipped before the bridge-qualification check. A synthetic column that IS a bridge column should appear in `show_columns='sparse'`.

**Fix:** Removed the synthetic-specific `continue` in the sparse branch. The single combined predicate `not (col.is_primary_key or col.name in fk_source_cols or col.name in bridge_cols)` covers all columns uniformly.

---

## Lesson 83 — A partial FK is only safe when the dictionary is wrong, not when data is sparse (2026-04-16)

**Context:** `generate_schema`; `enumeration_default = '.'`.

**Mistake:** Added a `UNIQUE` constraint and partial FK based on `enumeration_default = '.'`, assuming the column would always be inapplicable. A file that legitimately populates the column violates the constraint.

**Rule:** Do not add constraints encoding assumptions about what values CIF files *will* supply. Only encode what the dictionary says they *must* supply.

---

## Lesson 84 — BFS for transitive bridges must return all shortest paths, not just the first (2026-04-16)

**Context:** `_find_transitive_bridge` in `dictionary/schema.py`.

**Mistake:** BFS collected all paths at minimum depth but returned only `results[0]`. For `pd_peak.radiation_id`, two equally-short paths exist (through `diffrn` and `pd_instr`); different CIF authors populate different paths.

**Fix:** Return the full `results` list. `BridgeColumnDef` carries `fallback_chains`. `_fill_bridge_columns` tries each chain in order per row.

---

## Lesson 85 — Bridge column lookups must not be keyed by `_cifflow_block_id` (2026-04-16)

**Context:** `_build_chain_lookups` and `_resolve_chain` in `ingest.py`.

**Mistake:** Keyed lookup by `(block_id, pk_val)`. In multi-block datasets, the source and bridge tables have different `_cifflow_block_id` values, so the lookup always missed.

**Rule:** Key by `pk_val` only. `merged_rows` is already dataset-scoped; `_cifflow_block_id` discrimination is unnecessary and harmful.

---

## Lesson 86 — When multiple bridge chains resolve, check them all for agreement (2026-04-16)

**Context:** `_fill_bridge_columns` in `ingest.py`.

**Mistake:** Stopped at the first non-None result, silently ignoring disagreements between chains.

**Fix:** Evaluate all chains; if results disagree, emit a warning naming all resolved values and use the first. If they agree, use silently.

---

## Lesson 87 — A complete spec does not authorise implementation (2026-04-18)

**Context:** Start of `CifWriter` + `clean` implementation session.

**Mistake:** After the previous session ended with "spec is ready", immediately began implementing without being asked. The user had to revert all changes.

**Rule:** Wait for explicit instruction ("implement", "go ahead") before writing any code. "Spec is ready" means design work is done — nothing more.

---

## Lesson 88 — A single-column loop reassigned to a different length is still consistent (2026-04-18)

**Context:** `tests/cifmodel/test_writer.py`.

**Mistake:** Asserted `build()` raises when a single-column loop's column is reassigned to a shorter list. It doesn't — with one column, all lengths are trivially equal.

**Rule:** Loop column-length validation requires at least two columns to detect a mismatch. Use a two-column loop in the test.

---

## Lesson 89 — Python chained comparison `a in b == c` is not `(a in b) == c` (2026-04-18)

**Context:** `tests/cifmodel/test_clean.py`.

**Mistake:** `assert "_error_value" in cif["b"]._tags == original_has_tag` evaluates as a chained comparison (`(a in b) and (b == c)`), not the intended `(a in b) == c`.

**Rule:** Always use explicit parentheses or two separate assertions.

---

## Lesson 90 — `strip_loop_padding` only fires when ALL columns have trailing PLACEHOLDERs (2026-04-18)

**Context:** `_strip_padding_in_ns` in `cifmodel/clean.py`.

**Rule:** `k = min(trailing PLACEHOLDER count per column)`. If any column has `k=0`, nothing is stripped. Tests must construct the model state directly with ALL columns' last values as PLACEHOLDERs — don't rely on the parser producing that pattern.

---

## Lesson 91 — Define helpers before the dict/mapping that references them (2026-04-19)

**Context:** `_db_checks.py`; `_TYPE_CONTENTS_RULES` dict.

**Mistake:** Dict placed before the helper functions it referenced. Python raises `NameError` at import time.

**Rule:** Module-level dicts/mappings that reference functions must appear after those functions. Module-level expressions are evaluated top-to-bottom at import time (unlike class bodies).

---

## Lesson 92 — Parametrize `None` separately when a downstream default converts it (2026-04-19)

**Context:** `TestTypeMapping.test_type_contents_stored_as_is` in `tests/dictionary/test_schema.py`.

**Mistake:** `None` was in the parametrize list. After adding `item.type_contents or 'Text'` in `generate_schema()`, the `None` case now produces `'Text'`, failing the assertion.

**Rule:** Give the sentinel value its own dedicated test asserting the converted output.

---

## Lesson 93 — Patching the wrong function when the real trigger is never called (2026-04-19)

**Context:** `TestInternalError` in `tests/validation/test_db_validate.py`.

**Mistake:** Patched `_check_keyless_cardinality` to raise, but the test table had PKs, so the keyless-only code path was never reached. The patch was never triggered; the test passed vacuously.

**Fix:** Patch `_run_validation` directly — always called regardless of table shape.

**Rule:** Before patching, verify the test setup actually exercises the code path that calls the patched function.

---

## Lesson 94 — Test message-content assertions break when `repr()` is used in format strings (2026-04-19)

**Context:** `tests/validation/test_db_validate.py`.

**Mistake:** Asserted `bad_key in r.message`, but the message was formatted with `{key!r}`, so the literal string never appeared verbatim.

**Rule:** Assert on structured fields (`r.value`, `r.tag`) rather than parsing the human-readable message. If you must check `message`, use the exact formatted form including `repr()` escaping.

---

## Lesson 95 — Use keyword-only kwargs for callback signature extension (2026-04-20)

**Context:** `on_error` callback in `ingest()`.

**Rule:** Extend callback signatures with keyword-only args with defaults (`*, table=None, column=None`) rather than positional args. Positional extension breaks every caller; keyword-only breaks only callers that don't declare `**kwargs`. Use `Callable[..., None]` as the type annotation and document the contract in prose.

---

## Lesson 96 — Test callbacks break when a new kwarg is added (2026-04-20)

**Context:** `on_error` callback extended to add `table`, `column`, `key_values`.

**Mistake:** Existing test lambdas `lambda msg, blk=None: ...` and `list.append` raised `TypeError` when called with keyword arguments.

**Rule:** Any time a callback gains optional keyword arguments, grep for every caller passing a bare lambda or method and update them to accept `**kw`.

---

## Lesson 97 — `_classify_pk_cols` returns 5-tuples; all unpack sites must match (2026-04-21)

**Context:** Loop branch of `_collect_all_blocks`.

**Mistake:** Still unpacking 3-tuples after the return was extended to 5-tuple.

**Rule:** When changing a return tuple's arity, grep for every unpack site before closing. The Set and Loop branches of the same function can diverge silently.

---

## Lesson 98 — `plan.specs` not `plan.blocks` (2026-04-21)

**Context:** `_ordered_tables_all_blocks`.

**Rule:** `OutputPlan.specs` is the list of `BlockSpec` objects. There is no `.blocks` attribute.

---

## Lesson 99 — `_sort_and_merge` discards ALL_BLOCKS plan ordering (2026-04-21)

**Context:** `emit()` in `output/emit.py`.

**Mistake:** ALL_BLOCKS blocks all have empty `anchor_frozenset`, so `_sort_and_merge` re-sorted them alphabetically, discarding the plan order.

**Fix:** ALL_BLOCKS skips `_sort_and_merge`: `ordered = [(b, None) for b in raw_blocks]`.

**Rule:** `_sort_and_merge` is designed for GROUPED/ORIGINAL anchor-key matching. Bypass it for ALL_BLOCKS.

---

## Lesson 100 — ALL_BLOCKS `dataset_id` must come from `_block_dataset_membership` (2026-04-21)

**Context:** `_collect_all_blocks`.

**Mistake:** Injected one `uuid.uuid4()` for all emitted blocks. Fidelity checker found differing `_audit_dataset.id` values.

**Rule:** `_resolve_dataset_id()` queries `_block_dataset_membership` per block. Returns existing ID, sorted list (emitted as `loop_`), or fresh UUID only when no membership data exists. `_BlockData.dataset_id` may be `str | list[str] | None`.

---

## Lesson 101 — Regex tokenizer: three correctness pitfalls (2026-04-22)

**Context:** Replacing the generator-based lexer with `re.finditer`.

1. **Unterminated triple-quoted strings:** Lazy `[\s\S]*?` fails silently if no closing `'''`/`"""` exists — add greedy `TDQ_UNT`/`TSQ_UNT` fallback patterns immediately after the terminated counterparts.
2. **`\n;` inside triple-quoted strings:** Pre-scan must track triple-quote openers and skip any multiline match before the closing `skip_until` offset.
3. **`:` inside bare words:** Use lookbehind `(?<=[\"'\]\}]):` — `:` is only structural when the immediately preceding character is a structural close.

---

## Lesson 102 — `_id_regime` quadratic scan: precompute with one pass (2026-04-23)

**Context:** `_id_regime` in `ingest.py`.

**Mistake:** Iterated all `~1.98M` merged rows per block (156 blocks) — O(blocks × rows), 12.9s.

**Fix:** `_compute_id_regimes()` does one O(n_rows) pass building a `dict[block_id, regime]`. `_id_regime` does a single `dict.get` lookup.

**Rule:** Any per-block function that iterates all merged rows is quadratic. Detect the pattern; replace with a single precompute pass.

---

## Lesson 103 — Parquet and Arrow IPC require a single schema per file; per-loop schemas are in-memory only (2026-04-26)

**Context:** Arrow IR design.

**Rule:** In-memory `Vec<RecordBatch>` can have per-batch schemas. Parquet/IPC files require a unified schema across all row-groups/batches. Write one file per batch (as `debug_parquet.py` does) or use a union schema with NULL padding for disk persistence.

---

## Lesson 104 — Arrow IPC is the correct Rust→Python transport for RecordBatches (2026-04-26)

**Context:** Phase B.2 — Arrow IR pipeline.

**Rule:** Use IPC bytes (`FileWriter` → `Cursor<Vec<u8>>` → `PyBytes`) rather than the `pyarrow` crate feature. The `pyarrow` feature pins `arrow-rs` to a specific `pyo3` version; IPC carries no such coupling. Deserialise with `pyarrow.ipc.open_file(io.BytesIO(data)).get_batch(0)` on the Python side.

---

## Lesson 105 — `parse_cif` eliminates the dict-unpacking pass (2026-04-26)

**Context:** `build()` in `builder.py`.

**Rule:** When target model types are PyO3 classes, the Rust parsing function should construct and return them directly rather than going via a Python dict intermediary. The dict path exists only for legacy compatibility.

---

## Lesson 106 — PyO3 types that replace Python classes must expose mutable state as Python objects (2026-04-26)

**Context:** Phase B.3 — PyO3-exposed CifFile.

**Rule:** Store fields that are mutated in-place by external Python code (`_tags`, `_loops`, etc.) as `PyObject = Py<PyAny>` with `#[pyo3(get, set)]`. The getter returns the same Python object — mutations propagate back automatically. Rust-native types cannot be mutated in-place from Python.

---

## Lesson 107 — Use `arrow` v54+ when enabling the `pyarrow` feature alongside `pyo3 = "^0.23"` (2026-04-26)

**Context:** `cifflow_core/Cargo.toml`.

**Rule:** Arrow 53's `pyarrow` feature declares `pyo3 = "^0.22"`; arrow 54 bumped to `"^0.23"`. Mixing arrow 53 + pyo3 0.23 + pyarrow feature is a hard compile-time conflict (`links` field clash via `pyo3-ffi`).

---

## Lesson 108 — Arrow bulk insert in DuckDB: one `register/execute/unregister` per table, not per block (2026-04-27)

**Context:** `_load_loop` in `duckdb_ingest.py`.

**Rule:** Accumulate all blocks' rows per table, then do a single Arrow insert per table. Reduces overhead from O(blocks × tables) to O(tables). Higher peak memory is acceptable for files that fit in RAM.

---

## Lesson 109 — `_id_regime` O(blocks × rows): precompute with one pass (DuckDB path) (2026-04-27)

**Context:** `_record_membership` in `ingest.py` (DuckDB path).

**Rule:** `_compute_id_regimes()` does one O(n_rows) pass building `dict[block_id, regime]`. `_record_membership` does a single dict lookup. Saved 9.81s → 0.27s for `second.cif`. See also Lesson 102 (Python path, same pattern).

---

## Lesson 110 — `tag_presence_rows` must be populated for non-winning blocks in DuckDB merge (2026-04-27)

**Context:** `extract_merged_rows` in `duckdb_ingest.py`.

**Mistake:** DuckDB merge path never populated `tag_presence_rows`, so non-winning blocks appeared to have no data — breaking ORIGINAL-mode emit for shared Set rows.

**Rule:** For every non-first occurrence of a `pk_key` from a different `block_id`, append `(block_id, tbl_name, col, pk_json)` for all PK and non-null data columns. Every merge path must populate this.

---

## Lesson 111 — First-occurrence rows in a merge loop: `list(vals)` + `continue` avoids millions of allocations (2026-04-27)

**Context:** `extract_merged_rows` merge loop.

**Rule:** Guard with `if pk_key not in winner_dict: winner_dict[pk_key] = list(vals); continue` before any conflict bookkeeping. Skip the inner null-fill loop and `seen_losers` initialisation for first occurrences. Defer `seen_losers` to `setdefault` on first actual conflict.

---

## Lesson 112 — DuckDB `FIRST(col ORDER BY ...) FILTER (WHERE col IS NOT NULL)` is catastrophically slow for wide tables (2026-04-27)

**Context:** `extract_merged_rows` — original GROUP BY query.

**Mistake:** For `cell` (3 rows, 60 columns), the ordered-aggregate query took 3067ms regardless of row count. DuckDB's ordered-aggregate with filter has O(n_cols) per-execute overhead.

**Fix:** Eliminated GROUP BY. Single `SELECT ... ORDER BY` + Python dict winner tracking replaces the aggregate.

**Rule:** Never use `FIRST(col ORDER BY ...) FILTER (WHERE col IS NOT NULL)` for tables with >~10 columns.

---

## Lesson 113 — DuckDB `fetchall()` creates Python tuples; for 500K+ rows use `fetch_arrow_table()` (2026-04-27)

**Context:** `extract_merged_rows` — fetching staging rows.

**Rule:** For large result sets, prefer `fetch_arrow_table()` (columnar, zero-copy from Arrow memory) over `fetchall()` (materialises all rows as Python tuples). Only fall back to `fetchall()` for < ~10K rows or when column-at-a-time access would be awkward.

---

## Lesson 114 — `_merge_keyed_fast` (no GROUP BY) was no faster than GROUP BY for large tables (2026-05-01)

**Context:** `duckdb_ingest.py` — merge optimisation attempt.

**Finding:** Direct INSERT without GROUP BY was identical in speed to GROUP BY. Both include `ROW_NUMBER() OVER (ORDER BY ...)` — the window sort is the bottleneck, not the aggregation.

**Rule:** Before adding an alternative implementation, benchmark it. If the bottleneck is in a shared sub-expression, alternative paths that still include it will show no improvement.

---

## Lesson 115 — On Windows, each DuckDB query triggers Python import machinery with AV overhead (2026-05-01)

**Context:** cProfile of the emit phase; Windows AV scanning.

**Finding:** Each of 19,500 `_fetch_rows` calls triggered ~8 import lookups → ~7 `nt.stat()` calls each. Total: ~45s of the 82s emit time.

**Fix:** Eliminating 19,500 → 125 queries (Lesson 116) reduced this proportionally.

**Rule:** On Windows, minimise DuckDB `execute()` calls in hot loops. AV overhead is 200–500μs per call regardless of query complexity.

---

## Lesson 116 — Pre-fetch all rows at the start of an emit pass to eliminate N+1 DuckDB queries (2026-05-01)

**Context:** `emit.py` collection functions.

**Mistake:** Called `_fetch_rows(conn, table_name, '"_cifflow_block_id" = ?', ...)` once per block per table — 19,500 queries for `second.cif`.

**Fix:** Added `_EmitCache` that pre-fetches all rows per table once at pass start. Rows indexed by `block_id`, PK tuple, and flat list. All subsequent lookups are pure in-memory dict operations.

**Rule:** Any emit pass that loops (blocks × tables) and queries DuckDB per iteration is O(blocks × tables) queries. Pre-fetch all tables once; serve lookups from an in-memory index.

---

## Lesson 117 — DuckDB migration audit: most modules were already DuckDB; only `test_check_fidelity.py` needed changes (2026-05-01)

**Context:** Post-migration audit of non-ingest files.

**Finding:** All source files already fully DuckDB. Only `test_check_fidelity.py` retained SQLite: 6 test helpers used `sqlite3.connect(':memory:')` as a duck-typed substitute.

**Fix:** Replaced with `duckdb.connect()`, removed `row_factory`, changed `sqlite3.OperationalError` mocks to `Exception`, `REAL` → `DOUBLE`. `test_schema.py` kept as-is — correctly uses SQLite to validate `emit_create_statements` SQLite DDL output.

**Rule:** When using a DB connection in a test as a duck-typed fixture, prefer the actual target DB type. Duck typing breaks as soon as a DB-specific API call is added.

---

## Lesson 118 — Propagation links must resolve transitively across block boundaries (2026-05-03)

**Context:** `_run_fk_fill_pass` in `duckdb_ingest.py`; ALL_BLOCKS re-ingest.

**Mistake:** Resolved propagation links only one level deep. In ALL_BLOCKS output, each category is a separate CIF block with a different `_cifflow_block_id`. A block-scoped FK fill looking for `pd_data` rows with `_cifflow_block_id = 'pd_meas_...'` found nothing (those rows are in `'pd_data_...'`), triggering UUID fallbacks and 4× row duplication.

**Fix:** Transitive resolution up to 8 levels. For each level, three block-scoped subqueries (same-loop/iter, scalars-loop, any-row-in-block) are added to the COALESCE.

**Rule:** Propagation links can form chains (A → B → C). Always follow the full transitive chain. Include all reachable ancestors in the COALESCE so any level sharing the current `_cifflow_block_id` can satisfy the lookup.

**Diagnostic note:** Add `[DIAG]` checkpoints before/after each FK fill pass to count raw table rows. If spurious rows appear after the composite FK stub phase (not during fill passes), the bug is in stub creation, not fill logic.

---

## Lesson 119 — `_loop_id`/`_iter_idx` do not survive to final structured tables (2026-05-05)

  **Context:** `duckdb_ingest.py` `_run_merge_tables`; `emit.py` `_compute_original_category_order`.

  **Mistake:** Assumed `_loop_id` was available in rows fetched from final structured tables. It exists only in `_raw_*` staging tables, which are dropped at the end of `_run_merge_tables`.

  **Fix:** Created `_loop_groups` infrastructure table. Populated it from `_raw_*` before each drop; queried it at emit time for union-find grouping.

  **Rule:** Any information that exists only in `_raw_*` staging tables is inaccessible at emit time. If it is needed downstream, it must be written to a persistent infrastructure table
  before `_raw_*` is dropped.

  **Diagnostic note:** When a new emit-time feature requires per-row ingest metadata, first verify the metadata survives ingest by querying `information_schema.tables` and spot-checking the
  relevant columns in a real connection.

  ---

  ## Lesson 120 — `preferred_category_order` must yield to user plan specs (2026-05-05)

  **Context:** `_render_block` in `emit.py`; ORIGINAL mode category ordering.

  **Mistake:** `preferred_category_order` (computed loop groupings) unconditionally replaced the user-supplied `BlockSpec` in `effective_spec`, so any user plan provided to ORIGINAL mode was
  silently discarded — including wildcard expansion and its associated warnings.

  **Fix:** Changed the override condition to `if data.preferred_category_order and spec is None`, so computed groupings only apply when the user has not provided a plan.

  **Rule:** Computed "default" ordering must never silently override an explicit user plan. Gate any auto-generated `preferred_category_order` application on `spec is None`; if both exist and
   must coexist, merge them explicitly rather than letting one shadow the other.

  ---

  ## Lesson 122 — ORIGINAL mode must ignore `OutputPlan`; use GROUPED for custom ordering (2026-05-05)

  **Context:** `emit()` in `emit.py`; ORIGINAL vs GROUPED mode semantics.

  **Mistake:** ORIGINAL mode passed the user-supplied `OutputPlan` to `_sort_and_merge`, which could reorder blocks and assign spec-based rendering that bypassed `preferred_category_order`. This silently broke ORIGINAL's fidelity guarantee.

  **Fix:** Added a dedicated ORIGINAL branch in `emit()` that always produces `[(b, None) for b in raw_blocks]` and emits a `UserWarning` if a plan was supplied.

  **Rule:** ORIGINAL mode's contract is to reconstruct blocks exactly as they were before ingestion — block order, loop groupings, and all. Any user customisation belongs in GROUPED mode. When adding a mode-specific bypass in `emit()`, handle it before the `_sort_and_merge` call, not inside `_render_block`.

  **Corollary:** When updating tests that use `OutputPlan`, pick the mode that matches the feature under test: GROUPED or ONE_BLOCK for plan features, ORIGINAL only when testing fidelity reconstruction. A blanket `replace_all` across emit mode strings will corrupt tests in other classes — edit each class individually.

  ---

  ## Lesson 121 — Positional join by `_cifflow_row_id` is equivalent to `_iter_idx` for same-block source loops (2026-05-05)

  **Context:** `_render_original_loop_group` in `emit.py`.

  **Finding:** Rows from different DDLm categories that came from the same source `loop_` block have the same count, are assigned sequential `_cifflow_row_id` values within the block, and
  appear in the same original order. Sorting each table's rows by `_cifflow_row_id` and zipping by index is exactly equivalent to joining by `_iter_idx`.

  **Rule:** When `_iter_idx` is unavailable in final tables, `_cifflow_row_id` order within a `_cifflow_block_id` is a safe positional proxy for source-loop row correspondence — provided the
  tables are known to originate from the same source loop (established via `_loop_groups`).

  ---

  ## Lesson 122 — `_render_merge_group` is oblivious to ORIGINAL-mode FK-PK suppression (2026-05-05)

  **Context:** `_render_original_loop_group` → `_render_merge_group` delegation path; `_suppressed_fk_pk_cols`.

  **Mistake:** `_render_original_loop_group` computed suppressed FK-PK columns and removed them from `per_table` column lists, but then delegated to `_render_merge_group` which re-read
  columns from `table_rows` independently — discarding all suppression work.

  **Fix:** Added `suppress_pk_cols: set[str] | None = None` parameter to `_render_merge_group`. The caller computes suppressed cols (from the first table, since PKs are shared) and passes
  them; `_render_merge_group` excludes them from `pk_in_first`.

  **Rule:** When a rendering function re-derives its own column list from raw data, any pre-filtering done by the caller is lost. Either pass the pre-filtered columns explicitly, or add a
  suppression parameter to the inner function.

  ---

  ## Lesson 123 — `_cifflow_row_id` is per-table, not globally comparable across tables (2026-05-05)

  **Context:** `_compute_original_category_order` in `emit.py`; ORIGINAL mode interleaving of Set scalars and Loop categories.

  **Mistake:** Attempted to use `_cifflow_row_id` values from different tables as a global ordering key to interleave Set categories with Loop categories. All Set scalar rows receive `row_id=1`; loop rows start at 1 within each table. The values are not comparable across tables.

  **Fix:** Introduced a shared `event_counter` in `load_block_data` (Python ingest path) that increments for each new Set-table first-encounter and each loop. These event positions are stored in `_loop_groups` as `min_row_id` via `executemany` after `flush_table_batches`. At emit time, `_compute_original_category_order` queries `_loop_groups` and uses these event positions as the sort key for all categories.

  **Rule:** Never use `_cifflow_row_id` as an ordering key across different tables — it is scoped to a single table's row batch and resets for each table. If intra-block ordering metadata is needed at emit time, it must be recorded explicitly during ingest and stored in a persistent infrastructure table.

  ---

  ## Lesson 124 — Winning-block column provenance is not tracked; extra columns appear in shared Set rows (2026-05-05)

  **Context:** `_fetch_rows_for_block` in `emit.py`; ORIGINAL mode output for shared Set categories (e.g. `pd_phase`, `pd_diffractogram`).

  **Finding:** `_tag_presence` records non-winning block contributions per column, so `_fetch_rows_for_block` can mask them for non-owning blocks. However, it does NOT record which columns were contributed by the winning block itself. A Set row "owned" by block A is returned fully unmasked, including columns contributed by other blocks B, C that happened to win that row for those columns.

  **Known gap:** ORIGINAL output for shared Set rows may include extra columns not present in the original source block. Fixing this requires tracking per-column winning-block provenance in `_populate_tag_presence` and applying masking to owned rows — a significant expansion of `_tag_presence` semantics.

  **Rule:** Record this as a known gap rather than attempting a partial fix. The overall ordering is correct; only column presence within shared rows is slightly over-inclusive.
