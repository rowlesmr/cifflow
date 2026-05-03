"""Tests for CifScalar."""

import pytest

from cifflow.cifmodel.scalar import CifScalar
from cifflow.types import ValueType


class TestCifScalarConstruction:
    def test_stores_value(self):
        s = CifScalar('hello', ValueType.STRING)
        assert str(s) == 'hello'

    def test_stores_value_type(self):
        s = CifScalar('hello', ValueType.STRING)
        assert s.value_type == ValueType.STRING

    def test_default_value_type_is_string(self):
        s = CifScalar('hello')
        assert s.value_type == ValueType.STRING

    def test_placeholder_value_type(self):
        s = CifScalar('.', ValueType.PLACEHOLDER)
        assert s.value_type == ValueType.PLACEHOLDER

    def test_double_quoted_value_type(self):
        s = CifScalar('.', ValueType.DOUBLE_QUOTED)
        assert s.value_type == ValueType.DOUBLE_QUOTED

    def test_multiline_value_type(self):
        s = CifScalar('line1\nline2', ValueType.MULTILINE_STRING)
        assert s.value_type == ValueType.MULTILINE_STRING


class TestCifScalarStrBehaviour:
    def test_equals_plain_str(self):
        s = CifScalar('hello', ValueType.STRING)
        assert s == 'hello'

    def test_isinstance_str(self):
        s = CifScalar('hello', ValueType.STRING)
        assert isinstance(s, str)

    def test_string_operations(self):
        s = CifScalar('Hello', ValueType.STRING)
        assert s.lower() == 'hello'
        assert s + ' world' == 'Hello world'
        assert s[0] == 'H'

    def test_in_check(self):
        s = CifScalar('.', ValueType.PLACEHOLDER)
        assert s in ('.', '?')

    def test_hash_matches_str(self):
        s = CifScalar('abc', ValueType.STRING)
        assert hash(s) == hash('abc')

    def test_usable_as_dict_key(self):
        s = CifScalar('key', ValueType.STRING)
        d = {s: 'value'}
        assert d['key'] == 'value'


class TestCifScalarBuilderIntegration:
    def test_scalar_tag_value_is_str(self):
        from cifflow.cifmodel.builder import build
        cif, _ = build("#\\#CIF_2.0\ndata_test\n_tag value\n")
        v = cif['test']['_tag'][0]
        assert isinstance(v, str)
        assert v == 'value'

    def test_placeholder_dot_stored_as_dot(self):
        from cifflow.cifmodel.builder import build
        cif, _ = build("#\\#CIF_2.0\ndata_test\n_tag .\n")
        v = cif['test']['_tag'][0]
        assert v == '.'

    def test_placeholder_question_stored_as_question(self):
        from cifflow.cifmodel.builder import build
        cif, _ = build("#\\#CIF_2.0\ndata_test\n_tag ?\n")
        v = cif['test']['_tag'][0]
        assert v == '?'

    def test_quoted_dot_stored_as_sentinel(self):
        from cifflow.cifmodel.builder import build
        cif, _ = build('#\\#CIF_2.0\ndata_test\n_tag "."\n')
        v = cif['test']['_tag'][0]
        assert v == '"."'

    def test_quoted_question_stored_as_sentinel(self):
        from cifflow.cifmodel.builder import build
        cif, _ = build('#\\#CIF_2.0\ndata_test\n_tag "?"\n')
        v = cif['test']['_tag'][0]
        assert v == '"?"'

    def test_multiline_value_is_str(self):
        from cifflow.cifmodel.builder import build
        cif, _ = build("#\\#CIF_2.0\ndata_test\n_tag\n;\nhello\n;\n")
        v = cif['test']['_tag'][0]
        assert isinstance(v, str)
        assert 'hello' in v

    def test_loop_values_are_str(self):
        from cifflow.cifmodel.builder import build
        cif, _ = build(
            "#\\#CIF_2.0\ndata_test\nloop_\n_a _b\n1 2\n3 4\n"
        )
        v = cif['test']['_a'][0]
        assert isinstance(v, str)
        assert v == '1'

    def test_pad_placeholder_is_question_mark(self):
        from cifflow.cifmodel.builder import build
        # Loop with 2 tags but 3 values — last row padded with ?
        cif, _ = build(
            "#\\#CIF_2.0\ndata_test\nloop_\n_a _b\n1 2\n3\n",
            mode='pad',
        )
        v = cif['test']['_b'][-1]
        assert v == '?'
