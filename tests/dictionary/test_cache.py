"""
Tests for save_dictionary / load_dictionary (dictionary/cache.py).
"""

import json
import pathlib
import tempfile

import pytest

from cifflow.dictionary.cache import load_dictionary, save_dictionary
from cifflow.dictionary.ddlm_item import DdlmItem
from cifflow.dictionary.ddlm_parser import DdlmDictionary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(def_id: str, scope: str = 'Item', cat: str = 'cat', obj: str = 'obj') -> DdlmItem:
    return DdlmItem(
        definition_id=def_id,
        scope=scope,
        definition_class='Datum' if scope == 'Item' else 'Set',
        category_id=cat if scope == 'Item' else None,
        object_id=obj if scope == 'Item' else None,
        type_purpose=None,
        type_source=None,
        type_container='Single',
        type_contents=None,
        linked_item_id=None,
        units_code=None,
        description=None,
    )


def _make_dict(**kwargs) -> DdlmDictionary:
    defaults = dict(
        name='TEST',
        title='Test Dict',
        version='1.0',
        categories={},
        items={},
        tag_to_item={},
        alias_to_definition_id={},
        deprecated_ids=set(),
        warnings=[],
    )
    defaults.update(kwargs)
    return DdlmDictionary(**defaults)


def _round_trip(dictionary: DdlmDictionary) -> DdlmDictionary:
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        path = pathlib.Path(f.name)
    try:
        save_dictionary(dictionary, path)
        return load_dictionary(path)
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Round-trip fidelity
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_scalar_fields_preserved(self):
        d = _make_dict(name='FOO', title='Foo Dict', version='2.0', warnings=['w1'])
        r = _round_trip(d)
        assert r.name == 'FOO'
        assert r.title == 'Foo Dict'
        assert r.version == '2.0'
        assert r.warnings == ['w1']

    def test_none_title_and_version(self):
        d = _make_dict(title=None, version=None)
        r = _round_trip(d)
        assert r.title is None
        assert r.version is None

    def test_categories_preserved(self):
        cat = _item('atom_site', scope='Category', cat=None, obj=None)
        d = _make_dict(
            categories={'atom_site': cat},
            tag_to_item={'atom_site': cat},
        )
        r = _round_trip(d)
        assert 'atom_site' in r.categories
        assert r.categories['atom_site'].definition_id == 'atom_site'

    def test_items_preserved(self):
        item = _item('_atom_site.fract_x', cat='atom_site', obj='fract_x')
        d = _make_dict(
            items={'_atom_site.fract_x': item},
            tag_to_item={'_atom_site.fract_x': item},
        )
        r = _round_trip(d)
        assert '_atom_site.fract_x' in r.items
        assert r.items['_atom_site.fract_x'].object_id == 'fract_x'

    def test_tag_to_item_reconstructed(self):
        item = _item('_atom_site.fract_x', cat='atom_site', obj='fract_x')
        d = _make_dict(
            items={'_atom_site.fract_x': item},
            tag_to_item={
                '_atom_site.fract_x': item,
                '_atom_site_fract_x': item,  # alias
            },
            alias_to_definition_id={'_atom_site_fract_x': '_atom_site.fract_x'},
        )
        r = _round_trip(d)
        assert '_atom_site.fract_x' in r.tag_to_item
        assert '_atom_site_fract_x' in r.tag_to_item
        assert r.tag_to_item['_atom_site_fract_x'] is r.tag_to_item['_atom_site.fract_x']

    def test_alias_to_definition_id_preserved(self):
        item = _item('_atom_site.fract_x', cat='atom_site', obj='fract_x')
        d = _make_dict(
            items={'_atom_site.fract_x': item},
            tag_to_item={'_atom_site.fract_x': item, '_old_alias': item},
            alias_to_definition_id={'_old_alias': '_atom_site.fract_x'},
        )
        r = _round_trip(d)
        assert r.alias_to_definition_id == {'_old_alias': '_atom_site.fract_x'}

    def test_deprecated_ids_is_set(self):
        item = _item('_old.tag')
        d = _make_dict(
            items={'_old.tag': item},
            tag_to_item={'_old.tag': item},
            deprecated_ids={'_old.tag'},
        )
        r = _round_trip(d)
        assert isinstance(r.deprecated_ids, set)
        assert '_old.tag' in r.deprecated_ids

    def test_empty_dictionary(self):
        d = _make_dict()
        r = _round_trip(d)
        assert r.categories == {}
        assert r.items == {}
        assert r.tag_to_item == {}
        assert r.deprecated_ids == set()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestLoadErrors:
    def test_missing_file_raises_value_error(self):
        with pytest.raises(ValueError, match='not found'):
            load_dictionary('/nonexistent/path/dict.json')

    def test_malformed_json_raises_value_error(self):
        with tempfile.NamedTemporaryFile(
            suffix='.json', mode='w', delete=False, encoding='utf-8'
        ) as f:
            f.write('{ not valid json }')
            path = pathlib.Path(f.name)
        try:
            with pytest.raises(ValueError, match='malformed JSON'):
                load_dictionary(path)
        finally:
            path.unlink(missing_ok=True)

    def test_invalid_categories_raises_value_error(self):
        # Lines 105-106: KeyError/TypeError in categories/items construction
        item = _item('_atom_site.fract_x', cat='atom_site', obj='fract_x')
        d = _make_dict(items={'_atom_site.fract_x': item}, tag_to_item={'_atom_site.fract_x': item})
        with tempfile.NamedTemporaryFile(
            suffix='.json', mode='w', delete=False, encoding='utf-8'
        ) as f:
            path = pathlib.Path(f.name)
        try:
            save_dictionary(d, path)
            data = json.loads(path.read_text(encoding='utf-8'))
            # Corrupt categories so DdlmItem(**v) fails
            data['categories'] = {'bad_cat': {'not_a_valid_field': True}}
            path.write_text(json.dumps(data), encoding='utf-8')
            with pytest.raises(ValueError, match='invalid dictionary cache structure'):
                load_dictionary(path)
        finally:
            path.unlink(missing_ok=True)

    def test_invalid_tag_to_item_structure_raises_value_error(self):
        # Line 119-120: KeyError in tag_to_item reconstruction (missing key)
        item = _item('_atom_site.fract_x', cat='atom_site', obj='fract_x')
        d = _make_dict(items={'_atom_site.fract_x': item}, tag_to_item={'_atom_site.fract_x': item})
        with tempfile.NamedTemporaryFile(
            suffix='.json', mode='w', delete=False, encoding='utf-8'
        ) as f:
            path = pathlib.Path(f.name)
        try:
            save_dictionary(d, path)
            data = json.loads(path.read_text(encoding='utf-8'))
            # Remove 'tag_to_item' key to cause KeyError when accessing it
            del data['tag_to_item']
            path.write_text(json.dumps(data), encoding='utf-8')
            with pytest.raises(ValueError, match='invalid tag_to_item'):
                load_dictionary(path)
        finally:
            path.unlink(missing_ok=True)

    def test_invalid_final_construction_raises_value_error(self):
        # Lines 135-136: KeyError/TypeError in DdlmDictionary construction
        item = _item('_atom_site.fract_x', cat='atom_site', obj='fract_x')
        d = _make_dict(items={'_atom_site.fract_x': item}, tag_to_item={'_atom_site.fract_x': item})
        with tempfile.NamedTemporaryFile(
            suffix='.json', mode='w', delete=False, encoding='utf-8'
        ) as f:
            path = pathlib.Path(f.name)
        try:
            save_dictionary(d, path)
            data = json.loads(path.read_text(encoding='utf-8'))
            # Remove 'name' so DdlmDictionary(**data) fails with KeyError
            del data['name']
            path.write_text(json.dumps(data), encoding='utf-8')
            with pytest.raises(ValueError, match='invalid dictionary cache structure'):
                load_dictionary(path)
        finally:
            path.unlink(missing_ok=True)

    def test_unknown_definition_id_in_tag_to_item_raises_value_error(self):
        item = _item('_atom_site.fract_x', cat='atom_site', obj='fract_x')
        d = _make_dict(
            items={'_atom_site.fract_x': item},
            tag_to_item={'_atom_site.fract_x': item},
        )
        with tempfile.NamedTemporaryFile(
            suffix='.json', mode='w', delete=False, encoding='utf-8'
        ) as f:
            path = pathlib.Path(f.name)
        try:
            save_dictionary(d, path)
            # Corrupt the tag_to_item to reference a nonexistent definition_id.
            data = json.loads(path.read_text(encoding='utf-8'))
            data['tag_to_item']['_bad_alias'] = '_does_not_exist'
            path.write_text(json.dumps(data), encoding='utf-8')
            with pytest.raises(ValueError, match='unknown'):
                load_dictionary(path)
        finally:
            path.unlink(missing_ok=True)
