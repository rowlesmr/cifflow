"""
Integration tests — CifParser → CifBuilder → CifFile.

Uses the build() convenience function and the existing test CIF files.
"""

import pathlib
import pytest

from cifflow.cifmodel.builder import build

CIF_DIR = pathlib.Path(__file__).parent.parent / 'cif_files'
COMCIFS = CIF_DIR / 'comcifs'


def load(path: pathlib.Path) -> str:
    return path.read_text(encoding='utf-8')


# ─────────────────────────────────────────────────────────────────────────────
# Inline CIF strings
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildScalars:
    def test_single_scalar(self):
        cif, errs = build('#\\#CIF_2.0\ndata_d\n_cell_a 5.432\n')
        assert errs == []
        assert cif['d']['_cell_a'] == ['5.432']

    def test_multiple_blocks(self):
        src = '#\\#CIF_2.0\ndata_a\n_x 1\ndata_b\n_x 2\n'
        cif, errs = build(src)
        assert errs == []
        assert cif.blocks == ['a', 'b']
        assert cif['a']['_x'] == ['1']
        assert cif['b']['_x'] == ['2']

    def test_placeholder_value(self):
        cif, errs = build('#\\#CIF_2.0\ndata_d\n_t .\n')
        assert errs == []
        assert cif['d']['_t'] == ['.']

    def test_quoted_value(self):
        cif, errs = build("#\\#CIF_2.0\ndata_d\n_t 'hello world'\n")
        assert errs == []
        assert cif['d']['_t'] == ['hello world']

    def test_multiline_value(self):
        cif, errs = build('#\\#CIF_2.0\ndata_d\n_t\n;line one\nline two\n;\n')
        assert errs == []
        assert cif['d']['_t'] == ['line one\nline two']

    def test_missing_block_raises(self):
        cif, _ = build('#\\#CIF_2.0\ndata_d\n_x 1\n')
        with pytest.raises(KeyError):
            _ = cif['nonexistent']

    def test_missing_tag_raises(self):
        cif, _ = build('#\\#CIF_2.0\ndata_d\n_x 1\n')
        with pytest.raises(KeyError):
            _ = cif['d']['_missing']

    def test_empty_block_name_emits_error_and_is_accessible(self):
        cif, errs = build('#\\#CIF_2.0\ndata_\n_x 1\n')
        syntactic = [e for e in errs if e.error_type == 'syntactic']
        assert len(syntactic) == 1
        assert 'empty name' in syntactic[0].message
        assert '' in cif.blocks
        assert cif['']['_x'] == ['1']


class TestBuildLoops:
    def test_simple_loop(self):
        src = '#\\#CIF_2.0\ndata_d\nloop_ _a _b\n1 x\n2 y\n3 z\n'
        cif, errs = build(src)
        assert errs == []
        assert cif['d']['_a'] == ['1', '2', '3']
        assert cif['d']['_b'] == ['x', 'y', 'z']
        assert cif['d'].loops == [['_a', '_b']]

    def test_loop_mismatch_pad(self):
        src = '#\\#CIF_2.0\ndata_d\nloop_ _a _b\n1 x\n2 y\n3\n'
        cif, errs = build(src, mode='pad')
        assert len(errs) == 1
        assert cif['d']['_b'][-1] == '?'

    def test_loop_mismatch_strict(self):
        src = '#\\#CIF_2.0\ndata_d\nloop_ _a _b\n1 x\n2 y\n3\n_after 99\n'
        cif, errs = build(src, mode='strict')
        assert len(errs) == 1
        assert '_after' not in cif['d']

    def test_empty_loop(self):
        src = '#\\#CIF_2.0\ndata_d\n_x 1\nloop_ _a _b\ndata_next\n_y 2\n'
        cif, errs = build(src)
        assert len(errs) == 1
        assert errs[0].error_type == 'syntactic'
        assert cif['next']['_y'] == ['2']


class TestBuildSaveFrames:
    def test_save_frame(self):
        src = '#\\#CIF_2.0\ndata_d\nsave_my_frame\n_def hello\nsave_\n'
        cif, errs = build(src)
        assert errs == []
        assert cif['d'].save_frames == ['my_frame']
        assert cif['d']['my_frame']['_def'] == ['hello']

    def test_save_frame_independent_of_block(self):
        src = (
            '#\\#CIF_2.0\ndata_d\n'
            '_block_tag 1\n'
            'save_f\n_frame_tag 2\nsave_\n'
        )
        cif, _ = build(src)
        assert '_frame_tag' not in cif['d']
        assert cif['d']['f']['_frame_tag'] == ['2']


class TestBuildContainers:
    def test_list_value(self):
        src = '#\\#CIF_2.0\ndata_d\n_t [1 2 3]\n'
        cif, errs = build(src)
        assert errs == []
        assert cif['d']['_t'] == [['1', '2', '3']]

    def test_table_value(self):
        src = '#\\#CIF_2.0\ndata_d\n_t {"k": v}\n'
        cif, errs = build(src)
        assert errs == []
        assert cif['d']['_t'] == [{'k': 'v'}]

    def test_list_in_loop_counts_as_one(self):
        src = '#\\#CIF_2.0\ndata_d\nloop_ _a\n[1 2]\n[3 4]\n'
        cif, errs = build(src)
        assert errs == []
        assert cif['d']['_a'] == [['1', '2'], ['3', '4']]


# ─────────────────────────────────────────────────────────────────────────────
# Real CIF files
# ─────────────────────────────────────────────────────────────────────────────

_SMALL_FILES = [
    'single_one.cif',
    'single_many_1.cif',
    'single_many_2.cif',
    'single_list.cif',
    'multi_one.cif',
    'multi_many.cif',
    'multi_list.cif',
    'second_short.cif',
]


@pytest.mark.parametrize('filename', _SMALL_FILES)
def test_real_file_no_semantic_errors(filename):
    cif, errs = build(load(CIF_DIR / filename))
    semantic = [e for e in errs if e.error_type == 'semantic']
    assert semantic == [], f'{filename}: unexpected semantic errors: {semantic}'


@pytest.mark.parametrize('filename', _SMALL_FILES)
def test_real_file_has_blocks(filename):
    cif, _ = build(load(CIF_DIR / filename))
    assert cif.blocks, f'{filename}: expected at least one block'


class TestDuplicateNames:
    def test_duplicate_block_error(self):
        src = '#\\#CIF_2.0\ndata_d\n_x 1\ndata_d\n_x 2\n'
        cif, errs = build(src)
        semantic = [e for e in errs if e.error_type == 'semantic']
        assert len(semantic) == 1
        assert 'd' in semantic[0].message

    def test_duplicate_block_both_in_blocks(self):
        src = '#\\#CIF_2.0\ndata_d\n_x 1\ndata_d\n_x 2\n'
        cif, _ = build(src)
        assert cif.blocks == ['d', 'd']

    def test_duplicate_block_getitem_returns_first(self):
        src = '#\\#CIF_2.0\ndata_d\n_x 1\ndata_d\n_x 2\n'
        cif, _ = build(src)
        assert cif['d']['_x'] == ['1']

    def test_duplicate_block_get_all_returns_both(self):
        src = '#\\#CIF_2.0\ndata_d\n_x 1\ndata_d\n_x 2\n'
        cif, _ = build(src)
        all_d = cif.get_all('d')
        assert len(all_d) == 2
        assert all_d[0]['_x'] == ['1']
        assert all_d[1]['_x'] == ['2']

    def test_duplicate_save_frame_error(self):
        src = '#\\#CIF_2.0\ndata_d\nsave_f\n_x 1\nsave_\nsave_f\n_x 2\nsave_\n'
        cif, errs = build(src)
        semantic = [e for e in errs if e.error_type == 'semantic']
        assert len(semantic) == 1
        assert 'f' in semantic[0].message

    def test_duplicate_save_frame_get_all_returns_both(self):
        src = '#\\#CIF_2.0\ndata_d\nsave_f\n_x 1\nsave_\nsave_f\n_x 2\nsave_\n'
        cif, _ = build(src)
        all_f = cif['d'].get_all('f')
        assert len(all_f) == 2
        assert all_f[0]['_x'] == ['1']
        assert all_f[1]['_x'] == ['2']


def test_real_file_values_accessible():
    cif, _ = build(load(CIF_DIR / 'single_one.cif'))
    block = cif[cif.blocks[0]]
    for tag in block.tags:
        vals = block[tag]
        assert isinstance(vals, list)
        assert len(vals) >= 1
