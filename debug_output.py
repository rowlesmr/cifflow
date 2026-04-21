"""
debug_output.py — CIF output module debugger
=============================================
Loads multi_one.cif with cif_pow.dic, validates, pretty-prints the report,
and writes one CIF file per emit mode.

Run from the repository root:
    python debug_output.py

Output files:
    output_original.cif          — ORIGINAL mode (one block per source block)
    output_grouped.cif           — GROUPED mode  (one block per Set-anchor key combo)
    output_one_block.cif         — ONE_BLOCK mode (everything in one block)
    output_all_blocks.cif        — ALL_BLOCKS mode (one block per schema category)
    *.fidelity.txt               — fidelity report for each mode vs. the input
"""

import pathlib
import sqlite3

ROOT     = pathlib.Path(__file__).parent
DIC_DIR  = ROOT / 'data' / 'dictionaries'

FILE_NAME = "tmp" # "multi_one" # "pathological_key_block"

CIF_FILE = ROOT / 'tests' / 'cif_files' / (FILE_NAME +'.cif')

DB_COMPACT_FILE = ROOT / (FILE_NAME +'_compact_db.db')



# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from pycifparse import (
    DictionaryLoader,
    directory_resolver,
    load_dictionary,
    save_dictionary,
    generate_schema,
    compactify_database,
    emit,
    EmitMode,
    OutputPlan,
    BlockSpec,
    validate,
)
from pycifparse.fidelity import check_fidelity
from pycifparse.types import CifVersion

# ---------------------------------------------------------------------------
# Step 1 — Dictionary
# ---------------------------------------------------------------------------

DIC_CACHE = ROOT / 'cif_pow_cache.json'

resolver   = directory_resolver(DIC_DIR)
dictionary = None

if DIC_CACHE.exists():
    try:
        dictionary = load_dictionary(DIC_CACHE)
        print(f'Loaded cached dictionary from {DIC_CACHE.name}')
    except ValueError:
        dictionary = None

if dictionary is None:
    loader     = DictionaryLoader(resolver=resolver)
    dictionary = loader.load(
        (DIC_DIR / 'cif_pow.dic').read_text(encoding='utf-8'),
        base_uri='cif_pow.dic',
    )
    save_dictionary(dictionary, DIC_CACHE)
    print(f'Parsed dictionary and cached to {DIC_CACHE.name}')

schema = generate_schema(dictionary)
print(f'Schema: {len(schema.tables)} tables, {len(schema.bridge_columns)} bridge columns\n')

# ---------------------------------------------------------------------------
# Step 2 — Validate (builds the in-memory database)
# ---------------------------------------------------------------------------

report = validate(CIF_FILE, schema)

# ---------------------------------------------------------------------------
# Step 3 — Pretty-print the validation report
# ---------------------------------------------------------------------------

SEV_ORDER = {'Error': 0, 'Warning': 1, 'Info': 2}
SEV_LABEL = {'Error': 'ERR ', 'Warning': 'WARN', 'Info': 'INFO'}

counts = {'Error': 0, 'Warning': 0, 'Info': 0}
for i in report.issues:
    counts[i.severity] += 1

print('=' * 72)
print(f'VALIDATION REPORT  --  {"PASSED" if report.passed else "FAILED"}')
print(f'  {counts["Error"]} error(s)  '
      f'{counts["Warning"]} warning(s)  '
      f'{counts["Info"]} info(s)')
print('=' * 72)

for issue in sorted(report.issues, key=lambda i: (SEV_ORDER[i.severity], i.stage)):
    label = SEV_LABEL[issue.severity]
    stage = f'[{issue.stage}]'

    # First line: severity + stage + check + block
    loc_parts = []
    if issue.block:
        loc_parts.append(f'block={issue.block!r}')
    if issue.table:
        loc_parts.append(f'table={issue.table!r}')
    if issue.column:
        loc_parts.append(f'col={issue.column!r}')
    if issue.row_id is not None:
        loc_parts.append(f'row={issue.row_id}')
    if issue.tag:
        loc_parts.append(f'tag={issue.tag!r}')
    if issue.value:
        loc_parts.append(f'value={issue.value!r}')
    if issue.line is not None:
        loc_parts.append(f'line={issue.line}')
    if issue.col is not None:
        loc_parts.append(f'col={issue.col}')
    if issue.key_values:
        kv_str = ', '.join(f'{k}={v!r}' for k, v in issue.key_values.items())
        loc_parts.append(f'keys={{{kv_str}}}')

    loc = ('  ' + '  '.join(loc_parts)) if loc_parts else ''
    check = f'({issue.check})' if issue.check else ''

    # Handle multi-line messages (e.g. bridge chain diagnostics)
    msg_lines = issue.message.splitlines()
    print(f'{label} {stage:12s} {check}')
    for line in msg_lines:
        print(f'       {line}')
    if loc:
        print(f'    {loc}')
    print()

print('=' * 72)
print()

if report.database is None:
    print('No database — skipping CIF emission.')
    raise SystemExit(1)

if report.database:
    if DB_COMPACT_FILE.exists():
        DB_COMPACT_FILE.unlink()  # start fresh each run

    dest = sqlite3.connect(DB_COMPACT_FILE)
    compactify_database(
        src=report.database,  # source connection (already populated by ingest)
        dst=dest,  # destination connection (must be empty)
        schema=schema,  # SchemaSpec used when src was populated
    )
    dest.close()








# ---------------------------------------------------------------------------
# Step 4 — Emit one CIF file per mode
# ---------------------------------------------------------------------------

MODES = [
    (EmitMode.ORIGINAL,   f'{FILE_NAME}_output_original.cif'),
    (EmitMode.ONE_BLOCK,  f'{FILE_NAME}_output_one_block.cif'),
    (EmitMode.ALL_BLOCKS, f'{FILE_NAME}_output_all_blocks.cif'),
    (EmitMode.GROUPED,    f'{FILE_NAME}_output_grouped.cif'),
]

# ---------------------------------------------------------------------------
# GROUPED output ordering spec — edit category_order and column_order here
# to test different output arrangements.
#
# category_order entries:
#   'category_name'           — emit this category at this position
#   'category_name*'          — emit this category + all schema descendants
#                               (children, grandchildren, …), alphabetically
#   ['cat_a', 'cat_b', ...]   — merge group: emit as a single loop_ if cats
#                               share identical non-synthetic PK columns;
#                               otherwise fall back to individual loops in order
#
# Categories not listed are appended alphabetically (Set-class first) after
# the listed ones.
#
# column_order maps category name -> list of column names that should appear
# first within that category; remaining columns follow alphabetically.
#
# BlockSpec.matches is a predicate on frozenset[str] of Set-category table
# names present in a candidate block.  None = catch-all (matches any block).
# First-match wins across the specs list.
#
# Current spec: a single catch-all spec that places diffrn and pd_instr
# at the top, followed by all pd_* descendants, then everything else.
# ---------------------------------------------------------------------------

GROUPED_PLAN = OutputPlan(
    specs=[
        BlockSpec(
            matches=None,           # catch-all: applies to every output block
            category_order=[
                'diffrn',           # diffrn Set first
                'diffrn_radiation', # its radiation entry
                'diffrn_radiation_wavelength',  # wavelengths loop
                'pd_instr',         # instrument Set
                'pd_diffractogram', # per-experiment Set
                [                   # merge group: compatible powder data loops
                    'pd_data',
                    'pd_meas',
                    'pd_proc',
                    'pd_calc',
                ],
                'pd_peak',          # peak list loop
                'refln',            # reflection loop
            ],
            column_order={
                'diffrn': ['id'],
                'diffrn_radiation': ['id', 'probe'],
                'pd_diffractogram': ['id', 'diffrn_id', 'instr_id'],
                'pd_peak': ['id', '2theta_centroid', 'intensity'],
            },
            single_block=False,     # one output block per anchor key combo
        ),
    ],
)

for mode, filename in MODES:
    plan = GROUPED_PLAN if mode == EmitMode.GROUPED else None
    cif_text = emit(
        report.database,
        schema,
        mode=mode,
        version=CifVersion.CIF_2_0,
        plan=plan,
        reconstruct_su=False,
        pretty=True,
        line_limit=2048,
    )
    out_path = ROOT / filename
    out_path.write_text(cif_text, encoding='utf-8')
    block_count = cif_text.count('\ndata_')
    print(f'{mode.value:12s}  ->  {filename}  ({block_count} block(s), {len(cif_text):,} chars)')

print()

# ---------------------------------------------------------------------------
# Step 5 — Fidelity check: compare each output CIF against the original input
# ---------------------------------------------------------------------------

print('=' * 72)
print('FIDELITY CHECKS  (output vs input)')
print('=' * 72)

for mode, filename in MODES:
    out_path = ROOT / filename
    fid_report_file = ROOT / (out_path.stem + '.fidelity.txt')
    fid = check_fidelity(
        CIF_FILE,
        out_path,
        schema=schema,
        report_file=fid_report_file,
    )
    status = 'PASS' if fid.passed else 'FAIL'
    n = len(fid.mismatches)
    print(f'{mode.value:12s}  {status}  ({n} mismatch(es))  -> {fid_report_file.name}')

print()
print('Done.')
