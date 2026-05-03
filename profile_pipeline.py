"""
cifflow — pipeline performance profiler
==========================================
Measures wall-clock time per pipeline phase, then runs cProfile over each
phase independently so hot functions can be identified without noise from
other phases.

Run from the repository root:

    python profile_pipeline.py                 # coarse timing only
    python profile_pipeline.py --profile       # coarse timing + cProfile per phase
    python profile_pipeline.py --top 30        # show top 30 functions (default 20)
    python profile_pipeline.py --input second  # use second.cif (large file)

Input files available (all under tests/cif_files/):
    one_structure   ~  1 KB   one_structure.cif   + cif_core.dic
    multi_one       ~  1 MB   multi_one.cif       + cif_pow.dic
    second          ~ 18 MB   second.cif          + cif_pow.dic  (default)
    third           ~  5 MB   third.cif           + cif_pow.dic

Outputs are written to profile_output/ in the repo root.
"""

import argparse
import contextlib
import cProfile
import io
import pathlib
import pstats
import sqlite3
import time

import cifflow as pcp

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT     = pathlib.Path(__file__).parent
DATA_DIR = ROOT / 'data' / 'dictionaries'
CIF_DIR  = ROOT / 'tests' / 'cif_files'
OUT_DIR  = ROOT / 'profile_output'

INPUTS = {
    'one_structure': (CIF_DIR / 'one_structure.cif', DATA_DIR / 'cif_core.dic', ROOT / 'profile_cif_core_cache.json'),
    'multi_one':     (CIF_DIR / 'multi_one.cif',     DATA_DIR / 'cif_pow.dic',  ROOT / 'profile_cif_pow_cache.json'),
    'second':        (CIF_DIR / 'second.cif',         DATA_DIR / 'cif_pow.dic',  ROOT / 'profile_cif_pow_cache.json'),
    'third':         (CIF_DIR / 'third.cif',          DATA_DIR / 'cif_pow.dic',  ROOT / 'profile_cif_pow_cache.json'),
}

# ---------------------------------------------------------------------------
# Timing context manager
# ---------------------------------------------------------------------------

class _Timer:
    """Context manager that records wall-clock elapsed time."""
    def __init__(self, label: str, results: list):
        self.label = label
        self.results = results
        self.elapsed = 0.0

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.elapsed = time.perf_counter() - self._t0
        self.results.append((self.label, self.elapsed))


def _print_timings(results: list[tuple[str, float]]) -> None:
    total = sum(t for _, t in results)
    print()
    print(f"  {'Phase':<45} {'Time':>8}   {'%':>5}")
    print(f"  {'-'*45} {'-'*8}   {'-'*5}")
    for label, elapsed in results:
        pct = 100 * elapsed / total if total else 0
        print(f"  {label:<45} {elapsed:>7.3f}s  {pct:>4.1f}%")
    print(f"  {'TOTAL':<45} {total:>7.3f}s")
    print()


# ---------------------------------------------------------------------------
# cProfile helpers
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _profiled(enabled: bool):
    """Yield a cProfile.Profile if enabled, else a no-op context."""
    if not enabled:
        yield None
        return
    pr = cProfile.Profile()
    pr.enable()
    try:
        yield pr
    finally:
        pr.disable()


def _print_profile(pr: cProfile.Profile, label: str, top_n: int, out_dir: pathlib.Path) -> None:
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s)
    ps.sort_stats('cumulative')
    ps.print_stats(top_n)
    report = s.getvalue()

    print(f"\n{'='*70}")
    print(f"  cProfile: {label}  (top {top_n} by cumulative time)")
    print('='*70)
    # Trim the pstats header noise to the useful table
    lines = report.splitlines()
    in_table = False
    for line in lines:
        if 'cumtime' in line or 'ncalls' in line:
            in_table = True
        if in_table:
            print(line)

    # Save full report to file
    out_path = out_dir / f"profile_{label.replace(' ', '_').lower()}.txt"
    out_path.write_text(report, encoding='utf-8')
    print(f"\n  Full report saved to: {out_path}")


# ---------------------------------------------------------------------------
# Pipeline phases
# ---------------------------------------------------------------------------

def phase_dict_load(dic_file: pathlib.Path, cache_file: pathlib.Path) -> pcp.DdlmDictionary:
    resolver = pcp.directory_resolver(dic_file.parent)
    path_resolver = pcp.directory_path_resolver(dic_file.parent)
    loader = pcp.DictionaryLoader(
        resolver=resolver,
        path_resolver=path_resolver,
    )
    if cache_file.exists():
        return pcp.load_dictionary(cache_file)
    dictionary = loader.load(dic_file.read_text(encoding='utf-8'))
    pcp.save_dictionary(dictionary, cache_file)
    return dictionary


def phase_schema_gen(dictionary: pcp.DdlmDictionary) -> pcp.SchemaSpec:
    return pcp.generate_schema(dictionary)


def phase_parse(cif_file: pathlib.Path) -> pcp.CifFile:
    cif_text = cif_file.read_text(encoding='utf-8')
    parsed, _ = pcp.build(cif_text)
    return parsed


def phase_apply_schema(conn: sqlite3.Connection, schema: pcp.SchemaSpec) -> None:
    pcp.apply_schema(conn, schema)
    pcp.apply_fallback_schema(conn)


def phase_ingest(conn: sqlite3.Connection, parsed: pcp.CifFile, schema: pcp.SchemaSpec) -> None:
    pcp.ingest(parsed, conn, schema)


def phase_compactify(conn: sqlite3.Connection, schema: pcp.SchemaSpec) -> sqlite3.Connection:
    compact_conn = sqlite3.connect(':memory:')
    pcp.compactify_database(conn, compact_conn, schema)
    return compact_conn


def phase_emit(conn: sqlite3.Connection, schema: pcp.SchemaSpec) -> None:
    pcp.emit(conn, schema, mode=pcp.EmitMode.ORIGINAL)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description='cifflow pipeline profiler')
    parser.add_argument('--input', choices=list(INPUTS), default='second',
                        help='Input file set to use (default: second)')
    parser.add_argument('--profile', action='store_true',
                        help='Run cProfile on each phase in addition to wall-clock timing')
    parser.add_argument('--top', type=int, default=20,
                        help='Number of functions to show in cProfile output (default: 20)')
    parser.add_argument('--no-cache', action='store_true',
                        help='Ignore existing dictionary cache and re-parse from scratch')
    args = parser.parse_args()

    cif_file, dic_file, cache_file = INPUTS[args.input]
    OUT_DIR.mkdir(exist_ok=True)

    if args.no_cache and cache_file.exists():
        cache_file.unlink()

    print(f"\ncifflow pipeline profiler")
    print(f"  Input:      {cif_file.name}  ({cif_file.stat().st_size / 1024:.0f} KB)")
    print(f"  Dictionary: {dic_file.name}")
    print(f"  Cache:      {'yes' if cache_file.exists() else 'no (will build)'}")
    print(f"  cProfile:   {'yes' if args.profile else 'no (use --profile to enable)'}")

    timings: list[tuple[str, float]] = []

    # ── Phase 1: dictionary load ──────────────────────────────────────────────
    with _Timer('1. Dictionary load (cache if present)', timings):
        with _profiled(args.profile) as pr:
            dictionary = phase_dict_load(dic_file, cache_file)
    if args.profile and pr:
        _print_profile(pr, 'dict_load', args.top, OUT_DIR)

    # ── Phase 2: schema generation ────────────────────────────────────────────
    with _Timer('2. Schema generation', timings):
        with _profiled(args.profile) as pr:
            schema = phase_schema_gen(dictionary)
    if args.profile and pr:
        _print_profile(pr, 'schema_gen', args.top, OUT_DIR)

    print(f"\n  Schema: {len(schema.tables)} tables, "
          f"{sum(len(t.columns) for t in schema.tables.values())} columns")

    # ── Phase 3: CIF parse ────────────────────────────────────────────────────
    with _Timer('3. CIF parse (build)', timings):
        with _profiled(args.profile) as pr:
            parsed = phase_parse(cif_file)
    if args.profile and pr:
        _print_profile(pr, 'cif_parse', args.top, OUT_DIR)

    # ── Phase 4: schema apply ─────────────────────────────────────────────────
    conn = sqlite3.connect(':memory:')
    conn.execute('PRAGMA foreign_keys = ON')

    with _Timer('4. Apply schema to SQLite', timings):
        with _profiled(args.profile) as pr:
            phase_apply_schema(conn, schema)
    if args.profile and pr:
        _print_profile(pr, 'apply_schema', args.top, OUT_DIR)

    # ── Phase 5: ingest ───────────────────────────────────────────────────────
    with _Timer('5. Ingest', timings):
        with _profiled(args.profile) as pr:
            phase_ingest(conn, parsed, schema)
    if args.profile and pr:
        _print_profile(pr, 'ingest', args.top, OUT_DIR)

    # ── Phase 6: compactify ───────────────────────────────────────────────────
    with _Timer('6. Compactify', timings):
        with _profiled(args.profile) as pr:
            compact_conn = phase_compactify(conn, schema)
    if args.profile and pr:
        _print_profile(pr, 'compactify', args.top, OUT_DIR)

    # ── Phase 7: emit (ORIGINAL) ──────────────────────────────────────────────
    with _Timer('7. Emit (ORIGINAL mode)', timings):
        with _profiled(args.profile) as pr:
            phase_emit(compact_conn, schema)
    if args.profile and pr:
        _print_profile(pr, 'emit', args.top, OUT_DIR)

    compact_conn.close()
    conn.close()

    print("\n=== Wall-clock timings ===")
    _print_timings(timings)

    if args.profile:
        print(f"  Full cProfile reports saved to: {OUT_DIR}/")


if __name__ == '__main__':
    main()
