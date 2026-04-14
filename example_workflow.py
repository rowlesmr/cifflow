"""
pycifparse — example workflow
==============================
Demonstrates the full pipeline: dictionary loading → SQLite ingestion → CIF emission.

All function arguments are shown explicitly so you can see every available
option without consulting the API reference.

Run from the repository root:
    python example_workflow.py

Output files are written to the current directory:
    cif_core_cache.json     — serialised dictionary (avoids re-parsing on reuse)
    output.db               — SQLite database ready for DB Browser for SQLite
    output_compact.db       — compacted copy (empty tables / all-NULL columns removed)
    output_original.cif     — CIF re-emitted in ORIGINAL mode (one block per source block)
    output_grouped.cif      — CIF re-emitted in GROUPED mode (grouped by Set anchor keys)
    output_one_block.cif    — CIF re-emitted in ONE_BLOCK mode (everything in one block)
    output_all_blocks.cif   — CIF re-emitted in ALL_BLOCKS mode (one block per category)
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

# Dictionary
DIC_DIR   = ROOT / 'data' / 'dictionaries'
DIC_FILE  = DIC_DIR / 'cif_pow.dic'
DIC_CACHE = ROOT / 'cif_pow_cache.json'   # JSON cache; delete to force re-parse

# CIF file to ingest
CIF_FILE = ROOT / 'tests' / 'cif_files' / 'multi_one.cif'





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
    IngestionError,
    resolve_tag,
    compactify_database,
    emit,
    EmitMode,
    OutputPlan,
    BlockSpec,
)
from pycifparse.fidelity import check_fidelity
from pycifparse.types import CifVersion

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

try:
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
except IngestionError as exc:
    # Two possible causes:
    #   • Semantic errors (key collisions with conflicting values, etc.) — detected
    #     during block processing; all errors collected before raising.
    #   • COMMIT failure (deferred FK violation, disk full, etc.) — first error is
    #     the SQLite message; subsequent entries are foreign_key_check rows.
    is_commit_failure = exc.errors and exc.errors[0].startswith('COMMIT failed:')
    if is_commit_failure:
        print(f'\n  COMMIT FAILED — database constraint violation:')
    else:
        print(f'\n  INGESTION FAILED — {len(exc.errors)} semantic error(s):')
    for i, err in enumerate(exc.errors, 1):
        print(f'    [{i:>3}] {err}')
    print('\n  The database transaction was rolled back.  No data was written.')
    conn.close()
    sys.exit(1)

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
# Step 8 — Emit CIF: ORIGINAL mode (one output block per source block)
# ---------------------------------------------------------------------------
# emit() reads the populated database and produces a valid CIF string.
# ORIGINAL is the simple inverse of ingestion: each source data_ block
# becomes one output block, in _block_id order.

print('\n=== Step 8: Emit CIF (ORIGINAL mode) ===')

ORIGINAL_CIF_FILE = ROOT / 'output_original.cif'

cif_original = emit(
    conn,                          # open sqlite3.Connection (read-only)
    schema,                        # SchemaSpec used during ingestion
    mode=EmitMode.ORIGINAL,        # one block per original _block_id (default)
    version=CifVersion.CIF_2_0,    # magic line and quoting strategy
    plan=None,                     # OutputPlan | None; None -> default ordering
    reconstruct_su=False,          # True -> merge (measurand, su) back into value(su)
    emit_defaults=True,            # True -> emit default-fill values (no-op for now)
)

ORIGINAL_CIF_FILE.write_text(cif_original, encoding='utf-8')

original_blocks = [l for l in cif_original.splitlines() if l.startswith('data_')]
print(f'  {len(original_blocks)} block(s) emitted -> {ORIGINAL_CIF_FILE.name}')
for header in original_blocks:
    print(f'    {header}')


# ---------------------------------------------------------------------------
# Step 9 — Emit CIF: GROUPED mode (grouped by Set-anchor key values)
# ---------------------------------------------------------------------------
# GROUPED traverses the FK graph (BFS) from each table to find the nearest
# Set-class ancestor.  Tables that share the same anchor key values are
# placed in the same output block, merging rows from multiple source blocks
# that carry the same Set-level identity.

print('\n=== Step 9: Emit CIF (GROUPED mode) ===')

GROUPED_CIF_FILE = ROOT / 'output_grouped.cif'

cif_grouped = emit(
    conn,
    schema,
    mode=EmitMode.GROUPED,
    version=CifVersion.CIF_2_0,
    plan=None,
    reconstruct_su=False,
    emit_defaults=True,
)

GROUPED_CIF_FILE.write_text(cif_grouped, encoding='utf-8')

grouped_blocks = [l for l in cif_grouped.splitlines() if l.startswith('data_')]
print(f'  {len(grouped_blocks)} block(s) emitted -> {GROUPED_CIF_FILE.name}')
for header in grouped_blocks:
    print(f'    {header}')


# ---------------------------------------------------------------------------
# Step 10 — Emit CIF: ONE_BLOCK mode with custom OutputPlan
# ---------------------------------------------------------------------------
# ONE_BLOCK collapses all data into a single block named 'output'.
# An OutputPlan + BlockSpec can override the default category and column
# ordering.  Categories not listed in BlockSpec.categories are appended
# alphabetically; columns not listed in BlockSpec.column_order follow
# alphabetically within their category.

print('\n=== Step 10: Emit CIF (ONE_BLOCK mode with OutputPlan) ===')

ONE_BLOCK_CIF_FILE = ROOT / 'output_one_block.cif'

# Example: emit cell parameters first (unit cell defines the coordinate system),
# then atom sites, then everything else in default order (Set-class alphabetical,
# then Loop-class alphabetical).
# Within each category: PK column(s) first, then remaining columns alphabetically.
spec = BlockSpec(
    category_order=['audit_dataset', 'structure', 'cell', 'atom_site'],
    column_order={
        'cell': ['structure_id',
                 'angle_alpha', 'angle_beta', 'angle_gamma',
                 'length_a', 'length_b', 'length_c'],
        'atom_site': ['label', 'structure_id',
                      'fract_x', 'fract_y', 'fract_z',
                      'type_symbol'],
    },
)
plan = OutputPlan(
    specs=[spec],   # single spec reused for all blocks (only one in ONE_BLOCK mode)
)

cif_one_block = emit(
    conn,
    schema,
    mode=EmitMode.ONE_BLOCK,
    version=CifVersion.CIF_2_0,
    plan=plan,
    reconstruct_su=False,
    emit_defaults=True,
)

ONE_BLOCK_CIF_FILE.write_text(cif_one_block, encoding='utf-8')

one_block_lines = [l for l in cif_one_block.splitlines() if l.startswith('data_')]
print(f'  {len(one_block_lines)} block(s) emitted -> {ONE_BLOCK_CIF_FILE.name}')

# Round-trip check: re-parse the emitted CIF and verify no errors.
cif_rt, rt_errors = build(
    cif_one_block,
    mode='strict',
)
if rt_errors:
    print(f'  WARNING: round-trip produced {len(rt_errors)} parse error(s):')
    for e in rt_errors:
        print(f'    [{e.error_type}] line {e.line}: {e.message}')
else:
    print(f'  Round-trip parse: OK  ({len(cif_rt.blocks)} block(s), no errors)')


# ---------------------------------------------------------------------------
# Step 11 — Emit CIF: ALL_BLOCKS mode (one block per Set-anchor key)
# ---------------------------------------------------------------------------
# ALL_BLOCKS mirrors GROUPED block partitioning: one output block per
# distinct Set-anchor key combination.  Set categories produce one block per
# row; Loop categories are grouped by the domain PK of the nearest Set
# ancestor.  Tables with no Set ancestor are grouped by _block_id.
# In CIF 2.0, _audit_dataset.id is injected into every block using a shared
# UUID so that a reader can identify all blocks as belonging to the same
# dataset.

print('\n=== Step 11: Emit CIF (ALL_BLOCKS mode) ===')

ALL_BLOCKS_CIF_FILE = ROOT / 'output_all_blocks.cif'

cif_all_blocks = emit(
    conn,
    schema,
    mode=EmitMode.ALL_BLOCKS,
    version=CifVersion.CIF_2_0,
    plan=None,
    reconstruct_su=False,
    emit_defaults=True,
)

ALL_BLOCKS_CIF_FILE.write_text(cif_all_blocks, encoding='utf-8')

all_blocks_headers = [l for l in cif_all_blocks.splitlines() if l.startswith('data_')]
print(f'  {len(all_blocks_headers)} block(s) emitted -> {ALL_BLOCKS_CIF_FILE.name}')
for header in all_blocks_headers:
    print(f'    {header}')

# Round-trip check: re-parse the emitted CIF and verify no errors.
cif_rt_ab, rt_ab_errors = build(cif_all_blocks, mode='strict')
if rt_ab_errors:
    print(f'  WARNING: round-trip produced {len(rt_ab_errors)} parse error(s):')
    for e in rt_ab_errors:
        print(f'    [{e.error_type}] line {e.line}: {e.message}')
else:
    print(f'  Round-trip parse: OK  ({len(cif_rt_ab.blocks)} block(s), no errors)')


# ---------------------------------------------------------------------------
# Step 12 — convert_database (not yet available; shown for future reference)
# ---------------------------------------------------------------------------
# convert_database() copies a TEXT-storage database to a new file and casts
# each column to the type indicated by ColumnDef.type_contents.
# CIF sentinels '.' and '?' are converted to NULL.

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
# Step 13 — Fidelity checks: original CIF vs each emitted output
# ---------------------------------------------------------------------------
# check_fidelity compares two CIF sources for semantic equivalence.
# It ingests both into fresh in-memory databases using the same schema and
# compares all structured tables and the fallback tier.
# ONE_BLOCK and ALL_BLOCKS combine data from all blocks into one or more
# output blocks, so they are not expected to be fully fidelity-equivalent
# to the original (different block structure).  ORIGINAL and GROUPED are
# expected to be equivalent when all data is dictionary-mapped.

print('\n=== Step 13: Fidelity checks ===')

fidelity_cases = [
    ('ORIGINAL',   ORIGINAL_CIF_FILE,   ORIGINAL_CIF_FILE.with_suffix('.fidelity.txt')),
    ('GROUPED',    GROUPED_CIF_FILE,    GROUPED_CIF_FILE.with_suffix('.fidelity.txt')),
    ('ONE_BLOCK',  ONE_BLOCK_CIF_FILE,  ONE_BLOCK_CIF_FILE.with_suffix('.fidelity.txt')),
    ('ALL_BLOCKS', ALL_BLOCKS_CIF_FILE, ALL_BLOCKS_CIF_FILE.with_suffix('.fidelity.txt')),
]

for mode_name, emitted_cif, report_path in fidelity_cases:
    report = check_fidelity(
        CIF_FILE,            # source A: original CIF file
        emitted_cif,         # source B: saved emitted CIF file
        schema,              # SchemaSpec for structured comparison
        report_file=report_path,
    )
    status = 'PASS' if report.passed else 'FAIL'
    n_mismatches = len(report.mismatches)
    print(f'  {mode_name:<12s}  {status}  '
          f'({n_mismatches} mismatch(es))  -> {report_path.name}')
    if not report.passed:
        by_kind: dict[str, list] = {}
        for m in report.mismatches:
            by_kind.setdefault(m.kind, []).append(m)
        for kind, items in sorted(by_kind.items()):
            print(f'    [{kind}]  {len(items)} mismatch(es)')
            for m in items[:3]:
                print(f'      {m.description}')
            if len(items) > 3:
                print(f'      ... and {len(items) - 3} more')


# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

conn.close()
print('\nDone.')
print(f'  {DB_FILE.name}              — SQLite database (DB Browser for SQLite)')
print(f'  {COMPACT_DB_FILE.name}      — compacted copy')
print(f'  {ORIGINAL_CIF_FILE.name}   — CIF (ORIGINAL mode)')
print(f'  {GROUPED_CIF_FILE.name}    — CIF (GROUPED mode)')
print(f'  {ONE_BLOCK_CIF_FILE.name}  — CIF (ONE_BLOCK mode)')
print(f'  {ALL_BLOCKS_CIF_FILE.name} — CIF (ALL_BLOCKS mode)')
print(f'  *.fidelity.txt              — fidelity reports for each mode')
