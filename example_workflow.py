"""
cifflow — example workflow
==============================
Demonstrates the full pipeline: dictionary loading → DuckDB ingestion → CIF emission.

All function arguments are shown explicitly so you can see every available
option without consulting the API reference.

Run from the repository root:
    python example_workflow.py

Output files are written to the current directory:
    cif_pow_cache.json      — serialised dictionary (avoids re-parsing on reuse)
    output.duckdb           — original ingest database (all columns VARCHAR)
    output_typed.duckdb     — typed copy (INTEGER/DOUBLE columns; sentinels → NULL)
    output_original.cif     — CIF re-emitted in ORIGINAL mode (one block per source block)
    output_grouped.cif      — CIF re-emitted in GROUPED mode (grouped by Set anchor keys)
    output_one_block.cif    — CIF re-emitted in ONE_BLOCK mode (everything in one block)
    output_all_blocks.cif   — CIF re-emitted in ALL_BLOCKS mode (one block per category)
"""

import pathlib
import sys

import time # to show execution times
from datetime import datetime

import duckdb


def print_time(msg: str, start, stop = None):
    if not stop:
        stop = time.time()
    print(f"{msg}: {stop-start:5.2f} s")

def get_now():
    print(datetime.now())
    return time.time()


# ---------------------------------------------------------------------------
# Configuration — edit these paths to suit your data
# ---------------------------------------------------------------------------

ROOT = pathlib.Path(__file__).parent

# Dictionary
DIC_DIR   = ROOT / 'data' / 'dictionaries'
DIC_FILE  = DIC_DIR / 'cif_pow.dic'
DIC_CACHE = ROOT / 'cif_pow_cache.json'   # JSON cache; delete to force re-parse

# CIF file to ingest
CIF_FILE = ROOT / 'tests' / 'cif_files' / "second_short.cif" #'multi_one.cif'  #

# Output databases
DB_FILE       = ROOT / 'output.duckdb'        # VARCHAR-storage (original ingest)
TYPED_DB_FILE = ROOT / 'output_typed.duckdb'  # typed copy (INTEGER/DOUBLE)

# When True: load the dictionary from DIC_CACHE if it exists (fast).
# When False: always parse DIC_FILE from scratch (slower but guaranteed fresh).
USE_CACHE = True


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
time_before_imports = get_now()
from cifflow import (
    DictionaryLoader,
    directory_resolver,
    load_dictionary,
    save_dictionary,
    generate_schema,
    visualise_schema,
    visualise_schema_html,
    build,
    ingest,
    IngestionError,
    resolve_tag,
    convert_database,
    emit,
    EmitMode,
    OutputPlan,
    BlockSpec,
    validate_database,
    validate,
    ValidationReport,
    DbValidationResult,
)
from cifflow.fidelity import check_fidelity
from cifflow.types import CifVersion
print_time("Done imports", time_before_imports, time.time())

# ---------------------------------------------------------------------------
# Step 1 — Load the dictionary
# ---------------------------------------------------------------------------
# DictionaryLoader resolves _import.get directives inside the dictionary file.
# directory_resolver maps URI filenames to files in a local directory.


print('=== Step 1: Load dictionary ===')
start_dictionary = get_now()

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
        resolver=resolver,           # SourceResolver used for _import.get
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

print_time("Finished dictionary", start_dictionary)

# ---------------------------------------------------------------------------
# Step 2 — Inspect a tag (optional; shows resolve_tag usage)
# ---------------------------------------------------------------------------

print('\n=== Step 2: Spot-check a tag via resolve_tag ===')
start_optional_tag = get_now()

for tag in ('_cell_length_a', '_pd_meas.2theta_range_min', '_unknown.nonexistent'):
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

print_time("Finished optional tag", start_optional_tag)

# ---------------------------------------------------------------------------
# Step 3 — Generate schema
# ---------------------------------------------------------------------------
# SchemaSpec describes every structured table, its columns, PKs, and FKs.
# It is derived from the DdlmDictionary and used by ingest().

print('\n=== Step 3: Generate schema ===')
start_generate_schema = get_now()

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

print_time("Generated schema", start_generate_schema)
start_visualise_schema = get_now()

dot = visualise_schema(schema, show_columns='sparse',
                       show_bridge=True,
                       show_parent_edges=True,
                       highlight_orphans=True,
                       highlight_components=True,
                       show_orphans=True,
                       layout='dot')
with open('schema.dot', 'w', encoding='utf-8') as f:
    f.write(dot)

html = visualise_schema_html(schema,
                             title='CIF_POW',
                             show_columns='sparse',
                             show_bridge=True,
                             show_parent_edges=False,
                             highlight_orphans=True,
                             highlight_components=False,
                             show_orphans=True,
                             show_legend=True,
                             concentrate=True,
                             hide_deprecated=True,
                             splines='ortho',
                             ranksep=1.0,
                             nodesep=0.4,
                             layout='dot')
with open('schema.html', 'w', encoding='utf-8') as f:
    f.write(html)

print_time("Finished visulise schema", start_visualise_schema)
# ---------------------------------------------------------------------------
# Step 4 — Parse the CIF file
# ---------------------------------------------------------------------------
# build() auto-detects CIF version from the magic line (#\#CIF_2.0 / #\#CIF_1.1).
# It returns (CifFile, list[ParseError]).  Parsing never raises on bad input.

print(f'\n=== Step 4: Parse CIF file ({CIF_FILE.name}) ===')
start_cif_parse = get_now()

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

print_time("Finished cif parse", start_cif_parse)


# ---------------------------------------------------------------------------
# Step 5 — Ingest into output.duckdb
# ---------------------------------------------------------------------------
# ingest() creates a file-backed DuckDB database, sets up all schema tables
# (always, even empty ones), and populates them from the parsed CifFile.
# All value columns are stored as VARCHAR; convert_database() (Step 7) adds
# typed copies.  Returns (connection, list[str]) of semantic warnings.

print(f'\n=== Step 5: Ingest into DuckDB ({DB_FILE.name}) ===')
start_ingest = get_now()


if DB_FILE.exists():
    DB_FILE.unlink()

try:
    conn, ingest_warnings = ingest(
        cif,                   # CifFile from build()
        db=duckdb.connect(     # file-backed database with reduced block size (db=None (the default) gives a fully functional in-memory connection):
            str(DB_FILE),      # 163 schema tables × 256 KB default = ~40 MB of overhead;
            config={'default_block_size': 16384},  # 16 KB → ~2 MB overhead instead
        ),
        schema=schema,         # SchemaSpec; None -> all tags to _cif_fallback
        propagate_fk=False,    # True -> fill missing non-key FK columns from block context
        dataset_id=None,       # str -> one dataset only; None -> all
    )
except IngestionError as exc:
    print(f'\n  INGESTION FAILED — {len(exc.errors)} error(s):')
    for i, err in enumerate(exc.errors, 1):
        print(f'    [{i:>3}] {err}')
    sys.exit(1)

if ingest_warnings:
    print(f'  {len(ingest_warnings)} semantic warning(s):')
    for w in ingest_warnings[:10]:
        print(f'    {w}')
    if len(ingest_warnings) > 10:
        print(f'    ... and {len(ingest_warnings) - 10} more')
else:
    print('  No semantic warnings')
print(f'  Database saved to: {DB_FILE}')

print_time("Finished ingest", start_ingest)


# ---------------------------------------------------------------------------
# Step 6 — Validate
# ---------------------------------------------------------------------------
# validate_database() runs all schema checks directly against an existing
# DuckDB connection — no second ingest needed.
# Returns list[DbValidationResult]; never raises.

print(f'\n=== Step 6: Validate ===')
start_validate = get_now()

#see also validate()

db_issues: list[DbValidationResult] = validate_database(
    conn,    # populated DuckDB connection from Step 5
    schema,  # SchemaSpec to validate against
)

SEV_ORDER = {'Error': 0, 'Warning': 1, 'Info': 2}
counts = {'Error': 0, 'Warning': 0, 'Info': 0}
for issue in db_issues:
    counts[issue.severity] += 1

passed = counts['Error'] == 0
status = 'PASSED' if passed else 'FAILED'
print(f'  Validation {status}: '
      f'{counts["Error"]} error(s), '
      f'{counts["Warning"]} warning(s), '
      f'{counts["Info"]} info(s)')

for issue in sorted(db_issues, key=lambda i: SEV_ORDER[i.severity]):
    loc = f'  block={issue.block_id!r}' if issue.block_id else ''
    tag  = f'  tag={issue.tag!r}' if issue.tag else ''
    val  = f'  value={issue.value!r}' if issue.value else ''
    print(f'  [{issue.severity:7s}] ({issue.check}){loc}{tag}{val}')
    print(f'           {issue.message}')

# Quick row-count summary
tables_with_rows = []
for table_name in schema.tables:
    count = conn.execute(
        f'SELECT COUNT(*) FROM "{table_name}"'
    ).fetchone()[0]
    if count:
        tables_with_rows.append((table_name, count))

fallback_count = conn.execute('SELECT COUNT(*) FROM "_cif_fallback"').fetchone()[0]

print(f'\n  Structured table rows:')
for name, count in sorted(tables_with_rows, key=lambda x: -x[1])[:15]:
    print(f'    {name:30s}  {count:>6} row(s)')
if not tables_with_rows:
    print('    (none)')
print(f'  Fallback tier (_cif_fallback): {fallback_count} row(s)')

print_time("Finished validate", start_validate)


# ---------------------------------------------------------------------------
# Step 7 — convert_database: copy VARCHAR-storage DB to typed-column DB
# ---------------------------------------------------------------------------
# convert_database() re-creates every structured table with proper INTEGER /
# DOUBLE / VARCHAR column types based on ColumnDef.type_contents.
# CIF sentinels '.' and '?' become NULL.
# SU suffixes (e.g. '1.23(5)') are stripped before casting, with a warning.
# Destination tables are created without NOT NULL or PRIMARY KEY constraints
# to avoid violations caused by bugs in the source dictionary.

print('\n=== Step 7: convert_database ===')
start_convert = get_now()

if TYPED_DB_FILE.exists():
    TYPED_DB_FILE.unlink()

typed_conn = duckdb.connect(str(TYPED_DB_FILE))

coercion_warnings = convert_database(
    src=conn,                        # source VARCHAR-storage DuckDB connection
    dst=typed_conn,                  # destination DuckDB connection (must be empty)
    schema=schema,                   # SchemaSpec for type information
    on_coercion_failure='null',      # 'null'  -> failed cast -> NULL (default)
                                     # 'keep'  -> leave VARCHAR value unchanged
                                     # 'error' -> raise on first failure
)
typed_conn.close()

if coercion_warnings:
    print(f'  {len(coercion_warnings)} coercion warning(s):')
    for w in coercion_warnings[:5]:
        print(f'    {w}')
    if len(coercion_warnings) > 5:
        print(f'    … and {len(coercion_warnings) - 5} more')
else:
    print('  No coercion warnings.')
print(f'  Typed database saved to: {TYPED_DB_FILE}')

print_time("Finished convert", start_convert)

# ---------------------------------------------------------------------------
# Step 8 — Emit CIF: ORIGINAL mode (one output block per source block)
# ---------------------------------------------------------------------------
# emit() reads the populated database and produces a valid CIF string.
# ORIGINAL is the simple inverse of ingestion: each source data_ block
# becomes one output block, in _cifflow_block_id order.

print('\n=== Step 8: Emit CIF (ORIGINAL mode) ===')
start_emit_original = get_now()

ORIGINAL_CIF_FILE = ROOT / 'output_original.cif'

cif_original = emit(
    conn,                          # open DuckDB connection (read-only)
    schema,                        # SchemaSpec used during ingestion
    mode=EmitMode.ORIGINAL,        # one block per original _cifflow_block_id (default)
    version=CifVersion.CIF_2_0,    # magic line and quoting strategy
    plan=None,                     # OutputPlan | None; None -> default ordering
    reconstruct_su=False,          # True -> merge (measurand, su) back into value(su)
)

ORIGINAL_CIF_FILE.write_text(cif_original, encoding='utf-8')

original_blocks = [l for l in cif_original.splitlines() if l.startswith('data_')]
print(f'  {len(original_blocks)} block(s) emitted -> {ORIGINAL_CIF_FILE.name}')
for header in original_blocks:
    print(f'    {header}')

print_time("Finished original emit", start_emit_original)
# ---------------------------------------------------------------------------
# Step 9 — Emit CIF: GROUPED mode (grouped by Set-anchor key values)
# ---------------------------------------------------------------------------
# GROUPED traverses the FK graph (BFS) from each table to find the nearest
# Set-class ancestor.  Tables that share the same anchor key values are
# placed in the same output block, merging rows from multiple source blocks
# that carry the same Set-level identity.

print('\n=== Step 9: Emit CIF (GROUPED mode) ===')
start_emit_grouped = get_now()

GROUPED_CIF_FILE = ROOT / 'output_grouped.cif'

cif_grouped = emit(
    conn,
    schema,
    mode=EmitMode.GROUPED,
    version=CifVersion.CIF_2_0,
    plan=None,
    reconstruct_su=False,
)

GROUPED_CIF_FILE.write_text(cif_grouped, encoding='utf-8')

grouped_blocks = [l for l in cif_grouped.splitlines() if l.startswith('data_')]
print(f'  {len(grouped_blocks)} block(s) emitted -> {GROUPED_CIF_FILE.name}')
for header in grouped_blocks:
    print(f'    {header}')

print_time("Finished grouped emit", start_emit_grouped)

# ---------------------------------------------------------------------------
# Step 10 — Emit CIF: ONE_BLOCK mode with custom OutputPlan
# ---------------------------------------------------------------------------
# ONE_BLOCK collapses all data into a single block named 'output'.
# An OutputPlan + BlockSpec can override the default category and column
# ordering.  Categories not listed in BlockSpec.categories are appended
# alphabetically; columns not listed in BlockSpec.column_order follow
# alphabetically within their category.

print('\n=== Step 10: Emit CIF (ONE_BLOCK mode with OutputPlan) ===')
start_emit_oneblock = get_now()

ONE_BLOCK_CIF_FILE = ROOT / 'output_one_block.cif'

spec = BlockSpec(
    category_order=['diffrn', 'pd_instr', 'pd_diffractogram', 'pd_data'],
    column_order={
        'diffrn': ['id'],
        'pd_diffractogram': ['id', 'diffrn_id', 'instr_id'],
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
)

ONE_BLOCK_CIF_FILE.write_text(cif_one_block, encoding='utf-8')

one_block_lines = [l for l in cif_one_block.splitlines() if l.startswith('data_')]
print(f'  {len(one_block_lines)} block(s) emitted -> {ONE_BLOCK_CIF_FILE.name}')

# # Round-trip check: re-parse the emitted CIF and verify no errors.
# cif_rt, rt_errors = build(cif_one_block, mode='strict')
# if rt_errors:
#     print(f'  WARNING: round-trip produced {len(rt_errors)} parse error(s):')
#     for e in rt_errors:
#         print(f'    [{e.error_type}] line {e.line}: {e.message}')
# else:
#     print(f'  Round-trip parse: OK  ({len(cif_rt.blocks)} block(s), no errors)')

print_time("Finished one-block emit", start_emit_oneblock)

# ---------------------------------------------------------------------------
# Step 11 — Emit CIF: ALL_BLOCKS mode (one block per Set-anchor key)
# ---------------------------------------------------------------------------
# ALL_BLOCKS mirrors GROUPED block partitioning: one output block per
# distinct Set-anchor key combination.  Set categories produce one block per
# row; Loop categories are grouped by the domain PK of the nearest Set
# ancestor.  Tables with no Set ancestor are grouped by _cifflow_block_id.

print('\n=== Step 11: Emit CIF (ALL_BLOCKS mode) ===')
start_emit_allblocks = get_now()

ALL_BLOCKS_CIF_FILE = ROOT / 'output_all_blocks.cif'

cif_all_blocks = emit(
    conn,
    schema,
    mode=EmitMode.ALL_BLOCKS,
    version=CifVersion.CIF_2_0,
    plan=None,
    reconstruct_su=False,
)

ALL_BLOCKS_CIF_FILE.write_text(cif_all_blocks, encoding='utf-8')

all_blocks_headers = [l for l in cif_all_blocks.splitlines() if l.startswith('data_')]
print(f'  {len(all_blocks_headers)} block(s) emitted -> {ALL_BLOCKS_CIF_FILE.name}')
for header in all_blocks_headers:
    print(f'    {header}')

# # Round-trip check: re-parse the emitted CIF and verify no errors.
# cif_rt_ab, rt_ab_errors = build(cif_all_blocks, mode='strict')
# if rt_ab_errors:
#     print(f'  WARNING: round-trip produced {len(rt_ab_errors)} parse error(s):')
#     for e in rt_ab_errors:
#         print(f'    [{e.error_type}] line {e.line}: {e.message}')
# else:
#     print(f'  Round-trip parse: OK  ({len(cif_rt_ab.blocks)} block(s), no errors)')

print_time("Finished all blocks emit", start_emit_allblocks)

# ---------------------------------------------------------------------------
# Step 12 — Fidelity checks: original CIF vs each emitted output
# ---------------------------------------------------------------------------
# check_fidelity compares two CIF sources for semantic equivalence.
# It ingests both into fresh in-memory databases using the same schema and
# compares all structured tables and the fallback tier.
# ONE_BLOCK and ALL_BLOCKS combine data from all blocks into one or more
# output blocks, so they are not expected to be fully fidelity-equivalent
# to the original (different block structure).  ORIGINAL and GROUPED are
# expected to be equivalent when all data is dictionary-mapped.

print('\n=== Step 12: Fidelity checks ===')
start_fidelity_checks = get_now()

fidelity_cases = [
    ('ORIGINAL',   ORIGINAL_CIF_FILE,   ORIGINAL_CIF_FILE.with_suffix('.fidelity.txt')),
    ('GROUPED',    GROUPED_CIF_FILE,    GROUPED_CIF_FILE.with_suffix('.fidelity.txt')),
    ('ONE_BLOCK',  ONE_BLOCK_CIF_FILE,  ONE_BLOCK_CIF_FILE.with_suffix('.fidelity.txt')),
    ('ALL_BLOCKS', ALL_BLOCKS_CIF_FILE, ALL_BLOCKS_CIF_FILE.with_suffix('.fidelity.txt')),
]

for mode_name, emitted_cif, report_path in fidelity_cases:
    print(f"Starting {mode_name}")
    start_individual_fidelity = get_now()
    fid = check_fidelity(
        CIF_FILE,            # source A: original CIF file
        emitted_cif,         # source B: saved emitted CIF file
        schema,              # SchemaSpec for structured comparison
        report_file=report_path,
    )
    status = 'PASS' if fid.passed else 'FAIL'
    n_mismatches = len(fid.mismatches)
    print(f'  {mode_name:<12s}  {status}  '
          f'({n_mismatches} mismatch(es))  -> {report_path.name}')
    if not fid.passed:
        by_kind: dict[str, list] = {}
        for m in fid.mismatches:
            by_kind.setdefault(m.kind, []).append(m)
        for kind, items in sorted(by_kind.items()):
            print(f'    [{kind}]  {len(items)} mismatch(es)')
            for m in items[:3]:
                print(f'      {m.description}')
            if len(items) > 3:
                print(f'      ... and {len(items) - 3} more')
    print_time(f"Finished {mode_name} fidelity check", start_individual_fidelity)

print_time("Finished all fidelity checks", start_fidelity_checks)

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

conn.close()
print('\nDone.')
print(f'  {DB_FILE.name}              — DuckDB database (VARCHAR storage)')
print(f'  {TYPED_DB_FILE.name}        — DuckDB database (typed: INTEGER/DOUBLE)')
print(f'  {ORIGINAL_CIF_FILE.name}   — CIF (ORIGINAL mode)')
print(f'  {GROUPED_CIF_FILE.name}    — CIF (GROUPED mode)')
print(f'  {ONE_BLOCK_CIF_FILE.name}  — CIF (ONE_BLOCK mode)')
print(f'  {ALL_BLOCKS_CIF_FILE.name} — CIF (ALL_BLOCKS mode)')
print(f'  *.fidelity.txt              — fidelity reports for each mode')
