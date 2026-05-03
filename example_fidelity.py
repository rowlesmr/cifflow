"""
cifflow — fidelity check example
=====================================
Demonstrates check_fidelity() by comparing two semantically equivalent CIF
files: ``multi_one.cif`` (24 data blocks spread across instrument, wavelength,
and measurement sections) and ``multi_one_as_oneblock.cif`` (the same data
collapsed into a single block).

Both files should contain the same data; check_fidelity() reports whether
that is actually the case.

Run from the repository root:
    python example_fidelity.py
"""

import pathlib

from cifflow import (
    check_fidelity,
    FidelityReport,
    FidelityMismatch,
    DictionaryLoader,
    directory_resolver,
    directory_path_resolver,
    generate_schema,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ROOT     = pathlib.Path(__file__).parent
DIC_DIR  = ROOT / 'data' / 'dictionaries'
DIC_FILE = DIC_DIR / 'cif_pow.dic'

CIF_A = ROOT / 'tests' / 'cif_files' / 'multi_one.cif'
CIF_B = ROOT / 'tests' / 'cif_files' / 'multi_one_as_oneblock.cif'
CIF_B = ROOT / 'output_grouped.cif'


# ---------------------------------------------------------------------------
# Step 1 — Load dictionary and generate schema
# ---------------------------------------------------------------------------

print('=== Step 1: Load dictionary ===')

resolver = directory_resolver(DIC_DIR)
dictionary = DictionaryLoader(resolver=resolver, path_resolver=directory_path_resolver(DIC_DIR)).load(
    DIC_FILE.read_text(encoding='utf-8'),
    base_uri=DIC_FILE.name,
)
schema = generate_schema(dictionary)

print(f'  Dictionary : {dictionary.name!r}')
print(f'  Schema     : {len(schema.tables)} structured tables')
if schema.warnings:
    print(f'  {len(schema.warnings)} schema warning(s) (first 3):')
    for w in schema.warnings[:3]:
        print(f'    {w}')


# ---------------------------------------------------------------------------
# Step 2 — Compare with a schema (structured + fallback comparison)
# ---------------------------------------------------------------------------

print(f'\n=== Step 2: check_fidelity with schema ===')
print(f'  A: {CIF_A.name}  ({sum(1 for l in CIF_A.read_text().splitlines() if l.startswith("data_"))} blocks)')
print(f'  B: {CIF_B.name}  ({sum(1 for l in CIF_B.read_text().splitlines() if l.startswith("data_"))} blocks)')

REPORT_FILE = ROOT / 'fidelity_report.txt'

report: FidelityReport = check_fidelity(
    CIF_A,                    # source A: str | Path | CifFile
    CIF_B,                    # source B: str | Path | CifFile
    schema,                   # SchemaSpec | str | Path | None
    report_file=REPORT_FILE,  # optional: write human-readable report to file
)

if report.passed:
    print('  Result : PASSED — sources are semantically identical')
else:
    print(f'  Result : FAILED — {len(report.mismatches)} mismatch(es)')
    # Group mismatches by kind for a concise summary
    by_kind: dict[str, list[FidelityMismatch]] = {}
    for m in report.mismatches:
        by_kind.setdefault(m.kind, []).append(m)
    for kind, items in sorted(by_kind.items()):
        print(f'    {kind:20s}  {len(items):>4} occurrence(s)')
        for m in items[:3]:
            print(f'      [{m.source}] {m.description}')
        if len(items) > 3:
            print(f'      ... and {len(items) - 3} more')


# ---------------------------------------------------------------------------
# Step 3 — Compare without a schema (fallback-only comparison)
# ---------------------------------------------------------------------------

print(f'\n=== Step 3: check_fidelity without schema (fallback only) ===')

report_no_schema: FidelityReport = check_fidelity(
    CIF_A,
    CIF_B,
    schema=None,   # no schema: all tags go to _cif_fallback
)

if report_no_schema.passed:
    print('  Result : PASSED')
else:
    print(f'  Result : FAILED — {len(report_no_schema.mismatches)} mismatch(es)')
    by_kind_ns: dict[str, list[FidelityMismatch]] = {}
    for m in report_no_schema.mismatches:
        by_kind_ns.setdefault(m.kind, []).append(m)
    for kind, items in sorted(by_kind_ns.items()):
        print(f'    {kind:20s}  {len(items):>4} occurrence(s)')


# ---------------------------------------------------------------------------
# Step 4 — Compare a file with itself (should always pass)
# ---------------------------------------------------------------------------

print(f'\n=== Step 4: self-comparison (should always pass) ===')

for path in (CIF_A, CIF_B):
    r = check_fidelity(path, path, schema)
    status = 'PASSED' if r.passed else f'FAILED ({len(r.mismatches)} mismatches)'
    print(f'  {path.name}  vs itself: {status}')


print('\nDone.')
