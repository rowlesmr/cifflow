"""
Tests for DictionaryLoader and DdlmDictionary.

Phase A: no-import parsing.
Phase B: _import.get resolution.
"""

import pathlib
import pytest

from pycifparse.dictionary.loader import DictionaryLoader, directory_resolver
from pycifparse.dictionary.ddlm_item import DdlmItem

DATA_DIR = pathlib.Path(__file__).parent.parent.parent / 'data' / 'dictionaries'

# ─────────────────────────────────────────────────────────────────────────────
# Hand-authored test dictionary (no imports)
# ─────────────────────────────────────────────────────────────────────────────

_NO_IMPORT_CIF = """\
#\\#CIF_2.0

data_TEST_DICT

_dictionary.title          TEST_DICT
_dictionary.version        1.0.0

# Head category — produces no table
save_TEST_HEAD
  _definition.id           TEST_HEAD
  _definition.scope        Category
  _definition.class        Head
  _name.category_id        TEST_HEAD
  _name.object_id          TEST_HEAD
save_

# Set category
save_ANIMAL
  _definition.id           ANIMAL
  _definition.scope        Category
  _definition.class        Set
  _name.category_id        ANIMAL
  _name.object_id          ANIMAL
  _description.text        'A set category for animals.'
save_

# Loop category with single key
save_LIMB
  _definition.id           LIMB
  _definition.scope        Category
  _definition.class        Loop
  _name.category_id        LIMB
  _name.object_id          LIMB
  _category_key.name       '_limb.id'
save_

# Loop category with composite key
save_BONE
  _definition.id           BONE
  _definition.scope        Category
  _definition.class        Loop
  _name.category_id        BONE
  _name.object_id          BONE
  loop_
    _category_key.name
      '_bone.limb_id'
      '_bone.position'
save_

# Dictionary-scope frame — should be skipped silently
save_CIF_DICT
  _definition.id           CIF_DICT
  _definition.scope        Dictionary
  _name.category_id        CIF_DICT
  _name.object_id          CIF_DICT
save_

# Item: _animal.name (Key, Text)
save_animal.name
  _definition.id           '_animal.name'
  _definition.class        Attribute
  _name.category_id        animal
  _name.object_id          name
  _type.purpose            Key
  _type.source             Assigned
  _type.container          Single
  _type.contents           Text
save_

# Item: _limb.id (Key, Text)
save_limb.id
  _definition.id           '_limb.id'
  _definition.class        Attribute
  _name.category_id        limb
  _name.object_id          id
  _type.purpose            Key
  _type.source             Assigned
  _type.container          Single
  _type.contents           Text
save_

# Item: _limb.count (Measurand, Integer) — _name.category_id differs from dot prefix
save_limb.count
  _definition.id           '_limb.count'
  _definition.class        Attribute
  _name.category_id        animal
  _name.object_id          limb_count
  _type.purpose            Measurand
  _type.source             Recorded
  _type.container          Single
  _type.contents           Integer
save_

# Item: _limb.mass (Measurand, Real) with aliases
save_limb.mass
  _definition.id           '_limb.mass'
  _definition.class        Attribute
  _name.category_id        limb
  _name.object_id          mass
  _type.purpose            Measurand
  _type.source             Recorded
  _type.container          Single
  _type.contents           Real
  _units.code              kg
  loop_
    _alias.definition_id
      '_limb_mass'
      '_old.limb.mass'
save_

# Item: _limb.mass_su (SU, Real) — linked to _limb.mass
save_limb.mass_su
  _definition.id           '_limb.mass_su'
  _definition.class        Attribute
  _name.category_id        limb
  _name.object_id          mass_su
  _type.purpose            SU
  _type.source             Recorded
  _type.container          Single
  _type.contents           Real
  _name.linked_item_id     '_limb.mass'
save_

# Item: _bone.limb_id (Link, Text) — FK to _limb.id
save_bone.limb_id
  _definition.id           '_bone.limb_id'
  _definition.class        Attribute
  _name.category_id        bone
  _name.object_id          limb_id
  _type.purpose            Link
  _type.source             Assigned
  _type.container          Single
  _type.contents           Text
  _name.linked_item_id     '_limb.id'
save_

# Item: _bone.position (Key, Integer)
save_bone.position
  _definition.id           '_bone.position'
  _definition.class        Attribute
  _name.category_id        bone
  _name.object_id          position
  _type.purpose            Key
  _type.source             Assigned
  _type.container          Single
  _type.contents           Integer
save_

# Item: deprecated with replacement
save_animal.legs
  _definition.id           '_animal.legs'
  _definition.class        Attribute
  _name.category_id        animal
  _name.object_id          legs
  _type.purpose            Measurand
  _type.source             Recorded
  _type.container          Single
  _type.contents           Integer
  _definition_replaced.id  1
  _definition_replaced.by  '_animal.name'
save_

# Item: deprecated with no replacement (PLACEHOLDER)
save_animal.extinct
  _definition.id           '_animal.extinct'
  _definition.class        Attribute
  _name.category_id        animal
  _name.object_id          extinct
  _type.purpose            Describe
  _type.source             Assigned
  _type.container          Single
  _type.contents           Text
  _definition_replaced.id  1
  _definition_replaced.by  .
save_

# Frame missing _definition.id — should warn and skip
save_bad_frame
  _name.category_id        animal
  _name.object_id          bad
save_

# Item frame missing _name.category_id — should warn and skip
save_no_cat
  _definition.id           '_no.cat'
  _definition.class        Attribute
  _name.object_id          cat
save_
"""


@pytest.fixture(scope='module')
def loader():
    warnings = []
    return DictionaryLoader(on_warning=warnings.append), warnings


@pytest.fixture(scope='module')
def dictionary():
    warnings = []
    d = DictionaryLoader(on_warning=warnings.append).load(_NO_IMPORT_CIF)
    return d, warnings


# ─────────────────────────────────────────────────────────────────────────────
# Phase A — no-import parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestDictionaryMetadata:
    def test_name(self, dictionary):
        d, _ = dictionary
        assert d.name == 'TEST_DICT'

    def test_title(self, dictionary):
        d, _ = dictionary
        assert d.title == 'TEST_DICT'

    def test_version(self, dictionary):
        d, _ = dictionary
        assert d.version == '1.0.0'


class TestCategoryExtraction:
    def test_set_category_present(self, dictionary):
        d, _ = dictionary
        assert 'animal' in d.categories

    def test_loop_category_present(self, dictionary):
        d, _ = dictionary
        assert 'limb' in d.categories
        assert 'bone' in d.categories

    def test_head_category_present(self, dictionary):
        d, _ = dictionary
        assert 'test_head' in d.categories

    def test_dictionary_scope_skipped(self, dictionary):
        d, _ = dictionary
        assert 'cif_dict' not in d.categories
        assert 'cif_dict' not in d.items

    def test_category_scope(self, dictionary):
        d, _ = dictionary
        assert d.categories['animal'].scope == 'Category'

    def test_category_class(self, dictionary):
        d, _ = dictionary
        assert d.categories['animal'].definition_class == 'Set'
        assert d.categories['limb'].definition_class == 'Loop'

    def test_single_category_key(self, dictionary):
        d, _ = dictionary
        assert d.categories['limb'].category_keys == ['_limb.id']

    def test_composite_category_keys(self, dictionary):
        d, _ = dictionary
        assert d.categories['bone'].category_keys == ['_bone.limb_id', '_bone.position']

    def test_description(self, dictionary):
        d, _ = dictionary
        assert d.categories['animal'].description == 'A set category for animals.'


class TestItemExtraction:
    def test_item_present(self, dictionary):
        d, _ = dictionary
        assert '_animal.name' in d.items

    def test_category_id_from_name_category_id(self, dictionary):
        # _limb.count has _name.category_id = animal (NOT limb)
        d, _ = dictionary
        item = d.items['_limb.count']
        assert item.category_id == 'animal'
        assert item.object_id == 'limb_count'

    def test_type_fields(self, dictionary):
        d, _ = dictionary
        item = d.items['_animal.name']
        assert item.type_purpose == 'Key'
        assert item.type_source == 'Assigned'
        assert item.type_container == 'Single'
        assert item.type_contents == 'Text'

    def test_units_code(self, dictionary):
        d, _ = dictionary
        assert d.items['_limb.mass'].units_code == 'kg'

    def test_su_linked_item(self, dictionary):
        d, _ = dictionary
        item = d.items['_limb.mass_su']
        assert item.type_purpose == 'SU'
        assert item.linked_item_id == '_limb.mass'

    def test_link_linked_item(self, dictionary):
        d, _ = dictionary
        item = d.items['_bone.limb_id']
        assert item.type_purpose == 'Link'
        assert item.linked_item_id == '_limb.id'

    def test_aliases(self, dictionary):
        d, _ = dictionary
        item = d.items['_limb.mass']
        assert '_limb_mass' in item.aliases
        assert '_old.limb.mass' in item.aliases

    def test_deprecated_with_replacement(self, dictionary):
        d, _ = dictionary
        item = d.items['_animal.legs']
        assert item.is_deprecated is True
        assert '_animal.name' in item.replaced_by

    def test_deprecated_without_replacement(self, dictionary):
        d, _ = dictionary
        item = d.items['_animal.extinct']
        assert item.is_deprecated is True
        assert '' in item.replaced_by  # PLACEHOLDER → empty string


class TestLookupTables:
    def test_tag_to_item_direct(self, dictionary):
        d, _ = dictionary
        assert '_animal.name' in d.tag_to_item
        assert d.tag_to_item['_animal.name'].definition_id == '_animal.name'

    def test_tag_to_item_alias(self, dictionary):
        d, _ = dictionary
        assert '_limb_mass' in d.tag_to_item
        assert d.tag_to_item['_limb_mass'].definition_id == '_limb.mass'

    def test_alias_to_definition_id(self, dictionary):
        d, _ = dictionary
        assert d.alias_to_definition_id['_limb_mass'] == '_limb.mass'
        assert d.alias_to_definition_id['_old.limb.mass'] == '_limb.mass'

    def test_deprecated_ids(self, dictionary):
        d, _ = dictionary
        assert '_animal.legs' in d.deprecated_ids
        assert '_animal.extinct' in d.deprecated_ids
        assert '_animal.name' not in d.deprecated_ids

    def test_tag_to_item_includes_categories(self, dictionary):
        d, _ = dictionary
        assert 'animal' in d.tag_to_item


class TestSkippedFrames:
    def test_missing_definition_id_warns(self, dictionary):
        _, warnings = dictionary
        assert any('missing _definition.id' in w for w in warnings)

    def test_missing_category_id_warns(self, dictionary):
        _, warnings = dictionary
        assert any('missing _name.category_id' in w for w in warnings)

    def test_missing_definition_id_not_in_items(self, dictionary):
        d, _ = dictionary
        # The bad_frame save frame had no _definition.id
        assert not any(
            item.object_id == 'bad' for item in d.items.values()
        )

    def test_missing_category_id_not_in_items(self, dictionary):
        d, _ = dictionary
        assert '_no.cat' not in d.items


class TestAliasCollision:
    def test_collision_warns(self):
        src = """\
#\\#CIF_2.0
data_D
save_A
  _definition.id   '_a.x'
  _definition.class Attribute
  _name.category_id a
  _name.object_id   x
  _alias.definition_id '_b.y'
save_
save_B
  _definition.id   '_b.y'
  _definition.class Attribute
  _name.category_id b
  _name.object_id   y
save_
"""
        warnings = []
        d = DictionaryLoader(on_warning=warnings.append).load(src)
        assert any('collides' in w for w in warnings)

    def test_collision_keeps_first(self):
        src = """\
#\\#CIF_2.0
data_D
save_A
  _definition.id   '_a.x'
  _definition.class Attribute
  _name.category_id a
  _name.object_id   x
  _alias.definition_id '_b.y'
save_
save_B
  _definition.id   '_b.y'
  _definition.class Attribute
  _name.category_id b
  _name.object_id   y
save_
"""
        d = DictionaryLoader().load(src)
        # '_b.y' is the definition_id of item B; alias '_b.y' → '_a.x' would collide.
        # The direct entry for '_b.y' (item B) should win.
        assert d.tag_to_item['_b.y'].definition_id == '_b.y'


# ─────────────────────────────────────────────────────────────────────────────
# Phase B — _import.get resolution
# ─────────────────────────────────────────────────────────────────────────────

# Minimal template dictionary used as import source.
_TEMPLATE_CIF = """\
#\\#CIF_2.0
data_TEMPLATE

save_general_su
  _definition.id           general_su
  _definition.scope        Category
  _definition.class        Set
  _name.category_id        general_su
  _name.object_id          general_su
  _type.purpose            SU
  _type.contents           Real
  _description.text        'General SU template.'
save_

save_general_key
  _definition.id           general_key
  _definition.scope        Category
  _definition.class        Set
  _name.category_id        general_key
  _name.object_id          general_key
  _type.purpose            Key
  _type.contents           Text
save_
"""

# Dictionary that imports from the template.
_IMPORT_CIF = """\
#\\#CIF_2.0
data_IMPORT_TEST

save_WIDGET
  _definition.id           WIDGET
  _definition.scope        Category
  _definition.class        Set
  _name.category_id        WIDGET
  _name.object_id          WIDGET
save_

# Item that imports _type.purpose and _type.contents from general_su template.
save_widget.value_su
  _definition.id           '_widget.value_su'
  _definition.class        Attribute
  _name.category_id        widget
  _name.object_id          value_su
  _name.linked_item_id     '_widget.value'
  _import.get              [{'file':template.cif  'save':general_su}]
save_
"""


def _make_resolver(files: dict[str, str]):
    def resolve(uri: str) -> str | None:
        return files.get(uri)
    return resolve


class TestImportContents:
    def test_imported_attributes_present(self):
        resolver = _make_resolver({'template.cif': _TEMPLATE_CIF})
        d = DictionaryLoader(resolver=resolver).load(_IMPORT_CIF)
        item = d.items.get('_widget.value_su')
        assert item is not None
        assert item.type_purpose == 'SU'
        assert item.type_contents == 'Real'
        assert item.description == 'General SU template.'

    def test_existing_attributes_not_overwritten_by_default(self):
        # _name.linked_item_id is in frame_data before import; dupl default is Exit.
        # But the template doesn't have _name.linked_item_id, so no conflict.
        resolver = _make_resolver({'template.cif': _TEMPLATE_CIF})
        d = DictionaryLoader(resolver=resolver).load(_IMPORT_CIF)
        item = d.items.get('_widget.value_su')
        assert item is not None
        assert item.linked_item_id == '_widget.value'


class TestImportDuplIgnore:
    def test_ignore_keeps_existing(self):
        src = """\
#\\#CIF_2.0
data_D
save_ITEM
  _definition.id           '_item.x'
  _definition.class        Attribute
  _name.category_id        item
  _name.object_id          x
  _type.purpose            Key
  _import.get              [{'file':t.cif  'save':tmpl  'dupl':Ignore}]
save_
"""
        template = """\
#\\#CIF_2.0
data_T
save_tmpl
  _definition.id   tmpl
  _type.purpose    Measurand
  _type.contents   Real
save_
"""
        resolver = _make_resolver({'t.cif': template})
        d = DictionaryLoader(resolver=resolver).load(src)
        item = d.items.get('_item.x')
        assert item.type_purpose == 'Key'  # existing value kept
        assert item.type_contents == 'Real'  # new value inserted (not in frame)


class TestImportDuplReplace:
    def test_replace_overwrites_existing(self):
        src = """\
#\\#CIF_2.0
data_D
save_ITEM
  _definition.id           '_item.x'
  _definition.class        Attribute
  _name.category_id        item
  _name.object_id          x
  _type.purpose            Key
  _import.get              [{'file':t.cif  'save':tmpl  'dupl':Replace}]
save_
"""
        template = """\
#\\#CIF_2.0
data_T
save_tmpl
  _definition.id   tmpl
  _type.purpose    Measurand
save_
"""
        resolver = _make_resolver({'t.cif': template})
        d = DictionaryLoader(resolver=resolver).load(src)
        item = d.items.get('_item.x')
        assert item.type_purpose == 'Measurand'  # replaced


class TestImportDuplExit:
    def test_exit_on_conflict_aborts(self):
        src = """\
#\\#CIF_2.0
data_D
save_ITEM
  _definition.id           '_item.x'
  _definition.class        Attribute
  _name.category_id        item
  _name.object_id          x
  _type.purpose            Key
  _import.get              [{'file':t.cif  'save':tmpl  'dupl':Exit}]
save_
"""
        template = """\
#\\#CIF_2.0
data_T
save_tmpl
  _definition.id   tmpl
  _type.purpose    Measurand
save_
"""
        warnings = []
        resolver = _make_resolver({'t.cif': template})
        d = DictionaryLoader(resolver=resolver, on_warning=warnings.append).load(src)
        assert any('dupl=Exit' in w or 'aborting' in w for w in warnings)


class TestImportMissIgnore:
    def test_miss_ignore_continues(self):
        src = """\
#\\#CIF_2.0
data_D
save_ANIMAL
  _definition.id           ANIMAL
  _definition.scope        Category
  _definition.class        Set
  _name.category_id        ANIMAL
  _name.object_id          ANIMAL
save_
save_widget.x
  _definition.id           '_widget.x'
  _definition.class        Attribute
  _name.category_id        widget
  _name.object_id          x
  _type.purpose            Key
  _import.get              [{'file':missing.cif  'save':tmpl  'miss':Ignore}]
save_
"""
        warnings = []
        d = DictionaryLoader(on_warning=warnings.append).load(src)
        assert any('ignored' in w for w in warnings)
        # Other items still parsed
        assert 'animal' in d.categories


class TestImportMissExit:
    def test_miss_exit_aborts(self):
        src = """\
#\\#CIF_2.0
data_D
save_widget.x
  _definition.id           '_widget.x'
  _definition.class        Attribute
  _name.category_id        widget
  _name.object_id          x
  _import.get              [{'file':missing.cif  'save':tmpl  'miss':Exit}]
save_
save_widget.y
  _definition.id           '_widget.y'
  _definition.class        Attribute
  _name.category_id        widget
  _name.object_id          y
save_
"""
        warnings = []
        d = DictionaryLoader(on_warning=warnings.append).load(src)
        assert any('aborting' in w for w in warnings)


class TestImportModeFull:
    def test_mode_full_skipped_with_warning(self):
        src = """\
#\\#CIF_2.0
data_D
save_widget.x
  _definition.id           '_widget.x'
  _definition.class        Attribute
  _name.category_id        widget
  _name.object_id          x
  _import.get              [{'file':t.cif  'save':tmpl  'mode':Full}]
save_
"""
        template = """\
#\\#CIF_2.0
data_T
save_tmpl
  _definition.id   tmpl
  _type.purpose    Key
save_
"""
        warnings = []
        resolver = _make_resolver({'t.cif': template})
        DictionaryLoader(resolver=resolver, on_warning=warnings.append).load(src)
        assert any('not supported' in w or 'Full' in w for w in warnings)


class TestImportOrdering:
    def test_directives_applied_in_order_field(self):
        # Two directives with explicit order; second should win (Replace).
        src = """\
#\\#CIF_2.0
data_D
save_widget.x
  _definition.id           '_widget.x'
  _definition.class        Attribute
  _name.category_id        widget
  _name.object_id          x
  _import.get              [
    {'file':t2.cif  'save':tmpl2  'dupl':Replace  'order':2}
    {'file':t1.cif  'save':tmpl1  'dupl':Replace  'order':1}
  ]
save_
"""
        t1 = """\
#\\#CIF_2.0
data_T1
save_tmpl1
  _definition.id   tmpl1
  _type.purpose    Key
save_
"""
        t2 = """\
#\\#CIF_2.0
data_T2
save_tmpl2
  _definition.id   tmpl2
  _type.purpose    Measurand
save_
"""
        resolver = _make_resolver({'t1.cif': t1, 't2.cif': t2})
        d = DictionaryLoader(resolver=resolver).load(src)
        item = d.items.get('_widget.x')
        # order=1 (t1) applied first → Key; order=2 (t2) applied second → Measurand
        assert item.type_purpose == 'Measurand'


class TestCaching:
    def test_source_cached(self):
        call_count = [0]
        template = """\
#\\#CIF_2.0
data_T
save_tmpl
  _definition.id   tmpl
  _type.purpose    Key
save_
"""
        def counting_resolver(uri: str) -> str | None:
            call_count[0] += 1
            return template if uri == 't.cif' else None

        src_template = """\
#\\#CIF_2.0
data_D
save_a
  _definition.id           '_a.x'
  _definition.class        Attribute
  _name.category_id        a
  _name.object_id          x
  _import.get              [{'file':t.cif  'save':tmpl}]
save_
save_b
  _definition.id           '_b.y'
  _definition.class        Attribute
  _name.category_id        b
  _name.object_id          y
  _import.get              [{'file':t.cif  'save':tmpl}]
save_
"""
        DictionaryLoader(resolver=counting_resolver).load(src_template)
        # Resolver should be called only once despite two imports from same file.
        assert call_count[0] == 1


class TestDirectoryResolver:
    def test_resolves_by_filename(self):
        resolver = directory_resolver(DATA_DIR)
        src = resolver('templ_attr.cif')
        assert src is not None
        assert '#\\#CIF_2.0' in src or '##CIF_2.0' in src or 'CIF_2.0' in src

    def test_returns_none_for_missing(self):
        resolver = directory_resolver(DATA_DIR)
        assert resolver('nonexistent_file_xyz.cif') is None

    def test_uses_last_uri_component(self):
        resolver = directory_resolver(DATA_DIR)
        src = resolver('https://example.com/dicts/templ_attr.cif')
        assert src is not None


# ─────────────────────────────────────────────────────────────────────────────
# Slow integration test — real cif_core.dic
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.slow
class TestRealDictionary:
    @pytest.fixture(scope='class')
    def cif_core(self):
        resolver = directory_resolver(DATA_DIR)
        src = (DATA_DIR / 'cif_core.dic').read_text(encoding='utf-8')
        warnings = []
        d = DictionaryLoader(
            resolver=resolver,
            on_warning=warnings.append,
        ).load(src, base_uri='cif_core.dic')
        return d, warnings

    def test_no_exceptions(self, cif_core):
        pass  # fixture would have raised

    def test_name(self, cif_core):
        d, _ = cif_core
        assert d.name == 'CIF_CORE'

    def test_items_populated(self, cif_core):
        d, _ = cif_core
        assert len(d.items) > 100

    def test_imported_type_attributes_present(self, cif_core):
        # Frames with _import.get should have _type.* populated from templates.
        d, _ = cif_core
        item = d.tag_to_item.get('_diffrn.ambient_pressure_su')
        assert item is not None
        assert item.type_purpose is not None

    def test_aliases_resolve(self, cif_core):
        d, _ = cif_core
        assert len(d.alias_to_definition_id) > 0
        # Pick any alias and confirm it resolves to a current definition.
        alias = next(iter(d.alias_to_definition_id))
        current = d.alias_to_definition_id[alias]
        assert current in d.tag_to_item

    def test_deprecated_ids_non_empty(self, cif_core):
        d, _ = cif_core
        assert len(d.deprecated_ids) > 0
