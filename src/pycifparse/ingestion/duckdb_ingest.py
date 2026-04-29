"""DuckDB-based hot path for CIF ingestion.

Replaces _process_loop, _process_scalar, _apply_fk, and _merge_into.
Python handles routing and schema metadata; DuckDB handles FK propagation
and cross-block merge.
"""

from __future__ import annotations

import json
import re
import uuid as _uuid_module
from collections import defaultdict, deque
from typing import Any, Callable

import pyarrow as pa

import duckdb

from pycifparse.cifmodel.model import CifBlock
from pycifparse.dictionary.schema import SchemaSpec, TableDef


# ---------------------------------------------------------------------------
# Value encoding (duplicated from ingest.py to avoid circular import)
# ---------------------------------------------------------------------------

_CONTAINER_PREFIX = '\x00'
_SU_RE = re.compile(
    r'^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\((\d+)\)$'
)


def encode_value(value):
    if isinstance(value, (list, dict)):
        vtype = 'list' if isinstance(value, list) else 'table'
        def _enc(v):
            if isinstance(v, list): return [_enc(i) for i in v]
            if isinstance(v, dict): return {k: _enc(x) for k, x in v.items()}
            return str(v)
        return _CONTAINER_PREFIX + json.dumps(_enc(value), ensure_ascii=False), vtype
    if value in ('.', '?'):
        return value, 'placeholder'
    if value in ('"."', '"?"'):
        return value, 'double_quoted'
    return value, 'string'


def split_su(raw: str):
    m = _SU_RE.match(raw)
    if not m:
        return None
    measurand, su_digits = m.group(1), m.group(2)
    e_match = re.search(r'[eE]([+-]?\d+)$', measurand)
    exponent = int(e_match.group(1)) if e_match else 0
    mantissa = measurand[:e_match.start()] if e_match else measurand
    dot_idx = mantissa.find('.')
    decimal_places = (len(mantissa) - dot_idx - 1) if dot_idx >= 0 else 0
    total_power = exponent - decimal_places
    su_int = int(su_digits)
    if total_power >= 0:
        scaled = str(su_int * (10 ** total_power))
    else:
        abs_power = -total_power
        s = str(su_int)
        if abs_power >= len(s):
            scaled = '0.' + '0' * (abs_power - len(s)) + s
        else:
            pos = len(s) - abs_power
            scaled = s[:pos] + '.' + s[pos:]
    return measurand, scaled

_SCALARS_LOOP_ID = '__scalars__'
_SYNTHETIC = frozenset({'_block_id', '_row_id', '_pycifparse_id'})


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _non_synthetic_pks(table: TableDef) -> list[str]:
    return [c.name for c in table.columns if c.is_primary_key and not c.is_synthetic]


def _non_pk_data_cols(table: TableDef) -> list[str]:
    return [c.name for c in table.columns if not c.is_primary_key and not c.is_synthetic]


def _topo_order(schema: SchemaSpec) -> list[str]:
    """Kahn's topological sort: parents before children in FK graph."""
    in_deg: dict[str, int] = {t: 0 for t in schema.tables}
    children: dict[str, list[str]] = defaultdict(list)
    for tbl_name, table in schema.tables.items():
        seen_targets: set[str] = set()
        for fk in table.foreign_keys:
            tgt = fk.target_table
            if tgt in schema.tables and tgt != tbl_name and tgt not in seen_targets:
                in_deg[tbl_name] += 1
                children[tgt].append(tbl_name)
                seen_targets.add(tgt)
    queue = deque(t for t, d in sorted(in_deg.items()) if d == 0)
    order: list[str] = []
    while queue:
        t = queue.popleft()
        order.append(t)
        for child in sorted(children[t]):
            in_deg[child] -= 1
            if in_deg[child] == 0:
                queue.append(child)
    seen = set(order)
    order += [t for t in schema.tables if t not in seen]
    return order


def _sibling_groups(schema: SchemaSpec) -> list[list[str]]:
    """Return groups of tables sharing identical non-synthetic PK column name sets."""
    by_pk: dict[frozenset, list[str]] = defaultdict(list)
    for tbl_name, table in schema.tables.items():
        pks = frozenset(_non_synthetic_pks(table))
        if pks:
            by_pk[pks].append(tbl_name)
    return [sorted(tbls) for tbls in by_pk.values() if len(tbls) > 1]


def _loops_compatible(table_names: list[str], schema: SchemaSpec) -> bool:
    if len(table_names) <= 1:
        return True
    pk_sets = [
        frozenset(c.name for c in schema.tables[t].columns
                  if c.is_primary_key and not c.is_synthetic)
        for t in table_names
    ]
    return all(pks == pk_sets[0] for pks in pk_sets[1:])


# ---------------------------------------------------------------------------
# DuckDB setup
# ---------------------------------------------------------------------------

def setup_duckdb(schema: SchemaSpec) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB connection and create one staging table per
    schema table.  Each staging table mirrors the schema columns (all TEXT)
    plus internal tracking columns: _block_id, _block_idx, _loop_id, _iter_idx.
    """
    db = duckdb.connect(':memory:')
    ddls = []
    for tbl_name, table in schema.tables.items():
        ns_cols = [c for c in table.columns if not c.is_synthetic]
        is_keyless = table.primary_keys == ['_pycifparse_id']
        extra = '"_pycifparse_id" TEXT, ' if is_keyless else ''
        col_defs = ', '.join(f'"{c.name}" TEXT' for c in ns_cols)
        ddl = (
            f'CREATE TABLE "_raw_{tbl_name}" ('
            f'_block_id TEXT NOT NULL, _block_idx INTEGER NOT NULL, '
            f'_loop_id TEXT NOT NULL, _iter_idx INTEGER NOT NULL, '
            f'{extra}'
        )
        if col_defs:
            ddl += col_defs
        else:
            ddl += '_dummy INTEGER'
        ddl += ')'
        ddls.append(ddl)
    db.execute('; '.join(ddls))
    return db


# ---------------------------------------------------------------------------
# Block loading
# ---------------------------------------------------------------------------

def load_block_data(
    block: CifBlock,
    block_id: str,
    block_idx: int,
    schema: SchemaSpec,
    tag_to_column: dict[str, tuple[str, str]],
    su_map: dict[str, str],
    deprecated_warned: set[str],
    emit: Callable[..., None],
) -> tuple[list[dict], dict[str, list[tuple]]]:
    """Route block tag data into per-table row lists.

    Returns (fallback_rows, table_batch) where table_batch maps each table
    name to a list of (block_id, block_idx, loop_id, iter_idx, cols_dict) tuples.
    The caller accumulates batches across all blocks then calls flush_table_batches
    once — a single Arrow INSERT per table rather than one per block.
    """
    fallback_rows: list[dict] = []
    table_batch: dict[str, list[tuple]] = {}

    loop_tag_to_idx: dict[str, int] = {}
    for i, loop_tags in enumerate(block.loops):
        for tag in loop_tags:
            loop_tag_to_idx[tag] = i

    processed_loops: set[int] = set()
    set_bufs: dict[str, dict[str, Any]] = {}
    loop_scalar_bufs: dict[str, dict[str, Any]] = {}
    loop_id_counter = 0

    for tag in block.tags:
        if tag in loop_tag_to_idx:
            loop_idx = loop_tag_to_idx[tag]
            if loop_idx in processed_loops:
                continue
            processed_loops.add(loop_idx)
            loop_tags_list = block.loops[loop_idx]
            loop_id_counter += 1
            loop_id_str = f'__loop_{loop_id_counter}__'
            loop_batch = _load_loop(
                block, block_id, block_idx, loop_id_str, loop_id_counter,
                loop_tags_list, schema, tag_to_column, su_map,
                deprecated_warned, emit, fallback_rows,
            )
            for tbl, rows in loop_batch.items():
                if tbl in table_batch:
                    table_batch[tbl].extend(rows)
                else:
                    table_batch[tbl] = rows
        else:
            _load_scalar_tag(block, block_id, tag, schema, tag_to_column, su_map,
                             deprecated_warned, emit, set_bufs, loop_scalar_bufs, fallback_rows)

    # Scalar buffers → one table_batch entry per table per block
    for tbl_name, cols in set_bufs.items():
        table_batch.setdefault(tbl_name, []).append(
            (block_id, block_idx, _SCALARS_LOOP_ID, 0, cols)
        )
    for tbl_name, cols in loop_scalar_bufs.items():
        table_batch.setdefault(tbl_name, []).append(
            (block_id, block_idx, _SCALARS_LOOP_ID, 0, cols)
        )

    return fallback_rows, table_batch


def _route_tag(
    tag: str,
    schema: SchemaSpec,
    tag_to_column: dict[str, tuple[str, str]],
    deprecated_warned: set[str],
    emit: Callable[..., None],
) -> tuple[str, tuple[str, str] | None]:
    tag_lc = tag.lower()
    canonical = schema.alias_to_definition_id.get(tag_lc, tag_lc)
    if canonical in schema.deprecated_ids and tag_lc not in deprecated_warned:
        deprecated_warned.add(tag_lc)
        if canonical != tag_lc:
            emit(f"tag '{tag_lc}' is deprecated (canonical: '{canonical}')")
        else:
            emit(f"tag '{tag_lc}' is deprecated")
    return canonical, tag_to_column.get(canonical)


def _maybe_split_su(
    stored: str | None,
    canonical: str,
    su_map: dict[str, str],
) -> tuple[str | None, str | None]:
    if canonical in su_map and stored is not None:
        parts = split_su(stored)
        if parts:
            return parts  # (measurand, su)
        return stored, None
    return stored, None


def _load_scalar_tag(
    block: CifBlock,
    block_id: str,
    tag: str,
    schema: SchemaSpec,
    tag_to_column: dict[str, tuple[str, str]],
    su_map: dict[str, str],
    deprecated_warned: set[str],
    emit: Callable[..., None],
    set_bufs: dict[str, dict[str, Any]],
    loop_scalar_bufs: dict[str, dict[str, Any]],
    fallback_rows: list[dict],
) -> None:
    val = block[tag][0]
    canonical, location = _route_tag(tag, schema, tag_to_column, deprecated_warned, emit)

    if location is None:
        stored, vtype = encode_value(val)
        fallback_rows.append({
            '_block_id': block_id, '_row_id': 1, 'tag': canonical,
            'value': stored, 'value_type': vtype, 'loop_id': None, 'col_index': None,
        })
        return

    tbl_name, col_name = location
    table = schema.tables[tbl_name]
    stored, _ = encode_value(val)
    stored, su_val = _maybe_split_su(stored, canonical, su_map)

    target_buf = set_bufs if table.category_class == 'Set' else loop_scalar_bufs
    target_buf.setdefault(tbl_name, {})[col_name] = stored
    if su_val is not None:
        target_buf[tbl_name][su_map[canonical]] = su_val


def _load_loop(
    block: CifBlock,
    block_id: str,
    block_idx: int,
    loop_id: str,
    loop_id_int: int,
    loop_tags: list[str],
    schema: SchemaSpec,
    tag_to_column: dict[str, tuple[str, str]],
    su_map: dict[str, str],
    deprecated_warned: set[str],
    emit: Callable[..., None],
    fallback_rows: list[dict],
) -> dict[str, list[tuple]]:
    """Build per-table row lists for one CIF loop.

    Returns table_batch: {tbl_name: [(block_id, block_idx, loop_id, iter_idx, cols_dict), ...]}
    No DuckDB operations here — caller accumulates across all blocks and flushes once.
    """
    routing: dict[str, tuple[str, tuple[str, str] | None]] = {}
    for tag in loop_tags:
        routing[tag] = _route_tag(tag, schema, tag_to_column, deprecated_warned, emit)

    loop_tables: dict[str, list[tuple[str, str, str]]] = {}
    fallback_tags: list[tuple[str, str, int]] = []

    for col_idx, tag in enumerate(loop_tags):
        canonical, location = routing[tag]
        if location:
            tbl, col = location
            loop_tables.setdefault(tbl, []).append((col, tag, canonical))
        else:
            fallback_tags.append((tag, canonical, col_idx))

    table_names = sorted(loop_tables.keys())
    if len(table_names) > 1 and not _loops_compatible(table_names, schema):
        emit("incompatible multi-category loop; routing all tags to _cif_fallback")
        fallback_tags = [(tag, routing[tag][0], i) for i, tag in enumerate(loop_tags)]
        loop_tables = {}
        table_names = []

    n_iters = len(block[loop_tags[0]]) if loop_tags else 0
    first_tbl = table_names[0] if table_names else None

    # Pre-fetch all tag value lists once
    tag_values: dict[str, list] = {tag: block[tag] for tag in loop_tags}

    # Build per-table batch rows (single pass over iterations)
    table_batch: dict[str, list[tuple]] = {}
    for iter_idx in range(n_iters):
        for tbl_name in table_names:
            cols: dict[str, Any] = {}
            for col_name, tag, canonical in loop_tables[tbl_name]:
                val = tag_values[tag][iter_idx]
                stored, _ = encode_value(val)
                stored, su_val = _maybe_split_su(stored, canonical, su_map)
                cols[col_name] = stored
                if su_val is not None:
                    cols[su_map[canonical]] = su_val
            table_batch.setdefault(tbl_name, []).append(
                (block_id, block_idx, loop_id, iter_idx, cols)
            )

    if fallback_tags:
        fallback_tag_values: dict[str, list] = {
            tag: block[tag] for tag, _, _ in fallback_tags
            if tag not in tag_values
        }
        fallback_tag_values.update(tag_values)
        for iter_idx in range(n_iters):
            fb_row_id = iter_idx + 1
            for tag, canonical, col_idx in fallback_tags:
                val = fallback_tag_values[tag][iter_idx]
                stored, vtype = encode_value(val)
                fallback_rows.append({
                    '_block_id': block_id,
                    '_row_id': fb_row_id,
                    'tag': canonical,
                    'value': stored,
                    'value_type': vtype,
                    'loop_id': loop_id_int,
                    'col_index': col_idx,
                    'ref_table': first_tbl,
                })

    return table_batch


def flush_table_batches(
    db: duckdb.DuckDBPyConnection,
    global_batch: dict[str, list[tuple]],
    populated: set[str] | None = None,
) -> None:
    """Insert all accumulated row data into DuckDB staging tables.

    One Arrow RecordBatch INSERT per table, regardless of block count.
    Builds Arrow arrays in a single pass over rows (column-major) for efficiency.
    """
    for tbl_name, rows in global_batch.items():
        if not rows:
            continue
        col_names = sorted({k for _, _, _, _, cols in rows for k in cols})
        if not col_names:
            continue

        # Single pass over rows to build all column arrays
        n = len(rows)
        bid_list: list[str | None] = [None] * n
        bidx_list: list[int] = [0] * n
        lid_list: list[str | None] = [None] * n
        iidx_list: list[int] = [0] * n
        col_lists: dict[str, list[str | None]] = {c: [None] * n for c in col_names}

        for i, (bid, bidx, lid, iidx, cols) in enumerate(rows):
            bid_list[i] = bid
            bidx_list[i] = bidx
            lid_list[i] = lid
            iidx_list[i] = iidx
            for c, v in cols.items():
                col_lists[c][i] = v

        all_cols = ['_block_id', '_block_idx', '_loop_id', '_iter_idx'] + col_names
        arrow_batch = pa.record_batch({
            '_block_id':  pa.array(bid_list,  type=pa.string()),
            '_block_idx': pa.array(bidx_list, type=pa.int32()),
            '_loop_id':   pa.array(lid_list,  type=pa.string()),
            '_iter_idx':  pa.array(iidx_list, type=pa.int32()),
            **{c: pa.array(col_lists[c], type=pa.string()) for c in col_names},
        })
        col_list = ', '.join(f'"{c}"' for c in all_cols)
        db.register('__batch__', arrow_batch)
        db.execute(f'INSERT INTO "_raw_{tbl_name}" ({col_list}) SELECT {col_list} FROM __batch__')
        db.unregister('__batch__')
        if populated is not None:
            populated.add(tbl_name)


# ---------------------------------------------------------------------------
# FK propagation (SQL)
# ---------------------------------------------------------------------------

def _run_fk_fill_pass(
    db: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    topo: list[str],
    tag_to_column: dict[str, tuple[str, str]],
    propagate_fk: bool,
    emit: Callable[..., None],
    populated: set[str] | None = None,
) -> None:
    """One pass of FK fill: propagate parent values into child FK columns."""
    for tbl_name in topo:
        if populated is not None and tbl_name not in populated:
            continue
        table = schema.tables[tbl_name]
        col_by_name = {c.name: c for c in table.columns if not c.is_synthetic}

        # --- Single-column FKs ---
        for fk in table.foreign_keys:
            if len(fk.source_columns) != 1:
                continue
            src_col = fk.source_columns[0]
            tgt_col = fk.target_columns[0]
            col = col_by_name.get(src_col)
            if col is None:
                continue
            is_key_fk = col.is_primary_key
            if not is_key_fk and not propagate_fk:
                continue

            tgt_tbl = fk.target_table
            if tgt_tbl not in schema.tables:
                if is_key_fk:
                    emit(
                        f"FK target '{tgt_tbl}'.'{tgt_col}' not in structured schema; "
                        f"leaving NULL"
                    )
                continue

            db.execute(f"""
                UPDATE "_raw_{tbl_name}" c
                SET "{src_col}" = COALESCE(
                    (SELECT p."{tgt_col}" FROM "_raw_{tgt_tbl}" p
                     WHERE p._block_id = c._block_id
                       AND p._loop_id = c._loop_id
                       AND p._iter_idx = c._iter_idx
                       AND p."{tgt_col}" IS NOT NULL
                     LIMIT 1),
                    (SELECT p."{tgt_col}" FROM "_raw_{tgt_tbl}" p
                     WHERE p._block_id = c._block_id
                       AND p._loop_id = '{_SCALARS_LOOP_ID}'
                       AND p."{tgt_col}" IS NOT NULL
                     LIMIT 1),
                    (SELECT p."{tgt_col}" FROM "_raw_{tgt_tbl}" p
                     WHERE p._block_id = c._block_id
                       AND p."{tgt_col}" IS NOT NULL
                     ORDER BY (p._loop_id = '{_SCALARS_LOOP_ID}') DESC, p._iter_idx
                     LIMIT 1)
                )
                WHERE c."{src_col}" IS NULL
            """)

        # --- Composite FKs ---
        for fk in table.foreign_keys:
            if len(fk.source_columns) <= 1:
                continue
            is_key_fk = all(
                col_by_name.get(sc) is not None and col_by_name[sc].is_primary_key
                for sc in fk.source_columns
            )
            if not is_key_fk and not propagate_fk:
                continue

            tgt_tbl = fk.target_table
            if tgt_tbl not in schema.tables:
                continue
            if not all(sc in col_by_name for sc in fk.source_columns):
                continue

            for src_col, tgt_col in zip(fk.source_columns, fk.target_columns):
                db.execute(f"""
                    UPDATE "_raw_{tbl_name}" c
                    SET "{src_col}" = COALESCE(
                        (SELECT p."{tgt_col}" FROM "_raw_{tgt_tbl}" p
                         WHERE p._block_id = c._block_id
                           AND p._loop_id = c._loop_id
                           AND p._iter_idx = c._iter_idx
                           AND p."{tgt_col}" IS NOT NULL
                         LIMIT 1),
                        (SELECT p."{tgt_col}" FROM "_raw_{tgt_tbl}" p
                         WHERE p._block_id = c._block_id
                           AND p._loop_id = '{_SCALARS_LOOP_ID}'
                           AND p."{tgt_col}" IS NOT NULL
                         LIMIT 1),
                        (SELECT p."{tgt_col}" FROM "_raw_{tgt_tbl}" p
                         WHERE p._block_id = c._block_id
                           AND p."{tgt_col}" IS NOT NULL
                         ORDER BY (p._loop_id = '{_SCALARS_LOOP_ID}') DESC, p._iter_idx
                         LIMIT 1)
                    )
                    WHERE c."{src_col}" IS NULL
                """)

        # --- Propagation links ---
        for col_name, target_def_id, default_val in schema.propagation_links.get(tbl_name, []):
            col = col_by_name.get(col_name)
            if col is None:
                continue
            if not col.is_primary_key and not propagate_fk:
                continue
            target_loc = tag_to_column.get(target_def_id)
            if target_loc:
                tgt_tbl2, tgt_col2 = target_loc
                if tgt_tbl2 in schema.tables:
                    db.execute(f"""
                        UPDATE "_raw_{tbl_name}" c
                        SET "{col_name}" = COALESCE(
                            (SELECT p."{tgt_col2}" FROM "_raw_{tgt_tbl2}" p
                             WHERE p._block_id = c._block_id
                               AND p._loop_id = c._loop_id
                               AND p._iter_idx = c._iter_idx
                               AND p."{tgt_col2}" IS NOT NULL
                             LIMIT 1),
                            (SELECT p."{tgt_col2}" FROM "_raw_{tgt_tbl2}" p
                             WHERE p._block_id = c._block_id
                               AND p._loop_id = '{_SCALARS_LOOP_ID}'
                               AND p."{tgt_col2}" IS NOT NULL
                             LIMIT 1),
                            (SELECT p."{tgt_col2}" FROM "_raw_{tgt_tbl2}" p
                             WHERE p._block_id = c._block_id
                               AND p."{tgt_col2}" IS NOT NULL
                             ORDER BY (p._loop_id = '{_SCALARS_LOOP_ID}') DESC, p._iter_idx
                             LIMIT 1)
                        )
                        WHERE c."{col_name}" IS NULL
                    """)
            if default_val is not None:
                db.execute(f"""
                    UPDATE "_raw_{tbl_name}"
                    SET "{col_name}" = ?
                    WHERE "{col_name}" IS NULL
                """, [default_val])


def _insert_key_fk_stubs(
    db: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    topo: list[str],
    populated: set[str] | None = None,
) -> None:
    """For key-FK PK columns still NULL, insert one parent stub row per block."""
    for tbl_name in topo:
        if populated is not None and tbl_name not in populated:
            continue
        table = schema.tables[tbl_name]
        col_by_name = {c.name: c for c in table.columns if not c.is_synthetic}
        for fk in table.foreign_keys:
            if len(fk.source_columns) != 1:
                continue
            src_col = fk.source_columns[0]
            tgt_col = fk.target_columns[0]
            col = col_by_name.get(src_col)
            if col is None or not col.is_primary_key:
                continue
            tgt_tbl = fk.target_table
            if tgt_tbl not in schema.tables:
                continue
            db.execute(f"""
                INSERT INTO "_raw_{tgt_tbl}" (_block_id, _block_idx, _loop_id, _iter_idx, "{tgt_col}")
                SELECT c._block_id, MIN(c._block_idx), '{_SCALARS_LOOP_ID}', 0, gen_random_uuid()::TEXT
                FROM "_raw_{tbl_name}" c
                WHERE c."{src_col}" IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM "_raw_{tgt_tbl}" p WHERE p._block_id = c._block_id
                  )
                GROUP BY c._block_id
            """)
            if populated is not None:
                populated.add(tgt_tbl)


def propagate_fk_sql(
    db: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    tag_to_column: dict[str, tuple[str, str]],
    propagate_fk: bool,
    emit: Callable[..., None],
    populated: set[str] | None = None,
) -> None:
    """Fill missing FK/PK columns in DuckDB staging tables."""
    topo = _topo_order(schema)

    _run_fk_fill_pass(db, schema, topo, tag_to_column, propagate_fk, emit, populated)

    _insert_key_fk_stubs(db, schema, topo, populated)
    _run_fk_fill_pass(db, schema, topo, tag_to_column, propagate_fk, emit, populated)

    # --- UUID generation for remaining NULL non-synthetic PKs ---
    sibling_groups = _sibling_groups(schema)
    sibling_canonicals: dict[str, str] = {}
    for group in sibling_groups:
        canonical = group[0]
        for t in group:
            sibling_canonicals[t] = canonical

    for tbl_name, table in schema.tables.items():
        if populated is not None and tbl_name not in populated:
            continue
        for pk_col in (c for c in table.columns if c.is_primary_key and not c.is_synthetic):
            has_single_fk = any(
                len(fk.source_columns) == 1 and fk.source_columns[0] == pk_col.name
                for fk in table.foreign_keys
            )
            if has_single_fk:
                continue

            canonical_tbl = sibling_canonicals.get(tbl_name, tbl_name)
            if canonical_tbl == tbl_name:
                db.execute(f"""
                    UPDATE "_raw_{tbl_name}"
                    SET "{pk_col.name}" = gen_random_uuid()::TEXT
                    WHERE "{pk_col.name}" IS NULL
                """)
            else:
                db.execute(f"""
                    UPDATE "_raw_{tbl_name}" s
                    SET "{pk_col.name}" = (
                        SELECT c."{pk_col.name}"
                        FROM "_raw_{canonical_tbl}" c
                        WHERE c._block_id = s._block_id
                          AND c._loop_id = s._loop_id
                          AND c._iter_idx = s._iter_idx
                          AND c."{pk_col.name}" IS NOT NULL
                        LIMIT 1
                    )
                    WHERE s."{pk_col.name}" IS NULL
                """)
                db.execute(f"""
                    UPDATE "_raw_{tbl_name}"
                    SET "{pk_col.name}" = gen_random_uuid()::TEXT
                    WHERE "{pk_col.name}" IS NULL
                """)

    # --- Create stub parent rows for non-null FK values ---
    for tbl_name in topo:
        if populated is not None and tbl_name not in populated:
            continue
        table = schema.tables[tbl_name]
        col_by_name = {c.name: c for c in table.columns if not c.is_synthetic}
        for fk in table.foreign_keys:
            if len(fk.source_columns) <= 1:
                continue
            tgt_tbl = fk.target_table
            if tgt_tbl not in schema.tables:
                continue
            src_cols = fk.source_columns
            tgt_cols = fk.target_columns
            if not all(sc in col_by_name for sc in src_cols):
                continue
            src_notnull = ' AND '.join(f'c."{sc}" IS NOT NULL' for sc in src_cols)
            not_exists_match = ' AND '.join(f'p."{tc}" = c."{sc}"' for sc, tc in zip(src_cols, tgt_cols))
            tgt_col_list = ', '.join(f'"{tc}"' for tc in tgt_cols)
            src_col_ref = ', '.join(f'c."{sc}"' for sc in src_cols)
            db.execute(f"""
                INSERT INTO "_raw_{tgt_tbl}" (_block_id, _block_idx, _loop_id, _iter_idx, {tgt_col_list})
                SELECT DISTINCT c._block_id, c._block_idx, '{_SCALARS_LOOP_ID}', 0, {src_col_ref}
                FROM "_raw_{tbl_name}" c
                WHERE {src_notnull}
                  AND NOT EXISTS (
                    SELECT 1 FROM "_raw_{tgt_tbl}" p
                    WHERE {not_exists_match}
                  )
            """)
            if populated is not None:
                populated.add(tgt_tbl)

    for tbl_name in topo:
        if populated is not None and tbl_name not in populated:
            continue
        table = schema.tables[tbl_name]
        for fk in table.foreign_keys:
            if len(fk.source_columns) != 1:
                continue
            src_col = fk.source_columns[0]
            tgt_col = fk.target_columns[0]
            tgt_tbl = fk.target_table
            if tgt_tbl not in schema.tables:
                continue
            db.execute(f"""
                INSERT INTO "_raw_{tgt_tbl}" (_block_id, _block_idx, _loop_id, _iter_idx, "{tgt_col}")
                SELECT DISTINCT c._block_id, c._block_idx, '{_SCALARS_LOOP_ID}', 0, c."{src_col}"
                FROM "_raw_{tbl_name}" c
                WHERE c."{src_col}" IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM "_raw_{tgt_tbl}" p
                    WHERE p."{tgt_col}" = c."{src_col}"
                  )
            """)
            if populated is not None:
                populated.add(tgt_tbl)


# ---------------------------------------------------------------------------
# Cross-block merge + conflict detection
# ---------------------------------------------------------------------------

def extract_merged_rows(
    db: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    emit_error: Callable[..., None],
    emit: Callable[..., None],
    populated: set[str] | None = None,
    tag_presence_rows: list | None = None,
) -> dict[str, dict[tuple, dict]]:
    """Detect cross-block conflicts, merge staging tables, and return merged_rows.

    merged_rows format: {tbl_name: {pk_tuple: row_dict}}
    row_dict keys include _block_id, _row_id, and all column names.

    Uses fetch_arrow_table() instead of fetchall() to avoid Python tuple
    allocation overhead for large result sets.
    """
    merged_rows: dict[str, dict[tuple, dict]] = {}
    row_id_counters: dict[str, int] = {}

    for tbl_name, table in schema.tables.items():
        if populated is not None and tbl_name not in populated:
            continue

        is_keyless = table.primary_keys == ['_pycifparse_id']
        ns_pks = _non_synthetic_pks(table)
        data_cols = _non_pk_data_cols(table)
        n_pks = len(ns_pks)
        n_data = len(data_cols)

        if is_keyless:
            data_sel = ', '.join(f'"{c}"' for c in data_cols) if data_cols else 'NULL AS _dummy'
            arrow_tbl = db.execute(
                f'SELECT _block_id, {data_sel}'
                f' FROM "_raw_{tbl_name}" ORDER BY _block_idx, _iter_idx'
            ).fetch_arrow_table()
            n_rows = len(arrow_tbl)
            if n_rows == 0:
                continue

            block_ids = arrow_tbl.column(0).to_pylist()
            data_arrays = [arrow_tbl.column(1 + j).to_pylist() for j in range(n_data)]

            tbl_rows: dict[tuple, dict] = {}
            for i in range(n_rows):
                row_dict = {'_block_id': block_ids[i]}
                for j, col in enumerate(data_cols):
                    row_dict[col] = data_arrays[j][i]
                pid = str(_uuid_module.uuid4())
                rid = _next_rid(row_id_counters, tbl_name)
                row_dict['_pycifparse_id'] = pid
                row_dict['_row_id'] = rid
                tbl_rows[(pid,)] = row_dict
            merged_rows[tbl_name] = tbl_rows
            continue

        if not ns_pks:
            continue

        pk_sel = ', '.join(f'"{pk}"' for pk in ns_pks)
        data_sel_part = (', ' + ', '.join(f'"{c}"' for c in data_cols)) if data_cols else ''

        # Single query: fetch all raw rows via Arrow (avoids Python tuple allocation).
        # Ordered so first occurrence of each pk_key wins (no GROUP BY needed).
        arrow_tbl = db.execute(
            f'SELECT _block_id, _block_idx, _loop_id, _iter_idx, {pk_sel}{data_sel_part}'
            f' FROM "_raw_{tbl_name}" ORDER BY _block_idx, _loop_id, _iter_idx'
        ).fetch_arrow_table()
        n_rows = len(arrow_tbl)
        if n_rows == 0:
            continue

        # Materialise column arrays once (C-level; much cheaper than 500K Python tuples)
        block_id_col  = arrow_tbl.column(0).to_pylist()
        block_idx_col = arrow_tbl.column(1).to_pylist()
        loop_id_col   = arrow_tbl.column(2).to_pylist()
        iter_idx_col  = arrow_tbl.column(3).to_pylist()
        pk_arrays  = [arrow_tbl.column(4 + j).to_pylist() for j in range(n_pks)]
        dat_arrays = [arrow_tbl.column(4 + n_pks + j).to_pylist() for j in range(n_data)]

        winner_blocks: dict[tuple, str] = {}
        winner_order: dict[tuple, tuple] = {}
        winners_map: dict[tuple, list] = {}
        seen_losers: dict[tuple, list[set]] = {}
        seen_tp: set[tuple] = set()

        for i in range(n_rows):
            row_block_id: str = block_id_col[i]
            pk_key = tuple(pk_arrays[j][i] for j in range(n_pks))
            vals = tuple(dat_arrays[j][i] for j in range(n_data))

            if pk_key not in winner_blocks:
                winner_blocks[pk_key] = row_block_id
                winner_order[pk_key] = (block_idx_col[i], loop_id_col[i], iter_idx_col[i])
                winners_map[pk_key] = list(vals)
                continue

            w = winners_map[pk_key]
            for k, val in enumerate(vals):
                if val is None:
                    continue
                if w[k] is None:
                    w[k] = val
                elif w[k] != val:
                    sl = seen_losers.get(pk_key)
                    if sl is None:
                        sl = [set() for _ in data_cols]
                        seen_losers[pk_key] = sl
                    if val not in sl[k]:
                        sl[k].add(val)
                        emit_error(
                            f"merge conflict on '{tbl_name}'.'{data_cols[k]}': "
                            f"keeping '{w[k]}', ignoring '{val}'",
                            table=tbl_name,
                            column=data_cols[k],
                            key_values=dict(zip(ns_pks, pk_key)),
                        )

            if (tag_presence_rows is not None
                    and row_block_id != winner_blocks[pk_key]):
                pk_json = json.dumps(list(pk_key))
                for pk_col in ns_pks:
                    tp_key = (row_block_id, pk_col, pk_json)
                    if tp_key not in seen_tp:
                        seen_tp.add(tp_key)
                        tag_presence_rows.append((row_block_id, tbl_name, pk_col, pk_json))
                for k, col in enumerate(data_cols):
                    if vals[k] is not None:
                        tp_key = (row_block_id, col, pk_json)
                        if tp_key not in seen_tp:
                            seen_tp.add(tp_key)
                            tag_presence_rows.append((row_block_id, tbl_name, col, pk_json))

        tbl_rows = {}
        for pk_key in sorted(winner_blocks, key=lambda k: winner_order[k]):
            row_dict = dict(zip(ns_pks, pk_key))
            row_dict['_block_id'] = winner_blocks[pk_key]
            w = winners_map[pk_key]
            for k, col in enumerate(data_cols):
                row_dict[col] = w[k]
            rid = _next_rid(row_id_counters, tbl_name)
            row_dict['_row_id'] = rid
            pk = tuple(row_dict.get(k) for k in table.primary_keys)
            tbl_rows[pk] = row_dict
        merged_rows[tbl_name] = tbl_rows

    return merged_rows


def _next_rid(counters: dict[str, int], table: str) -> int:
    val = counters.get(table, 1)
    counters[table] = val + 1
    return val
