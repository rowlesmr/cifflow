"""
Smoke tests for src/pycifparse/debug.py.

These tests verify that the three public entry points (debug_lex, DebugHandler,
debug_parse) run without raising and produce sensible output.  They do not
assert exact formatting — that would be brittle — but do check that key
content (token types, event names, error markers) appears in the output.
"""

import io

import pytest

from pycifparse.debug import DebugHandler, debug_lex, debug_parse
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
# debug_lex
# ---------------------------------------------------------------------------

class TestDebugLex:
    def test_runs_without_error(self):
        _capture(debug_lex, _SIMPLE)

    def test_contains_token_types(self):
        out = _capture(debug_lex, _SIMPLE)
        assert 'keyword' in out
        assert 'tag'     in out
        assert 'value'   in out

    def test_contains_value_type(self):
        out = _capture(debug_lex, _SIMPLE)
        assert 'string' in out

    def test_contains_position(self):
        out = _capture(debug_lex, _SIMPLE)
        # Line numbers should appear
        assert '1' in out
        assert '2' in out

    def test_cif11_version_label(self):
        out = _capture(debug_lex, _CIF11)
        assert '1.1' in out

    def test_cif20_version_label(self):
        out = _capture(debug_lex, _SIMPLE)
        assert '2.0' in out

    def test_lexer_error_reported(self):
        # Malformed SU in CIF 2.0 should show a lex error line.
        out = _capture(debug_lex, '#\\#CIF_2.0\ndata_d\n_t 1.0(bad)\n')
        assert 'LEX ERROR' in out or 'invalid SU' in out

    def test_explicit_version_kwarg(self):
        from pycifparse.types import CifVersion
        # Passing version explicitly should not raise.
        _capture(debug_lex, 'data_d\n_t v\n', version=CifVersion.CIF_1_1)


# ---------------------------------------------------------------------------
# DebugHandler
# ---------------------------------------------------------------------------

class TestDebugHandler:
    def test_runs_without_error(self):
        buf = io.StringIO()
        CifParser(DebugHandler(file=buf)).parse(_SIMPLE)

    def test_contains_data_block_event(self):
        buf = io.StringIO()
        CifParser(DebugHandler(file=buf)).parse(_SIMPLE)
        assert 'on_data_block' in buf.getvalue()

    def test_contains_add_tag(self):
        buf = io.StringIO()
        CifParser(DebugHandler(file=buf)).parse(_SIMPLE)
        assert 'add_tag' in buf.getvalue()

    def test_contains_add_value(self):
        buf = io.StringIO()
        CifParser(DebugHandler(file=buf)).parse(_SIMPLE)
        assert 'add_value' in buf.getvalue()

    def test_loop_events_present(self):
        buf = io.StringIO()
        CifParser(DebugHandler(file=buf)).parse(_WITH_LOOP)
        out = buf.getvalue()
        assert 'on_loop_start' in out
        assert 'on_loop_end'   in out

    def test_table_events_present(self):
        buf = io.StringIO()
        CifParser(DebugHandler(file=buf)).parse(_WITH_TABLE)
        out = buf.getvalue()
        assert 'on_table_start' in out
        assert 'on_table_key'   in out
        assert 'on_table_end'   in out

    def test_error_reported(self):
        buf = io.StringIO()
        CifParser(DebugHandler(file=buf)).parse(_WITH_ERROR)
        assert 'SYNTACTIC' in buf.getvalue() or 'syntactic' in buf.getvalue().lower()

    def test_adjacency_error_reported(self):
        buf = io.StringIO()
        CifParser(DebugHandler(file=buf)).parse(_WITH_TABLE_SPACED)
        assert 'whitespace between' in buf.getvalue()

    def test_show_values_false_suppresses_add_value(self):
        buf = io.StringIO()
        CifParser(DebugHandler(file=buf, show_values=False)).parse(_SIMPLE)
        assert 'add_value' not in buf.getvalue()

    def test_forwarding_to_inner_handler(self):
        """All events must reach the inner handler."""
        from tests.parser.test_parser import RecordingHandler
        inner = RecordingHandler()
        buf   = io.StringIO()
        CifParser(DebugHandler(inner, file=buf)).parse(_WITH_LOOP)
        names = inner.event_names()
        assert 'on_data_block'  in names
        assert 'on_loop_start'  in names
        assert 'add_value'      in names
        assert 'on_loop_end'    in names

    def test_nesting_indentation(self):
        """Loop values should be indented more than the loop_start line."""
        buf = io.StringIO()
        CifParser(DebugHandler(file=buf)).parse(_WITH_LOOP)
        lines = buf.getvalue().splitlines()
        loop_start = next(l for l in lines if 'on_loop_start' in l)
        value_line = next(l for l in lines if 'add_value' in l)
        loop_indent  = len(loop_start)  - len(loop_start.lstrip())
        value_indent = len(value_line)  - len(value_line.lstrip())
        assert value_indent > loop_indent


# ---------------------------------------------------------------------------
# debug_parse
# ---------------------------------------------------------------------------

class TestDebugParse:
    def test_runs_without_error(self):
        _capture(debug_parse, _SIMPLE)

    def test_contains_both_sections(self):
        out = _capture(debug_parse, _SIMPLE)
        assert 'token stream' in out
        assert 'parser events' in out

    def test_show_tokens_false_omits_token_stream(self):
        out = _capture(debug_parse, _SIMPLE, show_tokens=False)
        assert 'token stream' not in out
        assert 'parser events' in out

    def test_show_values_false_forwarded(self):
        out = _capture(debug_parse, _SIMPLE, show_values=False)
        assert 'add_value' not in out

    def test_inner_handler_receives_events(self):
        from tests.parser.test_parser import RecordingHandler
        inner = RecordingHandler()
        _capture(debug_parse, _SIMPLE, inner=inner)
        assert any(e.name == 'on_data_block' for e in inner.events)

    def test_real_cif_file(self, tmp_path):
        """debug_parse must not raise on a real file."""
        import pathlib
        src = (pathlib.Path(__file__).parent / 'cif_files' / 'comcifs' / 'simple_data.cif'
               ).read_text(encoding='utf-8')
        _capture(debug_parse, src)

    def test_path_input(self, tmp_path):
        """debug_parse accepts a pathlib.Path."""
        import pathlib
        p = tmp_path / 'test.cif'
        p.write_text(_SIMPLE, encoding='utf-8')
        out = _capture(debug_parse, p)
        assert 'on_data_block' in out

    def test_file_object_input(self, tmp_path):
        """debug_parse accepts an open text file object."""
        import io
        out = _capture(debug_parse, io.StringIO(_SIMPLE))
        assert 'on_data_block' in out


class TestDebugLexFileInput:
    def test_path_input(self, tmp_path):
        """debug_lex accepts a pathlib.Path."""
        p = tmp_path / 'test.cif'
        p.write_text(_SIMPLE, encoding='utf-8')
        out = _capture(debug_lex, p)
        assert 'token stream' in out
        assert 'keyword' in out

    def test_file_object_input(self):
        """debug_lex accepts an open text file object."""
        import io
        out = _capture(debug_lex, io.StringIO(_SIMPLE))
        assert 'token stream' in out
