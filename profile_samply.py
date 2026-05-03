"""
Samply-friendly profiling entry point.

Run with:
    samply record python profile_samply.py [cif_file] [--repeat N]

Or for plain wall-clock timing without samply:
    python profile_samply.py [cif_file] [--repeat N] [--arrow] [--build]

Defaults to tests/cif_files/second.cif (18 MB).

Flags:
  --repeat N   run the hot function N times (default: 10 for samply, 1 otherwise)
  --arrow      time build_arrow_file()  (default)
  --build      time build() instead
  --both       time both and compare
"""

import argparse
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).parent
DEFAULT_CIF = ROOT / "tests" / "cif_files" / "second.cif"


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("cif_file", nargs="?", default=str(DEFAULT_CIF))
    p.add_argument("--repeat", type=int, default=10)
    p.add_argument("--arrow", action="store_true", default=False)
    p.add_argument("--build", action="store_true", default=False)
    p.add_argument("--both",  action="store_true", default=False)
    return p.parse_args()


def _time(label, fn, repeat):
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn()
        times.append(time.perf_counter() - t0)
    times.sort()
    mid = times[len(times) // 2]
    print(f"  {label:30s}  min={times[0]*1000:.1f}ms  median={mid*1000:.1f}ms  max={times[-1]*1000:.1f}ms  (n={repeat})")
    return result


def main():
    args = _parse_args()
    path = str(args.cif_file)

    import cifflow as pcp

    size_kb = pathlib.Path(path).stat().st_size / 1024
    print(f"File: {path}  ({size_kb:.0f} KB)")
    print()

    run_arrow = args.arrow or args.both or (not args.build)
    run_build = args.build or args.both

    if run_arrow:
        _time("build_arrow_file()", lambda: pcp.build_arrow_file(path), args.repeat)

    if run_build:
        source = pathlib.Path(path).read_text(encoding="utf-8")
        _time("build() [str→CifFile]", lambda: pcp.build(source), args.repeat)

    if run_arrow and run_build:
        print()
        source = pathlib.Path(path).read_text(encoding="utf-8")
        _time("Python open+read",        lambda: pathlib.Path(path).read_text(encoding="utf-8"), args.repeat)
        _time("build_arrow() [str→Arrow]", lambda: pcp.build_arrow(source), args.repeat)


if __name__ == "__main__":
    main()
