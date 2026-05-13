# cifflow — API Reference

All public symbols are importable from the top-level `cifflow` package unless
otherwise noted.

---

## Module layout

```
cifflow/
├── __init__.py           # Top-level re-exports (all public symbols)
├── types.py              # CifVersion, ValueType, ParseError, CifParserEvents
├── cifflow_core/      # PyO3 Rust extension (CifFile, CifBlock, CifSaveFrame,
│                         #   parse_cif, parse_arrow, parse_arrow_file)
├── lexer/
│   ├── lexer.py          # Lexer (internal)
│   └── tokens.py         # Token, LexerError (internal)
├── parser/
│   └── parser.py         # CifParser
├── cifmodel/
│   ├── model.py          # CifFile, CifBlock, CifSaveFrame, CifValue (re-exports from Rust)
│   ├── builder.py        # CifBuilder, build(), build_arrow(), build_arrow_file(), cif_to_arrow()
│   ├── writer.py         # CifWriter, BlockWriter, SaveFrameWriter, CifInput
│   ├── clean.py          # clean, CleanWarning
│   └── textfield.py      # transform_multiline (internal)
├── dictionary/
│   ├── ddlm_item.py      # DdlmItem
│   ├── ddlm_parser.py    # DdlmDictionary (data container)
│   ├── loader.py         # DictionaryLoader, directory_resolver, directory_path_resolver, SourceResolver
│   ├── cache.py          # save_dictionary, load_dictionary
│   ├── schema.py         # ForeignKeyDef, ColumnDef, TableDef, SchemaSpec,
│   │                     #   generate_schema, emit_create_statements,
│   │                     #   emit_fallback_create_statements
│   ├── schema_apply.py   # (stub — apply_schema/apply_fallback_schema removed; setup is internal to ingest())
│   ├── resolver.py       # ResolvedTag, resolve_tag
│   ├── visualise.py      # visualise_schema, visualise_schema_html
│   └── js/               # bundled JS package data (viz.js 2.1.2, svg-pan-zoom 3.6.1)
├── ingestion/
│   ├── ingest.py         # ingest(), IngestionError
│   └── duckdb_ingest.py  # DuckDB table setup and bulk-load helpers (internal)
├── database/
│   └── compact.py        # convert_database()
├── output/
│   ├── emit.py           # emit()
│   ├── plan.py           # EmitMode, OutputPlan, BlockSpec
│   └── quote.py          # quote(), make_text_field()
├── fidelity/
│   └── check.py          # check_fidelity()
├── inspect/
│   ├── _lexer.py         # inspect_lexer
│   ├── _parser.py        # inspect_parse, ParseHandler
│   ├── _model.py         # inspect_model
│   ├── _schema.py        # inspect_schema
│   └── _ingest.py        # inspect_ingest, TraceEvent
└── validation/
    └── _validate.py      # validate(), ValidationReport, ValidationIssue
```

---

## Example scripts

Two end-to-end example scripts live in the repository root.  They are runnable
as-is from the repository root and demonstrate the full public API in context.

### `example_workflow.py`

Full pipeline demonstration: dictionary loading → schema generation → CIF parsing
→ DuckDB ingestion → CIF emission in all four modes.

Steps covered:

1. Load `cif_pow.dic` via `DictionaryLoader` (with JSON cache)
2. Spot-check a tag via `resolve_tag`
3. Generate schema via `generate_schema`
4. Parse a CIF file via `build`
5. Ingest via `ingest` (returns a `duckdb.DuckDBPyConnection`; schema setup is internal)
6. Emit in `ORIGINAL` mode
7. Emit in `GROUPED` mode
8. Emit in `ONE_BLOCK` mode with a custom `OutputPlan`
9. Emit in `ALL_BLOCKS` mode
10. Type-cast export via `convert_database`
11. Fidelity checks for all four emit modes via `check_fidelity`

Output files written: `output_original.cif`, `output_grouped.cif`,
`output_one_block.cif`, `output_all_blocks.cif`.

### `example_fidelity.py`

Fidelity comparison demonstration: two semantically equivalent CIF files
(`multi_one.cif` — 24 blocks; `multi_one_as_oneblock.cif` — 1 block) are compared
using `check_fidelity`.

Steps covered:

1. Load `cif_pow.dic` and generate schema
2. Compare with schema (structured + fallback); writes `fidelity_report.txt`
3. Compare without schema (fallback only)
4. Self-comparison of each file (should always pass)

Output file written: `fidelity_report.txt`.
