# pycifparse

A Python library for parsing, storing, and outputting Crystallographic Information Files (CIF).

**Python ≥ 3.10 · Apache 2.0 · v0.0.1 · pre-release (not yet on PyPI)**

---

## What it does

- Parses CIF 1.1 and CIF 2.0 files, including all string types (triple-quoted, multiline text fields, embedded quotes) and save frames
- Loads DDLm dictionaries with full `_import.get` resolution, producing a typed schema
- Ingests parsed CIF data into SQLite using the dictionary-derived schema: one table per category, foreign keys enforced, unknown tags routed to a fallback tier
- Emits valid CIF from a populated database in four modes: ORIGINAL, GROUPED, ONE_BLOCK, ALL_BLOCKS
- Trusts the user. If you pass in multiple blocks, the program assumes they all belong together, and, failing key value clashes, can be interpreted as a single database/experiment.
- Visualises a schema as a Graphviz DOT string or a self-contained interactive HTML file

---

## Key properties

**Error-tolerant.** The parser never raises on malformed input. Every structural problem produces an explicit error event; parsing continues and all recoverable data is preserved.

**No silent data loss.** Duplicate tag values are preserved. Tags not mapped by the dictionary go to a fallback table, not a discard pile.

**Round-trip fidelity.** For well-formed input, emitted CIF re-parses to the same data. All values are stored and emitted as raw strings; `ValueType` provenance (placeholder `.` and `?` vs quoted equivalents) is preserved throughout.

**Streaming and low-memory.** The parser is event-driven. The ingestion layer processes events incrementally and writes to SQLite in bounded batches.

**No runtime dependencies.** Only the Python standard library is required.

---

## Installation

pycifparse is not yet on PyPI. Install directly from source:

```
git clone https://github.com/rowlesmr/pycifparse.git
cd pycifparse
pip install -e ".[dev]"
```

---

## Quick start

### Parse a CIF file

```python
from pycifparse import build

text = open('structure.cif', encoding='utf-8').read()
cif, errors = build(text)   # never raises; errors is a list[ParseError]

for block_name in cif.blocks:
    block = cif[block_name]
    print(f'{block_name}: {len(block.tags)} tags, {len(block.loops)} loops')
```

The best way to resolve errors is to inspect the list of errors, edit the 
file accordingly, and try again. We didn't want to make assumptions on 
how to correct the errors for you.




### Full pipeline: dictionary → SQLite → CIF

```python
import sqlite3
from pycifparse import (
    DictionaryLoader, directory_resolver,
    generate_schema, apply_schema, apply_fallback_schema,
    build, ingest, emit, EmitMode,
)
from pycifparse.types import CifVersion

# 1. Load dictionary
loader = DictionaryLoader(resolver=directory_resolver('data/dictionaries'))
dictionary = loader.load(open('data/dictionaries/cif_core.dic', encoding='utf-8').read(),
                         base_uri='cif_core.dic')

# 2. Derive SQLite schema
schema = generate_schema(dictionary)

# 3. Parse CIF
cif, errors = build(open('structure.cif', encoding='utf-8').read())

# 4. Ingest into SQLite
conn = sqlite3.connect('output.db')
conn.isolation_level = None
apply_schema(conn, schema)
apply_fallback_schema(conn)
ingest(cif, conn, schema)

# 5. Emit CIF
output = emit(conn, schema, mode=EmitMode.ORIGINAL, version=CifVersion.CIF_2_0)
open('output.cif', 'w', encoding='utf-8').write(output)
```

See `example_workflow.py` in the repository root for a fully annotated end-to-end demonstration covering all four emission modes, compactification, type-cast export, and fidelity checking.

The full API reference is in [`docs/api.md`](docs/api.md).

---

## Architecture

```
Parser → Event Stream → IR → Dictionary-aware Mapping → SQLite → Output/API
```

| Layer | Responsibility |
|---|---|
| Lexer | Tokenisation, `ValueType` assignment |
| Parser | Token sequence interpretation, error recovery, event emission |
| IR (CIF model) | Event accumulation, loop validation, multiline text transformation |
| Dictionary | DDLm parsing, schema derivation |
| SQLite | Persistent storage: structured tables when a dictionary is present, fallback tier otherwise |
| Output | Valid CIF regeneration; Python/NumPy/pandas API surface |

Layer responsibilities are strictly separated. The parser does not know about the dictionary. The dictionary does not know about the IR. The output layer only reads from SQLite.

---

## Status

All stages are complete and tested.

| Stage | Feature |
|---|---|
| 1–2 | CIF 1.1 and 2.0 parser + IR (CIF model) |
| 3 | DDLm dictionary loading (`_import.get`, alias resolution, deprecation) |
| 4 | SQLite schema generation (Set/Loop → tables, PKs, FKs, bridge columns, fallback tier) |
| 5 | SQLite ingestion: structured tables + fallback tier; FK propagation; error recovery |
| 6 | CIF emission (ORIGINAL, GROUPED, ONE_BLOCK, ALL_BLOCKS); pretty-print; line-length enforcement; decimal alignment; schema visualisation |

---

## Development

Run the fast test suite (excludes tests that load large real-world CIF files):

```
pytest -m "not slow"
```

Run the full suite including slow tests:

```
pytest
```

---

## License

Apache 2.0. See `LICENSE`.

The bundled JavaScript files (`viz.js` 2.1.2 and `svg-pan-zoom` 3.6.1) used by
`visualise_schema_html` are MIT-licensed. Licence notices are in
`src/pycifparse/dictionary/js/LICENSES.txt`.
