"""
Integration tests — Steps 6 and 7.

Step 6: non-comcifs CIF files (real-world inputs).
Step 7: CIF 1.1 specific paths — quoting rules, character-set validation,
        unquoted values containing brackets/braces/colons.

Also covers the table-key adjacency check (whitespace between key and colon).
"""

import pathlib

import pytest

from pycifparse.types import ValueType
from tests.parser.test_parser import Event, RecordingHandler, parse

CIF_DIR   = pathlib.Path(__file__).parent.parent / 'cif_files'
COMCIFS   = CIF_DIR / 'comcifs'
NONCOMCIF = CIF_DIR


def load(path: pathlib.Path) -> str:
    return path.read_text(encoding='utf-8')


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 — Non-comcifs integration tests
# ─────────────────────────────────────────────────────────────────────────────

# Small files (< 1 MB): run in the default test suite.
_SMALL_FILES = [
    'ideal_condensed.cif',
    'single_one.cif',
    'single_many_1.cif',
    'single_many_2.cif',
    'single_list.cif',
    'multi_one.cif',
    'multi_many.cif',
    'multi_list.cif',
    'second_short.cif',
]

# Large files (> 1 MB): marked slow; skipped unless -m slow or -m 'slow or …'.
_LARGE_FILES = [
    'ideal.cif',       # 43 MB
    'first.cif',       # 11 MB
    'second.cif',      # 18 MB
    'third.cif',       #  4 MB
    'fourth.cif',      # 75 MB
]


@pytest.mark.parametrize('filename', _SMALL_FILES)
def test_noncomcifs_no_errors(filename):
    h = parse(load(NONCOMCIF / filename))
    assert h.errors == [], (
        f'{filename}: unexpected errors:\n'
        + '\n'.join(f'  line {e.line}: {e.message}' for e in h.errors[:5])
    )


@pytest.mark.slow
@pytest.mark.parametrize('filename', _LARGE_FILES)
def test_noncomcifs_large_no_errors(filename):
    h = parse(load(NONCOMCIF / filename))
    assert h.errors == [], (
        f'{filename}: unexpected errors:\n'
        + '\n'.join(f'  line {e.line}: {e.message}' for e in h.errors[:5])
    )


def test_noncomcifs_ideal_condensed_data_blocks():
    h = parse(load(NONCOMCIF / 'ideal_condensed.cif'))
    blocks = [e.args[0] for e in h.non_error_events() if e.name == 'on_data_block']
    assert 'overall' in blocks
    assert any(b.startswith('phase_') for b in blocks)


def test_noncomcifs_second_short_has_data_blocks():
    h = parse(load(NONCOMCIF / 'second_short.cif'))
    blocks = [e.args[0] for e in h.non_error_events() if e.name == 'on_data_block']
    assert len(blocks) >= 3


def test_noncomcifs_timestamp_as_single_value():
    """Timestamps like 2007-12-18T12:16:55+02:00 must be one STRING token."""
    h = parse(load(NONCOMCIF / 'second_short.cif'))
    vals = [e.args for e in h.events if e.name == 'add_value']
    timestamps = [v for v, _ in vals if 'T' in v and ':' in v]
    assert timestamps, 'expected at least one timestamp value'
    for ts in timestamps:
        assert ts.count(':') >= 2, f'timestamp split: {ts!r}'


# ─────────────────────────────────────────────────────────────────────────────
# Step 7 — CIF 1.1 specific paths
# ─────────────────────────────────────────────────────────────────────────────

class TestCIF11Quoting:
    """CIF 1.1 embedded-quote rule: closing quote only when followed by ws/EOF."""

    def test_embedded_single_quote(self):
        # "don't" — the ' before 't' is not followed by whitespace, so not closing.
        src = load(COMCIFS / 'cif1_quoting.cif')
        h = parse(src)
        assert h.errors == []

    def test_sq_value(self):
        src = load(COMCIFS / 'cif1_quoting.cif')
        h = parse(src)
        ev = h.non_error_events()
        vals = {e.args[0]: e.args for e in ev if e.name == 'add_value'}
        assert vals["don't rock the boat"] == ("don't rock the boat", ValueType.SINGLE_QUOTED)

    def test_dq_value_with_embedded_quote(self):
        # The \" inside is a backslash + double-quote (CIF has no escape sequences).
        # The " before 'o' is not followed by whitespace so is not the closing delimiter.
        src = load(COMCIFS / 'cif1_quoting.cif')
        h = parse(src)
        ev = h.non_error_events()
        vals = {e.args[0]: e.args for e in ev if e.name == 'add_value'}
        key = 'What\'s this ab\\"out?'
        assert key in vals
        assert vals[key][1] == ValueType.DOUBLE_QUOTED

    def test_cif11_triple_quoted_is_lexical_error(self):
        # Triple-quoted strings are invalid in CIF 1.1.
        h = parse("#\\#CIF_1.1\ndata_d\n_t '''hello'''\n")
        assert h.has_error_containing('not valid in CIF 1.x')


class TestCIF11UnquotedBrackets:
    """In CIF 1.1, [ ] { } : are ordinary characters in bare words."""

    def test_bracket_mid_is_single_value(self):
        src = load(COMCIFS / 'cif11_unquoted.cif')
        h = parse(src)
        ev = h.non_error_events()
        vals = {e.args[0] for e in ev if e.name == 'add_value'}
        assert 'Fc^*^=kFc[1+0.001xFc^2^\\l^3^/sin(2\\q)]^-1/4^' in vals

    def test_bracket_end_is_single_value(self):
        src = load(COMCIFS / 'cif11_unquoted.cif')
        h = parse(src)
        ev = h.non_error_events()
        vals = {e.args[0] for e in ev if e.name == 'add_value'}
        assert 'a[42]' in vals

    def test_brace_begin_is_single_value(self):
        src = load(COMCIFS / 'cif11_unquoted.cif')
        h = parse(src)
        ev = h.non_error_events()
        vals = {e.args[0] for e in ev if e.name == 'add_value'}
        assert '{foo}bar' in vals

    def test_brace_mid_is_single_value(self):
        src = load(COMCIFS / 'cif11_unquoted.cif')
        h = parse(src)
        ev = h.non_error_events()
        vals = {e.args[0] for e in ev if e.name == 'add_value'}
        assert 'bar{foo}bar' in vals

    def test_no_errors(self):
        src = load(COMCIFS / 'cif11_unquoted.cif')
        h = parse(src)
        assert h.errors == []

    def test_colon_in_cif11_bare_word(self):
        h = parse('#\\#CIF_1.1\ndata_d\n_t time:12:00\n')
        ev = h.non_error_events()
        assert Event('add_value', ('time:12:00', ValueType.STRING)) in ev
        assert h.errors == []


class TestCIF11InvalidBrackets:
    """In CIF 1.1, [ is not a list opener — it ends up as an unquoted value."""

    def test_bracket_becomes_bare_word_value(self):
        src = load(COMCIFS / 'cif1_invalid.cif')
        h = parse(src)
        ev = h.events
        # _name should receive [ as its value (bare word, STRING)
        tag_idx = next(i for i, e in enumerate(ev) if e == Event('add_tag', ('_name',)))
        # find the next add_value event
        val_ev = next(e for e in ev[tag_idx:] if e.name == 'add_value')
        assert val_ev == Event('add_value', ('[', ValueType.STRING))

    def test_produces_errors(self):
        src = load(COMCIFS / 'cif1_invalid.cif')
        h = parse(src)
        assert h.errors, 'expected at least one error for invalid CIF 1.1 list syntax'

    def test_no_list_start_event(self):
        src = load(COMCIFS / 'cif1_invalid.cif')
        h = parse(src)
        assert not any(e.name == 'on_list_start' for e in h.events)


class TestCIF11CharacterValidation:
    """CIF 1.1 allows only printable ASCII (32–126) plus HT, LF, CR."""

    def test_non_ascii_in_cif11_quoted_string_emits_error(self):
        # U+00E9 (é) is non-ASCII — invalid in CIF 1.1.
        h = parse("#\\#CIF_1.1\ndata_d\n_t 'caf\u00e9'\n")
        assert h.has_error_containing('U+00E9')
        # Value is still emitted despite the error.
        assert any(e.name == 'add_value' for e in h.events)

    def test_non_ascii_in_cif11_multiline_emits_error(self):
        h = parse("#\\#CIF_1.1\ndata_d\n_t\n;caf\u00e9\n;\n")
        assert h.has_error_containing('U+00E9')

    def test_control_char_in_cif11_quoted_string_emits_error(self):
        # U+0001 is a control character — invalid in CIF 1.1.
        h = parse("#\\#CIF_1.1\ndata_d\n_t 'bad\x01char'\n")
        assert h.has_error_containing('U+0001')

    def test_vt_in_cif11_quoted_string_emits_error(self):
        # VT (U+000B) is explicitly excluded from CIF 1.1.
        h = parse("#\\#CIF_1.1\ndata_d\n_t 'bad\x0bchar'\n")
        assert h.has_error_containing('U+000B')

    def test_ff_in_cif11_quoted_string_emits_error(self):
        # FF (U+000C) is explicitly excluded from CIF 1.1.
        h = parse("#\\#CIF_1.1\ndata_d\n_t 'bad\x0cchar'\n")
        assert h.has_error_containing('U+000C')

    def test_tab_in_cif11_quoted_string_is_valid(self):
        # HT (U+0009) is explicitly permitted.
        h = parse("#\\#CIF_1.1\ndata_d\n_t 'col1\tcol2'\n")
        assert not h.has_error_containing('U+0009')
        assert Event('add_value', ('col1\tcol2', ValueType.SINGLE_QUOTED)) in h.events

    def test_non_ascii_in_cif2_is_valid(self):
        # CIF 2.0 allows the full Unicode range.
        h = parse("#\\#CIF_2.0\ndata_d\n_t 'caf\u00e9'\n")
        assert h.errors == []
        assert Event('add_value', ('caf\u00e9', ValueType.SINGLE_QUOTED)) in h.events

    def test_all_printable_ascii_valid_in_cif11(self):
        # Characters 32–126 should produce no char-set errors.
        printable = ''.join(chr(c) for c in range(32, 127) if chr(c) not in ("'",))
        h = parse(f"#\\#CIF_1.1\ndata_d\n_t '{printable}'\n")
        char_errors = [e for e in h.errors
                       if 'U+' in e.message and 'not permitted' in e.message]
        assert char_errors == []


# ─────────────────────────────────────────────────────────────────────────────
# Colon in CIF 2.0 unquoted values
# ─────────────────────────────────────────────────────────────────────────────

class TestColonInValues:
    def test_timestamp_is_one_token(self):
        h = parse('#\\#CIF_2.0\ndata_d\n_t 2007-12-18T12:16:55+02:00\n')
        assert h.errors == []
        assert Event('add_value', ('2007-12-18T12:16:55+02:00', ValueType.STRING)) in h.events

    def test_colon_value(self):
        h = parse('#\\#CIF_2.0\ndata_d\n_t val:with:colon\n')
        assert h.errors == []
        assert Event('add_value', ('val:with:colon', ValueType.STRING)) in h.events

    def test_strings_cif_no_errors(self):
        h = parse(load(COMCIFS / 'strings.cif'))
        assert h.errors == []

    def test_strings_cif_timestamp(self):
        h = parse(load(COMCIFS / 'strings.cif'))
        vals = {e.args[0] for e in h.events if e.name == 'add_value'}
        assert '2007-12-18T12:16:55+02:00' in vals

    def test_colon_still_structural_in_table(self):
        h = parse("#\\#CIF_2.0\ndata_d\n_t {'k':v}\n")
        assert h.errors == []
        assert Event('on_table_key', ('k', ValueType.SINGLE_QUOTED)) in h.events
        assert Event('add_value', ('v', ValueType.STRING)) in h.events


# ─────────────────────────────────────────────────────────────────────────────
# Table key adjacency
# ─────────────────────────────────────────────────────────────────────────────

class TestTableKeyAdjacency:
    def test_adjacent_colon_no_error(self):
        h = parse("#\\#CIF_2.0\ndata_d\n_t {'k':v}\n")
        assert not h.has_error_containing('whitespace between')

    def test_space_before_colon_emits_error(self):
        # With whitespace before ':', ':v' is lexed as a single bare-word token.
        # The parser never sees a standalone ':' so the "whitespace between" path
        # is not reached; instead the key gets a "not followed by : separator" error.
        h = parse("#\\#CIF_2.0\ndata_d\n_t {'k' :v}\n")
        assert h.has_error_containing('not followed by : separator')

    def test_space_before_colon_still_produces_key_value(self):
        # ':v' is a single token; the key is recovered but the value is ':v'.
        h = parse("#\\#CIF_2.0\ndata_d\n_t {'k' :v}\n")
        ev = h.non_error_events()
        assert Event('on_table_key', ('k', ValueType.SINGLE_QUOTED)) in ev
        assert Event('add_value', (':v', ValueType.STRING)) in ev

    def test_newline_before_colon_emits_error(self):
        # Same as space: newline before ':' means ':v' is a bare-word token.
        h = parse("#\\#CIF_2.0\ndata_d\n_t {'k'\n:v}\n")
        assert h.has_error_containing('not followed by : separator')

    def test_space_after_colon_no_error(self):
        # Whitespace AFTER the colon is permitted (wspace-data-value).
        h = parse("#\\#CIF_2.0\ndata_d\n_t {'k': v}\n")
        assert not h.has_error_containing('whitespace between')

    def test_table_data_cif_has_no_adjacency_errors(self):
        # All entries in table_data.cif use adjacent colons.
        h = parse(load(COMCIFS / 'table_data.cif'))
        adjacency_errors = [e for e in h.errors
                            if 'whitespace between' in e.message]
        assert adjacency_errors == []


# ─────────────────────────────────────────────────────────────────────────────
# Colon-prefixed bare-word values (enumeration ranges)
# ─────────────────────────────────────────────────────────────────────────────

class TestColonPrefixedValues:
    """
    Unquoted values that begin with ':' — e.g. ':100.0' in enumeration ranges.

    Per CIF 2.0 EBNF, ':' is a restrict-char and may appear anywhere in a
    wsdelim-string, including as the first character.  The ':' table separator
    is only a standalone token when directly adjacent to a preceding token
    (no whitespace between them).
    """

    def setup_method(self):
        self.h = parse(load(CIF_DIR / 'enumeration_range.cif'))

    def test_no_errors(self):
        assert self.h.errors == []

    def test_mid_colon_value(self):
        # '0.0:100.0' — colon in the middle of a bare word
        assert Event('add_value', ('0.0:100.0', ValueType.STRING)) in self.h.events

    def test_trailing_colon_value(self):
        # '0.0:' — bare word ending with colon
        assert Event('add_value', ('0.0:', ValueType.STRING)) in self.h.events

    def test_leading_colon_value(self):
        # ':100.0' — bare word starting with colon (the bug case)
        assert Event('add_value', (':100.0', ValueType.STRING)) in self.h.events
