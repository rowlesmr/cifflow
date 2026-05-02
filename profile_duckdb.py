"""Break down DuckDB time per table in extract_merged_rows."""
import json, pathlib, sqlite3, time, uuid as _uuid_module
import duckdb
import pycifparse as pcp
from pycifparse.ingestion.duckdb_ingest import (
    setup_duckdb, load_block_data, flush_table_batches,
    propagate_fk_sql, _non_synthetic_pks, _non_pk_data_cols,
    _SCALARS_LOOP_ID,
)
from pycifparse.ingestion.ingest import build_tag_to_column, build_su_map, _select_blocks

ROOT = pathlib.Path(__file__).parent
CIF_FILE = ROOT / 'tests' / 'cif_files' / 'second.cif'
DIC_FILE = ROOT / 'data' / 'dictionaries' / 'cif_pow.dic'
CACHE    = ROOT / 'profile_cif_pow_cache.json'

resolver = pcp.directory_resolver(DIC_FILE.parent)
path_resolver = pcp.directory_path_resolver(DIC_FILE.parent)
loader = pcp.DictionaryLoader(resolver=resolver, path_resolver=path_resolver)
dictionary = pcp.load_dictionary(CACHE) if CACHE.exists() else loader.load(DIC_FILE.read_text(encoding='utf-8'))
schema = pcp.generate_schema(dictionary)
cif_text = CIF_FILE.read_text(encoding='utf-8')
parsed, _ = pcp.build(cif_text)

tag_to_column = build_tag_to_column(schema)
su_map = build_su_map(schema)
blocks = _select_blocks(parsed, None)

def emit(msg, **kw): pass

t0 = time.perf_counter()
db = setup_duckdb(schema)
populated = set()
global_batch = {}
for position, block in enumerate(blocks):
    fallback, table_batch = load_block_data(
        block, block.name, position, schema, tag_to_column, su_map, set(), emit,
    )
    for tbl, rows in table_batch.items():
        if tbl in global_batch:
            global_batch[tbl].extend(rows)
        else:
            global_batch[tbl] = rows
flush_table_batches(db, global_batch, populated)
propagate_fk_sql(db, schema, tag_to_column, False, emit, populated)
t1 = time.perf_counter()
print(f"Pre-extract setup: {t1-t0:.3f}s")

# Now time individual table queries
table_times = []
row_counts = []
for tbl_name, table in schema.tables.items():
    if tbl_name not in populated:
        continue
    ns_pks = _non_synthetic_pks(table)
    data_cols = _non_pk_data_cols(table)
    n_pks = len(ns_pks)
    n_data = len(data_cols)
    is_keyless = table.primary_keys == ['_pycifparse_id']

    t_start = time.perf_counter()
    if is_keyless:
        data_sel = ', '.join(f'"{c}"' for c in data_cols) if data_cols else 'NULL AS _dummy'
        arrow_tbl = db.execute(
            f'SELECT _block_id, {data_sel}'
            f' FROM "_raw_{tbl_name}" ORDER BY _block_idx, _iter_idx'
        ).fetch_arrow_table()
    else:
        pk_sel = ', '.join(f'"{pk}"' for pk in ns_pks)
        data_sel_part = (', ' + ', '.join(f'"{c}"' for c in data_cols)) if data_cols else ''
        arrow_tbl = db.execute(
            f'SELECT _block_id, _block_idx, _loop_id, _iter_idx, {pk_sel}{data_sel_part}'
            f' FROM "_raw_{tbl_name}" ORDER BY _block_idx, _loop_id, _iter_idx'
        ).fetch_arrow_table()
    t_end = time.perf_counter()
    n_rows = len(arrow_tbl)
    n_cols = len(arrow_tbl.schema)
    table_times.append((t_end - t_start, tbl_name, n_rows, n_cols))
    row_counts.append(n_rows)

table_times.sort(reverse=True)
total_query = sum(t for t, _, _, _ in table_times)
print(f"\nTop 15 slowest DuckDB queries in extract_merged_rows:")
print(f"{'Table':<35} {'Time':>7}  {'Rows':>8}  {'Cols':>5}")
print('-' * 65)
for t, tbl, rows, cols in table_times[:15]:
    print(f"  {tbl:<33} {t:>6.3f}s  {rows:>8,}  {cols:>5}")
print(f"\nTotal query time (all tables): {total_query:.3f}s")
print(f"Total rows: {sum(row_counts):,}")
