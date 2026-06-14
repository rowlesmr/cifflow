"""Branch-coverage tests for _render_set_category helper functions.

Tests cover _build_set_quads, _requote_set_quads, and _decimal_align_set_quads
extracted from _render_set_category, plus the orchestrator itself.
"""
import pytest

from cifflow.dictionary.ddlm_item import DdlmItem
from cifflow.dictionary.ddlm_parser import DdlmDictionary
from cifflow.dictionary.schema import generate_schema
from cifflow.output.emit import (
    _build_set_quads,
    _decimal_align_set_quads,
    _requote_set_quads,
    _render_set_category,
)
from cifflow.types import CifVersion

VER = CifVersion.CIF_2_0


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _item(definition_id, category_id, object_id, *, type_purpose=None,
          type_contents=None, linked_item_id=None):
    return DdlmItem(
        definition_id=definition_id, scope='Item', definition_class='Datum',
        category_id=category_id, object_id=object_id,
        type_purpose=type_purpose, type_source=None, type_container='Single',
        type_contents=type_contents, linked_item_id=linked_item_id,
        units_code=None, description=None,
    )


def _cat(definition_id, cat_class, category_keys=None):
    return DdlmItem(
        definition_id=definition_id, scope='Category',
        definition_class=cat_class, category_id=None, object_id=None,
        type_purpose=None, type_source=None, type_container='Single',
        type_contents=None, linked_item_id=None, units_code=None,
        description=None, category_keys=category_keys or [],
    )


def _make_dict(cats, items):
    categories = {c.definition_id: c for c in cats}
    item_map = {i.definition_id: i for i in items}
    return DdlmDictionary(
        name='TEST', title=None, version=None,
        categories=categories, items=item_map,
        tag_to_item={**categories, **item_map},
        alias_to_definition_id={}, deprecated_ids=set(),
    )


def _schema_set():
    """cell (Set PK=id, col=length_a Real, col=name Text, col=length_a_su SU for length_a)."""
    cats = [_cat('_cell', 'Set', ['_cell.id'])]
    items = [
        _item('_cell.id', '_cell', 'id', type_purpose='Key', type_contents='Text'),
        _item('_cell.length_a', '_cell', 'length_a', type_contents='Real'),
        _item('_cell.length_a_su', '_cell', 'length_a_su',
              type_purpose='SU', linked_item_id='_cell.length_a', type_contents='Real'),
        _item('_cell.name', '_cell', 'name', type_contents='Text'),
    ]
    return generate_schema(_make_dict(cats, items))


_SCHEMA = _schema_set()
_TABLE_DEF = _SCHEMA.tables['cell']
_SU_MAP = {'length_a': 'length_a_su'}


# ---------------------------------------------------------------------------
# Tests: _build_set_quads
# ---------------------------------------------------------------------------

class TestBuildSetQuads:
    def test_basic_quads_built(self):
        row = {'id': 'S1', 'length_a': '5.0', 'name': 'test'}
        quads = _build_set_quads(['id', 'length_a', 'name'], row, 'cell',
                                  _SCHEMA, VER, False, {}, None)
        assert len(quads) == 3
        tags = [q[0] for q in quads]
        assert '_cell.id' in tags

    def test_null_value_skipped(self):
        row = {'id': 'S1', 'length_a': None, 'name': 'test'}
        quads = _build_set_quads(['id', 'length_a', 'name'], row, 'cell',
                                  _SCHEMA, VER, False, {}, None)
        cols = [q[1] for q in quads]
        assert 'length_a' not in cols

    def test_su_merged_when_present(self):
        row = {'length_a': '5.123', 'length_a_su': '0.003'}
        quads = _build_set_quads(['length_a'], row, 'cell',
                                  _SCHEMA, VER, True, _SU_MAP, None)
        assert len(quads) == 1
        assert quads[0][2] == '5.123(3)'  # value with SU merged

    def test_su_null_leaves_value_unchanged(self):
        row = {'length_a': '5.123', 'length_a_su': None}
        quads = _build_set_quads(['length_a'], row, 'cell',
                                  _SCHEMA, VER, True, _SU_MAP, None)
        assert quads[0][2] == '5.123'

    def test_reconstruct_su_false_ignores_su_map(self):
        row = {'length_a': '5.123', 'length_a_su': '0.003'}
        quads = _build_set_quads(['length_a'], row, 'cell',
                                  _SCHEMA, VER, False, _SU_MAP, None)
        assert quads[0][2] == '5.123'  # no SU merging

    def test_multiline_token_initially_folded_with_line_limit(self):
        # A very long value that quote() would render as multiline
        long_val = 'A' * 200
        row = {'name': long_val}
        quads = _build_set_quads(['name'], row, 'cell',
                                  _SCHEMA, VER, False, {}, 80)
        # The initial quote produces a semicolon text field (\n at start)
        # line_limit path should apply make_text_field
        assert len(quads) == 1


# ---------------------------------------------------------------------------
# Tests: _requote_set_quads
# ---------------------------------------------------------------------------

class TestRequoteSetQuads:
    def _quad(self, tag, col, value, token):
        return (tag, col, value, token)

    def test_line_too_long_requoted_as_multiline(self):
        q = self._quad('_cell.name', 'name', 'hello', 'hello')
        quads, new_width = _requote_set_quads([q], tag_width=0, line_limit=5, pretty=False)
        # '_cell.name  hello' is 17 chars > 5; should be requoted as multiline
        assert quads[0][3].startswith('\n')

    def test_line_fits_unchanged(self):
        q = self._quad('_cell.id', 'id', 'S1', 'S1')
        quads, new_width = _requote_set_quads([q], tag_width=0, line_limit=200, pretty=False)
        assert quads[0][3] == 'S1'

    def test_already_multiline_not_requoted(self):
        token = '\n; text field\n;'
        q = self._quad('_cell.name', 'name', 'text field', token)
        quads, _ = _requote_set_quads([q], tag_width=0, line_limit=5, pretty=False)
        assert quads[0][3] == token  # unchanged

    def test_pretty_tag_width_recomputed(self):
        q1 = self._quad('_cell.length_a', 'length_a', '5.0', '5.0')
        q2 = self._quad('_cell.id', 'id', 'X', 'X')
        quads, new_width = _requote_set_quads([q1, q2], tag_width=5, line_limit=200, pretty=True)
        # No requoting happened; tag_width should be max of inline tag lengths
        assert new_width == max(len('_cell.length_a'), len('_cell.id'))

    def test_pretty_tag_width_excludes_multiline_tokens(self):
        q1 = self._quad('_cell.length_a', 'length_a', '5.0', '5.0')
        q2 = self._quad('_cell.name', 'name', 'X', '\n; X\n;')
        quads, new_width = _requote_set_quads([q1, q2], tag_width=5, line_limit=200, pretty=True)
        # Only q1 is inline; width = len('_cell.length_a')
        assert new_width == len('_cell.length_a')


# ---------------------------------------------------------------------------
# Tests: _decimal_align_set_quads
# ---------------------------------------------------------------------------

class TestDecimalAlignSetQuads:
    def test_real_tokens_aligned(self):
        quads = [
            ('_cell.length_a', 'length_a', '5.0', '5.0'),
            ('_cell.length_a', 'length_a', '10.50', '10.50'),
        ]
        result = _decimal_align_set_quads(quads, _TABLE_DEF)
        tokens = [q[3] for q in result]
        # Decimal-aligned: both have same width
        assert len(tokens[0]) == len(tokens[1])

    def test_non_real_tokens_unchanged(self):
        quads = [('_cell.name', 'name', 'hello', 'hello')]
        result = _decimal_align_set_quads(quads, _TABLE_DEF)
        assert result[0][3] == 'hello'

    def test_multiline_real_token_excluded_from_alignment(self):
        quads = [
            ('_cell.length_a', 'length_a', '5.0', '5.0'),
            ('_cell.length_a', 'length_a', '10.5', '\n; 10.5\n;'),
        ]
        result = _decimal_align_set_quads(quads, _TABLE_DEF)
        # The multiline token should not affect alignment and remain unchanged
        assert result[1][3] == '\n; 10.5\n;'

    def test_no_real_tokens_unchanged(self):
        quads = [('_cell.name', 'name', 'hello', 'hello')]
        result = _decimal_align_set_quads(quads, _TABLE_DEF)
        assert result == quads


# ---------------------------------------------------------------------------
# Integration tests: _render_set_category
# ---------------------------------------------------------------------------

class TestRenderSetCategory:
    def test_basic_rendering(self):
        row = {'id': 'S1', 'length_a': '5.0', 'name': 'test'}
        result = _render_set_category(row, ['id', 'length_a', 'name'],
                                       'cell', _SCHEMA, VER, _TABLE_DEF,
                                       False, False, None)
        assert any('_cell.id' in l for l in result)
        assert any('S1' in l for l in result)

    def test_null_cols_omitted(self):
        row = {'id': 'S1', 'length_a': None}
        result = _render_set_category(row, ['id', 'length_a'],
                                       'cell', _SCHEMA, VER, _TABLE_DEF,
                                       False, False, None)
        assert not any('length_a' in l for l in result)

    def test_pretty_mode(self):
        row = {'id': 'S1', 'length_a': '5.0', 'name': 'test'}
        result = _render_set_category(row, ['id', 'length_a', 'name'],
                                       'cell', _SCHEMA, VER, _TABLE_DEF,
                                       False, True, None)
        assert len(result) == 3
        assert any('S1' in l for l in result)
        assert any('5.0' in l for l in result)
        assert any('test' in l for l in result)

    def test_reconstruct_su(self):
        row = {'length_a': '5.123', 'length_a_su': '0.003'}
        result = _render_set_category(row, ['length_a'],
                                       'cell', _SCHEMA, VER, _TABLE_DEF,
                                       True, False, None)
        assert any('5.123(3)' in l for l in result)
