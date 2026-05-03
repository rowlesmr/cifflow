"""
Tests for CifBuilder — event-driven construction of CifFile.
"""

import pytest
from pycifparse.cifmodel.builder import CifBuilder
from pycifparse.cifmodel.model import CifBlock, CifSaveFrame
from pycifparse.types import ParseError, ValueType


def make_builder(mode='pad'):
    errors = []
    b = CifBuilder(on_error=errors.append, mode=mode)
    return b, errors


SQ = ValueType.SINGLE_QUOTED
DQ = ValueType.DOUBLE_QUOTED
ST = ValueType.STRING
ML = ValueType.MULTILINE_STRING
PH = ValueType.PLACEHOLDER


# ─────────────────────────────────────────────────────────────────────────────
# Basic block and tag handling
# ─────────────────────────────────────────────────────────────────────────────

class TestBasicBlocks:
    def test_empty_file(self):
        b, _ = make_builder()
        assert b.result.blocks == []

    def test_single_block(self):
        b, _ = make_builder()
        b.on_data_block('test')
        assert b.result.blocks == ['test']

    def test_multiple_blocks_in_order(self):
        b, _ = make_builder()
        b.on_data_block('first')
        b.on_data_block('second')
        b.on_data_block('third')
        assert b.result.blocks == ['first', 'second', 'third']

    def test_scalar_tag_value(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_cell_a')
        b.add_value('5.432', ST)
        assert b.result['d']['_cell_a'] == ['5.432']

    def test_multiple_scalars(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_x')
        b.add_value('1', ST)
        b.add_tag('_y')
        b.add_value('2', ST)
        assert b.result['d']['_x'] == ['1']
        assert b.result['d']['_y'] == ['2']

    def test_duplicate_tag_values_preserved(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_t')
        b.add_value('first', ST)
        b.add_tag('_t')
        b.add_value('second', ST)
        assert b.result['d']['_t'] == ['first', 'second']

    def test_placeholder_stored_as_string(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_t')
        b.add_value('?', PH)
        assert b.result['d']['_t'] == ['?']

    def test_values_before_block_ignored(self):
        b, _ = make_builder()
        b.add_tag('_orphan')
        b.add_value('lost', ST)
        assert b.result.blocks == []


# ─────────────────────────────────────────────────────────────────────────────
# Save frames
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveFrames:
    def test_save_frame_accessible(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.on_save_frame_start('my_frame')
        b.add_tag('_def')
        b.add_value('hello', ST)
        b.on_save_frame_end()
        sf = b.result['d']['my_frame']
        assert isinstance(sf, CifSaveFrame)
        assert sf['_def'] == ['hello']

    def test_save_frames_in_order(self):
        b, _ = make_builder()
        b.on_data_block('d')
        for name in ('f1', 'f2', 'f3'):
            b.on_save_frame_start(name)
            b.on_save_frame_end()
        assert b.result['d'].save_frames == ['f1', 'f2', 'f3']

    def test_block_and_save_frame_tags_independent(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_block_tag')
        b.add_value('block_val', ST)
        b.on_save_frame_start('f')
        b.add_tag('_frame_tag')
        b.add_value('frame_val', ST)
        b.on_save_frame_end()
        assert b.result['d']['_block_tag'] == ['block_val']
        assert '_frame_tag' not in b.result['d']
        assert b.result['d']['f']['_frame_tag'] == ['frame_val']


# ─────────────────────────────────────────────────────────────────────────────
# Loops
# ─────────────────────────────────────────────────────────────────────────────

class TestLoops:
    def test_simple_loop(self):
        b, errs = make_builder()
        b.on_data_block('d')
        b.on_loop_start(['_a', '_b'])
        for v in ('1', 'x', '2', 'y', '3', 'z'):
            b.add_value(v, ST)
        b.on_loop_end()
        assert errs == []
        assert b.result['d']['_a'] == ['1', '2', '3']
        assert b.result['d']['_b'] == ['x', 'y', 'z']

    def test_loop_in_tags_and_loops(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.on_loop_start(['_x', '_y'])
        b.add_value('a', ST)
        b.add_value('b', ST)
        b.on_loop_end()
        assert b.result['d'].loops == [['_x', '_y']]

    def test_single_tag_loop(self):
        b, errs = make_builder()
        b.on_data_block('d')
        b.on_loop_start(['_t'])
        for v in ('a', 'b', 'c'):
            b.add_value(v, ST)
        b.on_loop_end()
        assert errs == []
        assert b.result['d']['_t'] == ['a', 'b', 'c']

    def test_loop_tags_in_block_tags(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.on_loop_start(['_p', '_q'])
        b.add_value('1', ST)
        b.add_value('2', ST)
        b.on_loop_end()
        assert '_p' in b.result['d'].tags
        assert '_q' in b.result['d'].tags


class TestLoopRowCountMismatch:
    def test_pad_mode_pads_with_placeholder(self):
        b, errs = make_builder(mode='pad')
        b.on_data_block('d')
        b.on_loop_start(['_a', '_b'])
        for v in ('1', 'x', '2', 'y', '3'):  # 5 values for 2 tags → 1 missing
            b.add_value(v, ST)
        b.on_loop_end()
        assert len(errs) == 1
        assert errs[0].error_type == 'semantic'
        assert b.result['d']['_a'] == ['1', '2', '3']
        assert b.result['d']['_b'] == ['x', 'y', '?']

    def test_strict_mode_stops(self):
        b, errs = make_builder(mode='strict')
        b.on_data_block('d')
        b.on_loop_start(['_a', '_b'])
        for v in ('1', 'x', '2', 'y', '3'):
            b.add_value(v, ST)
        b.on_loop_end()
        assert len(errs) == 1
        assert errs[0].error_type == 'semantic'
        # Subsequent events ignored
        b.add_tag('_after')
        b.add_value('should_not_appear', ST)
        assert '_after' not in b.result['d']

    def test_strict_mode_loop_not_added(self):
        b, _ = make_builder(mode='strict')
        b.on_data_block('d')
        b.on_loop_start(['_a', '_b'])
        b.add_value('1', ST)  # only 1 of 2 needed
        b.on_loop_end()
        assert b.result['d'].loops == []

    def test_pad_mode_loop_added(self):
        b, _ = make_builder(mode='pad')
        b.on_data_block('d')
        b.on_loop_start(['_a', '_b'])
        b.add_value('1', ST)
        b.on_loop_end()
        assert b.result['d'].loops == [['_a', '_b']]


class TestEmptyLoop:
    def test_empty_loop_stored_in_pad_mode(self):
        # Empty loop detection is the parser's job (syntactic error).
        # The builder stores whatever it receives without re-checking.
        b, _ = make_builder(mode='pad')
        b.on_data_block('d')
        b.on_loop_start(['_x'])
        b.on_loop_end()
        assert b.result['d'].loops == [['_x']]

    def test_empty_loop_no_builder_error(self):
        # The builder does not emit its own error for empty loops;
        # that is the parser's responsibility.
        b, errs = make_builder()
        b.on_data_block('d')
        b.on_loop_start(['_x', '_y'])
        b.on_loop_end()
        assert errs == []


# ─────────────────────────────────────────────────────────────────────────────
# Container value counting in loops
# ─────────────────────────────────────────────────────────────────────────────

class TestContainerValueCounting:
    def test_list_counts_as_one_value(self):
        b, errs = make_builder()
        b.on_data_block('d')
        b.on_loop_start(['_tag'])
        b.on_list_start()
        b.add_value('a', ST)
        b.add_value('b', ST)
        b.on_list_end()        # ← this is 1 complete loop value
        b.add_value('x', ST)   # ← this is a 2nd complete loop value
        b.on_loop_end()
        assert errs == []
        vals = b.result['d']['_tag']
        assert len(vals) == 2
        assert vals[0] == ['a', 'b']
        assert vals[1] == 'x'

    def test_table_counts_as_one_value(self):
        b, errs = make_builder()
        b.on_data_block('d')
        b.on_loop_start(['_tag'])
        b.on_table_start()
        b.on_table_key('k', DQ)
        b.add_value('v', ST)
        b.on_table_end()       # ← 1 complete loop value
        b.on_loop_end()
        assert errs == []
        vals = b.result['d']['_tag']
        assert vals == [{'k': 'v'}]

    def test_nested_list_counts_as_one(self):
        b, errs = make_builder()
        b.on_data_block('d')
        b.on_loop_start(['_a', '_b'])
        b.on_list_start()
        b.on_list_start()
        b.add_value('nested', ST)
        b.on_list_end()
        b.on_list_end()        # ← 1 value for _a
        b.add_value('scalar', ST)  # ← 1 value for _b
        b.on_loop_end()
        assert errs == []
        assert b.result['d']['_a'] == [[['nested']]]
        assert b.result['d']['_b'] == ['scalar']

    def test_inner_values_not_counted_for_loop(self):
        # Values inside a container don't advance the loop index
        b, errs = make_builder()
        b.on_data_block('d')
        b.on_loop_start(['_a', '_b'])
        b.on_list_start()
        # These 10 add_value calls are all inside the list — count as 0 loop values
        for i in range(10):
            b.add_value(str(i), ST)
        b.on_list_end()          # ← now 1 loop value (for _a)
        b.add_value('second', ST)  # ← 1 loop value (for _b)
        b.on_loop_end()
        assert errs == []
        assert b.result['d']['_a'] == [list(str(i) for i in range(10))]
        assert b.result['d']['_b'] == ['second']


# ─────────────────────────────────────────────────────────────────────────────
# Multiline text transformation
# ─────────────────────────────────────────────────────────────────────────────

class TestMultilineTransformation:
    def test_plain_multiline_stored_as_is(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_t')
        b.add_value('line one\nline two', ML)
        assert b.result['d']['_t'] == ['line one\nline two']

    def test_fold_applied(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_t')
        b.add_value('\\\nfirst\\\nsecond', ML)
        assert b.result['d']['_t'] == ['firstsecond']

    def test_spec_example_applied(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_t')
        raw = (
            'prefix:\\\n'
            'prefix:data_example\n'
            'prefix:_text\n'
            'prefix:;This line was\\\n'
            'prefix: folded.\n'
            'prefix:;'
        )
        b.add_value(raw, ML)
        assert b.result['d']['_t'] == ['data_example\n_text\n;This line was folded.\n;']

    def test_non_multiline_not_transformed(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_t')
        # A single-quoted value that starts with \ must NOT be transformed
        b.add_value('\\\nwould be folded', SQ)
        assert b.result['d']['_t'] == ['\\\nwould be folded']


# ─────────────────────────────────────────────────────────────────────────────
# Duplicate block and save frame names
# ─────────────────────────────────────────────────────────────────────────────

class TestDuplicateBlockNames:
    def test_duplicate_block_emits_error(self):
        b, errs = make_builder()
        b.on_data_block('d')
        b.on_data_block('d')
        assert len(errs) == 1
        assert errs[0].error_type == 'semantic'
        assert 'd' in errs[0].message

    def test_duplicate_block_both_present(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_x')
        b.add_value('first', ST)
        b.on_data_block('d')
        b.add_tag('_x')
        b.add_value('second', ST)
        assert b.result.blocks == ['d', 'd']

    def test_duplicate_block_getitem_returns_first(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_x')
        b.add_value('first', ST)
        b.on_data_block('d')
        b.add_tag('_x')
        b.add_value('second', ST)
        assert b.result['d']['_x'] == ['first']

    def test_duplicate_block_get_all_returns_both(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_x')
        b.add_value('first', ST)
        b.on_data_block('d')
        b.add_tag('_x')
        b.add_value('second', ST)
        all_d = b.result.get_all('d')
        assert len(all_d) == 2
        assert all_d[0]['_x'] == ['first']
        assert all_d[1]['_x'] == ['second']

    def test_non_duplicate_block_no_error(self):
        b, errs = make_builder()
        b.on_data_block('a')
        b.on_data_block('b')
        assert errs == []


class TestDuplicateSaveFrameNames:
    def test_duplicate_save_frame_emits_error(self):
        b, errs = make_builder()
        b.on_data_block('d')
        b.on_save_frame_start('f')
        b.on_save_frame_end()
        b.on_save_frame_start('f')
        b.on_save_frame_end()
        assert len(errs) == 1
        assert errs[0].error_type == 'semantic'
        assert 'f' in errs[0].message

    def test_duplicate_save_frame_both_present(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.on_save_frame_start('f')
        b.add_tag('_x')
        b.add_value('first', ST)
        b.on_save_frame_end()
        b.on_save_frame_start('f')
        b.add_tag('_x')
        b.add_value('second', ST)
        b.on_save_frame_end()
        assert b.result['d'].save_frames == ['f', 'f']

    def test_duplicate_save_frame_getitem_returns_first(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.on_save_frame_start('f')
        b.add_tag('_x')
        b.add_value('first', ST)
        b.on_save_frame_end()
        b.on_save_frame_start('f')
        b.add_tag('_x')
        b.add_value('second', ST)
        b.on_save_frame_end()
        assert b.result['d']['f']['_x'] == ['first']

    def test_duplicate_save_frame_get_all_returns_both(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.on_save_frame_start('f')
        b.add_tag('_x')
        b.add_value('first', ST)
        b.on_save_frame_end()
        b.on_save_frame_start('f')
        b.add_tag('_x')
        b.add_value('second', ST)
        b.on_save_frame_end()
        all_f = b.result['d'].get_all('f')
        assert len(all_f) == 2
        assert all_f[0]['_x'] == ['first']
        assert all_f[1]['_x'] == ['second']


# ─────────────────────────────────────────────────────────────────────────────
# Canonical caseless matching
# ─────────────────────────────────────────────────────────────────────────────

class TestCaselessMatching:
    """Block/save-frame/tag names are stored as NFC(casefold(NFD(x)))."""

    def test_block_name_casefolded(self):
        b, _ = make_builder()
        b.on_data_block('ABC')
        assert b.result.blocks == ['abc']

    def test_block_lookup_caseless(self):
        b, _ = make_builder()
        b.on_data_block('ABC')
        b.add_tag('_t')
        b.add_value('1', ST)
        assert b.result['abc']['_t'] == ['1']
        assert b.result['ABC']['_t'] == ['1']

    def test_tag_name_casefolded(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_CELL.LENGTH_A')
        b.add_value('3.0', ST)
        assert '_cell.length_a' in b.result['d']
        assert '_CELL.LENGTH_A' in b.result['d']

    def test_caseless_duplicate_block_detected(self):
        b, errs = make_builder()
        b.on_data_block('ABC')
        b.on_data_block('abc')
        b.on_data_block('aBc')
        assert len(b.result.blocks) == 3
        assert all(name == 'abc' for name in b.result.blocks)
        assert len(errs) == 2

    def test_caseless_duplicate_tag_merged(self):
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_cell.length_a')
        b.add_value('3.0', ST)
        b.add_tag('_CELL.LENGTH_A')
        b.add_value('3.1', ST)
        vals = b.result['d']['_cell.length_a']
        assert len(vals) == 2
        assert vals[0] == '3.0'
        assert vals[1] == '3.1'

    def test_caseless_fixture(self):
        """pow_multiple_blocks_canonical_case.cif: 3 blocks all match 'abc'."""
        from pycifparse.cifmodel.builder import build
        import pathlib
        src = pathlib.Path('tests/cif_files/pycifparse/pow_multiple_blocks_canonical_case.cif').read_text()
        cif, errs = build(src)
        assert len(cif.blocks) == 3
        assert all(n == 'abc' for n in cif.blocks)
        assert sum(1 for e in errs if 'duplicate' in e.message) == 2
        # _cell.LENGTH_A in block 0 casefolded to same as _cell.length_a → 2 values
        block0 = cif.get_all('abc')[0]
        assert len(block0['_cell.length_a']) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Stopped-state coverage
# ─────────────────────────────────────────────────────────────────────────────

class TestStoppedState:
    """Tests that events after builder.stop() are ignored (line 143, 161, etc.)"""

    def _stopped_builder(self):
        b, errors = make_builder(mode='strict')
        b.on_data_block('d')
        # Trigger a strict-mode loop error to stop the builder
        b.on_loop_start(['_a', '_b'])
        b.add_value('1', ST)  # only one value for a 2-tag loop
        b.on_loop_end()       # incomplete row → strict mode stops
        assert b._stopped
        return b, errors

    def test_on_data_block_after_stop_ignored(self):
        # Line 143
        b, _ = self._stopped_builder()
        b.on_data_block('extra')
        assert 'extra' not in b.result.blocks

    def test_on_save_frame_start_after_stop_ignored(self):
        # Line 161
        b, _ = self._stopped_builder()
        b.on_save_frame_start('sf')
        # No crash, no save frame added (block is 'd' with no frames)

    def test_on_save_frame_end_after_stop_ignored(self):
        # Line 171
        b, _ = self._stopped_builder()
        b.on_save_frame_end()  # must not crash

    def test_on_list_start_after_stop_ignored(self):
        # Line 190
        b, _ = self._stopped_builder()
        b.on_list_start()  # must not crash

    def test_on_list_end_after_stop_ignored(self):
        # Line 195
        b, _ = self._stopped_builder()
        b.on_list_end()  # must not crash

    def test_on_table_start_after_stop_ignored(self):
        # Line 201
        b, _ = self._stopped_builder()
        b.on_table_start()  # must not crash

    def test_on_table_key_after_stop_ignored(self):
        # Line 206 (stopped branch) / Line 208 (container not a table)
        b, _ = self._stopped_builder()
        b.on_table_key('k', ST)  # must not crash

    def test_on_table_end_after_stop_ignored(self):
        # Line 213
        b, _ = self._stopped_builder()
        b.on_table_end()  # must not crash

    def test_on_loop_start_after_stop_ignored(self):
        # Line 220
        b, _ = self._stopped_builder()
        b.on_loop_start(['_x'])  # must not crash

    def test_on_loop_end_after_stop_ignored(self):
        # Line 228
        b, _ = self._stopped_builder()
        b.on_loop_end()  # must not crash


class TestEdgeCaseDispatch:
    """Cover edge cases in _dispatch_value and loop/container paths."""

    def test_table_value_assigned_when_key_set(self):
        # Line 122->125: table dispatch with current_key set
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_t')
        b.on_table_start()
        b.on_table_key('k', ST)
        b.add_value('v', ST)   # dispatches to table with current_key='k'
        b.on_table_end()
        assert b.result['d']['_t'] == [{'k': 'v'}]

    def test_loop_end_with_no_tags(self):
        # Lines 234-235: on_loop_end with n==0 (loop was started with no tags)
        b, _ = make_builder()
        b.on_data_block('d')
        b._in_loop = True
        b._loop_tags = []
        b._loop_value_index = 0
        b._loop_buffers = {}
        b.on_loop_end()  # n==0 → early return
        assert not b._in_loop

    def test_loop_end_with_null_namespace(self):
        # Line 257->260: ns is None → _add_loop not called
        b, _ = make_builder()
        # Don't call on_data_block so _block is None (ns is None)
        b._in_loop = True
        b._loop_tags = ['_a']
        b._loop_value_index = 1
        b._loop_buffers = {'_a': []}
        b.on_loop_end()  # ns is None → no _add_loop call, no crash

    def test_on_table_key_when_container_is_list_not_table(self):
        # Line 208->exit: top is a list, not _TableInProgress
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_t')
        b.on_list_start()  # push a list onto container_stack
        b.on_table_key('k', ST)  # container is a list → no-op
        b.on_list_end()

    def test_on_table_end_when_container_is_list_not_table(self):
        # Line 215->exit: top is a list (not _TableInProgress) when on_table_end called
        b, _ = make_builder()
        b.on_data_block('d')
        b.add_tag('_t')
        b.on_list_start()  # push a list
        b.on_table_end()   # pops the list, which is not a _TableInProgress

    def test_save_frame_end_with_none_save_frame(self):
        # Line 172->174: on_save_frame_end when _save_frame is None
        b, _ = make_builder()
        b.on_data_block('d')
        b._save_frame = None
        b.on_save_frame_end()  # _save_frame is None → skip _add_save_frame
