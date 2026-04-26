"""
debug_parquet.py — dump a CIF file to per-batch Parquet files using build_arrow.

Usage:
    python debug_parquet.py <cif_file> [<output_dir>]

Output defaults to a directory named <cif_stem>_parquet/ beside the CIF file.
One Parquet file per RecordBatch is written:
    batch_000.parquet   (block 0, scalars or loop 0, …)
    batch_001.parquet
    …

Each batch's schema contains only the five metadata columns plus the tags
present in that specific batch — matching the compiled_path.md Phase B spec
exactly.  No NULL padding, no unified union schema.
"""

import pathlib
import sys

import pyarrow.parquet as pq

from pycifparse import build_arrow


def dump_cif_to_parquet(cif_path: pathlib.Path, out_dir: pathlib.Path) -> None:
    source = cif_path.read_text(encoding='utf-8')
    batches, errors = build_arrow(source)

    if errors:
        print(f'  {len(errors)} parse error(s):')
        for e in errors:
            print(f'    [{e.error_type}] line {e.line}: {e.message}')

    if not batches:
        print('  No data to write.')
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    for i, batch in enumerate(batches):
        path = out_dir / f'batch_{i:03d}.parquet'
        pq.write_table(batch, path)
        block_name = batch.column('_block_name')[0].as_py()
        loop_id    = batch.column('_loop_id')[0].as_py()
        n_tags     = batch.num_columns - 5
        print(f'  batch {i:>3}  {block_name!r:35s}  {loop_id:20s}  '
              f'{batch.num_rows} row(s), {n_tags} tag(s)')

    total_kb = sum((out_dir / f'batch_{i:03d}.parquet').stat().st_size
                   for i in range(len(batches))) // 1024
    print(f'\n  {len(batches)} batch(es), {total_kb} KB total')
    print(f'  -> {out_dir}')


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cif_path = pathlib.Path(sys.argv[1])
    if not cif_path.exists():
        print(f'Error: file not found: {cif_path}')
        sys.exit(1)

    out_dir = (pathlib.Path(sys.argv[2]) if len(sys.argv) > 2
               else cif_path.with_suffix('') / (cif_path.stem + '_parquet'))

    print(f'CIF:    {cif_path}')
    print(f'Output: {out_dir}')
    dump_cif_to_parquet(cif_path, out_dir)
    print('Done.')


if __name__ == '__main__':
    main()
