"""Branch-coverage tests for _render_positional_join (extracted from _render_original_loop_group).

Tests cover the positional-join rendering path in isolation, without DuckDB.
"""
import pytest

from cifflow.dictionary.ddlm_item import DdlmItem
from cifflow.dictionary.ddlm_parser import DdlmDictionary
from cifflow.dictionary.schema import generate_schema
from cifflow.output.emit import _render_positional_join
from cifflow.types import CifVersion


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


def _schema_two_tables():
    """atom_site (Loop, PK=label, col=x_fract) and atom_site_b (Loop, PK=label, col=y_fract)."""
    cats = [
        _cat('_atom_site', 'Loop', ['_atom_site.label']),
        _cat('_atom_site_b', 'Loop', ['_atom_site_b.label']),
    ]
    items = [
        _item('_atom_site.label', '_atom_site', 'label', type_purpose='Key', type_contents='Text'),
        _item('_atom_site.x_fract', '_atom_site', 'x_fract', type_contents='Real'),
        _item('_atom_site_b.label', '_atom_site_b', 'label', type_purpose='Key', type_contents='Text'),
        _item('_atom_site_b.y_fract', '_atom_site_b', 'y_fract', type_contents='Real'),
    ]
    return generate_schema(_make_dict(cats, items))


def _schema_with_su():
    """atom_site (Loop, PK=label, col=x_fract with SU)."""
    cats = [_cat('_atom_site', 'Loop', ['_atom_site.label'])]
    items = [
        _item('_atom_site.label', '_atom_site', 'label', type_purpose='Key', type_contents='Text'),
        _item('_atom_site.x_fract', '_atom_site', 'x_fract', type_contents='Real'),
        _item('_atom_site.x_fract_su', '_atom_site', 'x_fract_su',
              type_purpose='SU', linked_item_id='_atom_site.x_fract', type_contents='Real'),
    ]
    return generate_schema(_make_dict(cats, items))


VER = CifVersion.CIF_2_0


def _row(rid, **kwargs):
    return {'_cifflow_row_id': rid, **kwargs}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRenderPositionalJoin:
    def test_no_rows_returns_empty(self):
        schema = _schema_two_tables()
        per_table = [
            ('atom_site', ['label', 'x_fract'], []),
            ('atom_site_b', ['label', 'y_fract'], []),
        ]
        result = _render_positional_join(per_table, schema, VER,
                                         reconstruct_su=False, pretty=False, line_limit=None)
        assert result == []

    def test_basic_two_table_join(self):
        schema = _schema_two_tables()
        per_table = [
            ('atom_site', ['label', 'x_fract'], [_row(1, label='C1', x_fract='0.5')]),
            ('atom_site_b', ['label', 'y_fract'], [_row(1, label='C1', y_fract='0.3')]),
        ]
        result = _render_positional_join(per_table, schema, VER,
                                         reconstruct_su=False, pretty=False, line_limit=None)
        assert result[0] == 'loop_'
        # Tags for both tables should appear
        joined = '\n'.join(result)
        assert '_atom_site.label' in joined
        assert '_atom_site_b.y_fract' in joined
        # Data row: 4 values on one line
        data_lines = [l for l in result if not l.startswith('loop_') and not l.startswith('  _')]
        assert len(data_lines) == 1
        assert 'C1' in data_lines[0]
        assert '0.3' in data_lines[0]

    def test_sparse_rows_padded_with_placeholder(self):
        """Table B has 1 row but table A has 2 — second B row should be '.'"""
        schema = _schema_two_tables()
        per_table = [
            ('atom_site', ['label', 'x_fract'], [
                _row(1, label='C1', x_fract='0.1'),
                _row(2, label='C2', x_fract='0.2'),
            ]),
            ('atom_site_b', ['label', 'y_fract'], [_row(1, label='C1', y_fract='0.5')]),
        ]
        result = _render_positional_join(per_table, schema, VER,
                                         reconstruct_su=False, pretty=False, line_limit=None)
        data_lines = [l for l in result if not l.startswith('loop_') and not l.startswith('  _')]
        assert len(data_lines) == 2
        assert '.' in data_lines[1]  # padded for missing B row

    def test_rows_sorted_by_row_id(self):
        """Rows out of insertion order should be sorted by _cifflow_row_id."""
        schema = _schema_two_tables()
        per_table = [
            ('atom_site', ['label', 'x_fract'], [
                _row(2, label='C2', x_fract='0.2'),
                _row(1, label='C1', x_fract='0.1'),
            ]),
            ('atom_site_b', ['label', 'y_fract'], [
                _row(2, label='C2', y_fract='0.5'),
                _row(1, label='C1', y_fract='0.3'),
            ]),
        ]
        result = _render_positional_join(per_table, schema, VER,
                                         reconstruct_su=False, pretty=False, line_limit=None)
        data_lines = [l for l in result if not l.startswith('loop_') and not l.startswith('  _')]
        # First row should be C1 (row_id=1)
        assert data_lines[0].split()[0] == 'C1'
        assert data_lines[1].split()[0] == 'C2'

    def test_reconstruct_su_merges_value(self):
        """With reconstruct_su=True, value(su) should be merged into the output token."""
        schema = _schema_with_su()
        per_table = [
            ('atom_site', ['label', 'x_fract'],
             [_row(1, label='C1', x_fract='0.123', x_fract_su='0.002')]),
        ]
        result = _render_positional_join(per_table, schema, VER,
                                         reconstruct_su=True, pretty=False, line_limit=None)
        data_lines = [l for l in result if not l.startswith('loop_') and not l.startswith('  _')]
        assert '0.123(2)' in data_lines[0]

    def test_reconstruct_su_null_su_leaves_value_unchanged(self):
        schema = _schema_with_su()
        per_table = [
            ('atom_site', ['label', 'x_fract'],
             [_row(1, label='C1', x_fract='0.123', x_fract_su=None)]),
        ]
        result = _render_positional_join(per_table, schema, VER,
                                         reconstruct_su=True, pretty=False, line_limit=None)
        data_lines = [l for l in result if not l.startswith('loop_') and not l.startswith('  _')]
        assert '0.123' in data_lines[0]
        assert '(' not in data_lines[0]

    def test_pretty_mode_runs_without_error(self):
        """pretty=True should trigger decimal-align path without crashing."""
        schema = _schema_two_tables()
        per_table = [
            ('atom_site', ['label', 'x_fract'], [
                _row(1, label='C1', x_fract='1.0'),
                _row(2, label='C2', x_fract='10.5'),
            ]),
            ('atom_site_b', ['label', 'y_fract'], [
                _row(1, label='C1', y_fract='0.3'),
                _row(2, label='C2', y_fract='1.20'),
            ]),
        ]
        result = _render_positional_join(per_table, schema, VER,
                                         reconstruct_su=False, pretty=True, line_limit=None)
        assert result[0] == 'loop_'
        data_lines = [l for l in result if not l.startswith('loop_') and not l.startswith('  _')]
        assert len(data_lines) == 2

    def test_null_value_emitted_as_placeholder(self):
        schema = _schema_two_tables()
        per_table = [
            ('atom_site', ['label', 'x_fract'], [_row(1, label='C1', x_fract=None)]),
            ('atom_site_b', ['label', 'y_fract'], [_row(1, label='C1', y_fract='0.3')]),
        ]
        result = _render_positional_join(per_table, schema, VER,
                                         reconstruct_su=False, pretty=False, line_limit=None)
        data_lines = [l for l in result if not l.startswith('loop_') and not l.startswith('  _')]
        tokens = data_lines[0].split()
        assert '.' in tokens  # NULL emitted as '.'
