"""
Tests for CifFile, CifBlock, CifSaveFrame data structures.
"""

import pytest
from pycifparse.cifmodel.model import CifBlock, CifFile, CifSaveFrame


# ─────────────────────────────────────────────────────────────────────────────
# CifFile
# ─────────────────────────────────────────────────────────────────────────────

class TestCifFile:
    def test_empty_blocks(self):
        f = CifFile()
        assert f.blocks == []

    def test_blocks_in_order(self):
        f = CifFile()
        f._add_block(CifBlock('a'))
        f._add_block(CifBlock('b'))
        f._add_block(CifBlock('c'))
        assert f.blocks == ['a', 'b', 'c']

    def test_getitem(self):
        f = CifFile()
        b = CifBlock('my_block')
        f._add_block(b)
        assert f['my_block'] is b

    def test_getitem_missing_raises(self):
        f = CifFile()
        with pytest.raises(KeyError):
            _ = f['nonexistent']

    def test_contains(self):
        f = CifFile()
        f._add_block(CifBlock('x'))
        assert 'x' in f
        assert 'y' not in f


# ─────────────────────────────────────────────────────────────────────────────
# CifBlock / CifSaveFrame
# ─────────────────────────────────────────────────────────────────────────────

class TestCifBlock:
    def setup_method(self):
        self.b = CifBlock('test')

    def test_name(self):
        assert self.b.name == 'test'

    def test_empty_tags(self):
        assert self.b.tags == []

    def test_append_value(self):
        self.b._append_value('_tag', 'val')
        assert self.b['_tag'] == ['val']

    def test_multiple_values_same_tag(self):
        self.b._append_value('_tag', 'a')
        self.b._append_value('_tag', 'b')
        assert self.b['_tag'] == ['a', 'b']

    def test_tags_in_order(self):
        self.b._append_value('_z', '1')
        self.b._append_value('_a', '2')
        self.b._append_value('_m', '3')
        assert self.b.tags == ['_z', '_a', '_m']

    def test_missing_tag_raises(self):
        with pytest.raises(KeyError):
            _ = self.b['_missing']

    def test_contains_tag(self):
        self.b._append_value('_t', 'v')
        assert '_t' in self.b
        assert '_other' not in self.b

    def test_loops_empty(self):
        assert self.b.loops == []

    def test_add_loop(self):
        buffers = {'_a': ['1', '2'], '_b': ['x', 'y']}
        self.b._add_loop(['_a', '_b'], buffers)
        assert self.b.loops == [['_a', '_b']]
        assert self.b['_a'] == ['1', '2']
        assert self.b['_b'] == ['x', 'y']

    def test_multiple_loops(self):
        self.b._add_loop(['_x'], {'_x': ['1', '2', '3']})
        self.b._add_loop(['_y', '_z'], {'_y': ['a'], '_z': ['b']})
        assert self.b.loops == [['_x'], ['_y', '_z']]

    def test_save_frames_empty(self):
        assert self.b.save_frames == []

    def test_add_save_frame(self):
        sf = CifSaveFrame('my_frame')
        self.b._add_save_frame(sf)
        assert self.b.save_frames == ['my_frame']
        assert self.b['my_frame'] is sf

    def test_save_frame_missing_raises(self):
        with pytest.raises(KeyError):
            _ = self.b['no_such_frame']

    def test_tag_and_save_frame_dispatch(self):
        # _tag routes to tags; frame_name routes to save frames
        self.b._append_value('_cell', 'abc')
        sf = CifSaveFrame('frame1')
        self.b._add_save_frame(sf)
        assert self.b['_cell'] == ['abc']
        assert self.b['frame1'] is sf


class TestCifSaveFrame:
    def test_save_frame_has_no_save_frames(self):
        sf = CifSaveFrame('f')
        assert not hasattr(sf, 'save_frames') or not isinstance(sf, CifBlock)

    def test_loops_independent(self):
        sf = CifSaveFrame('f')
        sf._add_loop(['_t'], {'_t': ['v']})
        assert sf.loops == [['_t']]
        assert sf['_t'] == ['v']


# ─────────────────────────────────────────────────────────────────────────────
# Duplicate block names
# ─────────────────────────────────────────────────────────────────────────────

class TestDuplicateBlocks:
    def test_duplicate_block_both_in_blocks(self):
        f = CifFile()
        f._add_block(CifBlock('a'))
        f._add_block(CifBlock('a'))
        assert f.blocks == ['a', 'a']

    def test_duplicate_block_getitem_returns_first(self):
        f = CifFile()
        b1 = CifBlock('a')
        b2 = CifBlock('a')
        b1._append_value('_x', 'first')
        b2._append_value('_x', 'second')
        f._add_block(b1)
        f._add_block(b2)
        assert f['a']['_x'] == ['first']

    def test_duplicate_block_get_all_returns_both(self):
        f = CifFile()
        b1 = CifBlock('a')
        b2 = CifBlock('a')
        b1._append_value('_x', 'first')
        b2._append_value('_x', 'second')
        f._add_block(b1)
        f._add_block(b2)
        all_a = f.get_all('a')
        assert len(all_a) == 2
        assert all_a[0]['_x'] == ['first']
        assert all_a[1]['_x'] == ['second']

    def test_duplicate_block_ids_distinct(self):
        f = CifFile()
        b1 = CifBlock('a')
        b2 = CifBlock('a')
        f._add_block(b1)
        f._add_block(b2)
        assert b1._id != b2._id

    def test_add_block_returns_duplicate_flag(self):
        f = CifFile()
        assert f._add_block(CifBlock('a')) is False
        assert f._add_block(CifBlock('a')) is True

    def test_get_all_non_duplicate_returns_one(self):
        f = CifFile()
        f._add_block(CifBlock('a'))
        assert len(f.get_all('a')) == 1

    def test_get_all_missing_name_returns_empty(self):
        f = CifFile()
        assert f.get_all('nonexistent') == []


# ─────────────────────────────────────────────────────────────────────────────
# Duplicate save frame names
# ─────────────────────────────────────────────────────────────────────────────

class TestDuplicateSaveFrames:
    def setup_method(self):
        self.b = CifBlock('d')

    def test_duplicate_save_frame_both_in_save_frames(self):
        self.b._add_save_frame(CifSaveFrame('f'))
        self.b._add_save_frame(CifSaveFrame('f'))
        assert self.b.save_frames == ['f', 'f']

    def test_duplicate_save_frame_getitem_returns_first(self):
        sf1 = CifSaveFrame('f')
        sf2 = CifSaveFrame('f')
        sf1._append_value('_x', 'first')
        sf2._append_value('_x', 'second')
        self.b._add_save_frame(sf1)
        self.b._add_save_frame(sf2)
        assert self.b['f']['_x'] == ['first']

    def test_duplicate_save_frame_get_all_returns_both(self):
        sf1 = CifSaveFrame('f')
        sf2 = CifSaveFrame('f')
        sf1._append_value('_x', 'first')
        sf2._append_value('_x', 'second')
        self.b._add_save_frame(sf1)
        self.b._add_save_frame(sf2)
        all_f = self.b.get_all('f')
        assert len(all_f) == 2
        assert all_f[0]['_x'] == ['first']
        assert all_f[1]['_x'] == ['second']

    def test_duplicate_save_frame_ids_distinct(self):
        sf1 = CifSaveFrame('f')
        sf2 = CifSaveFrame('f')
        self.b._add_save_frame(sf1)
        self.b._add_save_frame(sf2)
        assert sf1._id != sf2._id

    def test_add_save_frame_returns_duplicate_flag(self):
        assert self.b._add_save_frame(CifSaveFrame('f')) is False
        assert self.b._add_save_frame(CifSaveFrame('f')) is True

    def test_get_all_non_duplicate_returns_one(self):
        self.b._add_save_frame(CifSaveFrame('f'))
        assert len(self.b.get_all('f')) == 1

    def test_get_all_missing_name_returns_empty(self):
        assert self.b.get_all('nonexistent') == []
