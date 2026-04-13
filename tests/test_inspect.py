"""
Smoke tests for src/pycifparse/inspect/.

These tests verify that the public entry points run without raising and produce
sensible output.  They do not assert exact formatting — that would be brittle —
but do check that key content (token types, event names, error markers) appears
in the output.
"""

import io

import pytest

from pycifparse.inspect import (
    ParseHandler,
    inspect_lexer,
    inspect_parse,
    inspect_schema,
    TraceEvent,
)
from pycifparse.parser.parser import CifParser
from pycifparse.types import ValueType

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
        from pycifparse.types import CifVersion
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
        from pycifparse.dictionary.loader import DictionaryLoader
        from pycifparse.dictionary.schema import generate_schema
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
        from pycifparse.dictionary.loader import DictionaryLoader
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
        assert '_block_id' in out
        assert '_row_id' in out

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


# ---------------------------------------------------------------------------
# inspect_ingest + TraceEvent
# ---------------------------------------------------------------------------

class TestInspectIngest:
    def test_returns_list(self):
        import sqlite3
        from pycifparse import build
        from pycifparse.dictionary.schema_apply import apply_fallback_schema
        from pycifparse.inspect import inspect_ingest

        cif, _ = build(_SIMPLE)
        conn = sqlite3.connect(':memory:')
        conn.isolation_level = None
        apply_fallback_schema(conn)

        buf = io.StringIO()
        result = inspect_ingest(cif, conn, schema=None, file=buf)
        conn.close()
        assert isinstance(result, list)

    def test_trace_events_are_trace_event_instances(self):
        import sqlite3
        from pycifparse import build
        from pycifparse.dictionary.schema_apply import apply_fallback_schema
        from pycifparse.inspect import inspect_ingest

        cif, _ = build(_SIMPLE)
        conn = sqlite3.connect(':memory:')
        conn.isolation_level = None
        apply_fallback_schema(conn)

        buf = io.StringIO()
        result = inspect_ingest(cif, conn, schema=None, file=buf)
        conn.close()
        for ev in result:
            assert isinstance(ev, TraceEvent)

    def test_output_written_to_file(self):
        import sqlite3
        from pycifparse import build
        from pycifparse.dictionary.schema_apply import apply_fallback_schema
        from pycifparse.inspect import inspect_ingest

        cif, _ = build(_SIMPLE)
        conn = sqlite3.connect(':memory:')
        conn.isolation_level = None
        apply_fallback_schema(conn)

        buf = io.StringIO()
        inspect_ingest(cif, conn, schema=None, file=buf)
        conn.close()
        assert buf.getvalue()  # something was written

    def test_header_present_in_output(self):
        import sqlite3
        from pycifparse import build
        from pycifparse.dictionary.schema_apply import apply_fallback_schema
        from pycifparse.inspect import inspect_ingest

        cif, _ = build(_SIMPLE)
        conn = sqlite3.connect(':memory:')
        conn.isolation_level = None
        apply_fallback_schema(conn)

        buf = io.StringIO()
        inspect_ingest(cif, conn, schema=None, file=buf)
        conn.close()
        assert 'inspect_ingest' in buf.getvalue()

    def test_trace_event_fields(self):
        ev = TraceEvent(kind='warning', detail='test detail', table='cell')
        assert ev.kind == 'warning'
        assert ev.detail == 'test detail'
        assert ev.table == 'cell'
        assert ev.block_id is None
        assert ev.tag is None
