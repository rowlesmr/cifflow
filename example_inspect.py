"""
cifflow — inspect workflow
==============================
Demonstrates every function in the cifflow.inspect family.

Each inspector pretty-prints the internal state of one pipeline layer.
They are zero-overhead when not called; import them only when debugging.

Run from the repository root:
    python example_inspect.py
"""

import pathlib

ROOT    = pathlib.Path(__file__).parent
DIC_DIR = ROOT / 'data' / 'dictionaries'

from cifflow.inspect import (
    inspect_lexer,
    inspect_parse,
    ParseHandler,
    inspect_model,
    inspect_schema,
    inspect_ingest,
    TraceEvent,
)


# ---------------------------------------------------------------------------
# Section 1 — inspect_lexer
# ---------------------------------------------------------------------------
# Shows the raw token stream: one line per token with its TokenType, ValueType,
# line/column, and raw content.  Lexical errors are flagged inline.

print('=' * 60)
print('Section 1 — inspect_lexer')
print('=' * 60)

CIF_SOURCE = """\
#\\#CIF_2.0
data_example
_cell.length_a  3.992
loop_
  _atom_site.label
  _atom_site.fract_x
  Se1  0.0
  Se2  0.5
"""

inspect_lexer(CIF_SOURCE)

# Accepts a pathlib.Path or open file object as well:
#   inspect_lexer(pathlib.Path('my_file.cif'))
#   inspect_lexer(open('my_file.cif'))


# ---------------------------------------------------------------------------
# Section 2 — inspect_parse
# ---------------------------------------------------------------------------
# Shows the token stream followed by the parser event stream.  Use
# show_tokens=False to suppress the token stream when you only care about
# events.  Use show_values=False to reduce noise for large loop tables.

print('=' * 60)
print('Section 2 — inspect_parse')
print('=' * 60)

inspect_parse(
    CIF_SOURCE,
    show_tokens=False,   # suppress token stream; events only
    show_values=True,    # show add_value lines (set False for large files)
)


# ---------------------------------------------------------------------------
# Section 3 — ParseHandler (forwarding)
# ---------------------------------------------------------------------------
# ParseHandler wraps any CifParserEvents handler and prints all events while
# forwarding them downstream.  Useful for inserting tracing into an existing
# parse pipeline.

print('=' * 60)
print('Section 3 — ParseHandler with forwarding')
print('=' * 60)

from cifflow.cifmodel.builder import CifBuilder
from cifflow.parser.parser import CifParser

builder = CifBuilder(on_error=lambda e: None)
CifParser(ParseHandler(builder, show_values=False)).parse(CIF_SOURCE)
cif = builder.result
print(f'  (CifFile built: {cif.blocks})')
print()


# ---------------------------------------------------------------------------
# Section 4 — inspect_model
# ---------------------------------------------------------------------------
# Shows the full pipeline result: token stream, parser events, and a tabular
# CifFile summary with loops displayed as aligned tables.

print('=' * 60)
print('Section 4 — inspect_model')
print('=' * 60)

inspect_model(
    CIF_SOURCE,
    show_tokens=False,   # suppress token stream
    show_values=False,   # suppress add_value lines from parser events
)


# ---------------------------------------------------------------------------
# Section 5 — inspect_schema
# ---------------------------------------------------------------------------
# Summarises the DuckDB schema derived from a DDLm dictionary.
# Accepts a SchemaSpec, a DdlmDictionary, a pathlib.Path, or a raw dic string.

print('=' * 60)
print('Section 5 — inspect_schema')
print('=' * 60)

# From a raw DDLm dictionary source string:
MINI_DIC = """\
#\\#CIF_2.0
data_MINI

save_CELL
  _definition.id       CELL
  _definition.scope    Category
  _definition.class    Set
  _name.category_id    cell
save_

save_cell.length_a
  _definition.id       '_cell.length_a'
  _definition.class    Attribute
  _name.category_id    cell
  _name.object_id      length_a
  _type.contents       Real
save_

save_ATOM_SITE
  _definition.id       ATOM_SITE
  _definition.scope    Category
  _definition.class    Loop
  _name.category_id    atom_site
  _category_key.name   '_atom_site.label'
save_

save_atom_site.label
  _definition.id       '_atom_site.label'
  _definition.class    Attribute
  _name.category_id    atom_site
  _name.object_id      label
  _type.purpose        Key
  _type.contents       Text
save_

save_atom_site.fract_x
  _definition.id       '_atom_site.fract_x'
  _definition.class    Attribute
  _name.category_id    atom_site
  _name.object_id      fract_x
  _type.contents       Real
save_
"""

inspect_schema(MINI_DIC)

# With DDL:
#   inspect_schema(MINI_DIC, show_ddl=True)

# From a file path (imports resolved from the same directory):
#   inspect_schema(pathlib.Path('data/dictionaries/cif_core.dic'))

# From a pre-loaded DdlmDictionary:
from cifflow.dictionary.loader import DictionaryLoader
from cifflow.dictionary.schema import generate_schema

dic    = DictionaryLoader().load(MINI_DIC)
schema = generate_schema(dic)
#   inspect_schema(dic)     # DdlmDictionary accepted directly
#   inspect_schema(schema)  # SchemaSpec accepted directly


# ---------------------------------------------------------------------------
# Section 6 — inspect_ingest
# ---------------------------------------------------------------------------
# Runs ingestion and pretty-prints a diagnostic trace: semantic warnings,
# errors, and FK violations.  Returns a list[TraceEvent] for programmatic use.

print('=' * 60)
print('Section 6 — inspect_ingest')
print('=' * 60)

from cifflow import build

cif, parse_errors = build(CIF_SOURCE)
if parse_errors:
    for e in parse_errors:
        print(f'  [parse error] {e.message}')

events: list[TraceEvent] = inspect_ingest(
    cif,
    schema=schema,          # pass None to route everything to _cif_fallback
    propagate_fk=False,
)

# Programmatic use of the returned trace:
warnings    = [e for e in events if e.kind == 'warning']
errors      = [e for e in events if e.kind == 'error']
fk_viols    = [e for e in events if e.kind == 'fk_violation']

print(f'  Captured: {len(warnings)} warning(s), '
      f'{len(errors)} error(s), '
      f'{len(fk_viols)} FK violation(s)')

# TraceEvent fields: kind, detail, block_id, table, tag
for ev in events:
    print(f'    {ev.kind:12s}  {ev.detail[:60]}')

print()
print('Done.')
