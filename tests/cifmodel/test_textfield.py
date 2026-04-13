"""
Tests for the multiline text field transformation pipeline.
"""

import pytest
from pycifparse.cifmodel.textfield import transform_multiline


# ─────────────────────────────────────────────────────────────────────────────
# No transformation (plain content)
# ─────────────────────────────────────────────────────────────────────────────

def test_plain_content_unchanged():
    raw = 'line one\nline two\nline three'
    assert transform_multiline(raw) == raw

def test_empty_string():
    assert transform_multiline('') == ''

def test_single_line():
    assert transform_multiline('just a line') == 'just a line'

def test_no_backslash_multiline():
    raw = 'alpha\nbeta\ngamma\n'
    assert transform_multiline(raw) == raw


# ─────────────────────────────────────────────────────────────────────────────
# Fold only (no prefix; content starts with \)
# ─────────────────────────────────────────────────────────────────────────────

def test_bare_fold_separator_triggers_folding():
    # First line is '\' → fold separator; subsequent lines unfolded
    raw = '\\\nfirst\\\nsecond'
    result = transform_multiline(raw)
    assert result == 'firstsecond'

def test_bare_fold_separator_removed():
    # The initial '\' line is consumed entirely
    raw = '\\\nline one\nline two'
    assert transform_multiline(raw) == 'line one\nline two'

def test_fold_joins_continuation():
    raw = '\\\nThis line is \\\ncontinued here.'
    assert transform_multiline(raw) == 'This line is continued here.'

def test_separator_at_end_disappears():
    # If the last character of a folded string is the fold separator
    #  then it doesn't appear in the result
    raw = '\\\nfirst\\\nsecond\\'
    result = transform_multiline(raw)
    assert result == 'firstsecond'

def test_fold_trailing_whitespace_after_backslash_ignored():
    # Fold separator: \ followed by spaces — the spaces are part of the separator
    raw = '\\\npart one\\   \npart two'
    assert transform_multiline(raw) == 'part onepart two'

def test_fold_only_some_lines():
    raw = '\\\nline one\nline two \\\nline three\nline four'
    assert transform_multiline(raw) == 'line one\nline two line three\nline four'

def test_fold_final_line_no_newline():
    # Final line may carry a fold separator without a trailing newline
    raw = '\\\nstart\\\nend\\'
    assert transform_multiline(raw) == 'startend'

def test_no_fold_if_first_line_not_separator():
    # Content starts with normal text → no fold protocol
    raw = 'normal\nline two \\\nline three'
    assert transform_multiline(raw) == 'normal\nline two \\\nline three'

def test_double_backslash_no_fold_escape():
    # \\ at position 0 → remove one backslash, no fold
    raw = '\\\\\nline two'
    assert transform_multiline(raw) == '\\\nline two'


# ─────────────────────────────────────────────────────────────────────────────
# Prefix only (double backslash variant — fold NOT triggered)
# ─────────────────────────────────────────────────────────────────────────────

def test_prefix_double_backslash_no_fold():
    # prefix:\\ → remove prefix from all lines, remove one backslash from first
    raw = 'p:\\\\\np:line two\np:line three'
    result = transform_multiline(raw)
    assert result == '\\\nline two\nline three'

def test_prefix_validation_fails_if_line_missing_prefix():
    # Line 2 does not start with 'p:' → prefix protocol does not apply
    raw = 'p:\\\nNOPREFIX\np:line three'
    assert transform_multiline(raw) == raw


# ─────────────────────────────────────────────────────────────────────────────
# Prefix + fold (the full pipeline)
# ─────────────────────────────────────────────────────────────────────────────

def test_spec_example():
    # From the CIF spec: prefix: 'prefix:', fold triggered by '\' on first line.
    # Input (raw lexer value, opening/closing ; already stripped):
    raw = (
        'prefix:\\\n'
        'prefix:data_example\n'
        'prefix:_text\n'
        'prefix:;This line was\\\n'
        'prefix: folded.\n'
        'prefix:;'
    )
    expected = 'data_example\n_text\n;This line was folded.\n;'
    assert transform_multiline(raw) == expected

def test_prefix_single_backslash_triggers_fold():
    # Prefix 'pfx:' with single \ → fold triggered
    raw = 'pfx:\\\npfx:hello \\\npfx:world'
    assert transform_multiline(raw) == 'hello world'

def test_prefix_removes_from_all_lines():
    raw = 'XX:\\\nXX:alpha\nXX:beta'
    assert transform_multiline(raw) == 'alpha\nbeta'

def test_prefix_semicolon_in_content_preserved():
    # A ; at the start of a prefix-stripped line is legal content (already past lexer)
    raw = 'p:\\\np:;This is fine\np:normal'
    assert transform_multiline(raw) == ';This is fine\nnormal'

def test_prefix_double_backslash_content_starts_with_backslash():
    # prefix:\\ → content starts literally with \, no folding
    raw = 'p:\\\\\np:rest'
    assert transform_multiline(raw) == '\\\nrest'

def test_no_fold_within_prefix_if_not_triggered():
    # Prefix double-backslash variant: trailing \ on lines are NOT fold separators
    raw = 'p:\\\\\np:line\\\np:next'
    result = transform_multiline(raw)
    # No fold protocol → trailing \ preserved
    assert result == '\\\nline\\\nnext'
