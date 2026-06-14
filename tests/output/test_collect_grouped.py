"""
Branch-coverage tests for _collect_grouped in cifflow.output.emit.

Tests call _collect_grouped directly and assert on _BlockData structure.
Branch groups (Stage 1 analysis):
  1. Empty / no-data
  2. Pure-loop blocks (keyless Set, fallback-only)
  3. Set-only source blocks (no loop tables)
  4. Basic merge / separate (keyed Set + PK-FK Loop)
  5. No-pkreach Loop with incidental Set
  6. Primary-anchor skip for incidental block
  7. Child-Set BFS in incidental block
  8. Orphan Loop table (rows span multiple fps)
  9. Single-fp Loop table absorbed into fingerprint block
 10. Multi-anchor bridge block (PK-stripping)
"""
from __future__ import annotations

import duckdb
import pytest

from cifflow import build, ingest, generate_schema
from cifflow.dictionary import DictionaryLoader
from cifflow.dictionary.schema import SchemaSpec
from cifflow.output.emit import _collect_grouped, _BlockData
from cifflow.types import CifVersion

_V = CifVersion.CIF_2_0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_schema(ddl: str) -> SchemaSpec:
    return generate_schema(DictionaryLoader().load(ddl))


def _conn(cif: str, schema: SchemaSpec | None = None) -> duckdb.DuckDBPyConnection:
    cf, errs = build(cif)
    assert not errs, errs
    conn, _ = ingest(cf, None, schema)
    return conn


def _cg(conn: duckdb.DuckDBPyConnection, schema: SchemaSpec) -> list[_BlockData]:
    return _collect_grouped(conn, schema, _V)


def _empty_schema() -> SchemaSpec:
    return SchemaSpec(tables={}, column_to_tag={})


def _find_block(blocks: list[_BlockData], **criteria) -> _BlockData | None:
    """Return first block matching ALL criteria (anchor_frozenset, name prefix, has_table)."""
    for b in blocks:
        if 'anchor' in criteria and b.anchor_frozenset != criteria['anchor']:
            continue
        if 'name_startswith' in criteria and not b.name.startswith(criteria['name_startswith']):
            continue
        if 'has_table' in criteria and criteria['has_table'] not in b.table_rows:
            continue
        if 'no_table' in criteria and criteria['no_table'] in b.table_rows:
            continue
        return b
    return None


# ---------------------------------------------------------------------------
# DDL strings
# ---------------------------------------------------------------------------

_KEYLESS_SET_DDL = """\
#\\#CIF_2.0
data_keyless_dic

save_CELL
  _definition.id        CELL
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     cell
save_

save_cell.length_a
  _definition.id        '_cell.length_a'
  _definition.class     Attribute
  _name.category_id     cell
  _name.object_id       length_a
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_
"""

_EXPT_PEAK_DDL = """\
#\\#CIF_2.0
data_ep_dic

save_EXPT
  _definition.id        EXPT
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     expt
  _category_key.name    '_expt.id'
save_

save_expt.id
  _definition.id        '_expt.id'
  _definition.class     Attribute
  _name.category_id     expt
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_expt.title
  _definition.id        '_expt.title'
  _definition.class     Attribute
  _name.category_id     expt
  _name.object_id       title
  _type.purpose         Describe
  _type.source          Recorded
  _type.container       Single
  _type.contents        Text
save_

save_PEAK
  _definition.id        PEAK
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     peak
  loop_
    _category_key.name
    '_peak.expt_id'
    '_peak.id'
save_

save_peak.id
  _definition.id        '_peak.id'
  _definition.class     Attribute
  _name.category_id     peak
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_peak.expt_id
  _definition.id        '_peak.expt_id'
  _definition.class     Attribute
  _name.category_id     peak
  _name.object_id       expt_id
  _type.purpose         Link
  _type.source          Related
  _type.container       Single
  _type.contents        Code
  _name.linked_item_id  '_expt.id'
save_

save_peak.intensity
  _definition.id        '_peak.intensity'
  _definition.class     Attribute
  _name.category_id     peak
  _name.object_id       intensity
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_
"""

# CRYSTAL (Set keyed) + ATOM (Loop, non-PK FK atom.crystal_id → crystal.id)
_INCIDENTAL_DDL = """\
#\\#CIF_2.0
data_incidental_dic

save_CRYSTAL
  _definition.id        CRYSTAL
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     crystal
  _category_key.name    '_crystal.id'
save_

save_crystal.id
  _definition.id        '_crystal.id'
  _definition.class     Attribute
  _name.category_id     crystal
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_ATOM
  _definition.id        ATOM
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     atom
  _category_key.name    '_atom.id'
save_

save_atom.id
  _definition.id        '_atom.id'
  _definition.class     Attribute
  _name.category_id     atom
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_atom.crystal_id
  _definition.id        '_atom.crystal_id'
  _definition.class     Attribute
  _name.category_id     atom
  _name.object_id       crystal_id
  _type.purpose         Link
  _type.source          Related
  _type.container       Single
  _type.contents        Code
  _name.linked_item_id  '_crystal.id'
save_
"""

# CRYSTAL (Set) + SYMMETRY (child-Set: sole domain PK crystal_id FKs to CRYSTAL)
# + ATOM (Loop, non-PK FK crystal_id → CRYSTAL)
_CHILD_SET_DDL = """\
#\\#CIF_2.0
data_child_set_dic

save_CRYSTAL
  _definition.id        CRYSTAL
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     crystal
  _category_key.name    '_crystal.id'
save_

save_crystal.id
  _definition.id        '_crystal.id'
  _definition.class     Attribute
  _name.category_id     crystal
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_SYMMETRY
  _definition.id        SYMMETRY
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     symmetry
  _category_key.name    '_symmetry.crystal_id'
save_

save_symmetry.crystal_id
  _definition.id        '_symmetry.crystal_id'
  _definition.class     Attribute
  _name.category_id     symmetry
  _name.object_id       crystal_id
  _type.purpose         Link
  _type.source          Related
  _type.container       Single
  _type.contents        Code
  _name.linked_item_id  '_crystal.id'
save_

save_symmetry.space_group
  _definition.id        '_symmetry.space_group'
  _definition.class     Attribute
  _name.category_id     symmetry
  _name.object_id       space_group
  _type.purpose         Describe
  _type.source          Recorded
  _type.container       Single
  _type.contents        Text
save_

save_ATOM
  _definition.id        ATOM
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     atom
  _category_key.name    '_atom.id'
save_

save_atom.id
  _definition.id        '_atom.id'
  _definition.class     Attribute
  _name.category_id     atom
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_atom.crystal_id
  _definition.id        '_atom.crystal_id'
  _definition.class     Attribute
  _name.category_id     atom
  _name.object_id       crystal_id
  _type.purpose         Link
  _type.source          Related
  _type.container       Single
  _type.contents        Code
  _name.linked_item_id  '_crystal.id'
save_
"""

# EXPT (Set keyed) + PEAK (Loop PK-FK to EXPT) + ELEMENT (Loop, no FK to any Set)
_ORPHAN_DDL = """\
#\\#CIF_2.0
data_orphan_dic

save_EXPT
  _definition.id        EXPT
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     expt
  _category_key.name    '_expt.id'
save_

save_expt.id
  _definition.id        '_expt.id'
  _definition.class     Attribute
  _name.category_id     expt
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_PEAK
  _definition.id        PEAK
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     peak
  loop_
    _category_key.name
    '_peak.expt_id'
    '_peak.id'
save_

save_peak.id
  _definition.id        '_peak.id'
  _definition.class     Attribute
  _name.category_id     peak
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_peak.expt_id
  _definition.id        '_peak.expt_id'
  _definition.class     Attribute
  _name.category_id     peak
  _name.object_id       expt_id
  _type.purpose         Link
  _type.source          Related
  _type.container       Single
  _type.contents        Code
  _name.linked_item_id  '_expt.id'
save_

save_ELEMENT
  _definition.id        ELEMENT
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     element
  _category_key.name    '_element.symbol'
save_

save_element.symbol
  _definition.id        '_element.symbol'
  _definition.class     Attribute
  _name.category_id     element
  _name.object_id       symbol
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_
"""

# EXPT (Set) + SAMPLE (Set) + PATTERN (Loop, composite PK-FK to both EXPT and SAMPLE)
_BRIDGE_DDL = """\
#\\#CIF_2.0
data_bridge_dic

save_EXPT
  _definition.id        EXPT
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     expt
  _category_key.name    '_expt.id'
save_

save_expt.id
  _definition.id        '_expt.id'
  _definition.class     Attribute
  _name.category_id     expt
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_expt.title
  _definition.id        '_expt.title'
  _definition.class     Attribute
  _name.category_id     expt
  _name.object_id       title
  _type.purpose         Describe
  _type.source          Recorded
  _type.container       Single
  _type.contents        Text
save_

save_SAMPLE
  _definition.id        SAMPLE
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     sample
  _category_key.name    '_sample.id'
save_

save_sample.id
  _definition.id        '_sample.id'
  _definition.class     Attribute
  _name.category_id     sample
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Code
save_

save_sample.name
  _definition.id        '_sample.name'
  _definition.class     Attribute
  _name.category_id     sample
  _name.object_id       name
  _type.purpose         Describe
  _type.source          Recorded
  _type.container       Single
  _type.contents        Text
save_

save_PATTERN
  _definition.id        PATTERN
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     pattern
  loop_
    _category_key.name
    '_pattern.expt_id'
    '_pattern.sample_id'
save_

save_pattern.expt_id
  _definition.id        '_pattern.expt_id'
  _definition.class     Attribute
  _name.category_id     pattern
  _name.object_id       expt_id
  _type.purpose         Link
  _type.source          Related
  _type.container       Single
  _type.contents        Code
  _name.linked_item_id  '_expt.id'
save_

save_pattern.sample_id
  _definition.id        '_pattern.sample_id'
  _definition.class     Attribute
  _name.category_id     pattern
  _name.object_id       sample_id
  _type.purpose         Link
  _type.source          Related
  _type.container       Single
  _type.contents        Code
  _name.linked_item_id  '_sample.id'
save_

save_pattern.counts
  _definition.id        '_pattern.counts'
  _definition.class     Attribute
  _name.category_id     pattern
  _name.object_id       counts
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_
"""


# ===========================================================================
# 1. Empty / no-data
# ===========================================================================

class TestEmpty:

    def test_bare_connection_no_tables_returns_empty_list(self):
        schema = _load_schema(_EXPT_PEAK_DDL)
        conn = duckdb.connect()
        blocks = _cg(conn, schema)
        assert blocks == []

    def test_no_schema_fallback_only_returns_pure_loop_block(self):
        # Tags not in schema go to _cif_fallback → pure_loop block with no anchor key.
        conn = _conn('#\\#CIF_2.0\ndata_test\n_foo.bar  1\n')
        blocks = _cg(conn, _empty_schema())
        assert len(blocks) == 1
        assert blocks[0].anchor_key_dict == {}


# ===========================================================================
# 2. Keyless Set → pure_loop block
# ===========================================================================

class TestKeylessSetPureLoop:

    @pytest.fixture
    def schema(self):
        return _load_schema(_KEYLESS_SET_DDL)

    def test_keyless_set_routes_to_pure_loop(self, schema):
        conn = _conn('#\\#CIF_2.0\ndata_b1\n_cell.length_a  5.4\n', schema)
        blocks = _cg(conn, schema)
        assert len(blocks) == 1
        assert 'cell' in blocks[0].table_rows

    def test_keyless_set_pure_loop_block_has_empty_anchor_key_dict(self, schema):
        conn = _conn('#\\#CIF_2.0\ndata_b1\n_cell.length_a  5.4\n', schema)
        blocks = _cg(conn, schema)
        assert blocks[0].anchor_key_dict == {}


# ===========================================================================
# 3. Set-only source block (keyed Set, no Loop tables)
# ===========================================================================

class TestSetOnlyBlock:

    @pytest.fixture
    def schema(self):
        return _load_schema(_EXPT_PEAK_DDL)

    def test_set_only_block_creates_fingerprint_block(self, schema):
        conn = _conn('#\\#CIF_2.0\ndata_e1\n_expt.id  E1\n', schema)
        blocks = _cg(conn, schema)
        fp_blocks = [b for b in blocks if b.anchor_frozenset == frozenset({'expt'})]
        assert len(fp_blocks) == 1

    def test_set_only_block_anchor_frozenset_contains_set_table(self, schema):
        conn = _conn('#\\#CIF_2.0\ndata_e1\n_expt.id  E1\n', schema)
        blocks = _cg(conn, schema)
        assert any(b.anchor_frozenset == frozenset({'expt'}) for b in blocks)

    def test_set_only_block_anchor_key_dict_has_pk_value(self, schema):
        conn = _conn('#\\#CIF_2.0\ndata_e1\n_expt.id  E1\n', schema)
        blocks = _cg(conn, schema)
        fp = _find_block(blocks, anchor=frozenset({'expt'}))
        assert fp is not None
        assert fp.anchor_key_dict.get('expt.id') == ['E1']


# ===========================================================================
# 4. Basic merge / separate (keyed Set + PK-FK Loop)
# ===========================================================================

_MERGE_CIF = (
    '#\\#CIF_2.0\n'
    'data_run1\n'
    '_expt.id  X1\n'
    'loop_\n  _peak.id\n  _peak.expt_id\n  _peak.intensity\n'
    '  p1  X1  100.0\n'
    '\n\n'
    'data_run2\n'
    '_expt.id  X1\n'
    'loop_\n  _peak.id\n  _peak.expt_id\n  _peak.intensity\n'
    '  p2  X1  200.0\n'
)

_SEPARATE_CIF = (
    '#\\#CIF_2.0\n'
    'data_run1\n'
    '_expt.id  X1\n'
    'loop_\n  _peak.id\n  _peak.expt_id\n  _peak.intensity\n'
    '  p1  X1  100.0\n'
    '\n\n'
    'data_run2\n'
    '_expt.id  X2\n'
    'loop_\n  _peak.id\n  _peak.expt_id\n  _peak.intensity\n'
    '  p2  X2  200.0\n'
)


class TestMergeAndSeparate:

    @pytest.fixture
    def schema(self):
        return _load_schema(_EXPT_PEAK_DDL)

    def test_same_set_key_produces_one_output_block(self, schema):
        conn = _conn(_MERGE_CIF, schema)
        blocks = _cg(conn, schema)
        fp_blocks = [b for b in blocks if 'expt' in b.anchor_frozenset]
        assert len(fp_blocks) == 1

    def test_merged_block_contains_all_loop_rows(self, schema):
        conn = _conn(_MERGE_CIF, schema)
        blocks = _cg(conn, schema)
        fp = _find_block(blocks, anchor=frozenset({'expt'}))
        assert fp is not None
        assert len(fp.table_rows.get('peak', [])) == 2

    def test_different_set_keys_produce_two_output_blocks(self, schema):
        conn = _conn(_SEPARATE_CIF, schema)
        blocks = _cg(conn, schema)
        fp_blocks = [b for b in blocks if 'expt' in b.anchor_frozenset]
        assert len(fp_blocks) == 2

    def test_suppress_fk_pk_is_true(self, schema):
        conn = _conn(_MERGE_CIF, schema)
        blocks = _cg(conn, schema)
        assert all(b.suppress_fk_pk for b in blocks)

    def test_suppress_all_fk_to_set_is_true(self, schema):
        conn = _conn(_MERGE_CIF, schema)
        blocks = _cg(conn, schema)
        assert all(b.suppress_all_fk_to_set for b in blocks)


# ===========================================================================
# 5. No-pkreach Loop with incidental Set
# ===========================================================================

# Block with both CRYSTAL (Set) and ATOM (Loop, non-PK FK to CRYSTAL).
# ATOM has no PK-FK path to any Set → no-pkreach arm in _block_fingerprint.
_INCIDENTAL_CIF = (
    '#\\#CIF_2.0\n'
    'data_b1\n'
    '_crystal.id  C1\n'
    'loop_\n  _atom.id\n  _atom.crystal_id\n'
    '  A1  C1\n'
    '  A2  C1\n'
)


class TestNoPKReachIncidental:

    @pytest.fixture
    def schema(self):
        return _load_schema(_INCIDENTAL_DDL)

    def test_no_pkreach_loop_source_block_goes_to_pure_loop(self, schema):
        # The source block has no PK-FK path from atom to crystal, so main_fps=[].
        # → source block routes to pure_loop_block_ids.
        conn = _conn(_INCIDENTAL_CIF, schema)
        blocks = _cg(conn, schema)
        # At least one block with empty anchor_key_dict (pure_loop or incidental).
        # Pure_loop block has 'atom' data (and 'crystal' from cache.rows_for_block).
        pure_loop = _find_block(blocks, anchor=frozenset({'crystal'}), no_table='atom')
        # The pure_loop block from b1 contains atom rows (all schema tables fetched).
        assert any('atom' in b.table_rows for b in blocks)

    def test_no_pkreach_incidental_set_creates_dedicated_block(self, schema):
        conn = _conn(_INCIDENTAL_CIF, schema)
        blocks = _cg(conn, schema)
        # An incidental block for crystal is produced with anchor_frozenset={'crystal'}.
        inc = _find_block(blocks, anchor=frozenset({'crystal'}), has_table='crystal')
        assert inc is not None

    def test_incidental_block_includes_loop_rows_via_non_pk_fk(self, schema):
        conn = _conn(_INCIDENTAL_CIF, schema)
        blocks = _cg(conn, schema)
        inc = _find_block(blocks, anchor=frozenset({'crystal'}), has_table='crystal')
        assert inc is not None
        assert 'atom' in inc.table_rows
        assert len(inc.table_rows['atom']) == 2

    def test_incidental_block_anchor_frozenset_is_set_table(self, schema):
        conn = _conn(_INCIDENTAL_CIF, schema)
        blocks = _cg(conn, schema)
        inc = _find_block(blocks, anchor=frozenset({'crystal'}), has_table='crystal')
        assert inc is not None
        assert inc.anchor_frozenset == frozenset({'crystal'})


# ===========================================================================
# 6. Primary-anchor skip for incidental block
# ===========================================================================

# Two source blocks:  b1 is Set-only (crystal.id=C1) → creates a main fingerprint.
# b2 is Loop+Set with same crystal.id via tag_presence.
# Because (crystal, C1) is already a primary fp anchor AND crystal has no child-Sets,
# the incidental block for (crystal, C1) from b2 is skipped.
_PRIMARY_SKIP_CIF = (
    '#\\#CIF_2.0\n'
    'data_b1\n'
    '_crystal.id  C1\n'
    '\n\n'
    'data_b2\n'
    '_crystal.id  C1\n'
    'loop_\n  _atom.id\n  _atom.crystal_id\n'
    '  A1  C1\n'
)

_CHILD_SET_SKIP_CIF = (
    '#\\#CIF_2.0\n'
    'data_b1\n'
    '_crystal.id  C1\n'
    '\n\n'
    'data_b2\n'
    '_crystal.id  C1\n'
    '_symmetry.crystal_id  C1\n'
    '_symmetry.space_group  Fm3m\n'
    'loop_\n  _atom.id\n  _atom.crystal_id\n'
    '  A1  C1\n'
)


class TestPrimaryAnchorSkip:

    def test_incidental_skipped_when_already_primary_anchor_no_child_sets(self):
        schema = _load_schema(_INCIDENTAL_DDL)
        conn = _conn(_PRIMARY_SKIP_CIF, schema)
        blocks = _cg(conn, schema)
        # Should have: 1 fingerprint block (crystal C1 from b1) + 1 pure_loop block (atom from b2).
        # No extra incidental block for crystal.
        crystal_blocks = [b for b in blocks if 'crystal' in b.anchor_frozenset]
        assert len(crystal_blocks) == 1  # only the fingerprint block

    def test_incidental_not_skipped_when_has_child_sets(self):
        # b1: crystal C1 Set-only → primary fp anchor for (crystal, C1).
        # b2: crystal C1 (tag_presence) + symmetry C1 (child-Set of crystal) + atom.
        # Crystal has child-Set SYMMETRY → _expand_with_child_sets != {crystal} → NOT skipped.
        schema = _load_schema(_CHILD_SET_DDL)
        conn = _conn(_CHILD_SET_SKIP_CIF, schema)
        blocks = _cg(conn, schema)
        crystal_blocks = [b for b in blocks if 'crystal' in b.anchor_frozenset]
        # Fingerprint block (b1) + incidental block (b2, not skipped because of child-Set)
        assert len(crystal_blocks) >= 2


# ===========================================================================
# 7. Child-Set BFS in incidental block
# ===========================================================================

_CHILD_BFS_CIF = (
    '#\\#CIF_2.0\n'
    'data_b1\n'
    '_crystal.id  C1\n'
    '_symmetry.crystal_id  C1\n'
    '_symmetry.space_group  Fm3m\n'
    'loop_\n  _atom.id\n  _atom.crystal_id\n'
    '  A1  C1\n'
    '  A2  C1\n'
)


class TestChildSetBFS:

    @pytest.fixture
    def schema(self):
        return _load_schema(_CHILD_SET_DDL)

    def test_child_set_collected_in_incidental_block(self, schema):
        # CRYSTAL is incidental (no PK-FK path from ATOM).
        # SYMMETRY is a child-Set of CRYSTAL → collected via BFS into the crystal incidental block.
        conn = _conn(_CHILD_BFS_CIF, schema)
        blocks = _cg(conn, schema)
        inc = _find_block(blocks, anchor=frozenset({'crystal'}), has_table='crystal')
        assert inc is not None
        assert 'symmetry' in inc.table_rows

    def test_child_set_bfs_applies_fk_filter(self, schema):
        # symmetry row's crystal_id must match the crystal anchor pk_val.
        conn = _conn(_CHILD_BFS_CIF, schema)
        blocks = _cg(conn, schema)
        inc = _find_block(blocks, anchor=frozenset({'crystal'}), has_table='crystal')
        assert inc is not None
        sym_rows = inc.table_rows.get('symmetry', [])
        assert all(r.get('crystal_id') == 'C1' for r in sym_rows)


# ===========================================================================
# 8. Orphan Loop table (rows span multiple fingerprint groups)
# ===========================================================================

# Two experiments; ELEMENT appears in both source blocks → orphan block.
# Symbols must be distinct per block so each block owns its rows (ingest
# deduplicates Loop rows by PK: e2's Fe row would belong to e1 if shared).
_ORPHAN_CIF = (
    '#\\#CIF_2.0\n'
    'data_e1\n'
    '_expt.id  E1\n'
    'loop_\n  _peak.id\n  _peak.expt_id\n  p1  E1\n'
    'loop_\n  _element.symbol\n  Fe\n  Cu\n'
    '\n\n'
    'data_e2\n'
    '_expt.id  E2\n'
    'loop_\n  _peak.id\n  _peak.expt_id\n  p2  E2\n'
    'loop_\n  _element.symbol\n  Ag\n'
)

# Only block e1 has ELEMENT rows → single-fp routing (not orphan).
_SINGLE_FP_CIF = (
    '#\\#CIF_2.0\n'
    'data_e1\n'
    '_expt.id  E1\n'
    'loop_\n  _peak.id\n  _peak.expt_id\n  p1  E1\n'
    'loop_\n  _element.symbol\n  Fe\n'
    '\n\n'
    'data_e2\n'
    '_expt.id  E2\n'
    'loop_\n  _peak.id\n  _peak.expt_id\n  p2  E2\n'
)


class TestOrphanTable:

    @pytest.fixture
    def schema(self):
        return _load_schema(_ORPHAN_DDL)

    def test_element_spanning_two_fps_produces_orphan_block(self, schema):
        conn = _conn(_ORPHAN_CIF, schema)
        blocks = _cg(conn, schema)
        orphan = _find_block(blocks, anchor=frozenset(), has_table='element')
        assert orphan is not None

    def test_orphan_block_contains_elements_from_all_source_blocks(self, schema):
        # Orphan block aggregates element rows owned by e1 (Fe, Cu) and e2 (Ag).
        conn = _conn(_ORPHAN_CIF, schema)
        blocks = _cg(conn, schema)
        orphan = _find_block(blocks, anchor=frozenset(), has_table='element')
        assert orphan is not None
        symbols = {r['symbol'] for r in orphan.table_rows['element']}
        assert 'Fe' in symbols
        assert 'Cu' in symbols
        assert 'Ag' in symbols

    def test_fingerprint_blocks_do_not_contain_orphan_table(self, schema):
        conn = _conn(_ORPHAN_CIF, schema)
        blocks = _cg(conn, schema)
        for b in blocks:
            if 'expt' in b.anchor_frozenset:
                assert 'element' not in b.table_rows


# ===========================================================================
# 9. Single-fp Loop table absorbed into fingerprint block
# ===========================================================================

class TestSingleFpTable:

    @pytest.fixture
    def schema(self):
        return _load_schema(_ORPHAN_DDL)

    def test_single_fp_element_absorbed_into_its_fp_block(self, schema):
        # ELEMENT rows are only in block e1 (fp for E1) → single_fp routing → included in E1 block.
        conn = _conn(_SINGLE_FP_CIF, schema)
        blocks = _cg(conn, schema)
        e1_block = next(
            b for b in blocks
            if b.anchor_frozenset == frozenset({'expt'})
            and b.anchor_key_dict.get('expt.id') == ['E1']
        )
        assert 'element' in e1_block.table_rows

    def test_single_fp_element_not_in_other_fp_block(self, schema):
        # E2 block should NOT contain ELEMENT rows (not in E2's source block).
        conn = _conn(_SINGLE_FP_CIF, schema)
        blocks = _cg(conn, schema)
        e2_block = next(
            b for b in blocks
            if b.anchor_frozenset == frozenset({'expt'})
            and b.anchor_key_dict.get('expt.id') == ['E2']
        )
        assert 'element' not in e2_block.table_rows

    def test_single_fp_no_orphan_block_produced(self, schema):
        # ELEMENT is in exactly one fp → single_fp_tables, not orphan_tables → no orphan block.
        conn = _conn(_SINGLE_FP_CIF, schema)
        blocks = _cg(conn, schema)
        orphan = _find_block(blocks, anchor=frozenset(), has_table='element')
        assert orphan is None


# ===========================================================================
# 10. Multi-anchor bridge block (PK-stripping for sets_with_own_block)
# ===========================================================================

# Block A: expt E1 + sample S1 + pattern(E1, S1) → bridge fp (two Set anchors).
# Block B: expt E2 only → single-anchor fp; adds 'expt' to sets_with_own_block.
# In the bridge block: expt rows are stripped to PK only (title removed);
# sample rows are kept full (sample not in sets_with_own_block).
_BRIDGE_CIF = (
    '#\\#CIF_2.0\n'
    'data_a\n'
    '_expt.id  E1\n'
    '_expt.title  "Experiment 1"\n'
    '_sample.id  S1\n'
    '_sample.name  "Sample 1"\n'
    'loop_\n  _pattern.expt_id\n  _pattern.sample_id\n  _pattern.counts\n'
    '  E1  S1  100.0\n'
    '\n\n'
    'data_b\n'
    '_expt.id  E2\n'
    '_expt.title  "Experiment 2"\n'
)


class TestMultiAnchorBridge:

    @pytest.fixture
    def schema(self):
        return _load_schema(_BRIDGE_DDL)

    def test_bridge_block_has_two_set_anchors(self, schema):
        conn = _conn(_BRIDGE_CIF, schema)
        blocks = _cg(conn, schema)
        bridge = next(
            (b for b in blocks if len(b.anchor_frozenset) == 2),
            None,
        )
        assert bridge is not None
        assert bridge.anchor_frozenset == frozenset({'expt', 'sample'})

    def test_bridge_strips_set_with_own_block_to_pk_only(self, schema):
        # expt has a single-anchor fp (block B, expt E2) → sets_with_own_block.
        # In the bridge block for A, expt rows must be stripped to PK columns only.
        conn = _conn(_BRIDGE_CIF, schema)
        blocks = _cg(conn, schema)
        bridge = next(b for b in blocks if len(b.anchor_frozenset) == 2)
        expt_row = bridge.table_rows['expt'][0]
        assert 'title' not in expt_row

    def test_bridge_keeps_full_data_for_set_not_in_sets_with_own_block(self, schema):
        # sample only appears in the bridge block → NOT in sets_with_own_block → full data kept.
        conn = _conn(_BRIDGE_CIF, schema)
        blocks = _cg(conn, schema)
        bridge = next(b for b in blocks if len(b.anchor_frozenset) == 2)
        sample_row = bridge.table_rows['sample'][0]
        assert 'name' in sample_row and sample_row['name'] is not None
