"""
Malformed-input tests — parser and lexer recovery.

Tests against tests/cif_files/malformed/.  Each test verifies that the parser
does not crash, the correct errors are emitted, and that tags around a malformed
region survive or are swallowed as expected.
"""

import pathlib

import pytest

from pycifparse.types import ValueType
from tests.parser.test_parser import Event, RecordingHandler, parse

MALFORMED = pathlib.Path(__file__).parent.parent / 'cif_files' / 'malformed'


def load(filename: str) -> str:
    return (MALFORMED / filename).read_text(encoding='utf-8')


def tag_names(h: RecordingHandler) -> list[str]:
    return [e.args[0] for e in h.events if e.name == 'add_tag']


def value_after_tag(h: RecordingHandler, tag: str):
    """Return the (value, value_type) emitted immediately after add_tag(tag)."""
    evs = h.events
    for i, e in enumerate(evs):
        if e.name == 'add_tag' and e.args[0] == tag:
            for ev in evs[i + 1:]:
                if ev.name == 'add_value':
                    return ev.args
                if ev.name == 'add_tag':
                    break
    return None


# ─────────────────────────────────────────────────────────────────────────────
# loops.cif
# ─────────────────────────────────────────────────────────────────────────────

class TestMalformedLoops:
    """
    Loop error cases — all IR concerns; the parser must not error on any of them.

    data_malformed_loop_1: value count (5) not divisible by tag count (2).
    data_malformed_loop_2: empty loop — zero values for two declared tags.
    """

    def setup_method(self):
        self.h = parse(load('loops.cif'))

    def test_no_crash(self):
        pass  # setup_method would have raised

    def test_no_parser_errors(self):
        # Row-count and empty-loop validation are the IR's responsibility.
        assert self.h.errors == []

    # ── data_malformed_loop_1: unbalanced value count ────────────────────────

    def test_surrounding_scalars(self):
        assert value_after_tag(self.h, '_tag1') == ('123.4', ValueType.STRING)
        assert value_after_tag(self.h, '_tag4') == ('456.4', ValueType.STRING)

    def test_loop_start_tags(self):
        assert Event('on_loop_start', (['_tag2', '_tag3'],)) in self.h.events

    def test_loop_five_values(self):
        loop_start = next(
            i for i, e in enumerate(self.h.events) if e.name == 'on_loop_start'
        )
        loop_end = next(
            i for i, e in enumerate(self.h.events) if e.name == 'on_loop_end'
        )
        loop_values = [
            e for e in self.h.events[loop_start:loop_end] if e.name == 'add_value'
        ]
        assert len(loop_values) == 5

    # ── data_malformed_loop_2: empty loop ────────────────────────────────────

    def test_empty_loop_block_present(self):
        blocks = [e.args[0] for e in self.h.events if e.name == 'on_data_block']
        assert 'malformed_loop_2' in blocks

    def test_empty_loop_emits_loop_start_and_end(self):
        # Find the on_loop_start in the second block (after malformed_loop_2)
        block2_idx = next(
            i for i, e in enumerate(self.h.events)
            if e.name == 'on_data_block' and e.args[0] == 'malformed_loop_2'
        )
        events_in_block2 = self.h.events[block2_idx:]
        assert Event('on_loop_start', (['_tag2', '_tag3'],)) in events_in_block2
        assert Event('on_loop_end') in events_in_block2

    def test_empty_loop_has_zero_values(self):
        block2_idx = next(
            i for i, e in enumerate(self.h.events)
            if e.name == 'on_data_block' and e.args[0] == 'malformed_loop_2'
        )
        loop_start = next(
            i for i, e in enumerate(self.h.events[block2_idx:], block2_idx)
            if e.name == 'on_loop_start'
        )
        loop_end = next(
            i for i, e in enumerate(self.h.events[loop_start:], loop_start)
            if e.name == 'on_loop_end'
        )
        values_in_loop = [
            e for e in self.h.events[loop_start:loop_end] if e.name == 'add_value'
        ]
        assert values_in_loop == []


# ─────────────────────────────────────────────────────────────────────────────
# containers.cif
# ─────────────────────────────────────────────────────────────────────────────

class TestMalformedTables:
    """Unclosed tables: implicit close on new tag or loop_."""

    def setup_method(self):
        self.h = parse(load('containers.cif'))

    def test_no_crash(self):
        pass

    def test_missing_close_on_tag_emits_error(self):
        assert self.h.has_error_containing('implicitly closed unclosed table')

    def test_missing_close_on_tag_emits_table_end(self):
        # on_table_end must follow the table key/value even when implicitly closed
        assert Event('on_table_end') in self.h.events

    def test_normal_tag_survives_after_implicit_close(self):
        assert value_after_tag(self.h, '_tag_normal_1') == ('123.45', ValueType.STRING)

    def test_missing_close_on_loop_emits_error(self):
        errors = [e.message for e in self.h.errors]
        loop_close_errors = [m for m in errors if 'loop_' in m and 'unclosed' in m]
        assert loop_close_errors

    def test_loop_processes_after_implicit_table_close(self):
        assert Event('on_loop_start', (['_tag1'],)) in self.h.events

    def test_key_whitespace_emits_syntactic_error(self):
        assert self.h.has_error_containing('whitespace between')

    def test_key_whitespace_still_produces_key_value(self):
        ev = self.h.non_error_events()
        assert Event('on_table_key', ('key', ValueType.DOUBLE_QUOTED)) in ev

    def test_missing_open_value_is_quoted_key(self):
        # "key": value } — no opening { so "key" is just the tag's value.
        assert value_after_tag(self.h, '_tag_missing_open') == ('key', ValueType.DOUBLE_QUOTED)

    def test_missing_open_colon_and_value_are_orphans(self):
        assert self.h.has_error_containing('no preceding tag')

    def test_missing_open_stray_brace_emits_error(self):
        assert self.h.has_error_containing('no open table')


class TestMalformedLists:
    """Unclosed lists: implicit close on new tag or loop_."""

    def setup_method(self):
        self.h = parse(load('containers.cif'))

    def test_missing_close_on_tag_emits_error(self):
        assert self.h.has_error_containing('implicitly closed unclosed list')

    def test_normal_tag_survives_after_implicit_list_close(self):
        # _tag_normal_1 appears in both the table and list blocks
        normals = [e for e in self.h.events if e == Event('add_tag', ('_tag_normal_1',))]
        assert len(normals) == 2

    def test_stray_close_bracket_emits_error(self):
        assert self.h.has_error_containing('no open list')

    def test_missing_open_orphan_values(self):
        # _tag_missing_open in the list block gets v1 as its value; v2 becomes orphan
        assert self.h.has_error_containing('no preceding tag')


# ─────────────────────────────────────────────────────────────────────────────
# strings2-0.cif  (CIF 2.0)
# ─────────────────────────────────────────────────────────────────────────────

class TestMalformedStrings20:
    """CIF 2.0 inline string recovery: closing at first matching delimiter."""

    def setup_method(self):
        self.h = parse(load('strings2-0.cif'))

    def test_no_crash(self):
        pass

    # ── matched double-quote: string closes early, tag after survives ────────

    def test_matched_dq_value(self):
        # "this "should not" be here" — closes at second char (first " in CIF 2.0)
        assert value_after_tag(self.h, '_tag_matched_dq_inside') == (
            'this ', ValueType.DOUBLE_QUOTED
        )

    def test_matched_dq_tag_survives(self):
        # _tag_might_get_swallowed_1 appears after the orphan tokens, before EOL
        assert '_tag_might_get_swallowed_1' in tag_names(self.h)
        assert value_after_tag(self.h, '_tag_might_get_swallowed_1') == (
            'hithere', ValueType.STRING
        )

    # ── unmatched double-quote: trailing " opens unterminated string ─────────

    def test_unmatched_dq_value(self):
        assert value_after_tag(self.h, '_tag_unmatched_dq_inside') == (
            'this should not', ValueType.DOUBLE_QUOTED
        )

    def test_unmatched_dq_swallows_tag(self):
        assert '_tag_might_get_swallowed_2' not in tag_names(self.h)

    def test_unmatched_dq_emits_lexer_error(self):
        assert self.h.has_error_containing('unterminated double_quoted string')

    # ── matched single-quote: same as matched double-quote ───────────────────

    def test_matched_sq_value(self):
        assert value_after_tag(self.h, '_tag_matched_sq_inside') == (
            'this ', ValueType.SINGLE_QUOTED
        )

    def test_matched_sq_tag_survives(self):
        assert '_tag_might_get_swallowed_3' in tag_names(self.h)
        assert value_after_tag(self.h, '_tag_might_get_swallowed_3') == (
            'hitheresomemore', ValueType.STRING
        )

    # ── unmatched single-quote ───────────────────────────────────────────────

    def test_unmatched_sq_value(self):
        assert value_after_tag(self.h, '_tag_unmatched_sq_inside') == (
            'this should not', ValueType.SINGLE_QUOTED
        )

    def test_unmatched_sq_swallows_tag(self):
        assert '_tag_might_get_swallowed_4' not in tag_names(self.h)

    def test_unmatched_sq_emits_lexer_error(self):
        assert self.h.has_error_containing('unterminated single_quoted string')

    # ── mismatched delimiters: wrong closing char → runs to EOL ─────────────

    def test_mismatched_dq_swallows_tag(self):
        assert '_tag_might_get_swallowed_5' not in tag_names(self.h)

    def test_mismatched_sq_swallows_tag(self):
        assert '_tag_might_get_swallowed_6' not in tag_names(self.h)

    # ── missing delimiters: no closing char at all → runs to EOL ────────────

    def test_missing_dq_swallows_tag(self):
        assert '_tag_will_get_swallowed_1' not in tag_names(self.h)

    def test_missing_sq_swallows_tag(self):
        assert '_tag_will_get_swallowed_2' not in tag_names(self.h)

    # ── recovery: _should_read_* tags parse cleanly ──────────────────────────

    @pytest.mark.parametrize('tag,expected', [
        ('_tag_should_read_1', ("this is 'ok'", ValueType.DOUBLE_QUOTED)),
        ('_tag_should_read_2', ('this is "OK"', ValueType.SINGLE_QUOTED)),
        ('_tag_should_read_3', ('"bookended double quotes"', ValueType.SINGLE_QUOTED)),
        ('_tag_should_read_4', ("'bookended single quotes'", ValueType.DOUBLE_QUOTED)),
        ('_tag_should_read_5', ('a normal string', ValueType.SINGLE_QUOTED)),
        ('_tag_should_read_6', ('a normal string', ValueType.DOUBLE_QUOTED)),
        ('_tag_should_read_7', ('a normal string', ValueType.DOUBLE_QUOTED)),
    ])
    def test_should_read_tags(self, tag, expected):
        assert value_after_tag(self.h, tag) == expected

    # ── triple-quoted: valid triple string ───────────────────────────────────

    def test_triple_valid_value(self):
        assert value_after_tag(self.h, '_tag_this_is_allowed') == (
            "this '''is a''' string with other tripled quotes",
            ValueType.TRIPLE_DOUBLE_QUOTED,
        )

    def test_triple_valid_no_error_for_that_tag(self):
        # No unterminated error should originate at _tag_this_is_allowed's value
        assert '_tag_wont_get_swallowed_1' in tag_names(self.h)
        assert value_after_tag(self.h, '_tag_wont_get_swallowed_1') == (
            '123.45', ValueType.STRING
        )

    # ── triple-quoted: mismatched delimiters span lines ──────────────────────

    def test_triple_mismatched_dq_swallows_tag(self):
        # _tag_will_get_swallowed_1 is inside the multiline triple-double-quoted value
        assert '_tag_will_get_swallowed_1' not in tag_names(self.h)

    def test_triple_mismatched_dq_value_spans_lines(self):
        # _tag_mismatched_delim_1 appears in both blocks; find the triple-quoted instance
        triple_val = None
        active = False
        for e in self.h.events:
            if e.name == 'add_tag' and e.args[0] == '_tag_mismatched_delim_1':
                active = True
            elif e.name == 'add_value' and active:
                if e.args[1] == ValueType.TRIPLE_DOUBLE_QUOTED:
                    triple_val = e.args
                active = False
        assert triple_val is not None, 'no triple_double_quoted value found for _tag_mismatched_delim_1'
        assert '_tag_will_get_swallowed_1' in triple_val[0]
        assert '_tag_mismatched_delim_2' in triple_val[0]

    def test_triple_wont_get_swallowed_2_survives(self):
        # After the mismatched """ closes, the next tag on that line is parseable
        assert '_tag_wont_get_swallowed_2' in tag_names(self.h)
        assert value_after_tag(self.h, '_tag_wont_get_swallowed_2') == (
            '2123.45', ValueType.STRING
        )

    def test_triple_mismatched_sq_swallows_to_eof(self):
        assert '_tag_will_get_swallowed_3' not in tag_names(self.h)
        assert '_tag_will_get_swallowed_4' not in tag_names(self.h)
        assert '_tag_will_get_swallowed_5' not in tag_names(self.h)

    def test_triple_mismatched_sq_emits_lexer_error(self):
        assert self.h.has_error_containing('unterminated triple_single_quoted string')


# ─────────────────────────────────────────────────────────────────────────────
# strings1-1.cif  (CIF 1.1)
# ─────────────────────────────────────────────────────────────────────────────

class TestMalformedStrings11:
    """
    CIF 1.1 inline string recovery.

    Key difference from CIF 2.0: a closing delimiter is only recognised when
    followed by whitespace or EOL.  This means embedded quotes (e.g. "s) extend
    the string further than a CIF 2.0 parser would.
    """

    def setup_method(self):
        self.h = parse(load('strings1-1.cif'))

    def test_no_crash(self):
        pass

    # ── CIF 1.1 embedded-quote rule: "s is NOT a closer ─────────────────────

    def test_embedded_dq_extends_value(self):
        # "this "should not" — the " before 's' is not followed by whitespace,
        # so in CIF 1.1 it is embedded.  The string closes at the " before ' '.
        assert value_after_tag(self.h, '_tag_ws_after_dq_inside_1') == (
            'this "should not', ValueType.DOUBLE_QUOTED
        )

    def test_embedded_dq_swallows_tag(self):
        # The trailing " on the line opens an unterminated string that swallows
        # _tag_might_get_swallowed_1 — unlike CIF 2.0 where it survives.
        assert '_tag_might_get_swallowed_1' not in tag_names(self.h)

    def test_ws_dq_value(self):
        # "this should not" — the " before ' ' is followed by whitespace,
        # so the string closes there in both CIF 1.1 and CIF 2.0.
        assert value_after_tag(self.h, '_tag_ws_after_dq_inside_2') == (
            'this should not', ValueType.DOUBLE_QUOTED
        )

    def test_ws_dq_swallows_tag(self):
        assert '_tag_might_get_swallowed_2' not in tag_names(self.h)

    # ── CIF 1.1 embedded single-quote rule ──────────────────────────────────

    def test_embedded_sq_extends_value(self):
        # 'this 'should not' — the ' before 's' is not followed by whitespace
        assert value_after_tag(self.h, '_tag_matched_sq_inside') == (
            "this 'should not", ValueType.SINGLE_QUOTED
        )

    def test_embedded_sq_swallows_tag(self):
        assert '_tag_might_get_swallowed_3' not in tag_names(self.h)

    def test_unmatched_sq_value(self):
        assert value_after_tag(self.h, '_tag_unmatched_sq_inside') == (
            'this should not', ValueType.SINGLE_QUOTED
        )

    def test_unmatched_sq_swallows_tag(self):
        assert '_tag_might_get_swallowed_4' not in tag_names(self.h)

    # ── mismatched and missing delimiters ────────────────────────────────────

    def test_mismatched_dq_swallows_tag(self):
        assert '_tag_might_get_swallowed_5' not in tag_names(self.h)

    def test_mismatched_sq_swallows_tag(self):
        assert '_tag_might_get_swallowed_6' not in tag_names(self.h)

    def test_missing_dq_swallows_tag(self):
        assert '_tag_will_get_swallowed_1' not in tag_names(self.h)

    def test_missing_sq_swallows_tag(self):
        assert '_tag_will_get_swallowed_2' not in tag_names(self.h)

    # ── recovery: _should_read_* tags parse cleanly ──────────────────────────

    @pytest.mark.parametrize('tag,expected', [
        ('_tag_should_read_1', ("this is ' ok '", ValueType.DOUBLE_QUOTED)),
        ('_tag_should_read_2', ('this is " OK "', ValueType.SINGLE_QUOTED)),
        ('_tag_should_read_3', ('"bookended double quotes"', ValueType.SINGLE_QUOTED)),
        ('_tag_should_read_4', ("'bookended single quotes'", ValueType.DOUBLE_QUOTED)),
        ('_tag_should_read_5', ('a normal string', ValueType.SINGLE_QUOTED)),
        ('_tag_should_read_6', ('a normal string', ValueType.DOUBLE_QUOTED)),
        ('_tag_should_read_7', ('a normal string', ValueType.SINGLE_QUOTED)),
        ('_tag_should_read_8', ('a normal string', ValueType.DOUBLE_QUOTED)),
    ])
    def test_should_read_tags(self, tag, expected):
        assert value_after_tag(self.h, tag) == expected

    # ── no triple-quoted strings ──────────────────────────────────────────────

    def test_no_triple_quoted_section(self):
        # strings1-1.cif has no data_malformed_triple_quoted_strings block
        blocks = [e.args[0] for e in self.h.events if e.name == 'on_data_block']
        assert not any('triple' in b for b in blocks)
