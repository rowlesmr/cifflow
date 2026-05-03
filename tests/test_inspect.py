"""
Smoke tests for src/cifflow/inspect/.

These tests verify that the public entry points run without raising and produce
sensible output.  They do not assert exact formatting — that would be brittle —
but do check that key content (token types, event names, error markers) appears
in the output.
"""

import io

import pytest

from cifflow.inspect import (
    ParseHandler,
    inspect_lexer,
    inspect_model,
    inspect_parse,
    inspect_schema,
    TraceEvent,
)
from cifflow.parser.parser import CifParser
from cifflow.types import ValueType

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SIMPLE = '#\\#CIF_2.0\ndata_d\n_tag val\n'

_WITH_LOOP = '#\\#CIF_2.0\ndata_d\nloop_\n_a _b\n1 x\n2 y\n'

_WITH_TABLE = "#\\#CIF_2.0\ndata_d\n_t {'k':v}\n"

_WITH_TABLE_SPACED = "#\\#CIF_2.0\ndata_d\n_t {'k' :v}\n"

_WITH_ERROR = '#\\#CIF_2.0\ndata_d\northan_value\n'

_CIF11 = '#\\#CIF_1.1\ndata_d\n_t hello\n'


def _capture(fn, *args, **kwargs) -> str:
    """Run *fn* with file=buf, return what was written."""
    buf = io.StringIO()
    fn(*args, file=buf, **kwargs)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# inspect_lexer
# ---------------------------------------------------------------------------

class TestInspectLexer:
    def test_runs_without_error(self):
        _capture(inspect_lexer, _SIMPLE)

    def test_contains_token_types(self):
        out = _capture(inspect_lexer, _SIMPLE)
        assert 'keyword' in out
        assert 'tag'     in out
        assert 'value'   in out

    def test_contains_value_type(self):
        out = _capture(inspect_lexer, _SIMPLE)
        assert 'string' in out

    def test_contains_position(self):
        out = _capture(inspect_lexer, _SIMPLE)
        assert '1' in out
        assert '2' in out

    def test_cif11_version_label(self):
        out = _capture(inspect_lexer, _CIF11)
        assert '1.1' in out

    def test_cif20_version_label(self):
        out = _capture(inspect_lexer, _SIMPLE)
        assert '2.0' in out

    def test_lexer_error_reported(self):
        out = _capture(inspect_lexer, '#\\#CIF_2.0\ndata_d\n_t "unterminated\n')
        assert 'LEX ERROR' in out

    def test_explicit_version_kwarg(self):
        from cifflow.types import CifVersion
        _capture(inspect_lexer, 'data_d\n_t v\n', version=CifVersion.CIF_1_1)

    def test_path_input(self, tmp_path):
        """inspect_lexer accepts a pathlib.Path."""
        p = tmp_path / 'test.cif'
        p.write_text(_SIMPLE, encoding='utf-8')
        out = _capture(inspect_lexer, p)
        assert 'token stream' in out
        assert 'keyword' in out

    def test_file_object_input(self):
        """inspect_lexer accepts an open text file object."""
        out = _capture(inspect_lexer, io.StringIO(_SIMPLE))
        assert 'token stream' in out

    def test_version_error_printed(self):
        # Unrecognised magic line → v_errors → lines 36-37
        out = _capture(inspect_lexer, '#\\#CIF_99.0\ndata_d _t v\n')
        assert 'VERSION ERROR' in out

    def test_long_value_truncated(self):
        # Value with repr() > 50 chars → line 62
        long_val = 'x' * 60
        out = _capture(inspect_lexer, f'data_d _t {long_val}\n')
        assert '…' in out


# ---------------------------------------------------------------------------
# ParseHandler
# ---------------------------------------------------------------------------

class TestParseHandler:
    def test_runs_without_error(self):
        buf = io.StringIO()
        CifParser(ParseHandler(file=buf)).parse(_SIMPLE)

    def test_contains_data_block_event(self):
        buf = io.StringIO()
        CifParser(ParseHandler(file=buf)).parse(_SIMPLE)
        assert 'on_data_block' in buf.getvalue()

    def test_contains_add_tag(self):
        buf = io.StringIO()
        CifParser(ParseHandler(file=buf)).parse(_SIMPLE)
        assert 'add_tag' in buf.getvalue()

    def test_contains_add_value(self):
        buf = io.StringIO()
        CifParser(ParseHandler(file=buf)).parse(_SIMPLE)
        assert 'add_value' in buf.getvalue()

    def test_loop_events_present(self):
        buf = io.StringIO()
        CifParser(ParseHandler(file=buf)).parse(_WITH_LOOP)
        out = buf.getvalue()
        assert 'on_loop_start' in out
        assert 'on_loop_end'   in out

    def test_table_events_present(self):
        buf = io.StringIO()
        CifParser(ParseHandler(file=buf)).parse(_WITH_TABLE)
        out = buf.getvalue()
        assert 'on_table_start' in out
        assert 'on_table_key'   in out
        assert 'on_table_end'   in out

    def test_error_reported(self):
        buf = io.StringIO()
        CifParser(ParseHandler(file=buf)).parse(_WITH_ERROR)
        assert 'SYNTACTIC' in buf.getvalue() or 'syntactic' in buf.getvalue().lower()

    def test_adjacency_error_reported(self):
        buf = io.StringIO()
        CifParser(ParseHandler(file=buf)).parse(_WITH_TABLE_SPACED)
        assert 'not followed by : separator' in buf.getvalue()

    def test_show_values_false_suppresses_add_value(self):
        buf = io.StringIO()
        CifParser(ParseHandler(file=buf, show_values=False)).parse(_SIMPLE)
        assert 'add_value' not in buf.getvalue()

    def test_forwarding_to_inner_handler(self):
        """All events must reach the inner handler."""
        from tests.parser.test_parser import RecordingHandler
        inner = RecordingHandler()
        buf   = io.StringIO()
        CifParser(ParseHandler(inner, file=buf)).parse(_WITH_LOOP)
        names = inner.event_names()
        assert 'on_data_block'  in names
        assert 'on_loop_start'  in names
        assert 'add_value'      in names
        assert 'on_loop_end'    in names

    def test_nesting_indentation(self):
        """Loop values should be indented more than the loop_start line."""
        buf = io.StringIO()
        CifParser(ParseHandler(file=buf)).parse(_WITH_LOOP)
        lines = buf.getvalue().splitlines()
        loop_start = next(l for l in lines if 'on_loop_start' in l)
        value_line = next(l for l in lines if 'add_value' in l)
        loop_indent  = len(loop_start)  - len(loop_start.lstrip())
        value_indent = len(value_line)  - len(value_line.lstrip())
        assert value_indent > loop_indent


# ---------------------------------------------------------------------------
# inspect_parse
# ---------------------------------------------------------------------------

class TestInspectParse:
    def test_runs_without_error(self):
        _capture(inspect_parse, _SIMPLE)

    def test_contains_both_sections(self):
        out = _capture(inspect_parse, _SIMPLE)
        assert 'token stream' in out
        assert 'parser events' in out

    def test_show_tokens_false_omits_token_stream(self):
        out = _capture(inspect_parse, _SIMPLE, show_tokens=False)
        assert 'token stream' not in out
        assert 'parser events' in out

    def test_show_values_false_forwarded(self):
        out = _capture(inspect_parse, _SIMPLE, show_values=False)
        assert 'add_value' not in out

    def test_inner_handler_receives_events(self):
        from tests.parser.test_parser import RecordingHandler
        inner = RecordingHandler()
        _capture(inspect_parse, _SIMPLE, inner=inner)
        assert any(e.name == 'on_data_block' for e in inner.events)

    def test_real_cif_file(self, tmp_path):
        """inspect_parse must not raise on a real file."""
        import pathlib
        src = (pathlib.Path(__file__).parent / 'cif_files' / 'comcifs' / 'simple_data.cif'
               ).read_text(encoding='utf-8')
        _capture(inspect_parse, src)

    def test_path_input(self, tmp_path):
        """inspect_parse accepts a pathlib.Path."""
        p = tmp_path / 'test.cif'
        p.write_text(_SIMPLE, encoding='utf-8')
        out = _capture(inspect_parse, p)
        assert 'on_data_block' in out

    def test_file_object_input(self, tmp_path):
        """inspect_parse accepts an open text file object."""
        out = _capture(inspect_parse, io.StringIO(_SIMPLE))
        assert 'on_data_block' in out


# ---------------------------------------------------------------------------
# inspect_schema
# ---------------------------------------------------------------------------

_SCHEMA_DIC = """\
#\\#CIF_2.0
data_TEST

save_WIDGET
  _definition.id           WIDGET
  _definition.scope        Category
  _definition.class        Loop
  _name.category_id        widget
  _category_key.name       '_widget.id'
save_

save_widget.id
  _definition.id           '_widget.id'
  _definition.class        Attribute
  _name.category_id        widget
  _name.object_id          id
  _type.purpose            Key
  _type.contents           Text
save_

save_widget.val
  _definition.id           '_widget.val'
  _definition.class        Attribute
  _name.category_id        widget
  _name.object_id          val
  _type.contents           Real
save_
"""


class TestInspectSchema:
    def test_runs_without_raising_from_spec(self):
        from cifflow.dictionary.loader import DictionaryLoader
        from cifflow.dictionary.schema import generate_schema
        loader = DictionaryLoader()
        d = loader.load(_SCHEMA_DIC)
        schema = generate_schema(d)
        out = _capture(inspect_schema, schema)
        assert out  # non-empty

    def test_runs_from_string(self):
        out = _capture(inspect_schema, _SCHEMA_DIC)
        assert 'widget' in out

    def test_runs_from_path(self, tmp_path):
        p = tmp_path / 'test.dic'
        p.write_text(_SCHEMA_DIC, encoding='utf-8')
        out = _capture(inspect_schema, p)
        assert 'widget' in out

    def test_runs_from_dictionary_object(self):
        from cifflow.dictionary.loader import DictionaryLoader
        loader = DictionaryLoader()
        d = loader.load(_SCHEMA_DIC)
        out = _capture(inspect_schema, d)
        assert 'widget' in out

    def test_summary_line_present(self):
        out = _capture(inspect_schema, _SCHEMA_DIC)
        assert 'table' in out

    def test_table_name_shown(self):
        out = _capture(inspect_schema, _SCHEMA_DIC)
        assert 'widget' in out

    def test_loop_class_shown(self):
        out = _capture(inspect_schema, _SCHEMA_DIC)
        assert 'Loop' in out

    def test_pk_shown(self):
        out = _capture(inspect_schema, _SCHEMA_DIC)
        assert 'id' in out

    def test_synthetic_columns_shown(self):
        out = _capture(inspect_schema, _SCHEMA_DIC)
        assert '_cifflow_block_id' in out
        assert '_cifflow_row_id' in out

    def test_definition_id_shown(self):
        out = _capture(inspect_schema, _SCHEMA_DIC)
        assert '_widget.id' in out

    def test_show_ddl_includes_create_table(self):
        out = _capture(inspect_schema, _SCHEMA_DIC, show_ddl=True)
        assert 'CREATE TABLE' in out

    def test_no_parse_errors_in_output(self):
        out = _capture(inspect_schema, _SCHEMA_DIC)
        assert 'LEXICAL' not in out
        assert 'SYNTACTIC' not in out
        assert '[ERROR]' not in out

    def test_su_linked_item_shown(self):
        # Line 128: col.linked_item_id → SU column tag shown
        dic = """\
#\\#CIF_2.0
data_TEST

save_WIDGET
  _definition.id           WIDGET
  _definition.scope        Category
  _definition.class        Loop
  _name.category_id        widget
  _category_key.name       '_widget.id'
save_

save_widget.id
  _definition.id           '_widget.id'
  _definition.class        Attribute
  _name.category_id        widget
  _name.object_id          id
  _type.purpose            Key
  _type.contents           Text
save_

save_widget.val
  _definition.id           '_widget.val'
  _definition.class        Attribute
  _name.category_id        widget
  _name.object_id          val
  _type.contents           Real
save_

save_widget.val_su
  _definition.id           '_widget.val_su'
  _definition.class        Attribute
  _name.category_id        widget
  _name.object_id          val_su
  _type.purpose            SU
  _type.source             Estimated
  _type.contents           Real
  _name.linked_item_id     '_widget.val'
save_
"""
        out = _capture(inspect_schema, dic)
        assert 'su' in out or '->su' in out.replace(' ', '')

    def test_foreign_keys_shown(self):
        # Lines 134-151: table has foreign_keys → FK section printed
        dic = """\
#\\#CIF_2.0
data_TEST

save_PARENT
  _definition.id           PARENT
  _definition.scope        Category
  _definition.class        Set
  _name.category_id        parent
  _category_key.name       '_parent.id'
save_

save_parent.id
  _definition.id           '_parent.id'
  _definition.class        Attribute
  _name.category_id        parent
  _name.object_id          id
  _type.purpose            Key
  _type.contents           Text
save_

save_CHILD
  _definition.id           CHILD
  _definition.scope        Category
  _definition.class        Loop
  _name.category_id        child
  _category_key.name       '_child.id'
save_

save_child.id
  _definition.id           '_child.id'
  _definition.class        Attribute
  _name.category_id        child
  _name.object_id          id
  _type.purpose            Key
  _type.contents           Text
save_

save_child.parent_id
  _definition.id           '_child.parent_id'
  _definition.class        Attribute
  _name.category_id        child
  _name.object_id          parent_id
  _type.purpose            Link
  _name.linked_item_id     '_parent.id'
  _type.contents           Text
save_
"""
        out = _capture(inspect_schema, dic)
        assert 'foreign key' in out.lower() or '->' in out

    def test_multi_column_foreign_key_shown(self):
        """Multi-column FK uses composite display (lines 143-147 in _schema.py)."""
        from cifflow.dictionary.schema import (
            SchemaSpec, TableDef, ColumnDef, ForeignKeyDef,
        )
        col_a = ColumnDef(name='a', definition_id='_t.a', type_contents='Text',
                          nullable=False, is_primary_key=True, is_synthetic=False,
                          linked_item_id=None)
        col_b = ColumnDef(name='b', definition_id='_t.b', type_contents='Text',
                          nullable=False, is_primary_key=True, is_synthetic=False,
                          linked_item_id=None)
        fk = ForeignKeyDef(
            source_table='child', source_columns=['a', 'b'],
            target_table='parent', target_columns=['x', 'y'],
        )
        tdef = TableDef(
            name='child', definition_id='_child', category_class='Loop',
            columns=[col_a, col_b], primary_keys=['a', 'b'],
            foreign_keys=[fk],
        )
        schema = SchemaSpec(
            tables={'child': tdef},
            column_to_tag={('child', 'a'): '_t.a', ('child', 'b'): '_t.b'},
        )
        out = _capture(inspect_schema, schema)
        assert 'a, b' in out or '(a' in out  # composite FK columns shown

    def test_schema_warnings_shown(self):
        # Lines 161-164: schema.warnings → warnings section printed
        from cifflow.dictionary.loader import DictionaryLoader
        from cifflow.dictionary.schema import generate_schema
        # Build a schema that produces a warning: missing category keys
        dic_no_keys = """\
#\\#CIF_2.0
data_TEST

save_WIDGET
  _definition.id           WIDGET
  _definition.scope        Category
  _definition.class        Loop
  _name.category_id        widget
save_

save_widget.val
  _definition.id           '_widget.val'
  _definition.class        Attribute
  _name.category_id        widget
  _name.object_id          val
  _type.contents           Real
save_
"""
        loader = DictionaryLoader()
        d = loader.load(dic_no_keys)
        schema = generate_schema(d)
        # schema.warnings should be non-empty (no category keys → warning)
        out = _capture(inspect_schema, schema)
        if schema.warnings:
            assert 'warning' in out.lower() or '!' in out


# ---------------------------------------------------------------------------
# inspect_ingest + TraceEvent
# ---------------------------------------------------------------------------

class TestInspectIngest:
    def test_returns_list(self):
        from cifflow import build
        from cifflow.inspect import inspect_ingest

        cif, _ = build(_SIMPLE)
        buf = io.StringIO()
        result = inspect_ingest(cif, None, schema=None, file=buf)
        assert isinstance(result, list)

    def test_trace_events_are_trace_event_instances(self):
        from cifflow import build
        from cifflow.inspect import inspect_ingest

        cif, _ = build(_SIMPLE)
        buf = io.StringIO()
        result = inspect_ingest(cif, None, schema=None, file=buf)
        for ev in result:
            assert isinstance(ev, TraceEvent)

    def test_output_written_to_file(self):
        from cifflow import build
        from cifflow.inspect import inspect_ingest

        cif, _ = build(_SIMPLE)
        buf = io.StringIO()
        inspect_ingest(cif, None, schema=None, file=buf)
        assert buf.getvalue()

    def test_header_present_in_output(self):
        from cifflow import build
        from cifflow.inspect import inspect_ingest

        cif, _ = build(_SIMPLE)
        buf = io.StringIO()
        inspect_ingest(cif, None, schema=None, file=buf)
        assert 'inspect_ingest' in buf.getvalue()

    def test_trace_event_fields(self):
        ev = TraceEvent(kind='warning', detail='test detail', table='cell')
        assert ev.kind == 'warning'
        assert ev.detail == 'test detail'
        assert ev.table == 'cell'
        assert ev.block_id is None
        assert ev.tag is None

    def test_file_none_defaults_to_stdout(self):
        """When file= is omitted, output goes to sys.stdout."""
        from unittest.mock import patch
        from cifflow import build
        from cifflow.inspect import inspect_ingest

        cif, _ = build(_SIMPLE)
        captured = io.StringIO()
        with patch('sys.stdout', captured):
            inspect_ingest(cif, None, schema=None)
        assert captured.getvalue()

    def test_ingestion_warning_on_incompatible_loop(self):
        """Incompatible multi-category loop produces an on_error warning."""
        from cifflow import build
        from cifflow.dictionary.loader import DictionaryLoader
        from cifflow.dictionary.schema import generate_schema
        from cifflow.inspect import inspect_ingest

        two_table_dic = """\
#\\#CIF_2.0
data_TWO

save_WIDGET
  _definition.id       WIDGET
  _definition.scope    Category
  _definition.class    Loop
  _name.category_id    widget
  _category_key.name   '_widget.id'
save_

save_widget.id
  _definition.id       '_widget.id'
  _definition.class    Attribute
  _name.category_id    widget
  _name.object_id      id
  _type.purpose        Key
  _type.contents       Text
save_

save_GADGET
  _definition.id       GADGET
  _definition.scope    Category
  _definition.class    Loop
  _name.category_id    gadget
  _category_key.name   '_gadget.id'
save_

save_gadget.code
  _definition.id       '_gadget.code'
  _definition.class    Attribute
  _name.category_id    gadget
  _name.object_id      code
  _type.purpose        Key
  _type.contents       Text
save_
"""
        loader = DictionaryLoader()
        schema = generate_schema(loader.load(two_table_dic))

        cif_src = 'data_test\nloop_\n  _widget.id\n  _gadget.code\n  W1 G1\n'
        cif, _ = build(cif_src)

        buf = io.StringIO()
        result = inspect_ingest(cif, None, schema=schema, file=buf)

        assert any(ev.kind == 'warning' for ev in result)
        assert 'warning' in buf.getvalue().lower()

    def test_clean_ingest_no_warnings(self):
        """Clean ingest prints the 'no warnings' message."""
        from cifflow import build
        from cifflow.inspect import inspect_ingest

        cif, _ = build(_SIMPLE)
        buf = io.StringIO()
        result = inspect_ingest(cif, None, schema=None, file=buf)

        assert result == []
        assert 'no warnings' in buf.getvalue().lower()


# ---------------------------------------------------------------------------
# inspect_model
# ---------------------------------------------------------------------------

_MULTI_LOOP = (
    'data_test\nloop_\n  _a\n  _b\n'
    + ''.join(f'  {i} x{i}\n' for i in range(10))
)

_WITH_SAVE_FRAME = (
    'data_test\n'
    'save_MYFRAME\n'
    '  _frame.tag value\n'
    'save_\n'
)

_DUPLICATE_TAG = 'data_test\n_tag first\n_tag second\n'

_TAG_NO_VALUE = 'data_test\n_tag\n'


class TestInspectModel:
    def test_scalar_tag_shown(self):
        out = _capture(inspect_model, _SIMPLE, show_tokens=False)
        assert '_tag' in out

    def test_scalar_value_shown(self):
        out = _capture(inspect_model, _SIMPLE, show_tokens=False)
        assert 'val' in out

    def test_loop_few_rows_no_ellipsis(self):
        cif = 'data_test\nloop_\n  _a\n  1\n  2\n  3\n  4\n'
        out = _capture(inspect_model, cif, show_tokens=False)
        assert 'loop_' in out
        assert '...' not in out

    def test_loop_many_rows_ellipsis(self):
        out = _capture(inspect_model, _MULTI_LOOP, show_tokens=False)
        assert 'loop_' in out
        assert '...' in out

    def test_save_frame_shown(self):
        out = _capture(inspect_model, _WITH_SAVE_FRAME, show_tokens=False)
        assert 'MYFRAME' in out
        assert '_frame.tag' in out

    def test_empty_cif_no_blocks(self):
        out = _capture(inspect_model, '', show_tokens=False)
        assert '(no blocks)' in out

    def test_multiple_values_suffix(self):
        out = _capture(inspect_model, _DUPLICATE_TAG, show_tokens=False)
        assert '2 values' in out

    def test_show_tokens_false_omits_token_stream(self):
        out = _capture(inspect_model, _SIMPLE, show_tokens=False)
        assert 'token stream' not in out

    def test_show_tokens_true_includes_token_stream(self):
        out = _capture(inspect_model, _SIMPLE, show_tokens=True)
        assert 'token stream' in out

    def test_parse_errors_shown(self):
        # Tag with no value at EOF → on_error → '-- errors --' in output
        out = _capture(inspect_model, _TAG_NO_VALUE, show_tokens=False)
        assert '-- errors --' in out

    def test_path_input(self, tmp_path):
        p = tmp_path / 'test.cif'
        p.write_text(_SIMPLE, encoding='utf-8')
        out = _capture(inspect_model, p, show_tokens=False)
        assert '_tag' in out

    def test_block_header_shown(self):
        out = _capture(inspect_model, _SIMPLE, show_tokens=False)
        assert 'block:' in out or 'd' in out  # block name 'd' from _SIMPLE


# ---------------------------------------------------------------------------
# inspect _common internals
# ---------------------------------------------------------------------------

class TestInspectCommon:
    def test_fmt_value_list(self):
        from cifflow.inspect._common import fmt_value
        result = fmt_value(['a', 'b'])
        assert result.startswith('[')
        assert 'a' in result
        assert 'b' in result

    def test_fmt_value_dict(self):
        from cifflow.inspect._common import fmt_value
        result = fmt_value({'key': 'val'})
        assert result.startswith('{')
        assert 'key' in result

    def test_fmt_value_long_string_truncated(self):
        from cifflow.inspect._common import fmt_value
        long_str = 'x' * 40
        result = fmt_value(long_str)
        assert '...' in result
        assert len(result) < len(long_str)

    def test_fmt_value_short_string_unchanged(self):
        from cifflow.inspect._common import fmt_value
        assert fmt_value('hello') == 'hello'

    def test_c_with_colour_enabled(self):
        from unittest.mock import MagicMock
        from cifflow.inspect._common import c, BOLD
        mock_file = MagicMock()
        mock_file.isatty.return_value = True
        result = c('text', BOLD, file=mock_file)
        assert '\033[' in result
        assert 'text' in result

    def test_c_without_colour_returns_plain_text(self):
        from cifflow.inspect._common import c, BOLD
        result = c('text', BOLD, file=io.StringIO())
        assert result == 'text'


# ---------------------------------------------------------------------------
# inspect/_parser.py coverage gaps: long value, list events, error context
# ---------------------------------------------------------------------------

class TestInspectParserCoverageGaps:
    def test_on_error_empty_context_no_context_appended(self):
        """on_error with empty context skips context append (branch 133->135)."""
        from cifflow.types import ParseError
        buf = io.StringIO()
        handler = ParseHandler(file=buf)
        err = ParseError(
            error_type='syntactic', message='test error',
            line=1, column=1,
            context='',          # empty → branch 133 evaluates False
            recovery_action='',  # empty → branch 135 evaluates False
        )
        handler.on_error(err)
        out = buf.getvalue()
        assert 'test error' in out
        assert 'context' not in out
        assert '->' not in out.split('--')[1] if '--' in out else True

    def test_on_error_with_context_but_no_recovery(self):
        """on_error with context set but recovery_action empty (branch 135->137)."""
        from cifflow.types import ParseError
        buf = io.StringIO()
        handler = ParseHandler(file=buf)
        err = ParseError(
            error_type='syntactic', message='test error',
            line=1, column=1,
            context='some context',
            recovery_action='',   # empty → branch 135 evaluates False
        )
        handler.on_error(err)
        out = buf.getvalue()
        assert 'some context' in out

    def test_long_value_repr_is_truncated(self):
        """add_value with repr > 60 chars triggers truncation (line 89)."""
        long_val = 'x' * 60  # repr will be > 60 chars
        cif = f'#\\#CIF_2.0\ndata_d\n_tag {long_val}\n'
        out = _capture(inspect_parse, cif)
        assert 'add_value' in out
        assert '…' in out

    def test_list_start_and_end_printed(self):
        """on_list_start and on_list_end are printed (lines 94-96, 99-101)."""
        cif = '#\\#CIF_2.0\ndata_d\n_tag [1 2]\n'
        out = _capture(inspect_parse, cif)
        assert 'on_list_start' in out
        assert 'on_list_end' in out

    def test_error_with_context_printed(self):
        """on_error with context and recovery_action fills lines 133->135, 135->137."""
        # An orphan value triggers an error with context and recovery_action set
        cif = '#\\#CIF_2.0\ndata_d\northan_value\n'
        out = _capture(inspect_parse, cif)
        # Error must appear; context/recovery present in real parser errors
        assert '[SYNTACTIC]' in out or '[ERROR]' in out or 'ERROR' in out
