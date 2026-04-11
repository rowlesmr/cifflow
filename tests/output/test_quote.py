"""
Tests for pycifparse.output.quote.

Each decision-tree rule is tested directly (checking the returned token) and
via a round-trip: quote() → embed in minimal CIF → build() → compare value.

Round-trip contract
-------------------
For a stored value ``s``:
  logical = s[1] if s in ('"."', '"?"') else s
  quote(s, version) must produce a token that, when parsed, yields ``logical``.

PLACEHOLDER values ('.' and '?', length 1) round-trip as themselves.
"""

import pytest

from pycifparse.cifmodel.builder import build
from pycifparse.output.quote import quote
from pycifparse.types import CifVersion

CIF20 = CifVersion.CIF_2_0
CIF11 = CifVersion.CIF_1_1


# ---------------------------------------------------------------------------
# Round-trip helper
# ---------------------------------------------------------------------------

def _logical(stored: str) -> str:
    """The logical value that a stored string represents."""
    if stored in ('"."', '"?"'):
        return stored[1]
    return stored


def _roundtrip(stored: str, version: CifVersion) -> str:
    """quote(stored) → minimal CIF → build() → return parsed value string."""
    token = quote(stored, version)
    magic = '#\\#CIF_2.0' if version == CIF20 else '#\\#CIF_1.1'
    if token.startswith('\n'):
        # Semicolon-delimited: tag must be on its own line before the field
        source = f'{magic}\ndata_t\n_v{token}\n'
    else:
        source = f'{magic}\ndata_t\n_v {token}\n'
    cif, errors = build(source)
    assert not errors, f'Parse errors on round-trip of {stored!r}: {errors}'
    return str(cif['t']['_v'][0])


def rt(stored: str, version: CifVersion) -> None:
    """Assert that stored round-trips correctly."""
    assert _roundtrip(stored, version) == _logical(stored)


# ---------------------------------------------------------------------------
# PLACEHOLDER (rule 1)
# ---------------------------------------------------------------------------

class TestPlaceholder:
    def test_dot_is_unquoted(self):
        assert quote('.', CIF20) == '.'

    def test_question_is_unquoted(self):
        assert quote('?', CIF20) == '?'

    def test_dot_roundtrip_20(self):
        rt('.', CIF20)

    def test_question_roundtrip_20(self):
        rt('?', CIF20)

    def test_dot_roundtrip_11(self):
        rt('.', CIF11)

    def test_question_roundtrip_11(self):
        rt('?', CIF11)


# ---------------------------------------------------------------------------
# Quoted placeholder storage encoding ('"."' / '"?"')
# ---------------------------------------------------------------------------

class TestQuotedPlaceholder:
    def test_quoted_dot_not_plain(self):
        # Must not be emitted unquoted — it's a string value '.'
        result = quote('"."', CIF20)
        assert result != '.'

    def test_quoted_question_not_plain(self):
        result = quote('"?"', CIF20)
        assert result != '?'

    def test_quoted_dot_roundtrip_20(self):
        rt('"."', CIF20)

    def test_quoted_question_roundtrip_20(self):
        rt('"?"', CIF20)

    def test_quoted_dot_roundtrip_11(self):
        rt('"."', CIF11)

    def test_quoted_question_roundtrip_11(self):
        rt('"?"', CIF11)


# ---------------------------------------------------------------------------
# Rule 2 — bare word
# ---------------------------------------------------------------------------

class TestBareWord:
    def test_simple_word(self):
        assert quote('hello', CIF20) == 'hello'

    def test_numeric_string(self):
        assert quote('3.992', CIF20) == '3.992'

    def test_numeric_with_su(self):
        assert quote('3.992(5)', CIF20) == '3.992(5)'

    def test_mixed_case(self):
        assert quote('Se1', CIF20) == 'Se1'

    def test_bare_word_roundtrip_20(self):
        rt('hello', CIF20)

    def test_bare_word_roundtrip_11(self):
        rt('hello', CIF11)

    def test_numeric_roundtrip_20(self):
        rt('3.992', CIF20)

    def test_numeric_roundtrip_11(self):
        rt('3.992', CIF11)


# ---------------------------------------------------------------------------
# Illegal bare-word starts
# ---------------------------------------------------------------------------

class TestIllegalStart:
    @pytest.mark.parametrize('value', [
        '_tag_like',
        '#comment_like',
        '$reference',
        '[list_like',
        '{table_like',
        ' leading_space',
        '\tleading_tab',
    ])
    def test_illegal_start_gets_quoted(self, value):
        result = quote(value, CIF20)
        assert result != value   # must not be bare word

    @pytest.mark.parametrize('keyword', [
        'loop_', 'LOOP_', 'Loop_',
        'stop_', 'STOP_',
        'global_', 'GLOBAL_',
    ])
    def test_reserved_keyword_gets_quoted(self, keyword):
        result = quote(keyword, CIF20)
        assert result != keyword

    @pytest.mark.parametrize('value', [
        'data_block',
        'DATA_BLOCK',
        'save_frame',
        'SAVE_FRAME',
    ])
    def test_reserved_prefix_gets_quoted(self, value):
        result = quote(value, CIF20)
        assert result != value

    def test_underscore_start_roundtrip(self):
        rt('_tag_like', CIF20)

    def test_data_prefix_roundtrip(self):
        rt('data_block', CIF20)

    def test_loop_keyword_roundtrip(self):
        rt('loop_', CIF20)

    def test_loop_keyword_roundtrip_11(self):
        rt('loop_', CIF11)


# ---------------------------------------------------------------------------
# Rules 3 & 4 — single-quoted (CIF 2.0 and 1.1)
# ---------------------------------------------------------------------------

class TestSingleQuoted:
    def test_space_gives_single_quotes(self):
        result = quote('hello world', CIF20)
        assert result == "'hello world'"

    def test_begins_with_space_gives_single_quotes(self):
        result = quote(' hello', CIF20)
        assert result == "' hello'"

    def test_double_quote_in_value_gives_single_quotes(self):
        result = quote('say "hi"', CIF20)
        assert result == "'say \"hi\"'"

    def test_double_quote_with_space_in_value_gives_single_quotes(self):
        result = quote('say c " c "c " hi" ', CIF20)
        assert result == "'say c \" c \"c \" hi\" '"

    def test_space_roundtrip_20(self):
        rt('hello world', CIF20)

    def test_begin_with_space_roundtrip_20(self):
        rt(' hello', CIF20)

    def test_double_in_value_roundtrip_20(self):
        rt('say "hi"', CIF20)

    def test_double_quote_with_space_in_value_roundtrip_20(self):
        rt('say c " c "c " hi" ', CIF20)

    def test_space_roundtrip_11(self):
        rt('hello world', CIF11)

    def test_begin_with_space_roundtrip_11(self):
        rt(' hello', CIF11)

    def test_double_in_value_roundtrip_11(self):
        rt('say "hi"', CIF11)


# ---------------------------------------------------------------------------
# Rule 5 — double-quoted
# ---------------------------------------------------------------------------

class TestDoubleQuoted:
    def test_single_quote_in_value_with_space_gives_double_quotes(self):
        # Has space (needs quoting) + has single-quote → must use double-quotes
        result = quote("it's a test", CIF20)
        assert result == '"it\'s a test"'

    def test_starts_with_single_quote_gives_double_quotes(self):
        # Starts with ' → illegal bare-word start → must be quoted
        # Has single-quote → use double-quotes
        result = quote("'hello'", CIF20)
        assert result == '"\'hello\'"'

    def test_single_quote_with_space_roundtrip_20(self):
        rt("it's a test", CIF20)

    def test_single_quote_with_space_roundtrip_11(self):
        rt("it's a test", CIF11)

    def test_single_quote_with_trailing_space_roundtrip_11(self):
        rt("its' a test", CIF11)

    def test_starts_with_single_quote_roundtrip_20(self):
        rt("'hello'", CIF20)

    def test_apostrophe_mid_word_gets_quoted(self):
        # ' mid-word causes CIF readers to enter single-quoted state; must quote
        result = quote("it's", CIF20)
        assert result != "it's"

    def test_apostrophe_mid_word_roundtrip(self):
        rt("it's", CIF20)

    def test_apostrophe_in_name_roundtrip(self):
        rt("O'Brien", CIF20)


# ---------------------------------------------------------------------------
# Rule 6 — both quote types present, no newline
# CIF 2.0: triple-single. CIF 1.1: semicolon.
# ---------------------------------------------------------------------------

class TestBothQuoteTypesNoNewline:
    BOTH = """it's a "test" """

    def test_cif20_gives_triple_single(self):
        result = quote(self.BOTH, CIF20)
        assert result.startswith("'''") and result.endswith("'''")

    def test_cif11_gives_semicolon(self):
        result = quote(self.BOTH, CIF11)
        assert result.startswith('\n;')

    def test_roundtrip_20(self):
        rt(self.BOTH, CIF20)

    def test_roundtrip_11(self):
        rt(self.BOTH, CIF11)


# ---------------------------------------------------------------------------
# Rule 7 — newline, no triple quotes (CIF 2.0: triple-single)
# CIF 1.1 goes straight to semicolon for any newline.
# ---------------------------------------------------------------------------

class TestNewline:
    NEWLINE = 'first line\nsecond line'

    def test_cif20_gives_triple_single(self):
        result = quote(self.NEWLINE, CIF20)
        assert result.startswith("'''") and result.endswith("'''")

    def test_cif11_gives_semicolon(self):
        result = quote(self.NEWLINE, CIF11)
        assert result.startswith('\n;')

    def test_roundtrip_20(self):
        rt(self.NEWLINE, CIF20)

    def test_roundtrip_11(self):
        rt(self.NEWLINE, CIF11)

    def test_newline_with_single_quote_roundtrip_20(self):
        rt("first\nit's second", CIF20)

    def test_newline_with_double_quote_roundtrip_20(self):
        rt('first\nsay "hi"', CIF20)

    def test_newline_with_both_quotes_roundtrip_20(self):
        rt("""first\nit's "complicated" """, CIF20)


# ---------------------------------------------------------------------------
# Rule 8 — contains ''' but not """ → triple-double (CIF 2.0)
# ---------------------------------------------------------------------------

class TestTripleSinglePresent:
    VALUE = "text with ''' triple single"

    def test_triple_single_in_value_uses_double_quotes(self):
        # Has ' (from ''') but no " → Rule 5: double-quoted. ''' inside "..." is harmless.
        result = quote(self.VALUE, CIF20)
        assert result.startswith('"') and result.endswith('"') and not result.startswith('"""')

    def test_roundtrip_20(self):
        rt(self.VALUE, CIF20)

    def test_with_newline_roundtrip_20(self):
        rt("line1\nhas ''' triple", CIF20)


# ---------------------------------------------------------------------------
# Rule 9 — contains """ but not ''' → triple-single (CIF 2.0)
# ---------------------------------------------------------------------------

class TestTripleDoublePresent:
    VALUE = 'text with """ triple double'

    def test_triple_double_in_value_uses_single_quotes(self):
        # Has " (from """) but no ' → Rule 4: single-quoted. """ inside '...' is harmless.
        result = quote(self.VALUE, CIF20)
        assert result.startswith("'") and result.endswith("'") and not result.startswith("'''")

    def test_roundtrip_20(self):
        rt(self.VALUE, CIF20)

    def test_with_newline_roundtrip_20(self):
        rt('line1\nhas """ triple', CIF20)


# ---------------------------------------------------------------------------
# Rules 10 & 11 — both triple types → semicolon / prefixed semicolon (CIF 2.0)
# ---------------------------------------------------------------------------

class TestBothTripleTypes:
    BOTH_TRIPLE = "has ''' and \"\"\""

    def test_gives_semicolon(self):
        result = quote(self.BOTH_TRIPLE, CIF20)
        assert result.startswith('\n;')

    def test_roundtrip_20(self):
        rt(self.BOTH_TRIPLE, CIF20)

    def test_both_triple_with_newline_roundtrip_20(self):
        rt("line\nhas '''\nand \"\"\"", CIF20)


class TestPrefixedSemicolon:
    # String that would close a plain semicolon field
    NEWLINE_SEMI = "line one\n;this would close the field\nline three"

    def test_cif20_uses_triple_quoted(self):
        # CIF 2.0: \n; inside '''...''' is not a closing delimiter — no prefix needed
        result = quote(self.NEWLINE_SEMI, CIF20)
        assert result.startswith("'''")

    def test_cif11_uses_prefix(self):
        result = quote(self.NEWLINE_SEMI, CIF11)
        assert '>' in result

    def test_roundtrip_20(self):
        rt(self.NEWLINE_SEMI, CIF20)

    def test_roundtrip_11(self):
        rt(self.NEWLINE_SEMI, CIF11)

    def test_multiple_newline_semis_roundtrip_20(self):
        rt("a\n;b\n;c", CIF20)

    def test_multiple_newline_semis_roundtrip_11(self):
        rt("a\n;b\n;c", CIF11)

    def test_semicolon_at_start_roundtrip_20(self):
        # String that starts with ';' on the first content line
        rt('\n;starts with semicolon', CIF20)

    def test_semicolon_at_start_roundtrip_11(self):
        rt('\n;starts with semicolon', CIF11)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string_gets_quoted(self):
        result = quote('', CIF20)
        assert result != ''   # must be quoted

    def test_empty_string_roundtrip_20(self):
        rt('', CIF20)

    def test_empty_string_roundtrip_11(self):
        rt('', CIF11)

    def test_single_space_roundtrip_20(self):
        rt(' ', CIF20)

    def test_single_space_roundtrip_11(self):
        rt(' ', CIF11)

    def test_only_newline_roundtrip_20(self):
        rt('\n', CIF20)

    def test_only_newline_roundtrip_11(self):
        rt('\n', CIF11)

    def test_unicode_no_quoting_needed(self):
        # Unicode characters that don't require quoting
        result = quote('Ångström', CIF20)
        assert result == 'Ångström'

    def test_unicode_roundtrip_20(self):
        rt('Ångström', CIF20)

    def test_long_plain_string_roundtrip_20(self):
        rt('a' * 200, CIF20)

    def test_json_stored_value_roundtrip_20(self):
        # JSON-encoded containers pass through as regular strings
        rt('["a","b"]', CIF20)
