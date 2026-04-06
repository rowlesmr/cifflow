"""Tests for DdlmItem dataclass."""

import pytest
from pycifparse.dictionary.ddlm_item import DdlmItem


def _minimal(**kwargs) -> DdlmItem:
    """Return a DdlmItem with all required fields set to sensible defaults."""
    defaults = dict(
        definition_id='_atom_site.fract_x',
        scope='Item',
        definition_class='Datum',
        category_id='atom_site',
        object_id='fract_x',
        type_purpose='Measurand',
        type_source='Recorded',
        type_container='Single',
        type_contents='Real',
        linked_item_id=None,
        units_code=None,
        description=None,
    )
    defaults.update(kwargs)
    return DdlmItem(**defaults)


class TestDdlmItemDefaults:
    def test_is_deprecated_defaults_false(self):
        item = _minimal()
        assert item.is_deprecated is False

    def test_enumeration_states_defaults_empty(self):
        item = _minimal()
        assert item.enumeration_states == []

    def test_category_keys_defaults_empty(self):
        item = _minimal()
        assert item.category_keys == []

    def test_aliases_defaults_empty(self):
        item = _minimal()
        assert item.aliases == []

    def test_replaced_by_defaults_empty(self):
        item = _minimal()
        assert item.replaced_by == []


class TestDdlmItemListIndependence:
    """List fields must be independent across instances."""

    def test_enumeration_states_independent(self):
        a = _minimal()
        b = _minimal()
        a.enumeration_states.append('x')
        assert b.enumeration_states == []

    def test_category_keys_independent(self):
        a = _minimal()
        b = _minimal()
        a.category_keys.append('_foo.id')
        assert b.category_keys == []

    def test_aliases_independent(self):
        a = _minimal()
        b = _minimal()
        a.aliases.append('_old_name')
        assert b.aliases == []

    def test_replaced_by_independent(self):
        a = _minimal()
        b = _minimal()
        a.replaced_by.append('_new_name')
        assert b.replaced_by == []


class TestDdlmItemFields:
    def test_required_fields_stored(self):
        item = _minimal(
            definition_id='_diffrn.ambient_pressure',
            scope='Item',
            definition_class='Datum',
            category_id='diffrn',
            object_id='ambient_pressure',
            type_purpose='Measurand',
            type_source='Recorded',
            type_container='Single',
            type_contents='Real',
            linked_item_id=None,
            units_code='kPa',
            description='Ambient pressure during measurement.',
        )
        assert item.definition_id == '_diffrn.ambient_pressure'
        assert item.category_id == 'diffrn'
        assert item.object_id == 'ambient_pressure'
        assert item.units_code == 'kPa'
        assert item.description == 'Ambient pressure during measurement.'

    def test_nullable_fields_accept_none(self):
        item = _minimal(
            category_id=None,
            object_id=None,
            type_purpose=None,
            type_source=None,
            type_contents=None,
            linked_item_id=None,
            units_code=None,
            description=None,
        )
        assert item.category_id is None
        assert item.object_id is None
        assert item.linked_item_id is None

    def test_is_deprecated_can_be_set(self):
        item = _minimal(is_deprecated=True)
        assert item.is_deprecated is True

    def test_replaced_by_empty_string_for_placeholder(self):
        # An empty string in replaced_by represents PLACEHOLDER ("."),
        # meaning deprecated with no replacement.
        item = _minimal(replaced_by=[''], is_deprecated=True)
        assert item.replaced_by == ['']
        assert item.is_deprecated is True

    def test_category_item_fields(self):
        item = _minimal(
            definition_id='atom_site',
            scope='Category',
            definition_class='Loop',
            category_id='atom_site',
            object_id=None,
            category_keys=['_atom_site.id'],
        )
        assert item.scope == 'Category'
        assert item.definition_class == 'Loop'
        assert item.category_keys == ['_atom_site.id']
