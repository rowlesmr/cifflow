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
import sys
import pathlib
import duckdb

ROOT     = pathlib.Path(__file__).parent
DIC_DIR  = ROOT / 'data' / 'dictionaries'

FILE_NAME = "second_short_decimated" #"multi_one" # "pathological_key_block""tmp" #

CIF_FILE = ROOT / 'tests' / 'cif_files' / (FILE_NAME +'.cif')

DB_CONVERT_FILE = ROOT / (FILE_NAME +'_convert_db.duckdb')



# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from cifflow import (
    DictionaryLoader,
    directory_resolver,
    load_dictionary,
    save_dictionary,
    generate_schema,
    convert_database,
    emit,
    EmitMode,
    OutputPlan,
    BlockSpec,
    validate,
)
from cifflow.fidelity import check_fidelity
from cifflow.types import CifVersion
from cifflow import OutputPlan, BlockSpec, any_of, has, all_of, only

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

report = validate(CIF_FILE, schema, propagate_fk=True)

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
    if DB_CONVERT_FILE.exists():
        DB_CONVERT_FILE.unlink()  # start fresh each run

    dest = duckdb.connect(str(DB_CONVERT_FILE))
    convert_database(
        src=report.database,  # source connection (already populated by ingest)
        dst=dest,             # destination connection (must be empty)
        schema=schema,        # SchemaSpec used when src was populated
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


conn = report.database


def _set_anchors(schema, table: str) -> frozenset[str]:
    """BFS forward along FKs; return every Set-class table reached, stopping at each Set."""
    visited, queue, anchors = set(), [table], set()
    while queue:
        cur = queue.pop()
        if cur in visited:
            continue
        visited.add(cur)
        td = schema.tables.get(cur)
        if td is None:
            continue
        if cur != table and td.category_class == 'Set':
            anchors.add(cur)
            # don't traverse through Set tables
        else:
            for fk in td.foreign_keys:
                queue.append(fk.target_table)
    return frozenset(anchors)


anchored_to_diffractogram = sorted(
    t for t, td in schema.tables.items()
    if td.category_class in ['Loop', "Set"]
    and _set_anchors(schema, t) == frozenset({'pd_diffractogram'})
)


for t, td in schema.tables.items():
    print(f"{t}: {td}")

for t in anchored_to_diffractogram:
    print(t)
sys.exit()

pd_phase_mass: TableDef(name='pd_phase_mass', definition_id='pd_phase_mass', category_class='Loop',
                        columns=[ColumnDef(name='_cifflow_block_id', definition_id='', type_contents=None, nullable=False, is_primary_key=False, is_synthetic=True, linked_item_id=None, type_container=None, enumeration_states=[], enumeration_range=None, type_dimension=None),
                                 ColumnDef(name='_cifflow_row_id', definition_id='', type_contents=None, nullable=False, is_primary_key=False, is_synthetic=True, linked_item_id=None, type_container=None, enumeration_states=[], enumeration_range=None, type_dimension=None),
                                 ColumnDef(name='diffractogram_id', definition_id='_pd_phase_mass.diffractogram_id', type_contents='Text', nullable=True, is_primary_key=True, is_synthetic=False, linked_item_id=None, type_container='Single', enumeration_states=[], enumeration_range=None, type_dimension=None),
                                 ColumnDef(name='phase_id', definition_id='_pd_phase_mass.phase_id', type_contents='Text', nullable=True, is_primary_key=True, is_synthetic=False, linked_item_id=None, type_container='Single', enumeration_states=[], enumeration_range=None, type_dimension=None),
                                 ColumnDef(name='absolute', definition_id='_pd_phase_mass.absolute', type_contents='Real', nullable=True, is_primary_key=False, is_synthetic=False, linked_item_id=None, type_container='Single', enumeration_states=[], enumeration_range='0.0:100.0', type_dimension=None),
                                 ColumnDef(name='absolute_su', definition_id='_pd_phase_mass.absolute_su', type_contents='Real', nullable=True, is_primary_key=False, is_synthetic=False, linked_item_id='_pd_phase_mass.absolute', type_container='Single', enumeration_states=[], enumeration_range=None, type_dimension=None),
                                 ColumnDef(name='original', definition_id='_pd_phase_mass.original', type_contents='Real', nullable=True, is_primary_key=False, is_synthetic=False, linked_item_id=None, type_container='Single', enumeration_states=[], enumeration_range='0.0:100.0', type_dimension=None),
                                 ColumnDef(name='original_su', definition_id='_pd_phase_mass.original_su', type_contents='Real', nullable=True, is_primary_key=False, is_synthetic=False, linked_item_id='_pd_phase_mass.original', type_container='Single', enumeration_states=[], enumeration_range=None, type_dimension=None),
                                 ColumnDef(name='percent', definition_id='_pd_phase_mass.percent', type_contents='Real', nullable=True, is_primary_key=False, is_synthetic=False, linked_item_id=None, type_container='Single', enumeration_states=[], enumeration_range='0.0:100.0', type_dimension=None),
                                 ColumnDef(name='percent_su', definition_id='_pd_phase_mass.percent_su', type_contents='Real', nullable=True, is_primary_key=False, is_synthetic=False, linked_item_id='_pd_phase_mass.percent', type_container='Single', enumeration_states=[], enumeration_range=None, type_dimension=None)],
                        primary_keys=['diffractogram_id', 'phase_id'],
                        foreign_keys=[ForeignKeyDef(source_table='pd_phase_mass', source_columns=['diffractogram_id'], target_table='pd_diffractogram', target_columns=['id']),
                                      ForeignKeyDef(source_table='pd_phase_mass', source_columns=['phase_id'], target_table='pd_phase', target_columns=['id'])]
                        )

structure: TableDef(name='structure', definition_id='structure', category_class='Set',
                    columns=[ColumnDef(name='_cifflow_block_id', definition_id='', type_contents=None, nullable=False, is_primary_key=False, is_synthetic=True, linked_item_id=None, type_container=None, enumeration_states=[], enumeration_range=None, type_dimension=None),
                             ColumnDef(name='_cifflow_row_id', definition_id='', type_contents=None, nullable=False, is_primary_key=False, is_synthetic=True, linked_item_id=None, type_container=None, enumeration_states=[], enumeration_range=None, type_dimension=None),
                             ColumnDef(name='id', definition_id='_structure.id', type_contents='Word', nullable=False, is_primary_key=True, is_synthetic=False, linked_item_id=None, type_container='Single', enumeration_states=[], enumeration_range=None, type_dimension=None),
                             ColumnDef(name='diffrn_id', definition_id='_structure.diffrn_id', type_contents='Word', nullable=True, is_primary_key=False, is_synthetic=False, linked_item_id=None, type_container='Single', enumeration_states=[], enumeration_range=None, type_dimension=None),
                             ColumnDef(name='phase_id', definition_id='_structure.phase_id', type_contents='Text', nullable=True, is_primary_key=False, is_synthetic=False, linked_item_id=None, type_container='Single', enumeration_states=[], enumeration_range=None, type_dimension=None),
                             ColumnDef(name='space_group_id', definition_id='_structure.space_group_id', type_contents='Word', nullable=True, is_primary_key=False, is_synthetic=False, linked_item_id=None, type_container='Single', enumeration_states=[], enumeration_range=None, type_dimension=None)],
                    primary_keys=['id'],
                    foreign_keys=[ForeignKeyDef(source_table='structure', source_columns=['diffrn_id'], target_table='diffrn', target_columns=['id']),
                                  ForeignKeyDef(source_table='structure', source_columns=['phase_id'], target_table='pd_phase', target_columns=['id']),
                                  ForeignKeyDef(source_table='structure', source_columns=['space_group_id'], target_table='space_group', target_columns=['id'])])


GROUPED_PLAN = OutputPlan(
    specs=[
        BlockSpec(
            matches=only("diffrn"),
            category_order=[],
        ),
        BlockSpec(
            matches=has(*schema.descendants('publication')),
            category_order=['publication'],   # other categories follow alphabetically
        ),
        BlockSpec(
            matches=all_of('pd_diffractogram', "pd_phase"),
            category_order=[],
        ),
        BlockSpec(
            matches=only("pd_diffractogram"),
            category_order=[
                 "pd_diffractogram",
                ['pd_data', 'pd_meas', 'pd_proc', 'pd_calc'],  # merge group
            ],
        ),
        BlockSpec(
            matches=only("pd_phase"),
            category_order=[],
        ),
        BlockSpec(
            matches=None,   # catch-all: anything not matched above, alphabetical order
        ),
    ],
)

# GROUPED_PLAN = OutputPlan(specs=[
#     BlockSpec(matches=lambda anchors: print(anchors) or True)
# ])

ALL_BLOCK_PLAN = OutputPlan(specs=[
    BlockSpec(
        # category_order=[
        #     'diffrn',
        #     'pd_diffractogram',  # plain name — emit this table first
        #     ['pd_data', 'pd_meas', "pd_proc", "pd_calc"],  # merge group — emitted as one loop_
        #     # anything not listed follows alphabetically (Set then Loop)
        # ],
        column_order={
            #'pd_diffractogram': ['id', 'instr_id', 'diffrn_id'],
            #'pd_calc_component': ['intensity_net', 'point_id', ],
            'cell': ['structure_id', 'length_a', 'length_b', 'length_c', 'angle_alpha', 'angle_beta', 'angle_gamma', 'mass', 'volume'],
        },
    )
])

PLANS = {EmitMode.GROUPED: GROUPED_PLAN,
         EmitMode.ALL_BLOCKS: ALL_BLOCK_PLAN,
         EmitMode.ONE_BLOCK: ALL_BLOCK_PLAN}

for mode, filename in MODES:
    plan = PLANS.get(mode, None)

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


sys.exit()

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
