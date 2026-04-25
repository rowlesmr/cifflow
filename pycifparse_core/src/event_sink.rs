// EventSink trait — decouples the parser from its output destination.
//
// PyEventSink calls Python CifParserEvents methods via PyO3.
// RawBuilder accumulates Rust data structures with zero Python overhead.

use pyo3::PyResult;
use crate::lexer::ValueType;

pub trait EventSink {
    fn on_data_block(&mut self, name: &str) -> PyResult<()>;
    fn on_save_frame_start(&mut self, name: &str) -> PyResult<()>;
    fn on_save_frame_end(&mut self) -> PyResult<()>;
    fn add_tag(&mut self, tag: &str) -> PyResult<()>;
    fn add_value(&mut self, value: &str, vtype: ValueType) -> PyResult<()>;
    fn on_list_start(&mut self) -> PyResult<()>;
    fn on_list_end(&mut self) -> PyResult<()>;
    fn on_table_start(&mut self) -> PyResult<()>;
    fn on_table_key(&mut self, key: &str, vtype: ValueType) -> PyResult<()>;
    fn on_table_end(&mut self) -> PyResult<()>;
    fn on_loop_start(&mut self, tags: &[String]) -> PyResult<()>;
    fn on_loop_end(&mut self) -> PyResult<()>;
    fn on_parse_error(
        &mut self,
        etype: &'static str,
        msg: &str,
        line: u32,
        col: u32,
        context: &str,
        recovery: &str,
    ) -> PyResult<()>;
}
