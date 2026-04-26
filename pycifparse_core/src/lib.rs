use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};

mod error;
mod event_sink;
mod lexer;
mod parser;
mod raw_builder;
mod textfield;
mod version;

use event_sink::EventSink;
use lexer::{Lexer, ValueType};
use parser::Parser;
use raw_builder::RawBuilder;
use version::{detect_version, CifVersion};

// ─────────────────────────────────────────────────────────────────────────────
// PyEventSink — EventSink that calls a Python CifParserEvents handler
// ─────────────────────────────────────────────────────────────────────────────

struct PyEventSink<'py> {
    handler:         &'py Bound<'py, PyAny>,
    parse_error_cls: Bound<'py, PyAny>,
    vt_objs:         [Bound<'py, PyAny>; 7],
}

impl<'py> PyEventSink<'py> {
    fn new(py: Python<'py>, handler: &'py Bound<'py, PyAny>) -> PyResult<Self> {
        let types_mod = py.import("pycifparse.types")?;
        let parse_error_cls = types_mod.getattr("ParseError")?;
        let vt_cls          = types_mod.getattr("ValueType")?;
        Ok(PyEventSink {
            handler,
            parse_error_cls,
            vt_objs: [
                vt_cls.getattr("MULTILINE_STRING")?,     // 0
                vt_cls.getattr("TRIPLE_DOUBLE_QUOTED")?, // 1
                vt_cls.getattr("TRIPLE_SINGLE_QUOTED")?, // 2
                vt_cls.getattr("DOUBLE_QUOTED")?,        // 3
                vt_cls.getattr("SINGLE_QUOTED")?,        // 4
                vt_cls.getattr("STRING")?,               // 5
                vt_cls.getattr("PLACEHOLDER")?,          // 6
            ],
        })
    }

    fn py_vt(&self, vt: ValueType) -> &Bound<'py, PyAny> {
        &self.vt_objs[vt as usize]
    }
}

impl<'py> EventSink for PyEventSink<'py> {
    fn on_data_block(&mut self, name: &str) -> PyResult<()> {
        self.handler.call_method1("on_data_block", (name,))?;
        Ok(())
    }
    fn on_save_frame_start(&mut self, name: &str) -> PyResult<()> {
        self.handler.call_method1("on_save_frame_start", (name,))?;
        Ok(())
    }
    fn on_save_frame_end(&mut self) -> PyResult<()> {
        self.handler.call_method1("on_save_frame_end", ())?;
        Ok(())
    }
    fn add_tag(&mut self, tag: &str) -> PyResult<()> {
        self.handler.call_method1("add_tag", (tag,))?;
        Ok(())
    }
    fn add_value(&mut self, value: &str, vt: ValueType) -> PyResult<()> {
        self.handler.call_method1("add_value", (value, self.py_vt(vt)))?;
        Ok(())
    }
    fn on_list_start(&mut self) -> PyResult<()> {
        self.handler.call_method1("on_list_start", ())?;
        Ok(())
    }
    fn on_list_end(&mut self) -> PyResult<()> {
        self.handler.call_method1("on_list_end", ())?;
        Ok(())
    }
    fn on_table_start(&mut self) -> PyResult<()> {
        self.handler.call_method1("on_table_start", ())?;
        Ok(())
    }
    fn on_table_key(&mut self, key: &str, vt: ValueType) -> PyResult<()> {
        self.handler.call_method1("on_table_key", (key, self.py_vt(vt)))?;
        Ok(())
    }
    fn on_table_end(&mut self) -> PyResult<()> {
        self.handler.call_method1("on_table_end", ())?;
        Ok(())
    }
    fn on_loop_start(&mut self, tags: &[String]) -> PyResult<()> {
        let py_tags: Vec<&str> = tags.iter().map(String::as_str).collect();
        self.handler.call_method1("on_loop_start", (py_tags,))?;
        Ok(())
    }
    fn on_loop_end(&mut self) -> PyResult<()> {
        self.handler.call_method1("on_loop_end", ())?;
        Ok(())
    }
    fn on_parse_error(
        &mut self, etype: &'static str, msg: &str,
        line: u32, col: u32, context: &str, recovery: &str,
    ) -> PyResult<()> {
        let pe = self.parse_error_cls.call1((
            etype, msg, line as usize, col as usize, context, recovery,
        ))?;
        self.handler.call_method1("on_error", (pe,))?;
        Ok(())
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// parse — Python-callback path (kept for CifBuilder programmatic API)
// ─────────────────────────────────────────────────────────────────────────────

#[pyfunction]
fn parse<'py>(
    py: Python<'py>,
    source: &str,
    handler: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let mut sink = PyEventSink::new(py, handler)?;

    let vr = detect_version(source);
    for e in &vr.errors {
        sink.on_parse_error(e.error_type, &e.message, e.line, e.column, &e.context, &e.recovery_action)?;
    }

    let lexer  = Lexer::new(&vr.remaining, vr.version, vr.line_offset);
    let tokens = lexer.tokenise();
    let mut parser = Parser::new();
    parser.parse(tokens, &mut sink)?;

    let types_mod       = py.import("pycifparse.types")?;
    let cif_version_cls = types_mod.getattr("CifVersion")?;
    let attr = match vr.version {
        CifVersion::Cif2_0 => "CIF_2_0",
        CifVersion::Cif1_1 => "CIF_1_1",
    };
    cif_version_cls.getattr(attr)
}

// ─────────────────────────────────────────────────────────────────────────────
// parse_raw — zero-Python-callback path
// ─────────────────────────────────────────────────────────────────────────────

/// Parse *source* CIF text entirely in Rust.  Returns a Python dict:
///
///   { "version": str, "errors": [...], "blocks": [...] }
///
/// No Python calls are made during parsing.  CifScalar objects are created
/// lazily in Python when block data is first accessed via __getitem__.
#[pyfunction]
#[pyo3(signature = (source, mode=None))]
fn parse_raw<'py>(py: Python<'py>, source: &str, mode: Option<&str>) -> PyResult<Bound<'py, PyDict>> {
    let mode_strict = mode == Some("strict");
    let vr = detect_version(source);
    let mut builder = RawBuilder::new(vr.version, mode_strict);

    for e in &vr.errors {
        builder.push_error(e);
    }

    let lexer  = Lexer::new(&vr.remaining, vr.version, vr.line_offset);
    let tokens = lexer.tokenise();
    let mut parser = Parser::new();
    parser.parse(tokens, &mut builder)?;

    builder.to_python(py)
}

// ─────────────────────────────────────────────────────────────────────────────
// parse_arrow — returns (list[bytes], list[error_dicts])
// ─────────────────────────────────────────────────────────────────────────────

#[pyfunction]
#[pyo3(signature = (source, mode=None))]
fn parse_arrow<'py>(
    py: Python<'py>,
    source: &str,
    mode: Option<&str>,
) -> PyResult<(Bound<'py, PyList>, Bound<'py, PyList>)> {
    let mode_strict = mode == Some("strict");
    let vr = detect_version(source);
    let mut builder = RawBuilder::new(vr.version, mode_strict);
    for e in &vr.errors {
        builder.push_error(e);
    }
    let lexer = Lexer::new(&vr.remaining, vr.version, vr.line_offset);
    let tokens = lexer.tokenise();
    let mut parser = Parser::new();
    parser.parse(tokens, &mut builder)?;

    let parsed = builder.finish();

    let errors = PyList::empty(py);
    for e in &parsed.errors {
        let d = PyDict::new(py);
        d.set_item("error_type", e.error_type)?;
        d.set_item("message", e.message.as_str())?;
        d.set_item("line", e.line)?;
        d.set_item("column", e.column)?;
        d.set_item("context", e.context.as_str())?;
        d.set_item("recovery_action", e.recovery_action.as_str())?;
        errors.append(&d)?;
    }

    let ipc_list = PyList::empty(py);
    for batch_bytes in parsed.to_ipc_batches() {
        ipc_list.append(PyBytes::new(py, &batch_bytes))?;
    }

    Ok((ipc_list, errors))
}

// ─────────────────────────────────────────────────────────────────────────────
// Module
// ─────────────────────────────────────────────────────────────────────────────

#[pymodule]
fn pycifparse_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    m.add_function(wrap_pyfunction!(parse_raw, m)?)?;
    m.add_function(wrap_pyfunction!(parse_arrow, m)?)?;
    Ok(())
}
