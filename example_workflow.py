"""
pycifparse — example workflow
==============================
Demonstrates the full pipeline from dictionary loading through SQLite ingestion.

All function arguments are shown explicitly so you can see every available
option without consulting the API reference.

Run from the repository root:
    python example_workflow.py

Output files are written to the current directory:
    cif_core_cache.json     — serialised dictionary (avoids re-parsing on reuse)
    output.db               — SQLite database ready for DB Browser for SQLite
"""

import pathlib
import sqlite3
import sys

# ---------------------------------------------------------------------------
# Configuration — edit these paths to suit your data
# ---------------------------------------------------------------------------

ROOT = pathlib.Path(__file__).parent

# Dictionary
DIC_DIR   = ROOT / 'data' / 'dictionaries'
DIC_FILE  = DIC_DIR / 'cif_core.dic'
DIC_CACHE = ROOT / 'cif_core_cache.json'   # JSON cache; delete to force re-parse

# CIF file to ingest
CIF_FILE = ROOT / 'tests' / 'cif_files' / 'one_structure.cif'

# Output database (opened directly as a file so DB Browser can open it)
DB_FILE = ROOT / 'output.db'

# When True: load the dictionary from DIC_CACHE if it exists (fast).
# When False: always parse DIC_FILE from scratch (slower but guaranteed fresh).
USE_CACHE = True


# ---------------------------------------------------------------------------
# Step 1 — Load the dictionary
# ---------------------------------------------------------------------------
# DictionaryLoader resolves _import.get directives inside the dictionary file.
# directory_resolver maps URI filenames to files in a local directory.

from pycifparse import (
    DictionaryLoader,
    directory_resolver,
    load_dictionary,
    save_dictionary,
    generate_schema,
    apply_schema,
    apply_fallback_schema,
    build,
    ingest,
    resolve_tag,
    compactify_database,
)

print('=== Step 1: Load dictionary ===')

dic_warnings: list[str] = []

def _on_dic_warning(message: str) -> None:
    dic_warnings.append(message)
    print(f'  [dictionary warning] {message}')


resolver = directory_resolver(
    DIC_DIR,    # directory to search for imported constituent files
)

if USE_CACHE and DIC_CACHE.exists():
    print(f'  Loading cached dictionary from {DIC_CACHE}')
    try:
        dictionary = load_dictionary(
            DIC_CACHE,   # path: str | pathlib.Path
        )
    except ValueError as exc:
        print(f'  Cache invalid ({exc}); falling back to full parse')
        dictionary = None
else:
    dictionary = None

if dictionary is None:
    print(f'  Parsing dictionary from {DIC_FILE}')
    loader = DictionaryLoader(
        resolver=resolver,      # SourceResolver used for _import.get
        on_warning=_on_dic_warning,  # called for non-fatal issues during load
    )
    dictionary = loader.load(
        DIC_FILE.read_text(encoding='utf-8'),
        base_uri=DIC_FILE.name,   # URI hint used when resolving relative imports
    )
    print(f'  Saving dictionary cache to {DIC_CACHE}')
    save_dictionary(
        dictionary,   # DdlmDictionary to serialise
        DIC_CACHE,    # output path: str | pathlib.Path
    )

print(f'  Dictionary: {dictionary.name!r}  '
      f'({len(dictionary.items)} items, {len(dictionary.categories)} categories)')
if dic_warnings:
    print(f'  {len(dic_warnings)} warning(s) during load')


# ---------------------------------------------------------------------------
# Step 2 — Inspect a tag (optional; shows resolve_tag usage)
# ---------------------------------------------------------------------------

print('\n=== Step 2: Spot-check a tag via resolve_tag ===')

for tag in ('_cell.length_a', '_atom_site.fract_x', '_unknown.nonexistent'):
    resolved = resolve_tag(
        tag,         # tag name to look up (case-insensitive)
        dictionary,  # DdlmDictionary to search
    )
    if resolved is None:
        print(f'  {tag!r:40s} -> unknown (will go to _cif_fallback)')
    else:
        alias_note = f' (alias for {resolved.definition_id!r})' if resolved.was_alias else ''
        depr_note  = ' [DEPRECATED]' if resolved.is_deprecated else ''
        print(f'  {tag!r:40s} -> {resolved.category_id}.{resolved.object_id}'
              f'{alias_note}{depr_note}')


# ---------------------------------------------------------------------------
# Step 3 — Generate schema
# ---------------------------------------------------------------------------
# SchemaSpec describes every structured table, its columns, PKs, and FKs.
# It is derived from the DdlmDictionary and used by apply_schema and ingest.

print('\n=== Step 3: Generate schema ===')

schema = generate_schema(
    dictionary,   # DdlmDictionary; must have been loaded with DictionaryLoader
)

if schema.warnings:
    for w in schema.warnings:
        print(f'  [schema warning] {w}')

print(f'  {len(schema.tables)} structured tables')
for table_name, table in sorted(schema.tables.items()):
    col_names = [c.name for c in table.columns if not c.is_synthetic]
    fk_count  = len(table.foreign_keys)
    print(f'    {table_name:30s}  PK={table.primary_keys}  '
          f'cols={len(col_names)}  FKs={fk_count}')


# ---------------------------------------------------------------------------
# Step 4 — Parse the CIF file
# ---------------------------------------------------------------------------
# build() auto-detects CIF version from the magic line (#\#CIF_2.0 / #\#CIF_1.1).
# It returns (CifFile, list[ParseError]).  Parsing never raises on bad input.

print(f'\n=== Step 4: Parse CIF file ({CIF_FILE.name}) ===')

cif, parse_errors = build(
    CIF_FILE.read_text(encoding='utf-8'),
    mode='pad',   # 'pad'    — incomplete loop rows padded with '?' (default)
                  # 'strict' — stop accumulating on first semantic error
)

if parse_errors:
    for e in parse_errors:
        print(f'  [{e.error_type}] line {e.line}: {e.message}')
else:
    print('  No parse errors')

print(f'  Blocks: {cif.blocks}')
for block_name in cif.blocks:
    block = cif[block_name]
    print(f'    {block_name!r:35s}  '
          f'{len(block.tags)} tags, {len(block.loops)} loops')


# ---------------------------------------------------------------------------
# Step 5 — Set up the database and ingest
# ---------------------------------------------------------------------------
# apply_schema creates the structured tables and enables FK enforcement.
# apply_fallback_schema creates _cif_fallback, _block_dataset_membership,
# and _validation_result; must be called on every database.
#
# We connect directly to a file so the result can be opened in DB Browser.

print(f'\n=== Step 5: Create database ({DB_FILE.name}) ===')

if DB_FILE.exists():
    DB_FILE.unlink()   # start fresh each run

conn = sqlite3.connect(str(DB_FILE))
conn.isolation_level = None   # autocommit off; ingest manages its own transaction

apply_schema(
    conn,                 # open sqlite3.Connection
    schema,               # SchemaSpec from generate_schema
    drop_existing=False,  # True -> DROP each table before recreating it
)

apply_fallback_schema(
    conn,                 # same connection
    drop_existing=False,  # True -> DROP fallback tables before recreating them
)

print('  Schema applied')


print('\n=== Step 6: Ingest CIF data ===')

ingest_warnings: list[str] = []

def _on_ingest_error(message: str) -> None:
    ingest_warnings.append(message)

semantic_errors = ingest(
    cif,                          # CifFile from build()
    conn,                         # connection with schema already applied
    schema,                       # SchemaSpec; pass None to route all tags to fallback
    propagate_fk=False,           # True -> fill missing non-key FK columns from
                                  #        block context (fk_accumulator).
                                  # Stub parent rows are always created for any FK
                                  # column that has a value, regardless of this flag.
    dataset_id=None,              # str -> ingest only blocks belonging to that dataset
                                  # None -> ingest all (raises ValueError on conflict)
    on_error=_on_ingest_error,    # non-fatal semantic error callback
)

all_warnings = ingest_warnings + semantic_errors
if all_warnings:
    print(f'  {len(all_warnings)} semantic warning(s):')
    for w in all_warnings[:10]:
        print(f'    {w}')
    if len(all_warnings) > 10:
        print(f'    ... and {len(all_warnings) - 10} more')
else:
    print('  No semantic warnings')

# Quick row-count summary
tables_with_rows = []
for table_name in schema.tables:
    try:
        count = conn.execute(
            f'SELECT COUNT(*) FROM "{table_name}"'
        ).fetchone()[0]
        if count:
            tables_with_rows.append((table_name, count))
    except sqlite3.OperationalError:
        pass

fallback_count = conn.execute('SELECT COUNT(*) FROM _cif_fallback').fetchone()[0]

print(f'\n  Structured table rows:')
for name, count in sorted(tables_with_rows, key=lambda x: -x[1])[:15]:
    print(f'    {name:30s}  {count:>6} row(s)')
if not tables_with_rows:
    print('    (none)')
print(f'  Fallback tier (_cif_fallback): {fallback_count} row(s)')
print(f'\n  Database saved to: {DB_FILE}')


# ---------------------------------------------------------------------------
# Step 7 — Compact export (optional; recommended for distribution)
# ---------------------------------------------------------------------------
# compactify_database() copies src into a new file, dropping empty tables and
# all-NULL columns.  The three fallback-tier tables are always present.
# Returns a list of info messages describing what was removed.

print('\n=== Step 7: Compact export ===')

COMPACT_DB_FILE = ROOT / 'output_compact.db'
if COMPACT_DB_FILE.exists():
    COMPACT_DB_FILE.unlink()

compact_conn = sqlite3.connect(str(COMPACT_DB_FILE))
compact_conn.isolation_level = None

compact_messages = compactify_database(
    src=conn,           # source connection (already populated by ingest)
    dst=compact_conn,   # destination connection (must be empty)
    schema=schema,      # SchemaSpec used when src was populated
)

compact_conn.close()

if compact_messages:
    for m in compact_messages:
        print(f'  {m}')
else:
    print('  Nothing dropped — all tables and columns populated')
print(f'  Compact database saved to: {COMPACT_DB_FILE}')


# ---------------------------------------------------------------------------
# Step 8 — convert_database (not yet available; shown for future reference)
# ---------------------------------------------------------------------------
# convert_database() copies a TEXT-storage database to a new file and casts
# each column to the type indicated by ColumnDef.type_contents.
# CIF sentinels '.' and '?' are converted to NULL.
# It is planned for Stage 5 and not yet part of the public API.

# Uncomment when available:
#
# from pycifparse import convert_database
#
# CONVERTED_DB_FILE = ROOT / 'output_typed.db'
# if CONVERTED_DB_FILE.exists():
#     CONVERTED_DB_FILE.unlink()
#
# dst_conn = sqlite3.connect(str(CONVERTED_DB_FILE))
# dst_conn.isolation_level = None
#
# coercion_warnings = convert_database(
#     src=conn,                        # source TEXT-storage connection
#     dst=dst_conn,                    # destination connection (empty)
#     schema=schema,                   # SchemaSpec for type information
#     on_coercion_failure='null',      # 'null'  -> failed cast -> NULL (default)
#                                      # 'keep'  -> leave TEXT value unchanged
#                                      # 'error' -> raise on first failure
# )
# dst_conn.close()
#
# if coercion_warnings:
#     print(f'  {len(coercion_warnings)} coercion warning(s) in typed database')
# print(f'  Typed database saved to: {CONVERTED_DB_FILE}')


# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

conn.close()
print('\nDone.')
print(f'  Open {DB_FILE} in DB Browser for SQLite to inspect the results.')
