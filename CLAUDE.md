# cifflow — Project Context

## Project Overview

A Python library for parsing, storing, and outputting Crystallographic Information Files (CIF).
The system is streaming, event-driven, dictionary-aware, and designed for correctness above all else.
All major development stages are complete. The system is in maintenance and upkeep mode.

Full specifications are in `prompts/`. Reference material (CIF specs, grammars) is in `references/`.
When in doubt about CIF syntax or behaviour, consult `references/` before implementing.

---

## Architecture

```
Parser -> Event Stream -> IR -> Dictionary-aware Mapping -> DuckDB -> Output/API
```

Layer responsibilities are strictly separated and must remain so. If a proposed change blurs a
boundary, raise it explicitly before implementing.

| Layer | Module(s) | Responsibility |
|-------|-----------|----------------|
| **Lexer** | `lexer/` | Tokenisation, ValueType/TokenType assignment only |
| **Parser** | `parser/` | Token sequence interpretation, event emission, error events |
| **IR** | `cifmodel/` | Event accumulation, loop validation, CifFile/CifBlock/CifSaveFrame |
| **Dictionary** | `dictionary/` | DDLm parsing, SchemaSpec generation |
| **Ingest** | `ingestion/` | DuckDB staging, merge, FK propagation, final table population |
| **Output** | `output/` | CIF regeneration (emit.py, quote.py) |
| **Validation** | `validation/` | Observation only — never gates processing |
| **Fidelity** | `fidelity/` | Round-trip correctness checking |
| **Inspect** | `inspect/` | Human-readable database inspection |
| **Database** | `database/` | compact.py (type coercion), DuckDB connection helpers |

---

## Non-Negotiable Constraints

These must never be violated under any circumstances:

1. No silent data loss
2. All parsed values emitted as raw strings; ValueType is assigned by the lexer only and never modified downstream
3. Event ordering must exactly match file order
4. Duplicate tag values must be preserved — never overwritten
5. Parser must not crash on malformed input; all malformed constructs generate explicit `on_error` events
6. When no dictionary is provided, all tags route to `_cif_fallback`; no data is discarded
7. The output layer must never emit invalid CIF
8. Validation is an observation layer — `validate()` reports violations but never raises or blocks; `ingest()` must never call `validate()` internally

---

## Priorities (in order)

1. Correctness and data preservation
2. Error tolerance and recovery
3. Streaming / low-memory operation
4. Near-linear performance scaling
5. Grammar formality

Optimise only after correctness is established.

---

## Guiding Principle

> Be liberal in what you accept, strict in what you emit.

- Correctness and transparency of errors above all
- Avoid unnecessary abstraction
- Make every change as simple as possible; prefer deleting lines to adding them
- Find root causes; no temporary fixes
- Only touch what is necessary

---

## Key Invariants

These encode non-obvious design decisions that are easy to violate accidentally.

### CIF value encoding in DuckDB
All value columns store TEXT. Presence states:

| Stored value | CIF meaning |
|---|---|
| `NULL` | tag absent from block |
| `'.'` | inapplicable (bare PLACEHOLDER) |
| `'?'` | unknown (bare PLACEHOLDER) |
| `'"."'` | literal `.` in any quoted form |
| `'"?"'` | literal `?` in any quoted form |
| anything else | real value, raw string |

`_cif_fallback` additionally has a `value_type` column to distinguish bare-word from quoted values.
This encoding must be preserved across all ingest, merge, emit, and compact code paths.

### Synthetic columns
`_cifflow_block_id`, `_cifflow_row_id`, and bridge columns are synthetic — they have no DDLm
`definition_id` and must be `is_synthetic=True`. `_active_cols` in `emit.py` filters them before
rendering. Any new infrastructure column must carry the synthetic flag.

### `_cifflow_row_id` scoping
`_cifflow_row_id` is global across the entire `ingest()` call — it never resets between blocks.
For tables where `(_cifflow_block_id, _cifflow_row_id)` is not the `PRIMARY KEY`, a table-level
`UNIQUE ("_cifflow_block_id", "_cifflow_row_id")` constraint is required. Never use `_cifflow_row_id UNIQUE` alone.

### FK-PK suppression in emit
In ORIGINAL and GROUPED modes, FK-PK columns pointing to a co-emitted Set category are suppressed
from output (they are implicit from block scope). This does NOT apply to ALL_BLOCKS or ONE_BLOCK.

### `_sort_and_merge` bypassed for ALL_BLOCKS
ALL_BLOCKS skips `_sort_and_merge` entirely and preserves collector output order directly.
`_sort_and_merge` is designed for GROUPED/ORIGINAL anchor-key matching only.

### Tag category resolution
A tag's category is always `_name.category_id` from its save frame — never inferred by splitting
the tag name on `.`. The dot-notation prefix and `_name.category_id` can differ.

### SQL identifiers
All SQL identifiers (table names, column names, FK references) must be double-quoted in generated
DDL using the `_qi(name)` helper. Never interpolate bare names into SQL strings.

### `_name.linked_item_id` is not an identity tag
`_IMPORT_IDENTITY_TAGS` contains only structural identity tags (`_definition.id`,
`_name.category_id`, `_name.object_id`, `_definition.scope`, `_definition.class`, `_import.get`).
`_name.linked_item_id` is a data attribute that must be inheritable from templates — do not add
it back to the identity set.

### Real value comparison uses `Decimal`
For fidelity comparison of `Real`-typed columns, use `format(Decimal(value), 'f')` — not `float()`.
`Decimal` preserves trailing zeros (significant figures). `1.2 ≠ 1.20` in crystallography.

---

## Known Fragile Areas

Tread carefully in these areas. Mistakes here are easy and expensive to debug.

**DuckDB ingest ↔ Python merge path sync**
`duckdb_ingest.py` and the Python-path merge code must be kept in sync. A change to merge
logic, `tag_presence_rows` population, or `_cifflow_block_id` handling usually requires a
corresponding change in the other path. Check both before closing.

**Propagation link resolution**
`_run_fk_fill_pass` must follow transitive chains (up to 8 levels). In ALL_BLOCKS output, each
category is a separate CIF block with a different `_cifflow_block_id` — block-scoped fills that
only look one level deep silently fail. If row counts are unexpectedly multiplied after re-ingest,
the first place to check is propagation link resolution.

**`_flush` column union**
`_flush` must compute the union of all row keys, not just `rows[0].keys()`. Stub rows created by
`_apply_fk` start slim; later merges grow them in-place but only for rows that actually received
real data. A slim row as `rows[0]` silently omits columns.

**GROUPED remaining-blocks sweep**
The remaining-blocks pass must sweep all schema tables, not just `block_id_tables`. Tables in
keyed-anchor groups whose rows have NULL FK columns cannot be found via FK-path joins and must
fall through to the block_id sweep.

**Emit ordering**
All emit mode collectors return `list[_BlockData]` — never render during collection. Post-collection
reordering (spec matching, merge, sort) must operate on `_BlockData` objects. Do not render until
final emission order is known.

**Bridge column lookups**
Bridge lookups are keyed by `pk_val` only — not `(block_id, pk_val)`. `merged_rows` is already
dataset-scoped; adding `_cifflow_block_id` as a discriminator breaks multi-block datasets where
source and bridge rows originate from different CIF data blocks.

**Windows + DuckDB query count**
On Windows, each DuckDB `execute()` call triggers Python import machinery that is intercepted by
AV scanning (~200–500μs overhead per call). Keep DuckDB queries out of hot loops — pre-fetch all
rows at pass start and serve lookups from in-memory dicts. See `_EmitCache` in `emit.py`.

**`tag_presence_rows` population**
Every code path that produces merged rows must populate `tag_presence_rows` for non-winning block
contributions. Omitting this breaks ORIGINAL-mode emit for all shared Set rows.

---

## Round-Trip Fidelity

Round-tripping guarantees semantic fidelity, not textual fidelity. Only guaranteed for input that
produced no `on_error` events during parsing.

**Must be preserved exactly:**
- All data block and save frame names
- All tag names and values as raw strings
- ValueType provenance (PLACEHOLDER must remain unquoted on output)
- Loop structure and column order

**Never permitted in output:**
- Invalid CIF constructs
- Non-canonical magic codes
- Duplicate tags (file is not round-trippable if present)

**Fidelity normalisation rules:**
- `NULL`, `'.'`, and `'?'` are semantically equivalent for structured table comparison — treat as
  identical. This does NOT apply to `_cif_fallback`.
- Real values: compare canonical `Decimal` fixed-point form, not floats.
- `_cifflow_block_id`, `_cifflow_row_id`, and `is_synthetic=True` columns are excluded from
  round-trip comparison.

---

## Operational Reference

### Run tests
```
.venv/Scripts/python -m pytest -x -q
```

### Full suite (including slow integration tests)
```
.venv/Scripts/python -m pytest -q
```

### Fidelity check (run after any ingest or emit change)
Ingest a known-good file, emit, re-ingest, compare. `second.cif` is the primary regression target
(156 blocks, ~1.98M merged rows, exercises most code paths).

### Regenerate schema
Load `cif_pow.dic` via `DictionaryLoader`, call `generate_schema`, call
`apply_schema`. See `tests/ingestion/test_integration.py` for the reference pattern.

---

## Checklist Before Any Change

Before changing ingest, emit, schema, or fidelity code:

- [ ] Do both the DuckDB ingest path and the Python merge path need updating?
- [ ] Does any synthetic column need `is_synthetic=True`?
- [ ] Does the change affect `tag_presence_rows` population?
- [ ] Does the change affect emit ordering or `_BlockData` construction?
- [ ] Run full test suite: all 1749 tests pass
- [ ] Run fidelity check against `second.cif`

---

## Task Management

1. Before making changes, state what you plan to do and why
2. Make changes incrementally; run tests after each logical unit of work
3. After any correction or non-obvious decision, record a lesson in `tasks/lessons.md`
4. Use `tasks/todo.md` to track what's open, what's next, and what decisions are pending
5. At session end, produce a session summary containing:
   - What was done (2–4 sentences) and the current test count/pass state
   - Any new lesson entries, numbered from the current highest in `tasks/lessons.md`,
     in the standard format (Context / Mistake / Fix / Rule)
   - An updated What's Next priority list ready to paste into `tasks/todo.md`
   - Any Open Decisions that were resolved or newly deferred
