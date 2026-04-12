"""
Lexer tests.

Conventions:
  - Helper `lex(src, version)` returns a flat list of Token objects.
  - Helper `vals(tokens)` extracts (value, value_type) pairs for quick assertions.
  - All CIF 2.0 sources use CifVersion.CIF_2_0; CIF 1.1 sources use CifVersion.CIF_1_1.
"""

import pathlib
import pytest

from pycifparse.lexer.lexer import Lexer
from pycifparse.lexer.tokens import Token
from pycifparse.types import CifVersion, TokenType, ValueType

CIF2 = CifVersion.CIF_2_0
CIF1 = CifVersion.CIF_1_1


def lex(src: str, version: CifVersion = CIF2, line_offset: int = 0):
    return list(Lexer(src, version, line_offset).tokens())


def vals(tokens):
    return [(t.value, t.value_type) for t in tokens]


def errors(tokens):
    return [e for t in tokens for e in t.errors]


def pp(tokens):
    print(f"\n{tokens=}")

# ─────────────────────────────────────────────────────────────────────────────
# Whitespace and comments
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_source():
    assert lex('') == []

def test_whitespace_only():
    assert lex('   \t\n  ') == []

def test_comment_only():
    assert lex('# this is a comment\n') == []

def test_comment_mid_line():
    tokens = lex('data_foo # comment\n_tag')
    assert tokens[0].value == 'data_foo'
    assert tokens[1].value == '_tag'
    assert len(tokens) == 2

def test_comment_data_item():
    tokens = lex('data_foo \n_tag #comment \n value')
    assert tokens[0].value == 'data_foo'
    assert tokens[1].value == '_tag'
    assert tokens[2].value == 'value'
    assert len(tokens) == 3


# ─────────────────────────────────────────────────────────────────────────────
# Tags
# ─────────────────────────────────────────────────────────────────────────────

def test_simple_tag():
    tokens = lex('_atom_site.x')
    assert len(tokens) == 1
    assert tokens[0].token_type == TokenType.TAG
    assert tokens[0].value == '_atom_site.x'
    assert tokens[0].value_type is None

def test_tag_at_start_of_line():
    tokens = lex('\n_tag value')
    assert tokens[0].token_type == TokenType.TAG
    assert tokens[0].value == '_tag'

def test_underscore_prefix_keyword_is_tag():
    # _data_foo starts with '_' so it's always a TAG
    tokens = lex('_data_foo')
    assert tokens[0].token_type == TokenType.TAG

def test_tag_after_multiline():
    tokens = lex('\n_tag1 \n;string\n;_tag2 value')
    assert tokens[2].token_type == TokenType.TAG
    assert tokens[2].value == '_tag2'

# ─────────────────────────────────────────────────────────────────────────────
# Keywords
# ─────────────────────────────────────────────────────────────────────────────

def test_data_keyword():
    tokens = lex('data_my_block')
    assert tokens[0].token_type == TokenType.KEYWORD
    assert tokens[0].value == 'data_my_block'

def test_data_keyword_bare():
    tokens = lex('data_')
    assert tokens[0].token_type == TokenType.KEYWORD
    assert tokens[0].value == 'data_'

def test_save_keyword_with_name():
    tokens = lex('save_myframe')
    assert tokens[0].token_type == TokenType.KEYWORD
    assert tokens[0].value == 'save_myframe'

def test_save_keyword_bare():
    tokens = lex('save_')
    assert tokens[0].token_type == TokenType.KEYWORD
    assert tokens[0].value == 'save_'

def test_loop_keyword():
    tokens = lex('loop_')
    assert tokens[0].token_type == TokenType.KEYWORD
    assert tokens[0].value == 'loop_'

def test_stop_keyword():
    tokens = lex('stop_')
    assert tokens[0].token_type == TokenType.KEYWORD
    assert tokens[0].value == 'stop_'

def test_global_keyword():
    tokens = lex('global_')
    assert tokens[0].token_type == TokenType.KEYWORD
    assert tokens[0].value == 'global_'

def test_keywords_case_insensitive():
    for kw in ('LOOP_', 'Loop_', 'DATA_foo', 'SAVE_frame', 'STOP_', 'GLOBAL_', 'LoOp_', 'dAtA_'):
        tokens = lex(kw)
        assert tokens[0].token_type == TokenType.KEYWORD, kw


# ─────────────────────────────────────────────────────────────────────────────
# Plain values
# ─────────────────────────────────────────────────────────────────────────────

def test_placeholder_dot():
    tokens = lex('.')
    assert tokens[0].value_type == ValueType.PLACEHOLDER
    assert tokens[0].value == '.'

def test_placeholder_question():
    tokens = lex('?')
    assert tokens[0].value_type == ValueType.PLACEHOLDER
    assert tokens[0].value == '?'

def test_unquoted_string():
    tokens = lex('hello')
    assert tokens[0].value_type == ValueType.STRING
    assert tokens[0].value == 'hello'

def test_numeric_integer():
    tokens = lex('42')
    assert tokens[0].value_type == ValueType.STRING
    assert tokens[0].value == '42'

def test_numeric_float():
    tokens = lex('3.14')
    assert tokens[0].value_type == ValueType.STRING

def test_numeric_exponential():
    tokens = lex('1.25e+03')
    assert tokens[0].value_type == ValueType.STRING
    assert tokens[0].value == '1.25e+03'

def test_numeric_su_valid():
    tokens = lex('0.0625(2)')
    assert tokens[0].value_type == ValueType.STRING
    assert tokens[0].value == '0.0625(2)'
    assert errors(tokens) == []

def test_numeric_su_malformed():
    # SU validation is not the lexer's responsibility — '12.3(AB)' is a valid
    # STRING token; semantic validation of SU notation happens downstream.
    tokens = lex('12.3(AB)')
    assert tokens[0].value_type == ValueType.STRING
    assert tokens[0].value == '12.3(AB)'
    assert errors(tokens) == []

def test_numeric_su_negative():
    tokens = lex('-0.12(3)')
    assert errors(tokens) == []
    assert tokens[0].value == '-0.12(3)'


# ─────────────────────────────────────────────────────────────────────────────
# Quoted strings
# ─────────────────────────────────────────────────────────────────────────────

def test_single_quoted():
    tokens = lex("'hello world'")
    assert tokens[0].value_type == ValueType.SINGLE_QUOTED
    assert tokens[0].value == 'hello world'
    assert errors(tokens) == []

def test_double_quoted():
    tokens = lex('"hello world"')
    assert tokens[0].value_type == ValueType.DOUBLE_QUOTED
    assert tokens[0].value == 'hello world'
    assert errors(tokens) == []

def test_quoted_dot_is_not_placeholder():
    assert lex('"."')[0].value_type == ValueType.DOUBLE_QUOTED
    assert lex("'.'")[0].value_type == ValueType.SINGLE_QUOTED

def test_quoted_question_is_not_placeholder():
    assert lex('"?"')[0].value_type == ValueType.DOUBLE_QUOTED
    assert lex("'?'")[0].value_type == ValueType.SINGLE_QUOTED

def test_quoted_numeric():
    tokens = lex("'1.0'")
    assert tokens[0].value_type == ValueType.SINGLE_QUOTED
    assert tokens[0].value == '1.0'

def test_quoted_keyword_is_value():
    tokens = lex('"data_"')
    assert tokens[0].token_type == TokenType.VALUE
    assert tokens[0].value_type == ValueType.DOUBLE_QUOTED

def test_unterminated_single_quoted():
    tokens = lex("'unterminated")
    assert tokens[0].value == 'unterminated'
    errs = errors(tokens)
    assert len(errs) == 1
    assert 'unterminated' in errs[0].message

def test_unterminated_double_quoted():
    tokens = lex('"unterminated')
    errs = errors(tokens)
    assert len(errs) == 1
    assert 'unterminated' in errs[0].message

def test_cif11_apostrophe_in_string():
    # CIF 1.1: closing ' must be followed by whitespace; so 'it's' = 'it' + s + error
    tokens = lex("'it's a test' value", version=CIF1)
    # The string terminates at the ' followed by space (after 'test')
    assert tokens[0].value == "it's a test"
    assert tokens[0].value_type == ValueType.SINGLE_QUOTED


# ─────────────────────────────────────────────────────────────────────────────
# Triple-quoted strings (CIF 2.0 only)
# ─────────────────────────────────────────────────────────────────────────────

def test_triple_single_quoted():
    tokens = lex("'''hello'''")
    assert tokens[0].value_type == ValueType.TRIPLE_SINGLE_QUOTED
    assert tokens[0].value == 'hello'
    assert errors(tokens) == []

def test_triple_double_quoted():
    tokens = lex('"""hello"""')
    assert tokens[0].value_type == ValueType.TRIPLE_DOUBLE_QUOTED
    assert tokens[0].value == 'hello'
    assert errors(tokens) == []

def test_triple_empty():
    assert lex("''''''")[0].value == ''
    assert lex('""""""')[0].value == ''

def test_triple_with_embedded_single_quotes():
    # ''''tricky''' — opening ''' then content "'tricky"
    tokens = lex("''''tricky'''")
    assert tokens[0].value == "'tricky"

def test_triple_with_embedded_beginning_double_quotes():
    tokens = lex('"""""tricky"""')
    assert tokens[0].value == '""tricky'

def test_triple_multiline():
    tokens = lex('"""first line\nsecond line"""')
    assert tokens[0].value == 'first line\nsecond line'

def test_triple_semicolon_at_col1_not_multiline_delimiter():
    # A ';' at column 1 inside a triple-quoted string must NOT terminate it
    src = '"""\n;embedded\n;"""'
    tokens = lex(src)
    assert tokens[0].value_type == ValueType.TRIPLE_DOUBLE_QUOTED
    assert ';embedded\n;' in tokens[0].value

def test_triple_quoted_in_cif11_is_error():
    tokens = lex("'''hello'''", version=CIF1)
    errs = errors(tokens)
    assert len(errs) >= 1
    assert 'CIF 1.x' in errs[0].message
    # Content still emitted as STRING
    assert tokens[0].value_type == ValueType.STRING
    assert tokens[0].value == 'hello'

def test_unterminated_triple():
    tokens = lex("'''unterminated")
    errs = errors(tokens)
    assert any('unterminated' in e.message for e in errs)


# ─────────────────────────────────────────────────────────────────────────────
# Multiline text fields
# ─────────────────────────────────────────────────────────────────────────────

def test_multiline_basic():
    src = '\n;text\n;'
    tokens = lex(src)
    assert tokens[0].value_type == ValueType.MULTILINE_STRING
    assert tokens[0].value == 'text'
    assert errors(tokens) == []

def test_multiline_content_starts_same_line_as_opening():
    # Content starts immediately after opening ';'
    src = '\n;line 1\nline 2\n;'
    tokens = lex(src)
    assert tokens[0].value == 'line 1\nline 2'

def test_multiline_empty():
    src = '\n;\n;'
    tokens = lex(src)
    assert tokens[0].value == ''

def test_multiline_preserves_internal_content():
    # text-delim = line-term, ';': any \n; closes the field.
    # '\n;still content' IS a closing delimiter; 'still content' becomes
    # the next tokens in NORMAL state.
    # Ends with an error of an unterminated multiline string
    src = '\n;line 1\n# not a comment\n;still content\n;'
    tokens = lex(src)
    assert tokens[0].value_type == ValueType.MULTILINE_STRING
    assert tokens[0].value == 'line 1\n# not a comment'
    # 'still' and 'content' are regular VALUE tokens after the closing ';'
    assert tokens[1].value == 'still'
    assert tokens[2].value == 'content'
    errs = errors(tokens)
    assert any('unterminated' in e.message for e in errs)
    assert len(errs) == 1

def test_multiline_semicolon_not_at_col1_is_content():
    src = '\n; ;not a delimiter\n ;end\n;'
    tokens = lex(src)
    assert tokens[0].value == ' ;not a delimiter\n ;end'

def test_comment_after_closing_delimiter_is_skipped():
    # After the closing ';', ' # comment' is whitespace + comment — skipped normally.
    src = '\n;text\n; # this is a comment\nnext_token'
    tokens = lex(src)
    assert tokens[0].value == 'text'
    assert tokens[1].value == 'next_token'

def test_comment_after_closing_delimiter_is_skipped_no_space():
    # After the closing ';', '# comment' is comment — skipped normally.
    src = '\n;text\n;# this is a comment\nnext_token'
    tokens = lex(src)
    assert tokens[0].value == 'text'
    assert tokens[1].value == 'next_token'

def test_value_after_closing_delimiter_is_tokenised():
    # Content after the closing ';' on the same line is tokenised normally.
    # This matches simple_loops.cif: '; 1.0' where '1.0' is the next loop value.
    src = '\n;v2\n; 1.0\nnext'
    tokens = lex(src)
    assert tokens[0].value == 'v2'
    assert tokens[1].value == '1.0'
    assert tokens[2].value == 'next'

def test_value_after_closing_delimiter_is_tokenised_no_space():
    # Content after the closing ';' on the same line is tokenised normally.
    # This matches simple_loops.cif: '; 1.0' where '1.0' is the next loop value.
    src = '\n;v2\n;1.0\tnext'
    tokens = lex(src)
    assert tokens[0].value == 'v2'
    assert tokens[1].value == '1.0'
    assert tokens[2].value == 'next'

def test_multiline_unterminated():
    src = '\n;text without closing'
    tokens = lex(src)
    errs = errors(tokens)
    assert any('unterminated' in e.message for e in errs)
    assert tokens[0].value == 'text without closing'

def test_multiline_line_numbers():
    src = 'data_foo\n;text\nwith\nmultiple\nlines\n;\n_tag'
    tokens = lex(src)
    assert tokens[0].value == 'data_foo'
    assert tokens[0].line == 1
    assert tokens[1].value_type == ValueType.MULTILINE_STRING
    assert tokens[1].line == 2
    assert tokens[2].value == '_tag'
    assert tokens[2].line == 7


# ─────────────────────────────────────────────────────────────────────────────
# CIF 2.0 structural delimiters
# ─────────────────────────────────────────────────────────────────────────────

def test_list_delimiters():
    tokens = lex('[1 2 3]')
    assert tokens[0].value == '['
    assert tokens[-1].value == ']'
    assert all(t.token_type == TokenType.VALUE for t in tokens)

def test_table_delimiters():
    tokens = lex('{"key": value}')
    assert tokens[0].value == '{'
    assert tokens[-1].value == '}'

def test_colon_is_standalone_token_cif2():
    tokens = lex('"key": value')
    colon_tokens = [t for t in tokens if t.value == ':']
    assert len(colon_tokens) == 1

def test_colon_is_standalone_token_colon_in_string_cif2():
    tokens = lex('"key": va:lue')
    colon_tokens = [t for t in tokens if t.value == ':']
    assert len(colon_tokens) == 1
    assert tokens[-1].value == 'va:lue'

def test_tag_with_subscript_brackets_cif2():
    # Tags (data-name) may contain '[' and ']' per CIF 2.0 EBNF (non-blank-char).
    # The lexer must not split '_axis.vector[1]' at '['.
    tokens = lex('_axis.vector[1]')
    assert len(tokens) == 1
    assert tokens[0].token_type == TokenType.TAG
    assert tokens[0].value == '_axis.vector[1]'

def test_save_keyword_with_subscript_brackets_cif2():
    # Save frame names (container-code) also use non-blank-char, so they may
    # contain '[' and ']'.  'save_axis.vector[1]' must be one KEYWORD token.
    tokens = lex('save_axis.vector[1]')
    assert len(tokens) == 1
    assert tokens[0].token_type == TokenType.KEYWORD
    assert tokens[0].value == 'save_axis.vector[1]'

def test_plain_value_still_split_at_bracket_cif2():
    # Plain unquoted values use restrict-char (no '['/']'), so 'foo[1]' must
    # be split into 'foo', '[', '1', ']'.
    tokens = lex('foo[1]')
    assert tokens[0].value == 'foo'
    assert tokens[1].value == '['
    assert tokens[2].value == '1'
    assert tokens[3].value == ']'

def test_plain_value_not_split_at_bracket_cif11():
    # Plain unquoted value: Fc^*^=kFc[1+0.001xFc^2^\l^3^/sin(2\q)]^-1/4^ is a single value.
    tokens = lex('Fc^*^=kFc[1+0.001xFc^2^\l^3^/sin(2\q)]^-1/4^', version=CIF1)
    assert len(tokens) == 1
    assert tokens[0].value == 'Fc^*^=kFc[1+0.001xFc^2^\l^3^/sin(2\q)]^-1/4^'

def test_delimiters_not_special_in_cif11():
    # In CIF 1.1 mode, '[', ']', '{', '}' are part of bare words
    tokens = lex('[value]', version=CIF1)
    assert len(tokens) == 1
    assert tokens[0].value == '[value]'
    assert tokens[0].value_type == ValueType.STRING
    assert False # this test is wrong need to re-spec the lexer. The characters above should be accepted, but should also have an error

def test_colon_part_of_bare_word_in_cif11():
    tokens = lex('http://example.com', version=CIF1)
    assert len(tokens) == 1
    assert tokens[0].value == 'http://example.com'


# ─────────────────────────────────────────────────────────────────────────────
# Line and column tracking
# ─────────────────────────────────────────────────────────────────────────────

def test_line_numbers():
    src = 'data_foo\n_tag\nvalue'
    tokens = lex(src)
    assert tokens[0].line == 1
    assert tokens[1].line == 2
    assert tokens[2].line == 3

def test_column_numbers():
    tokens = lex('data_foo _tag')
    assert tokens[0].column == 1
    assert tokens[1].column == 10

def test_line_offset():
    # With line_offset=1, tokens start at line 2
    tokens = lex('_tag value', line_offset=1)
    assert tokens[0].line == 2

def test_crlf_normalised():
    tokens = lex('data_foo\r\n_tag\r\nvalue')
    assert tokens[0].line == 1
    assert tokens[1].line == 2
    assert tokens[2].line == 3


def test_cr_only_normalised():
    tokens = lex('data_foo\r_tag\rvalue')
    assert tokens[0].line == 1
    assert tokens[1].line == 2
    assert tokens[2].line == 3


def test_mixed_line_endings_normalised():
    # \n, \r\n, and \r in the same source.
    tokens = lex('data_foo\n_a\r\n_b\rvalue')
    assert tokens[0].line == 1   # data_foo
    assert tokens[1].line == 2   # _a
    assert tokens[2].line == 3   # _b
    assert tokens[3].line == 4   # value


def test_crlf_multiline_field():
    # The closing text-delim is \n; after normalisation, regardless of
    # whether the original file used \r\n.
    tokens = lex('data_d\r\n_t\r\n;content\r\n;\r\n')
    ml = next(t for t in tokens if t.token_type.value == 'value'
              and t.value_type is not None
              and t.value_type.value == 'multiline_string')
    assert ml.value == 'content'
    assert ml.errors == []


def test_cr_only_multiline_field():
    tokens = lex('data_d\r_t\r;content\r;\r')
    ml = next(t for t in tokens if t.token_type.value == 'value'
              and t.value_type is not None
              and t.value_type.value == 'multiline_string')
    assert ml.value == 'content'
    assert ml.errors == []


def test_mixed_line_endings_multiline_field():
    # Multiline content with all three EOL styles inside.
    tokens = lex('data_d\n_t\n;line1\r\nline2\rline3\n;\n')
    ml = next(t for t in tokens if t.token_type.value == 'value'
              and t.value_type is not None
              and t.value_type.value == 'multiline_string')
    assert ml.value == 'line1\nline2\nline3'
    assert ml.errors == []


# ─────────────────────────────────────────────────────────────────────────────
# Real CIF file smoke tests
# ─────────────────────────────────────────────────────────────────────────────

CIF_DIR = pathlib.Path(__file__).parent.parent / 'cif_files'


def _load(filename, version=CIF2, subdir='comcifs'):
    path = CIF_DIR / subdir / filename
    src = path.read_text(encoding='utf-8-sig')
    # Strip magic line (already consumed by version detection in real use)
    lines = src.splitlines(keepends=True)
    if lines and lines[0].lstrip('\ufeff').startswith('#\\#'):
        src = ''.join(lines[1:])
        offset = 1
    else:
        offset = 0
    return lex(src, version, offset)


def test_simple_data_no_errors():
    tokens = _load('simple_data.cif')
    assert errors(tokens) == []


def test_simple_loops_no_errors():
    tokens = _load('simple_loops.cif')
    assert errors(tokens) == []


def test_triple_file_no_errors():
    tokens = _load('triple.cif')
    assert errors(tokens) == []


def test_text_fields_no_errors():
    tokens = _load('text_fields.cif')
    assert errors(tokens) == []


def test_simple_containers_no_errors():
    tokens = _load('simple_containers.cif')
    assert errors(tokens) == []


def test_cif11_ver1_no_errors():
    tokens = _load('ver1.cif', version=CIF1)
    assert errors(tokens) == []
