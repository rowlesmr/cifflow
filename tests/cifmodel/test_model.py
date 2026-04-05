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
