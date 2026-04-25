use pyo3::prelude::*;

mod error;
mod version;
mod lexer;
mod parser;

use lexer::Lexer;
use parser::{Parser, PyCtx};
use version::{detect_version, CifVersion};

/// Parse *source* CIF text, calling methods on *handler* (a Python
/// CifParserEvents implementation) for each parser event.
///
/// Returns the detected CifVersion enum member (CifVersion.CIF_1_1 or
/// CifVersion.CIF_2_0).  Raises a Python exception only for unrecoverable
/// internal errors (not for CIF-level errors, which are reported via
/// handler.on_error()).
#[pyfunction]
fn parse<'py>(py: Python<'py>, source: &str, handler: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    // Import needed Python types once per call (cached by Python's import system).
    let types_mod = py.import("pycifparse.types")?;
    let parse_error_cls = types_mod.getattr("ParseError")?;
    let value_type_cls  = types_mod.getattr("ValueType")?;
    let cif_version_cls = types_mod.getattr("CifVersion")?;

    let ctx = PyCtx {
        handler,
        parse_error_cls,
        vt_multiline_string:     value_type_cls.getattr("MULTILINE_STRING")?,
        vt_triple_double_quoted: value_type_cls.getattr("TRIPLE_DOUBLE_QUOTED")?,
        vt_triple_single_quoted: value_type_cls.getattr("TRIPLE_SINGLE_QUOTED")?,
        vt_double_quoted:        value_type_cls.getattr("DOUBLE_QUOTED")?,
        vt_single_quoted:        value_type_cls.getattr("SINGLE_QUOTED")?,
        vt_string:               value_type_cls.getattr("STRING")?,
        vt_placeholder:          value_type_cls.getattr("PLACEHOLDER")?,
    };

    // Version detection
    let vr = detect_version(source);
    for e in &vr.errors {
        let py_err = ctx.make_parse_error_from_rust(e)?;
        handler.call_method1("on_error", (py_err,))?;
    }

    // Lex
    let lexer = Lexer::new(&vr.remaining, vr.version, vr.line_offset);
    let tokens = lexer.tokenise();

    // Parse
    let mut parser = Parser::new();
    parser.parse(tokens, &ctx)?;

    // Return the detected version as a Python CifVersion enum member.
    let attr = match vr.version {
        CifVersion::Cif2_0 => "CIF_2_0",
        CifVersion::Cif1_1 => "CIF_1_1",
    };
    cif_version_cls.getattr(attr)
}

#[pymodule]
fn pycifparse_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    Ok(())
}
