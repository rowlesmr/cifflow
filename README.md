# cifflow

Parse, store, validate, and emit Crystallographic Information Files (CIF).

**Python ≥ 3.10 · Apache 2.0 · v0.1.3 · [PyPI](https://pypi.org/project/cifflow/)**

---

## What it does

- Parses CIF 1.1 and CIF 2.0 files, including all string types (triple-quoted, multiline text fields, embedded quotes) and save frames
- Loads DDLm dictionaries with full `_import.get` resolution, producing a typed schema
- Focused on multi-block powder CIF files
- Ingests parsed CIF data into DuckDB using the dictionary-derived schema: one table per category, foreign keys enforced, unknown tags routed to a fallback tier
- Emits valid CIF from a populated database in four modes: ORIGINAL, GROUPED, ONE_BLOCK, ALL_BLOCKS
- Trusts the user — if you pass in multiple blocks, the program assumes they all belong together and, failing key value clashes, can be interpreted as a single database/experiment
- Constructs `CifFile` objects programmatically from Python values (`CifWriter`), and performs arbitrary edits: add/remove/rename tags, loops, blocks, and save frames
- Removes common parse-time artefacts automatically (`clean`): orphan error tags, duplicate blocks/save frames/tags, loop padding; for anything beyond these automatic fixes, use `CifWriter`
- Visualises a schema as a Graphviz DOT string or a self-contained interactive HTML file
- Returns data as Apache Arrow `RecordBatch` objects directly from the Rust parser (`build_arrow`, `build_arrow_file`)

---

## Key properties

**Error-tolerant.** The parser never raises on malformed input. Every structural problem produces an explicit error event; parsing continues and all recoverable data is preserved.

**No silent data loss.** Duplicate tag values are preserved. Tags not mapped by the dictionary go to a fallback table, not a discard pile.

**Round-trip fidelity.** For well-formed input, emitted CIF re-parses to the same data. All values are stored and emitted as raw strings; `ValueType` provenance (placeholder `.` and `?` vs quoted equivalents) is preserved throughout.

**Canonical caseless names.** Block names, save frame names, and tag names are stored in Unicode canonical caseless form (`NFC(casefold(NFD(x)))`). Lookups are automatically casefolded: `cif["ABC"]` finds a block stored as `"abc"`.

**Streaming parser.** The parser is event-driven. CIF source is consumed in a single pass; the IR accumulates events incrementally. The Rust extension provides high-throughput Arrow output without any Python file objects.

---

## Installation

```
pip install cifflow
```

`duckdb` and `pyarrow` are installed automatically.

To install from source (includes the Rust extension):

```
git clone https://github.com/rowlesmr/cifflow.git
cd cifflow
pip install -e ".[dev]"
maturin develop
```

---

## Quick start

### Parse a CIF file

```python
from cifflow import build

text = open('structure.cif', encoding='utf-8').read()
cif, errors = build(text)   # never raises; errors is a list[ParseError]

for block_name in cif.blocks:          # block names are always lowercase
    block = cif[block_name]
    print(f'{block_name}: {len(block.tags)} tags, {len(block.loops)} loops')
```

The best way to resolve errors is to inspect the list of errors, edit the
file accordingly, and try again. No assumptions are made about how to correct
errors automatically.


### Full pipeline: dictionary → DuckDB → CIF

```python
import pathlib
from cifflow import (
    DictionaryLoader, directory_resolver,
    save_dictionary, load_dictionary,
    generate_schema,
    build, ingest, emit, EmitMode,
)
from cifflow.types import CifVersion

# 1. Load dictionary (with JSON cache to avoid re-parsing on every run)
cache = pathlib.Path('cif_pow_cache.json')
resolver = directory_resolver('data/dictionaries')
if cache.exists():
    dictionary = load_dictionary(cache)
else:
    dictionary = DictionaryLoader(resolver=resolver).load(
        open('data/dictionaries/cif_pow.dic', encoding='utf-8').read())
    save_dictionary(dictionary, cache)

# 2. Derive schema
schema = generate_schema(dictionary)

# 3. Parse CIF
cif, errors = build(open('all_the_data.cif', encoding='utf-8').read())

# 4. Ingest into an in-memory DuckDB database
#    Pass a file path string to persist: ingest(cif, 'output.db', schema=schema)
conn, warnings = ingest(cif, schema=schema)

# 5. Emit CIF
output = emit(conn, schema, mode=EmitMode.ORIGINAL, version=CifVersion.CIF_2_0)
open('output.cif', 'w', encoding='utf-8').write(output)
```

See `example_workflow.py` in the repository root for a fully annotated end-to-end demonstration covering all four emission modes, type-cast export, and fidelity checking.

The full API reference is in [`docs/api.md`](docs/api.md).

---

## Architecture

```
Parser → Event Stream → IR → Dictionary-aware Mapping → DuckDB → Output/API
```

| Layer         | Responsibility |
|---------------|---|
| Lexer         | Tokenisation, `ValueType` assignment |
| Parser        | Token sequence interpretation, error recovery, event emission |
| IR (CIFModel) | Event accumulation, loop validation, multiline text transformation |
| Dictionary    | DDLm parsing, schema derivation |
| DuckDB        | Persistent storage: structured tables when a dictionary is present, fallback tier otherwise |
| Output        | Valid CIF regeneration; Python/NumPy/pandas API surface |

Layer responsibilities are strictly separated. The parser does not know about the dictionary. The dictionary does not know about the IR. The output layer only reads from DuckDB.

---

## Status

All stages are complete and tested.

| Stage | Feature |
|---|---|
| 1–2 | CIF 1.1 and 2.0 parser + IR (CIF model) |
| 3 | DDLm dictionary loading (`_import.get`, alias resolution, deprecation) |
| 4 | DuckDB schema generation (Set/Loop → tables, PKs, FKs, bridge columns, fallback tier) |
| 5 | DuckDB ingestion: structured tables + fallback tier; FK propagation; error recovery; canonical caseless name matching |
| 6 | CIF emission (ORIGINAL, GROUPED, ONE_BLOCK, ALL_BLOCKS); pretty-print; line-length enforcement; decimal alignment; schema visualisation; programmatic `CifFile` construction (`CifWriter`); cleaning parser artefacts (`clean`); type-cast export (`convert_database`); fidelity checking (`check_fidelity`); validation (`validate`) |

---

## Development

Run the fast test suite (excludes tests that load large real-world CIF files):

```
.venv/Scripts/python.exe -m pytest -m "not slow"
```

Run the full suite including slow tests:

```
.venv/Scripts/python.exe -m pytest
```

After modifying the Rust extension, recompile before running Python tests:

```
.venv/Scripts/maturin develop
```

---

## License

Apache 2.0. See `LICENSE`.

The bundled JavaScript files (`viz.js` 2.1.2 and `svg-pan-zoom` 3.6.1) used by
`visualise_schema_html` are MIT-licensed. Licence notices are in
`src/cifflow/dictionary/js/LICENSES.txt`.
