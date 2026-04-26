// EventSink trait — decouples the parser from its output destination.
//
// The trait is PyO3-free: all methods return (). PyEventSink (in lib.rs)
// stores any Python exception internally and re-raises it after parse()
// returns, so the parser itself never touches the Python runtime.

use crate::lexer::ValueType;

pub trait EventSink {
    fn on_data_block(&mut self, name: &str);
    fn on_save_frame_start(&mut self, name: &str);
    fn on_save_frame_end(&mut self);
    fn add_tag(&mut self, tag: &str);
    fn add_value(&mut self, value: &str, vtype: ValueType);
    fn on_list_start(&mut self);
    fn on_list_end(&mut self);
    fn on_table_start(&mut self);
    fn on_table_key(&mut self, key: &str, vtype: ValueType);
    fn on_table_end(&mut self);
    fn on_loop_start(&mut self, tags: &[String]);
    fn on_loop_end(&mut self);
    fn on_parse_error(
        &mut self,
        etype: &'static str,
        msg: &str,
        line: u32,
        col: u32,
        context: &str,
        recovery: &str,
    );
}
