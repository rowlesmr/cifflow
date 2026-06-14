"""Branch-coverage tests for _collect_structure helper functions.

Tests target _segregate_structure_blocks and _collect_satellite_merges
in isolation using minimal _BlockData instances (no DuckDB required).
"""
import pytest

from cifflow.output.emit import (
    _BlockData,
    _collect_satellite_merges,
    _segregate_structure_blocks,
)


# ---------------------------------------------------------------------------
# _BlockData factory
# ---------------------------------------------------------------------------

def _bd(anchor_frozenset, anchor_key_dict=None, table_rows=None, name='blk'):
    return _BlockData(
        name=name,
        table_rows=table_rows or {},
        fallback_rows=[],
        anchor_frozenset=anchor_frozenset,
        anchor_key_dict=anchor_key_dict or {},
        suppress_fk_pk=True,
        suppress_all_fk_to_set=True,
    )


# ---------------------------------------------------------------------------
# Tests: _segregate_structure_blocks
# ---------------------------------------------------------------------------

class TestSegregateStructureBlocks:
    def test_structure_block_goes_to_structure_list(self):
        b = _bd(frozenset({'structure'}))
        structs, pd_phases, sgs, models, others = _segregate_structure_blocks([b])
        assert structs == [b]
        assert not pd_phases and not sgs and not models and not others

    def test_pd_phase_block_keyed_by_phase_id(self):
        b = _bd(frozenset({'pd_phase'}), anchor_key_dict={'pd_phase.id': ['p1']})
        _, pd_phases, _, _, others = _segregate_structure_blocks([b])
        assert pd_phases == {'p1': b}
        assert not others

    def test_pd_phase_block_multiple_ids(self):
        b = _bd(frozenset({'pd_phase'}), anchor_key_dict={'pd_phase.id': ['p1', 'p2']})
        _, pd_phases, _, _, _ = _segregate_structure_blocks([b])
        assert pd_phases['p1'] is b and pd_phases['p2'] is b

    def test_space_group_block_keyed_by_sg_id(self):
        b = _bd(frozenset({'space_group'}), anchor_key_dict={'space_group.id': ['sg1']})
        _, _, sgs, _, others = _segregate_structure_blocks([b])
        assert sgs == {'sg1': b}
        assert not others

    def test_model_block_with_structure_id_routed_to_model_map(self):
        b = _bd(frozenset({'model'}), table_rows={'model': [{'structure_id': 's1'}]})
        _, _, _, models, others = _segregate_structure_blocks([b])
        assert models == {'s1': [b]}
        assert not others

    def test_model_block_without_structure_id_goes_to_other(self):
        b = _bd(frozenset({'model'}), table_rows={'model': [{'label': 'M1'}]})
        _, _, _, models, others = _segregate_structure_blocks([b])
        assert not models
        assert others == [b]

    def test_model_block_empty_rows_goes_to_other(self):
        b = _bd(frozenset({'model'}), table_rows={})
        _, _, _, models, others = _segregate_structure_blocks([b])
        assert not models
        assert others == [b]

    def test_unrecognised_anchor_goes_to_other(self):
        b = _bd(frozenset({'atom_site'}))
        structs, pd_phases, sgs, models, others = _segregate_structure_blocks([b])
        assert others == [b]
        assert not structs and not pd_phases and not sgs and not models

    def test_empty_input_returns_all_empty(self):
        structs, pd_phases, sgs, models, others = _segregate_structure_blocks([])
        assert structs == [] and not pd_phases and not sgs and not models and others == []

    def test_multiple_block_types_mixed(self):
        s = _bd(frozenset({'structure'}), name='s')
        p = _bd(frozenset({'pd_phase'}), anchor_key_dict={'pd_phase.id': ['p1']}, name='p')
        o = _bd(frozenset({'diffractogram'}), name='o')
        structs, pd_phases, _, _, others = _segregate_structure_blocks([s, p, o])
        assert structs == [s]
        assert 'p1' in pd_phases
        assert others == [o]

    def test_pd_phase_block_with_no_phase_id_goes_nowhere(self):
        # anchor_key_dict has no 'pd_phase.id' key → no entry in pd_phases, not in others either
        b = _bd(frozenset({'pd_phase'}), anchor_key_dict={})
        _, pd_phases, _, _, others = _segregate_structure_blocks([b])
        assert not pd_phases
        assert not others


# ---------------------------------------------------------------------------
# Tests: _collect_satellite_merges
# ---------------------------------------------------------------------------

class TestCollectSatelliteMerges:
    def _structure_block(self, rows):
        return _bd(frozenset({'structure'}), table_rows={'structure': rows})

    def test_matching_phase_id_absorbed(self):
        phase_blk = _bd(frozenset({'pd_phase'}))
        struct = self._structure_block([{'phase_id': 'p1', 'space_group_id': None, 'id': 's1'}])
        consumed = set()
        to_merge = _collect_satellite_merges(struct, {'p1': phase_blk}, {}, {}, consumed)
        assert phase_blk in to_merge
        assert id(phase_blk) in consumed

    def test_placeholder_phase_id_not_absorbed(self):
        phase_blk = _bd(frozenset({'pd_phase'}))
        struct = self._structure_block([{'phase_id': '.', 'space_group_id': None, 'id': 's1'}])
        consumed = set()
        to_merge = _collect_satellite_merges(struct, {'.': phase_blk}, {}, {}, consumed)
        assert not to_merge

    def test_unknown_phase_id_not_absorbed(self):
        phase_blk = _bd(frozenset({'pd_phase'}))
        struct = self._structure_block([{'phase_id': 'p1', 'space_group_id': None, 'id': 's1'}])
        consumed = set()
        to_merge = _collect_satellite_merges(struct, {'p2': phase_blk}, {}, {}, consumed)
        assert not to_merge

    def test_matching_space_group_id_absorbed(self):
        sg_blk = _bd(frozenset({'space_group'}))
        struct = self._structure_block([{'phase_id': None, 'space_group_id': 'sg1', 'id': 's1'}])
        consumed = set()
        to_merge = _collect_satellite_merges(struct, {}, {'sg1': sg_blk}, {}, consumed)
        assert sg_blk in to_merge
        assert id(sg_blk) in consumed

    def test_single_model_referent_absorbed(self):
        model_blk = _bd(frozenset({'model'}))
        struct = self._structure_block([{'phase_id': None, 'space_group_id': None, 'id': 's1'}])
        consumed = set()
        to_merge = _collect_satellite_merges(struct, {}, {}, {'s1': [model_blk]}, consumed)
        assert model_blk in to_merge
        assert id(model_blk) in consumed

    def test_multiple_model_referents_not_absorbed(self):
        m1 = _bd(frozenset({'model'}), name='m1')
        m2 = _bd(frozenset({'model'}), name='m2')
        struct = self._structure_block([{'phase_id': None, 'space_group_id': None, 'id': 's1'}])
        consumed = set()
        to_merge = _collect_satellite_merges(struct, {}, {}, {'s1': [m1, m2]}, consumed)
        assert not to_merge

    def test_no_matching_satellites_returns_empty(self):
        struct = self._structure_block([{'phase_id': None, 'space_group_id': None, 'id': 's1'}])
        consumed = set()
        to_merge = _collect_satellite_merges(struct, {}, {}, {}, consumed)
        assert to_merge == []
        assert not consumed

    def test_same_satellite_not_added_twice(self):
        # Two structure rows referencing same phase block
        phase_blk = _bd(frozenset({'pd_phase'}))
        struct = self._structure_block([
            {'phase_id': 'p1', 'space_group_id': None, 'id': 's1'},
            {'phase_id': 'p1', 'space_group_id': None, 'id': 's1'},
        ])
        consumed = set()
        to_merge = _collect_satellite_merges(struct, {'p1': phase_blk}, {}, {}, consumed)
        assert to_merge.count(phase_blk) == 1

    def test_no_structure_rows_returns_empty(self):
        struct = _bd(frozenset({'structure'}), table_rows={'structure': []})
        consumed = set()
        to_merge = _collect_satellite_merges(struct, {}, {}, {}, consumed)
        assert to_merge == []
