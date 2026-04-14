"""
Parser tests — streaming, event-driven CIF parser.

Uses a RecordingHandler to capture the event sequence, then asserts on that
sequence (or on specific event attributes).
"""

import pathlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pytest

from pycifparse.parser.parser import CifParser
from pycifparse.types import CifParserEvents, ParseError, ValueType


# ─────────────────────────────────────────────────────────────────────────────
# Recording handler
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Event:
    name: str
    args: tuple = field(default_factory=tuple)

    def __repr__(self) -> str:
        return f'{self.name}({", ".join(repr(a) for a in self.args)})'


class RecordingHandler:
    """Captures every event emitted by the parser."""

    def __init__(self) -> None:
        self.events: List[Event] = []
        self.errors: List[ParseError] = []

    # ── CifParserEvents protocol ─────────────────────────────────────────

    def on_data_block(self, name: str) -> None:
        self.events.append(Event('on_data_block', (name,)))

    def on_save_frame_start(self, name: str) -> None:
        self.events.append(Event('on_save_frame_start', (name,)))

    def on_save_frame_end(self) -> None:
        self.events.append(Event('on_save_frame_end'))

    def add_tag(self, tag_name: str) -> None:
        self.events.append(Event('add_tag', (tag_name,)))

    def add_value(self, value: str, value_type: ValueType) -> None:
        self.events.append(Event('add_value', (value, value_type)))

    def on_list_start(self) -> None:
        self.events.append(Event('on_list_start'))

    def on_list_end(self) -> None:
        self.events.append(Event('on_list_end'))

    def on_table_start(self) -> None:
        self.events.append(Event('on_table_start'))

    def on_table_end(self) -> None:
        self.events.append(Event('on_table_end'))

    def on_table_key(self, key: str, value_type: ValueType) -> None:
        self.events.append(Event('on_table_key', (key, value_type)))

    def on_loop_start(self, tags: List[str]) -> None:
        self.events.append(Event('on_loop_start', (tags,)))

    def on_loop_end(self) -> None:
        self.events.append(Event('on_loop_end'))

    def on_error(self, error: ParseError) -> None:
        self.events.append(Event('on_error', (error,)))
        self.errors.append(error)

    # ── Helpers ──────────────────────────────────────────────────────────

    def event_names(self) -> List[str]:
        return [e.name for e in self.events]

    def non_error_events(self) -> List[Event]:
        return [e for e in self.events if e.name != 'on_error']

    def error_messages(self) -> List[str]:
        return [e.message for e in self.errors]

    def has_error_containing(self, fragment: str) -> bool:
        return any(fragment in msg for msg in self.error_messages())


def parse(source: str) -> RecordingHandler:
    h = RecordingHandler()
    CifParser(h).parse(source)
    return h


# ─────────────────────────────────────────────────────────────────────────────
# CIF file helper
# ─────────────────────────────────────────────────────────────────────────────

CIF_DIR = pathlib.Path(__file__).parent.parent / 'cif_files' / 'comcifs'


def load_cif(name: str) -> str:
    return (CIF_DIR / name).read_text(encoding='utf-8')


# ─────────────────────────────────────────────────────────────────────────────
# Minimal smoke test: empty source
# ─────────────────────────────────────────────────────────────────────────────

class TestEmptySource:
    def test_empty_string_emits_no_events(self):
        h = parse('')
        assert h.events == []

    def test_whitespace_only_emits_no_events(self):
        h = parse('   \n\n\t\n')
        assert h.events == []

    def test_comment_only_emits_no_events(self):
        h = parse('# just a comment\n')
        assert h.events == []


# ─────────────────────────────────────────────────────────────────────────────
# Data blocks
# ─────────────────────────────────────────────────────────────────────────────

class TestDataBlocks:
    def test_single_empty_data_block(self):
        h = parse('#\\#CIF_2.0\ndata_foo\n')
        assert h.non_error_events() == [Event('on_data_block', ('foo',))]
        assert h.errors == []

    def test_data_block_name_preserved_verbatim(self):
        h = parse('data_My_Block_123\n')
        assert h.events[0] == Event('on_data_block', ('My_Block_123',))

    def test_data_block_name_with_strange_chars(self):
        h = parse('data_My-[1](block)\n')
        assert h.events[0] == Event('on_data_block', ('My-[1](block)',))

    def test_empty_data_block_name(self):
        h = parse('data_\n')
        assert h.has_error_containing('empty name')
        assert any(e.name == 'on_data_block' and e.args[0] == '' for e in h.events)

    def test_multiple_data_blocks(self):
        h = parse('data_a\ndata_b\ndata_c\n')
        names = [e.args[0] for e in h.non_error_events()
                 if e.name == 'on_data_block']
        assert names == ['a', 'b', 'c']

    def test_second_data_block_closes_first(self):
        # No explicit "block close" event; just two on_data_block calls.
        h = parse('data_first\n_tag val\ndata_second\n')
        ev = h.non_error_events()
        assert ev[0] == Event('on_data_block', ('first',))
        assert ev[-1] == Event('on_data_block', ('second',))


# ─────────────────────────────────────────────────────────────────────────────
# Tag–value pairs
# ─────────────────────────────────────────────────────────────────────────────

class TestTagValue:
    def test_string_value(self):
        h = parse('data_d\n_tag unquoted\n')
        ev = h.non_error_events()
        assert ev[1] == Event('add_tag', ('_tag',))
        assert ev[2] == Event('add_value', ('unquoted', ValueType.STRING))

    def test_placeholder_dot(self):
        h = parse('data_d\n_t .\n')
        assert h.non_error_events()[2] == Event('add_value', ('.', ValueType.PLACEHOLDER))

    def test_placeholder_question(self):
        h = parse('data_d\n_t ?\n')
        assert h.non_error_events()[2] == Event('add_value', ('?', ValueType.PLACEHOLDER))

    def test_single_quoted(self):
        h = parse("data_d\n_t 'hello world'\n")
        assert h.non_error_events()[2] == Event('add_value', ('hello world', ValueType.SINGLE_QUOTED))

    def test_double_quoted(self):
        h = parse('data_d\n_t "hello world"\n')
        assert h.non_error_events()[2] == Event('add_value', ('hello world', ValueType.DOUBLE_QUOTED))

    def test_multiline_string(self):
        # The '\n' before the closing ';' is part of the text-delim; not in content.
        src = 'data_d\n_t\n;line one\nline two\n;\n'
        h = parse(src)
        assert h.non_error_events()[2] == Event(
            'add_value', ('line one\nline two', ValueType.MULTILINE_STRING))

    def test_multiline_string_empty(self):
        src = 'data_d\n_t\n;\n;\n'
        h = parse(src)
        assert h.non_error_events()[2] == Event('add_value', ('', ValueType.MULTILINE_STRING))

    def test_quoted_dot_is_not_placeholder(self):
        h = parse("data_d\n_t '.'\n")
        ev = h.non_error_events()[2]
        assert ev == Event('add_value', ('.', ValueType.SINGLE_QUOTED))

    def test_quoted_question_is_not_placeholder(self):
        h = parse('data_d\n_t "?"\n')
        ev = h.non_error_events()[2]
        assert ev == Event('add_value', ('?', ValueType.DOUBLE_QUOTED))

    def test_multiple_tags(self):
        src = 'data_d\n_a 1\n_b 2\n_c 3\n'
        h = parse(src)
        ev = h.non_error_events()
        assert ev[1] == Event('add_tag', ('_a',))
        assert ev[2] == Event('add_value', ('1', ValueType.STRING))
        assert ev[3] == Event('add_tag', ('_b',))
        assert ev[4] == Event('add_value', ('2', ValueType.STRING))
        assert ev[5] == Event('add_tag', ('_c',))
        assert ev[6] == Event('add_value', ('3', ValueType.STRING))

    def test_tag_outside_data_block_emits_error(self):
        h = parse('_tag val\n')
        assert h.has_error_containing('outside data block')

    def test_consecutive_tags_emit_error_and_placeholder(self):
        h = parse('data_d\n_a\n_b val\n')
        assert h.has_error_containing('no value')
        # _a should get a ? placeholder
        ev = h.non_error_events()
        tag_a_idx = next(i for i, e in enumerate(ev) if e == Event('add_tag', ('_a',)))
        assert ev[tag_a_idx + 1] == Event('add_value', ('?', ValueType.PLACEHOLDER))


# ─────────────────────────────────────────────────────────────────────────────
# Save frames
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveFrames:
    def test_basic_save_frame(self):
        src = 'data_d\nsave_SF\n_tag val\nsave_\n'
        h = parse(src)
        ev = h.non_error_events()
        assert ev[1] == Event('on_save_frame_start', ('SF',))
        assert ev[4] == Event('on_save_frame_end')
        assert h.errors == []

    def test_save_frame_eof_terminates_with_error(self):
        src = 'data_d\nsave_SF\n_tag val\n'
        h = parse(src)
        ev = h.non_error_events()
        assert ev[-1] == Event('on_save_frame_end')
        assert len(h.errors) == 1
        assert h.has_error_containing('unterminated')

    def test_nested_save_frame_emits_error(self):
        src = 'data_d\nsave_outer\nsave_inner\nsave_\n'
        h = parse(src)
        assert h.has_error_containing('nested save frame')
        # Both frames still emit their events.
        names = [e.name for e in h.events if 'save_frame' in e.name]
        assert 'on_save_frame_start' in names
        assert 'on_save_frame_end' in names

    def test_bare_save_outside_frame_emits_error(self):
        src = 'data_d\nsave_\n'
        h = parse(src)
        assert h.has_error_containing('outside save frame')

    def test_save_frame_outside_data_block_emits_error(self):
        src = 'save_SF\n_t v\nsave_\n'
        h = parse(src)
        assert h.has_error_containing('outside data block')

    def test_data_block_closes_save_frame(self):
        src = 'data_d\nsave_SF\n_t v\ndata_e\n'
        h = parse(src)
        ev = h.non_error_events()
        assert Event('on_save_frame_end') in ev
        assert Event('on_data_block', ('e',)) in ev
        assert h.errors == []

# ─────────────────────────────────────────────────────────────────────────────
# Loops
# ─────────────────────────────────────────────────────────────────────────────

class TestLoops:
    def test_single_column_loop(self):
        src = 'data_d\nloop_\n_x\n1\n2\n3\n'
        h = parse(src)
        ev = h.non_error_events()
        assert ev[1] == Event('on_loop_start', (['_x'],))
        assert ev[2] == Event('add_value', ('1', ValueType.STRING))
        assert ev[3] == Event('add_value', ('2', ValueType.STRING))
        assert ev[4] == Event('add_value', ('3', ValueType.STRING))
        assert ev[5] == Event('on_loop_end')

    def test_multi_column_loop(self):
        src = 'data_d\nloop_\n_a\n_b\n1 x\n2 y\n'
        h = parse(src)
        ev = h.non_error_events()
        assert ev[1] == Event('on_loop_start', (['_a', '_b'],))
        assert ev[2] == Event('add_value', ('1', ValueType.STRING))
        assert ev[3] == Event('add_value', ('x', ValueType.STRING))
        assert ev[4] == Event('add_value', ('2', ValueType.STRING))
        assert ev[5] == Event('add_value', ('y', ValueType.STRING))
        assert ev[6] == Event('on_loop_end')

    def test_loop_terminated_by_stop_(self):
        src = 'data_d\nloop_\n_x\n1 2\nstop_\n_t v\n'
        h = parse(src)
        ev = h.non_error_events()
        stop_idx = ev.index(Event('on_loop_end'))
        tag_idx  = ev.index(Event('add_tag', ('_t',)))
        assert stop_idx < tag_idx
        assert h.errors == []

    def test_stop_outside_loop_emits_error(self):
        src = 'data_d\nstop_\n'
        h = parse(src)
        assert h.has_error_containing('stop_ outside loop')

    def test_loop_terminated_by_new_loop(self):
        src = 'data_d\nloop_\n_x\n1\nloop_\n_y\n2\n'
        h = parse(src)
        ev = h.non_error_events()
        loop_ends = [e for e in ev if e.name == 'on_loop_end']
        assert len(loop_ends) == 2
        assert h.errors == []

    def test_loop_terminated_by_tag(self):
        src = 'data_d\nloop_\n_x\n1 2\n_t v\n'
        h = parse(src)
        ev = h.non_error_events()
        assert Event('on_loop_end') in ev

    def test_loop_terminated_by_data_block(self):
        src = 'data_d\nloop_\n_x\n1\ndata_e\n'
        h = parse(src)
        ev = h.non_error_events()
        assert Event('on_loop_end') in ev
        assert Event('on_data_block', ('e',)) in ev

    def test_loop_at_eof_terminates_cleanly(self):
        src = 'data_d\nloop_\n_x\n1 2 3'
        h = parse(src)
        ev = h.non_error_events()
        assert ev[-1] == Event('on_loop_end')
        assert h.errors == []

    def test_loop_with_no_tags_emits_error(self):
        src = 'data_d\nloop_\nval\n'
        h = parse(src)
        assert h.has_error_containing('no tags')

    def test_consecutive_loops_first_empty_emits_error(self):
        # First loop has no values before second loop_ terminates it — syntactic error.
        src = 'data_d\nloop_\n_x\nloop_\n_y\n1\n'
        h = parse(src)
        ev = h.non_error_events()
        loop_starts = [e for e in ev if e.name == 'on_loop_start']
        loop_ends   = [e for e in ev if e.name == 'on_loop_end']
        assert len(loop_starts) == 2
        assert len(loop_ends)   == 2
        assert len(h.errors) == 1
        assert h.has_error_containing('no values')

    def test_loop_outside_data_block_emits_error(self):
        src = 'loop_\n_x\n1\n'
        h = parse(src)
        assert h.has_error_containing('outside data block')

    def test_loop_values_include_multiline(self):
        # From simple_loops.cif: ;v2\n; is the multiline value.
        src = '#\\#CIF_2.0\ndata_d\nloop_\n_col1\n_col2\n_col3\n1 v1 ?\n2\n;v2\n; 1.0\n3 v3 x\n'
        h = parse(src)
        ev = h.non_error_events()
        vals = [e.args for e in ev if e.name == 'add_value']
        assert ('v2', ValueType.MULTILINE_STRING) in vals
        # 1.0 after closing ; is tokenised normally as STRING
        assert ('1.0', ValueType.STRING) in vals


# ─────────────────────────────────────────────────────────────────────────────
# Lists (CIF 2.0 only)
# ─────────────────────────────────────────────────────────────────────────────

class TestLists:
    def test_empty_list(self):
        h = parse('#\\#CIF_2.0\ndata_d\n_t []\n')
        ev = h.non_error_events()
        assert Event('on_list_start') in ev
        assert Event('on_list_end') in ev

    def test_single_element_list(self):
        h = parse('#\\#CIF_2.0\ndata_d\n_t [42]\n')
        ev = h.non_error_events()
        assert ev[2] == Event('on_list_start')
        assert ev[3] == Event('add_value', ('42', ValueType.STRING))
        assert ev[4] == Event('on_list_end')

    def test_multi_element_list(self):
        h = parse('#\\#CIF_2.0\ndata_d\n_t [1 2 3]\n')
        ev = h.non_error_events()
        vals = [e.args[0] for e in ev if e.name == 'add_value']
        assert vals == ['1', '2', '3']

    def test_nested_lists(self):
        h = parse('#\\#CIF_2.0\ndata_d\n_t [[1 2] [3 4]]\n')
        ev = h.non_error_events()
        names = [e.name for e in ev]
        assert names.count('on_list_start') == 3
        assert names.count('on_list_end') == 3

    def test_list_closes_active_tag(self):
        h = parse('#\\#CIF_2.0\ndata_d\n_t [1]\n_u 2\n')
        assert not h.has_error_containing('has no value')

    def test_unclosed_list_at_eof(self):
        h = parse('#\\#CIF_2.0\ndata_d\n_t [1 2')
        assert h.has_error_containing('unterminated list')
        # on_list_end is still emitted
        assert any(e.name == 'on_list_end' for e in h.events)

    def test_extra_close_bracket_emits_error(self):
        h = parse('#\\#CIF_2.0\ndata_d\n_t 5\n]\n')
        assert h.has_error_containing('no open list')

    def test_list_without_tag_emits_error_and_attaches_to_error_value(self):
        h = parse('#\\#CIF_2.0\ndata_d\n[1 2]\n')
        assert h.has_error_containing('container without preceding tag')
        assert any(e == Event('add_tag', ('_error_value',)) for e in h.events)

    def test_list_with_mixed_value_types(self):
        src = "#\\#CIF_2.0\ndata_d\n_t [bare 'sq' \"dq\" .]\n"
        h = parse(src)
        ev = h.non_error_events()
        vals = [e.args for e in ev if e.name == 'add_value']
        assert ('bare', ValueType.STRING) in vals
        assert ('sq', ValueType.SINGLE_QUOTED) in vals
        assert ('dq', ValueType.DOUBLE_QUOTED) in vals
        assert ('.', ValueType.PLACEHOLDER) in vals


# ─────────────────────────────────────────────────────────────────────────────
# Tables (CIF 2.0 only)
# ─────────────────────────────────────────────────────────────────────────────

class TestTables:
    def test_empty_table(self):
        h = parse('#\\#CIF_2.0\ndata_d\n_t {}\n')
        ev = h.non_error_events()
        assert Event('on_table_start') in ev
        assert Event('on_table_end') in ev
        assert h.errors == []

    def test_single_key_value_pair(self):
        h = parse("#\\#CIF_2.0\ndata_d\n_t {'key':val}\n")
        ev = h.non_error_events()
        assert Event('on_table_start') in ev
        assert Event('on_table_key', ('key', ValueType.SINGLE_QUOTED)) in ev
        assert Event('add_value', ('val', ValueType.STRING)) in ev
        assert Event('on_table_end') in ev
        assert h.errors == []

    def test_multiple_key_value_pairs(self):
        src = "#\\#CIF_2.0\ndata_d\n_t {'a':1 'b':2}\n"
        h = parse(src)
        ev = h.non_error_events()
        keys = [e.args[0] for e in ev if e.name == 'on_table_key']
        assert keys == ['a', 'b']

    def test_double_quoted_key(self):
        h = parse('#\\#CIF_2.0\ndata_d\n_t {"key":val}\n')
        ev = h.non_error_events()
        assert Event('on_table_key', ('key', ValueType.DOUBLE_QUOTED)) in ev

    def test_unquoted_key_emits_error(self):
        h = parse('#\\#CIF_2.0\ndata_d\n_t {key:val}\n')
        assert h.has_error_containing('unquoted')

    def test_missing_colon_emits_error(self):
        h = parse("#\\#CIF_2.0\ndata_d\n_t {'a' 'b':2}\n")
        assert h.has_error_containing('not followed by :')

    def test_table_value_is_list(self):
        h = parse("#\\#CIF_2.0\ndata_d\n_t {'k':[1 2]}\n")
        ev = h.non_error_events()
        assert Event('on_list_start') in ev
        assert Event('on_list_end') in ev
        assert h.errors == []

    def test_table_value_is_nested_table(self):
        h = parse("#\\#CIF_2.0\ndata_d\n_t {'k':{'j':v}}\n")
        ev = h.non_error_events()
        assert ev.count(Event('on_table_start')) == 2
        assert ev.count(Event('on_table_end')) == 2
        assert h.errors == []

    def test_unclosed_table_at_eof(self):
        h = parse("#\\#CIF_2.0\ndata_d\n_t {'k':v")
        assert h.has_error_containing('unterminated table')
        assert any(e.name == 'on_table_end' for e in h.events)

    def test_extra_close_brace_emits_error(self):
        h = parse('#\\#CIF_2.0\ndata_d\n_t 5\n}\n')
        assert h.has_error_containing('no open table')

    def test_table_multiline_value(self):
        src = "#\\#CIF_2.0\ndata_d\n_t {'k':\n;text\n;\n}\n"
        h = parse(src)
        ev = h.non_error_events()
        assert Event('add_value', ('text', ValueType.MULTILINE_STRING)) in ev
        assert h.errors == []


# ─────────────────────────────────────────────────────────────────────────────
# Orphan values
# ─────────────────────────────────────────────────────────────────────────────

class TestOrphanValues:
    def test_orphan_scalar_emits_error_and_error_value_tag(self):
        h = parse('data_d\norphan\n')
        assert h.has_error_containing('has no preceding tag')
        assert Event('add_tag', ('_error_value',)) in h.events

    def test_orphan_value_is_still_emitted(self):
        h = parse('data_d\norphan\n')
        assert Event('add_value', ('orphan', ValueType.STRING)) in h.events

    def test_second_orphan_also_gets_error_value_tag(self):
        h = parse('data_d\nfirst\nsecond\n')
        error_tags = [e for e in h.events if e == Event('add_tag', ('_error_value',))]
        assert len(error_tags) == 2


# ─────────────────────────────────────────────────────────────────────────────
# global_ handling
# ─────────────────────────────────────────────────────────────────────────────

class TestGlobal:
    def test_global_emits_error_and_halts(self):
        h = parse('data_d\n_t v\nglobal_\n_u w\n')
        assert h.has_error_containing('global_')
        # _u should not appear; parsing halted
        tags = [e.args[0] for e in h.events if e.name == 'add_tag']
        assert '_u' not in tags

    def test_global_closes_open_loop_before_halting(self):
        h = parse('data_d\nloop_\n_x\n1\nglobal_\n')
        assert Event('on_loop_end') in h.events

    def test_global_closes_open_save_frame_before_halting(self):
        h = parse('data_d\nsave_SF\n_t v\nglobal_\n')
        assert Event('on_save_frame_end') in h.events


# ─────────────────────────────────────────────────────────────────────────────
# Loop interactions with containers
# ─────────────────────────────────────────────────────────────────────────────

class TestLoopContainerInteractions:
    def test_list_in_loop(self):
        src = '#\\#CIF_2.0\ndata_d\nloop_\n_x\n[1 2]\n[3]\n'
        h = parse(src)
        ev = h.non_error_events()
        assert ev.count(Event('on_list_start')) == 2
        assert ev.count(Event('on_list_end')) == 2

    def test_table_in_loop(self):
        src = "#\\#CIF_2.0\ndata_d\nloop_\n_x\n{'a':1}\n{'b':2}\n"
        h = parse(src)
        ev = h.non_error_events()
        assert ev.count(Event('on_table_start')) == 2
        assert ev.count(Event('on_table_end')) == 2

    def test_unclosed_list_in_loop_closes_implicitly(self):
        src = '#\\#CIF_2.0\ndata_d\nloop_\n_x\n[1 2\n_t v\n'
        h = parse(src)
        assert any(e.name == 'on_list_end' for e in h.events)
        assert any(e.name == 'on_loop_end' for e in h.events)
        assert h.has_error_containing('implicitly closed')


# ─────────────────────────────────────────────────────────────────────────────
# CIF 1.1 version path
# ─────────────────────────────────────────────────────────────────────────────

class TestCIF11:
    def test_cif11_basic_tag_value(self):
        # No magic line → CIF 1.1 defaulting.
        h = parse('data_d\n_tag val\n')
        ev = h.non_error_events()
        assert Event('on_data_block', ('d',)) in ev
        assert Event('add_tag', ('_tag',)) in ev
        assert Event('add_value', ('val', ValueType.STRING)) in ev

    def test_cif11_single_quoted_string(self):
        h = parse("data_d\n_t 'hello'\n")
        assert Event('add_value', ('hello', ValueType.SINGLE_QUOTED)) in h.non_error_events()

    def test_cif11_list_bracket_is_string_not_list(self):
        # In CIF 1.1, [ is not a structural delimiter — it's a bare-word character.
        h = parse('data_d\n_t [notalist]\n')
        ev = h.non_error_events()
        assert not any(e.name == 'on_list_start' for e in ev)
        assert Event('add_value', ('[notalist]', ValueType.STRING)) in ev

    def test_cif11_magic_line(self):
        h = parse('#\\#CIF_1.1\ndata_d\n_t v\n')
        assert h.errors == []
        assert Event('on_data_block', ('d',)) in h.non_error_events()

    def test_unknown_cif_version_defaults_cif2(self):
        h = parse('#\\#CIF_9.9\ndata_d\n_t [1]\n')
        assert h.has_error_containing('unrecognised CIF version')
        # Defaulted to CIF 2.0 — [ is a list opener.
        assert any(e.name == 'on_list_start' for e in h.events)


# ─────────────────────────────────────────────────────────────────────────────
# CIF file smoke tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSmokeFiles:
    def test_simple_data_no_errors(self):
        src = load_cif('simple_data.cif')
        h = parse(src)
        assert h.errors == []

    def test_simple_data_block_name(self):
        src = load_cif('simple_data.cif')
        h = parse(src)
        assert Event('on_data_block', ('simple_data',)) in h.non_error_events()

    def test_simple_data_values(self):
        src = load_cif('simple_data.cif')
        h = parse(src)
        ev = h.non_error_events()
        vals_by_tag = {}
        pending = None
        for e in ev:
            if e.name == 'add_tag':
                pending = e.args[0]
            elif e.name == 'add_value' and pending:
                vals_by_tag[pending] = e.args
                pending = None

        assert vals_by_tag['_unknown_value'] == ('?', ValueType.PLACEHOLDER)
        assert vals_by_tag['_na_value'] == ('.', ValueType.PLACEHOLDER)
        assert vals_by_tag['_unquoted_string'] == ('unquoted', ValueType.STRING)
        assert vals_by_tag['_sq_string'] == ('sq', ValueType.SINGLE_QUOTED)
        assert vals_by_tag['_dq_string'] == ('dq', ValueType.DOUBLE_QUOTED)
        assert vals_by_tag['_text_string'] == ('text', ValueType.MULTILINE_STRING)
        assert vals_by_tag['_numb_plain'] == ('1.25e+03', ValueType.STRING)
        assert vals_by_tag['_numb_su'] == ('0.0625(2)', ValueType.STRING)
        assert vals_by_tag['_numb_quoted'] == ('1.0', ValueType.SINGLE_QUOTED)
        # Quoted ? and . are NOT placeholders.
        assert vals_by_tag['_query_quoted'] == ('?', ValueType.DOUBLE_QUOTED)
        assert vals_by_tag['_dot_quoted'] == ('.', ValueType.SINGLE_QUOTED)

    def test_simple_loops_no_errors(self):
        src = load_cif('simple_loops.cif')
        h = parse(src)
        assert h.errors == []

    def test_simple_loops_structure(self):
        src = load_cif('simple_loops.cif')
        h = parse(src)
        ev = h.non_error_events()
        loop_starts = [e for e in ev if e.name == 'on_loop_start']
        loop_ends   = [e for e in ev if e.name == 'on_loop_end']
        assert len(loop_starts) == 3
        assert len(loop_ends)   == 3
        assert loop_starts[0].args[0] == ['_col1', '_col2', '_col3']
        assert loop_starts[1].args[0] == ['_single']
        assert loop_starts[2].args[0] == ['_scalar_a', '_scalar_b']

    def test_simple_loops_multiline_value(self):
        """Multiline value in loop: content is 'v2', followed by '1.0' as next value."""
        src = load_cif('simple_loops.cif')
        h = parse(src)
        ev = h.non_error_events()
        vals = [e.args for e in ev if e.name == 'add_value']
        assert ('v2', ValueType.MULTILINE_STRING) in vals
        assert ('1.0', ValueType.STRING) in vals

    def test_list_data_no_errors(self):
        src = load_cif('list_data.cif')
        h = parse(src)
        assert h.errors == []

    def test_list_data_empty_lists(self):
        src = load_cif('list_data.cif')
        h = parse(src)
        ev = h.non_error_events()
        list_starts = [e for e in ev if e.name == 'on_list_start']
        # Multiple lists in the file.
        assert len(list_starts) > 0

    def test_table_data_no_errors(self):
        src = load_cif('table_data.cif')
        h = parse(src)
        assert h.errors == []

    def test_table_data_structure(self):
        src = load_cif('table_data.cif')
        h = parse(src)
        ev = h.non_error_events()
        table_starts = [e for e in ev if e.name == 'on_table_start']
        table_ends   = [e for e in ev if e.name == 'on_table_end']
        assert len(table_starts) == len(table_ends)
        assert len(table_starts) > 0

    def test_empty_file_no_events(self):
        src = load_cif('empty.cif')
        h = parse(src)
        assert h.non_error_events() == []

    def test_comment_only_no_events(self):
        src = load_cif('comment_only.cif')
        h = parse(src)
        assert h.non_error_events() == []

    def test_simple_containers_no_errors(self):
        src = load_cif('simple_containers.cif')
        h = parse(src)
        assert h.errors == []

    def test_triple_quoted_strings_no_errors(self):
        src = load_cif('triple.cif')
        h = parse(src)
        assert h.errors == []

    def test_ver2_no_errors(self):
        src = load_cif('ver2.cif')
        h = parse(src)
        assert h.errors == []

    def test_ver1_no_errors(self):
        src = load_cif('ver1.cif')
        h = parse(src)
        assert h.errors == []


# ─────────────────────────────────────────────────────────────────────────────
# Additional coverage tests
# ─────────────────────────────────────────────────────────────────────────────

class TestParserCoverageGaps:
    """Tests targeting specific uncovered branches."""

    def test_save_close_outside_save_frame_is_error(self):
        # save_ with no open frame → line 346-348 (else branch at 341)
        h = parse('data_b save_')
        assert h.has_error_containing('outside save frame')

    def test_global_keyword_is_fatal(self):
        # global_ → halts parser (lines 350-365)
        h = parse('data_b _tag global_')
        assert h.has_error_containing('global_')

    def test_global_with_open_containers_halts(self):
        # global_ while containers are open → line 356
        h = parse('#\\#CIF_2.0\ndata_b _tag [ global_')
        assert h.has_error_containing('global_')

    def test_loop_with_no_tags_is_error(self):
        # loop_ immediately followed by a value, not a tag → lines 378-382
        h = parse('data_b loop_ value')
        assert h.has_error_containing('no tags')

    def test_table_key_has_no_value_before_close(self):
        # { 'key': } — key+colon present but table closes before value → lines 191-194
        h = parse("#\\#CIF_2.0\ndata_b _t {'key': }")
        assert h.has_error_containing('no value') or h.has_error_containing('placeholder')

    def test_colon_in_table_value_position_is_error(self):
        # { 'k': 1 : } — extra colon in value position → line 566
        h = parse("#\\#CIF_2.0\ndata_b _t {'k':1 :}")
        assert h.has_error_containing('value position') or h.has_error_containing('no pending key')

    def test_container_open_in_table_key_position(self):
        # { [ ] } — list opens while table expects a key → lines 472-475
        h = parse('#\\#CIF_2.0\ndata_b _t { [ ] }')
        assert h.has_error_containing('table key position') or h.has_error_containing('table')

    def test_container_open_after_key_no_colon(self):
        # { 'key' [ ] } — after key, list opens without colon → lines 477-483
        h = parse("#\\#CIF_2.0\ndata_b _t {'key' [ ] }")
        assert h.has_error_containing('missing : separator') or h.has_error_containing('colon')

    def test_triple_quoted_key_colon_adjacent(self):
        # Triple-quoted key followed by colon — exercises lines 534-537
        h = parse('#\\#CIF_2.0\ndata_b _t {"""key""":1}')
        # Should parse and emit on_table_key
        assert 'on_table_key' in h.event_names()

    def test_colon_not_adjacent_to_key_warns(self):
        # Key and colon on separate lines — exercises line 546→556 warning path
        h = parse("#\\#CIF_2.0\ndata_b _t {'key'\n: 1}")
        # The parser warns but still emits on_table_key
        assert 'on_table_key' in h.event_names()
