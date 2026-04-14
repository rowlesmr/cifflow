"""Tests for resolver.py — resolve_tag."""

from pycifparse.dictionary.ddlm_item import DdlmItem
from pycifparse.dictionary.ddlm_parser import DdlmDictionary
from pycifparse.dictionary.resolver import ResolvedTag, resolve_tag


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dict() -> DdlmDictionary:
    """
    Return a small dictionary with:
    - _atom_site.fract_x   (current, non-deprecated)
    - _atom_site.fract_y   (deprecated, replaced by _atom_site.fract_x)
    - _atom_site.label     (alias: _atom.label)
    - atom_site            (Category item — no object_id)
    """
    def _item(definition_id, category_id, object_id, *, aliases=None,
              is_deprecated=False):
        return DdlmItem(
            definition_id=definition_id, scope='Item',
            definition_class='Datum', category_id=category_id,
            object_id=object_id, type_purpose=None, type_source=None,
            type_container='Single', type_contents=None,
            linked_item_id=None, units_code=None, description=None,
            aliases=aliases or [], is_deprecated=is_deprecated,
        )

    fract_x = _item('_atom_site.fract_x', 'atom_site', 'fract_x')
    fract_y = _item('_atom_site.fract_y', 'atom_site', 'fract_y',
                    is_deprecated=True)
    label = _item('_atom_site.label', 'atom_site', 'label',
                  aliases=['_atom.label'])
    cat = DdlmItem(
        definition_id='atom_site', scope='Category',
        definition_class='Loop', category_id='atom_site', object_id=None,
        type_purpose=None, type_source=None, type_container='Single',
        type_contents=None, linked_item_id=None, units_code=None,
        description=None,
    )

    tag_to_item = {
        '_atom_site.fract_x': fract_x,
        '_atom_site.fract_y': fract_y,
        '_atom_site.label': label,
        '_atom.label': label,   # alias
        'atom_site': cat,
    }
    alias_to_def = {'_atom.label': '_atom_site.label'}

    return DdlmDictionary(
        name='TEST', title=None, version=None,
        categories={'atom_site': cat},
        items={
            '_atom_site.fract_x': fract_x,
            '_atom_site.fract_y': fract_y,
            '_atom_site.label': label,
        },
        tag_to_item=tag_to_item,
        alias_to_definition_id=alias_to_def,
        deprecated_ids={'_atom_site.fract_y'},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResolveTagCurrentTag:
    def test_returns_resolved_tag(self):
        d = _make_dict()
        r = resolve_tag('_atom_site.fract_x', d)
        assert isinstance(r, ResolvedTag)

    def test_definition_id(self):
        d = _make_dict()
        r = resolve_tag('_atom_site.fract_x', d)
        assert r.definition_id == '_atom_site.fract_x'

    def test_category_id(self):
        d = _make_dict()
        r = resolve_tag('_atom_site.fract_x', d)
        assert r.category_id == 'atom_site'

    def test_object_id(self):
        d = _make_dict()
        r = resolve_tag('_atom_site.fract_x', d)
        assert r.object_id == 'fract_x'

    def test_was_alias_false(self):
        d = _make_dict()
        r = resolve_tag('_atom_site.fract_x', d)
        assert r.was_alias is False

    def test_is_deprecated_false(self):
        d = _make_dict()
        r = resolve_tag('_atom_site.fract_x', d)
        assert r.is_deprecated is False


class TestResolveTagAlias:
    def test_resolves_alias_to_current_definition_id(self):
        d = _make_dict()
        r = resolve_tag('_atom.label', d)
        assert r.definition_id == '_atom_site.label'

    def test_was_alias_true(self):
        d = _make_dict()
        r = resolve_tag('_atom.label', d)
        assert r.was_alias is True

    def test_alias_gives_correct_category_and_column(self):
        d = _make_dict()
        r = resolve_tag('_atom.label', d)
        assert r.category_id == 'atom_site'
        assert r.object_id == 'label'


class TestResolveTagDeprecated:
    def test_deprecated_tag_returns_result(self):
        d = _make_dict()
        r = resolve_tag('_atom_site.fract_y', d)
        assert r is not None

    def test_is_deprecated_true(self):
        d = _make_dict()
        r = resolve_tag('_atom_site.fract_y', d)
        assert r.is_deprecated is True

    def test_deprecated_was_alias_false(self):
        d = _make_dict()
        r = resolve_tag('_atom_site.fract_y', d)
        assert r.was_alias is False


class TestResolveTagUnknown:
    def test_unknown_tag_returns_none(self):
        d = _make_dict()
        assert resolve_tag('_nonexistent.tag', d) is None

    def test_empty_string_returns_none(self):
        d = _make_dict()
        assert resolve_tag('', d) is None


class TestResolveTagCaseInsensitive:
    def test_uppercase_resolves(self):
        d = _make_dict()
        r = resolve_tag('_ATOM_SITE.FRACT_X', d)
        assert r is not None
        assert r.definition_id == '_atom_site.fract_x'

    def test_mixed_case_resolves(self):
        d = _make_dict()
        r = resolve_tag('_Atom_Site.Fract_X', d)
        assert r is not None
        assert r.object_id == 'fract_x'

    def test_alias_uppercase_resolves(self):
        d = _make_dict()
        r = resolve_tag('_ATOM.LABEL', d)
        assert r is not None
        assert r.was_alias is True


class TestResolveTagEdgeCases:
    def test_category_item_returns_none(self):
        """Category item has object_id=None → return None at line 77."""
        d = _make_dict()
        # 'atom_site' is the category entry (object_id=None)
        assert resolve_tag('atom_site', d) is None

    def test_alias_to_deprecated_item(self):
        """was_alias=True and is_deprecated=True combined."""
        deprecated = DdlmItem(
            definition_id='_atom_site.fract_y', scope='Item',
            definition_class='Datum', category_id='atom_site',
            object_id='fract_y', type_purpose=None, type_source=None,
            type_container='Single', type_contents=None,
            linked_item_id=None, units_code=None, description=None,
            is_deprecated=True,
        )
        d = DdlmDictionary(
            name='T', title=None, version=None,
            categories={},
            items={'_atom_site.fract_y': deprecated},
            tag_to_item={
                '_atom_site.fract_y': deprecated,
                '_old.fract_y': deprecated,  # alias
            },
            alias_to_definition_id={'_old.fract_y': '_atom_site.fract_y'},
            deprecated_ids={'_atom_site.fract_y'},
        )
        r = resolve_tag('_old.fract_y', d)
        assert r is not None
        assert r.was_alias is True
        assert r.is_deprecated is True
