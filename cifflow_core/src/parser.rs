// CIF Parser — generic over EventSink.
//
// Parser::parse<S: EventSink> is PyO3-free: it calls sink methods directly
// and returns ().  PyEventSink captures any Python exception internally;
// the caller checks for it after parse() returns.

use crate::event_sink::EventSink;
use crate::lexer::{Token, TokenType, ValueType};

// ─────────────────────────────────────────────────────────────────────────────
// Container stack frames
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq)]
enum TableState { Key, Colon, Value }

#[derive(Debug)]
struct TableFrame {
    state: TableState,
    pending_key: Option<String>,
    pending_key_vtype: Option<ValueType>,
    pending_key_line: u32,
    pending_key_col: u32,
    pending_key_width: Option<u32>,
}

#[derive(Debug)]
enum Frame { List, Table(TableFrame) }

// ─────────────────────────────────────────────────────────────────────────────
// Parser
// ─────────────────────────────────────────────────────────────────────────────

pub struct Parser {
    in_data_block: bool,
    in_save_frame: bool,
    in_loop:       bool,
    loop_tags:       Vec<String>,
    loop_has_values: bool,
    active_tag:      Option<String>,
    tag_base_depth:  usize,
    container_stack: Vec<Frame>,
    halted:   bool,
    last_line: u32,
    last_col:  u32,
}

impl Parser {
    pub fn new() -> Self {
        Parser {
            in_data_block: false,
            in_save_frame: false,
            in_loop:       false,
            loop_tags:       Vec::new(),
            loop_has_values: false,
            active_tag:      None,
            tag_base_depth:  0,
            container_stack: Vec::new(),
            halted:    false,
            last_line: 1,
            last_col:  1,
        }
    }

    pub fn parse<S: EventSink>(&mut self, tokens: Vec<Token>, sink: &mut S) {
        let n = tokens.len();
        let mut i = 0;
        while i < n && !self.halted {
            let tok = &tokens[i];
            self.flush_errors(tok, sink);
            self.last_line = tok.line;
            self.last_col  = tok.column;
            i = self.dispatch(&tokens, i, sink);
        }
        if !self.halted {
            self.handle_eof(sink);
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    fn flush_errors<S: EventSink>(&self, tok: &Token, sink: &mut S) {
        for le in &tok.errors {
            sink.on_parse_error("lexical", &le.message, le.line, le.column, &le.context, "lexer recovery");
        }
    }

    fn emit<S: EventSink>(
        &self, sink: &mut S,
        etype: &'static str, msg: &str,
        line: u32, col: u32, context: &str, recovery: &str,
    ) {
        sink.on_parse_error(etype, msg, line, col, context, recovery);
    }

    // ── Container helpers ─────────────────────────────────────────────────────

    fn cleanup_table_frame<S: EventSink>(
        &mut self, frame: TableFrame,
        line: u32, col: u32, context: &str, sink: &mut S,
    ) {
        match frame.state {
            TableState::Colon => {
                if let Some(key) = frame.pending_key {
                    self.emit(sink, "syntactic",
                        &format!("table key {key:?} missing : separator"),
                        line, col, context,
                        "emitted on_table_key; inserted ? placeholder");
                    let vt = frame.pending_key_vtype.unwrap_or(ValueType::String);
                    sink.on_table_key(&key, vt);
                    sink.add_value("?", ValueType::Placeholder);
                }
            }
            TableState::Value => {
                self.emit(sink, "syntactic", "table key has no value",
                    line, col, context, "inserted ? placeholder");
                sink.add_value("?", ValueType::Placeholder);
            }
            TableState::Key => {}
        }
    }

    fn close_all_containers<S: EventSink>(
        &mut self, line: u32, col: u32, context: &str, reason: &str, sink: &mut S,
    ) {
        while let Some(frame) = self.container_stack.pop() {
            match frame {
                Frame::List => {
                    sink.on_list_end();
                    self.emit(sink, "syntactic",
                        &format!("implicitly closed unclosed list ({reason})"),
                        line, col, context, "emitted on_list_end");
                }
                Frame::Table(tf) => {
                    self.cleanup_table_frame(tf, line, col, context, sink);
                    sink.on_table_end();
                    self.emit(sink, "syntactic",
                        &format!("implicitly closed unclosed table ({reason})"),
                        line, col, context, "emitted on_table_end");
                }
            }
        }
        self.active_tag = None;
    }

    fn close_active_tag<S: EventSink>(
        &mut self, line: u32, col: u32, context: &str, reason: &str, sink: &mut S,
    ) {
        if let Some(tag) = self.active_tag.take() {
            self.emit(sink, "syntactic",
                &format!("tag {tag:?} has no value ({reason})"),
                line, col, context, "inserted ? placeholder");
            sink.add_value("?", ValueType::Placeholder);
        }
    }

    fn close_loop<S: EventSink>(
        &mut self, line: u32, col: u32, context: &str, reason: &str, sink: &mut S,
    ) {
        if !self.container_stack.is_empty() {
            self.close_all_containers(line, col, context, reason, sink);
            self.emit(sink, "syntactic",
                &format!("unterminated container(s) in loop value ({reason})"),
                line, col, context, "containers implicitly closed");
        }
        if !self.loop_has_values {
            let tags_repr = format!("{:?}", self.loop_tags);
            self.emit(sink, "syntactic",
                &format!("loop has tags {tags_repr} but no values"),
                line, col, context, "loop emitted empty");
        }
        sink.on_loop_end();
        self.in_loop = false;
        self.loop_tags.clear();
        self.loop_has_values = false;
    }

    fn prepare_for_keyword<S: EventSink>(
        &mut self, line: u32, col: u32, context: &str, keyword: &str, sink: &mut S,
    ) {
        if self.in_loop {
            let reason = format!("terminated by {keyword}");
            self.close_loop(line, col, context, &reason, sink);
        } else {
            if !self.container_stack.is_empty() {
                let reason = format!("terminated by {keyword}");
                self.close_all_containers(line, col, context, &reason, sink);
            }
            let reason = format!("terminated by {keyword}");
            self.close_active_tag(line, col, context, &reason, sink);
        }
    }

    fn after_close_container(&mut self) {
        if let Some(Frame::Table(ref mut top)) = self.container_stack.last_mut() {
            if top.state == TableState::Value {
                top.state = TableState::Key;
            }
        }
        if self.active_tag.is_some() && self.container_stack.len() == self.tag_base_depth {
            self.active_tag = None;
        }
        if self.in_loop && self.container_stack.is_empty() {
            self.loop_has_values = true;
        }
    }

    // ── Main dispatch ─────────────────────────────────────────────────────────

    fn dispatch<S: EventSink>(
        &mut self, tokens: &[Token], i: usize, sink: &mut S,
    ) -> usize {
        let tok = &tokens[i];
        match tok.token_type {
            TokenType::Keyword => self.handle_keyword(tokens, i, sink),
            TokenType::Tag     => { self.handle_tag(tok, sink); i + 1 }
            TokenType::Value   => { self.handle_value(tok, sink); i + 1 }
        }
    }

    // ── Keyword handling ──────────────────────────────────────────────────────

    fn handle_keyword<S: EventSink>(
        &mut self, tokens: &[Token], i: usize, sink: &mut S,
    ) -> usize {
        let tok = &tokens[i];
        let lower = tok.value.to_ascii_lowercase();

        if lower == "global_" {
            self.handle_global(tok, sink);
            return i + 1;
        }

        if lower == "stop_" {
            if self.in_loop {
                self.close_loop(tok.line, tok.column, &tok.value, "stop_", sink);
            } else {
                self.emit(sink, "syntactic", "stop_ outside loop",
                    tok.line, tok.column, &tok.value, "ignored");
            }
            return i + 1;
        }

        if lower == "loop_" {
            self.prepare_for_keyword(tok.line, tok.column, &tok.value, "loop_", sink);
            if !self.in_data_block {
                self.emit(sink, "syntactic", "loop_ outside data block",
                    tok.line, tok.column, &tok.value, "continuing");
            }
            return self.start_loop(tokens, i, sink);
        }

        // data_ / save_
        let tok_value = tok.value.clone();
        self.prepare_for_keyword(tok.line, tok.column, &tok.value, &tok_value, sink);

        if lower.starts_with("data_") {
            let name = tok.value[5..].to_string();
            if name.is_empty() {
                self.emit(sink, "syntactic", "data block with empty name",
                    tok.line, tok.column, &tok.value, "using empty string");
            }
            if self.in_save_frame {
                sink.on_save_frame_end();
                self.in_save_frame = false;
            }
            self.in_data_block = true;
            sink.on_data_block(&name);
        } else if lower.starts_with("save_") && lower.len() > 5 {
            let name = tok.value[5..].to_string();
            if !self.in_data_block {
                self.emit(sink, "syntactic", "save frame outside data block",
                    tok.line, tok.column, &tok.value, "continuing");
            }
            if self.in_save_frame {
                self.emit(sink, "syntactic", "nested save frame",
                    tok.line, tok.column, &tok.value,
                    "implicitly closed previous save frame");
                sink.on_save_frame_end();
            }
            self.in_save_frame = true;
            sink.on_save_frame_start(&name);
        } else if lower == "save_" {
            if self.in_save_frame {
                sink.on_save_frame_end();
                self.in_save_frame = false;
            } else {
                self.emit(sink, "syntactic", "save_ (frame close) outside save frame",
                    tok.line, tok.column, &tok.value, "ignored");
            }
        }

        i + 1
    }

    fn handle_global<S: EventSink>(&mut self, tok: &Token, sink: &mut S) {
        if self.in_loop {
            self.close_loop(tok.line, tok.column, &tok.value, "global_", sink);
        } else {
            if !self.container_stack.is_empty() {
                self.close_all_containers(tok.line, tok.column, &tok.value, "global_", sink);
            }
            self.close_active_tag(tok.line, tok.column, &tok.value, "global_", sink);
        }
        if self.in_save_frame {
            sink.on_save_frame_end();
            self.in_save_frame = false;
        }
        self.emit(sink, "syntactic", "global_ is reserved and not permitted in CIF",
            tok.line, tok.column, &tok.value, "parsing halted");
        self.halted = true;
    }

    fn start_loop<S: EventSink>(
        &mut self, tokens: &[Token], i: usize, sink: &mut S,
    ) -> usize {
        let tok = &tokens[i];
        let mut j = i + 1;
        let mut tags: Vec<String> = Vec::new();

        while j < tokens.len() {
            let nxt = &tokens[j];
            if nxt.token_type != TokenType::Tag { break; }
            self.flush_errors(nxt, sink);
            tags.push(nxt.value.clone());
            j += 1;
        }

        if tags.is_empty() {
            self.emit(sink, "syntactic", "loop_ with no tags — loop skipped",
                tok.line, tok.column, &tok.value, "loop ignored");
            return j;
        }

        self.in_loop = true;
        self.loop_tags = tags.clone();
        self.loop_has_values = false;

        sink.on_loop_start(&tags);
        j
    }

    // ── Tag handling ──────────────────────────────────────────────────────────

    fn handle_tag<S: EventSink>(&mut self, tok: &Token, sink: &mut S) {
        if self.in_loop {
            let reason = format!("new tag {:?}", tok.value);
            self.close_loop(tok.line, tok.column, &tok.value, &reason, sink);
        } else if !self.container_stack.is_empty() {
            self.emit(sink, "syntactic",
                &format!("tag {:?} encountered inside open container", tok.value),
                tok.line, tok.column, &tok.value, "implicitly closing containers");
            let reason = format!("tag {:?}", tok.value);
            self.close_all_containers(tok.line, tok.column, &tok.value, &reason, sink);
        }

        let reason = format!("new tag {:?}", tok.value);
        self.close_active_tag(tok.line, tok.column, &tok.value, &reason, sink);

        if !self.in_data_block {
            self.emit(sink, "syntactic",
                &format!("tag {:?} outside data block", tok.value),
                tok.line, tok.column, &tok.value, "continuing");
        }

        self.active_tag = Some(tok.value.clone());
        self.tag_base_depth = self.container_stack.len();
        sink.add_tag(&tok.value);
    }

    // ── Value handling ────────────────────────────────────────────────────────

    fn handle_value<S: EventSink>(&mut self, tok: &Token, sink: &mut S) {
        let v = tok.value.as_str();
        let vt = tok.value_type.unwrap_or(ValueType::String);

        if v == "[" { self.open_list(tok, sink); return; }
        if v == "]" { self.close_list(tok, sink); return; }
        if v == "{" { self.open_table(tok, sink); return; }
        if v == "}" { self.close_table(tok, sink); return; }
        if v == ":" {
            let in_table = matches!(self.container_stack.last(), Some(Frame::Table(_)));
            if in_table {
                self.handle_table_colon(tok, sink);
                return;
            }
        }

        self.dispatch_scalar_value(v, vt, tok, sink);
    }

    fn ensure_value_context<S: EventSink>(&mut self, tok: &Token, sink: &mut S) {
        if !self.in_loop && self.active_tag.is_none() && self.container_stack.is_empty() {
            self.emit(sink, "syntactic", "container without preceding tag",
                tok.line, tok.column, &tok.value, "attached to _cifflow_error_value");
            sink.add_tag("_cifflow_error_value");
            self.active_tag = Some("_cifflow_error_value".to_string());
            self.tag_base_depth = 0;
        }
    }

    fn notify_parent_table_of_container_open<S: EventSink>(
        &mut self, tok: &Token, sink: &mut S,
    ) {
        let table_action: Option<(TableState, Option<String>, Option<ValueType>)> =
            if let Some(Frame::Table(ref f)) = self.container_stack.last() {
                Some((f.state.clone(), f.pending_key.clone(), f.pending_key_vtype))
            } else {
                None
            };

        let Some((state, pending_key, pending_key_vtype)) = table_action else {
            return;
        };

        match state {
            TableState::Key => {
                self.emit(sink, "syntactic", "container in table key position",
                    tok.line, tok.column, &tok.value,
                    "treating container as table value (no key)");
                if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                    f.state = TableState::Value;
                }
            }
            TableState::Colon => {
                if let Some(key) = pending_key {
                    self.emit(sink, "syntactic",
                        &format!("table key {key:?} missing : separator"),
                        tok.line, tok.column, &tok.value,
                        "emitted on_table_key; treating container as value");
                    let vt = pending_key_vtype.unwrap_or(ValueType::String);
                    sink.on_table_key(&key, vt);
                    if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                        f.pending_key = None;
                        f.state = TableState::Value;
                    }
                }
            }
            TableState::Value => {}
        }
    }

    fn open_list<S: EventSink>(&mut self, tok: &Token, sink: &mut S) {
        self.ensure_value_context(tok, sink);
        self.notify_parent_table_of_container_open(tok, sink);
        self.container_stack.push(Frame::List);
        sink.on_list_start();
    }

    fn close_list<S: EventSink>(&mut self, tok: &Token, sink: &mut S) {
        if !matches!(self.container_stack.last(), Some(Frame::List)) {
            self.emit(sink, "syntactic", "unexpected ] — no open list",
                tok.line, tok.column, &tok.value, "ignored");
            return;
        }
        self.container_stack.pop();
        sink.on_list_end();
        self.after_close_container();
    }

    fn open_table<S: EventSink>(&mut self, tok: &Token, sink: &mut S) {
        self.ensure_value_context(tok, sink);
        self.notify_parent_table_of_container_open(tok, sink);
        self.container_stack.push(Frame::Table(TableFrame {
            state: TableState::Key,
            pending_key: None,
            pending_key_vtype: None,
            pending_key_line: 0,
            pending_key_col: 0,
            pending_key_width: None,
        }));
        sink.on_table_start();
    }

    fn close_table<S: EventSink>(&mut self, tok: &Token, sink: &mut S) {
        if !matches!(self.container_stack.last(), Some(Frame::Table(_))) {
            self.emit(sink, "syntactic", "unexpected } — no open table",
                tok.line, tok.column, &tok.value, "ignored");
            return;
        }
        let frame = self.container_stack.pop();
        if let Some(Frame::Table(tf)) = frame {
            self.cleanup_table_frame(tf, tok.line, tok.column, &tok.value, sink);
        }
        sink.on_table_end();
        self.after_close_container();
    }

    fn handle_table_colon<S: EventSink>(
        &mut self, tok: &Token, sink: &mut S,
    ) {
        let info: Option<(TableState, Option<String>, Option<ValueType>, u32, u32, Option<u32>)> =
            if let Some(Frame::Table(ref f)) = self.container_stack.last() {
                Some((
                    f.state.clone(),
                    f.pending_key.clone(),
                    f.pending_key_vtype,
                    f.pending_key_line,
                    f.pending_key_col,
                    f.pending_key_width,
                ))
            } else { None };

        let Some((state, pending_key, pending_key_vtype, pk_line, pk_col, pk_width)) = info
        else { return; };

        match state {
            TableState::Colon => {
                if let Some(width) = pk_width {
                    let adj_col = pk_col + width;
                    if tok.line != pk_line || tok.column != adj_col {
                        let key = pending_key.as_deref().unwrap_or("");
                        self.emit(sink, "syntactic",
                            &format!("whitespace between table key {key:?} and : separator"),
                            tok.line, tok.column, &tok.value, "accepted");
                    }
                }
                let key = pending_key.unwrap_or_default();
                let vt  = pending_key_vtype.unwrap_or(ValueType::String);
                sink.on_table_key(&key, vt);
                if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                    f.pending_key = None;
                    f.pending_key_vtype = None;
                    f.pending_key_width = None;
                    f.state = TableState::Value;
                }
            }
            TableState::Key => {
                self.emit(sink, "syntactic", "unexpected : in table — no pending key",
                    tok.line, tok.column, &tok.value, "ignored");
            }
            TableState::Value => {
                self.emit(sink, "syntactic", "unexpected : in table value position",
                    tok.line, tok.column, &tok.value, "ignored");
            }
        }
    }

    fn dispatch_scalar_in_table<S: EventSink>(
        &mut self, value: &str, vt: ValueType, tok: &Token, sink: &mut S,
    ) {
        let state = if let Some(Frame::Table(ref f)) = self.container_stack.last() {
            f.state.clone()
        } else { return; };

        match state {
            TableState::Key => {
                let quoted = matches!(vt,
                    ValueType::SingleQuoted | ValueType::DoubleQuoted |
                    ValueType::TripleSingleQuoted | ValueType::TripleDoubleQuoted);
                if !quoted {
                    self.emit(sink, "syntactic",
                        &format!("table key must be a quoted string, got unquoted: {value:?}"),
                        tok.line, tok.column, &tok.value, "treating as key anyway");
                }
                let width = match vt {
                    ValueType::SingleQuoted | ValueType::DoubleQuoted =>
                        Some((value.len() as u32) + 2),
                    ValueType::TripleSingleQuoted | ValueType::TripleDoubleQuoted =>
                        if !value.contains('\n') { Some((value.len() as u32) + 6) } else { None },
                    _ => None,
                };
                if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                    f.pending_key       = Some(value.to_string());
                    f.pending_key_vtype = Some(vt);
                    f.pending_key_line  = tok.line;
                    f.pending_key_col   = tok.column;
                    f.pending_key_width = width;
                    f.state = TableState::Colon;
                }
            }
            TableState::Colon => {
                let (key, kv) = if let Some(Frame::Table(ref f)) = self.container_stack.last() {
                    (f.pending_key.clone().unwrap_or_default(), f.pending_key_vtype)
                } else { (String::new(), None) };

                self.emit(sink, "syntactic",
                    &format!("table key {key:?} not followed by : separator"),
                    tok.line, tok.column, &tok.value,
                    "emitted on_table_key; treating current token as value");
                let kv = kv.unwrap_or(ValueType::String);
                sink.on_table_key(&key, kv);
                sink.add_value(value, vt);
                if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                    f.pending_key = None;
                    f.pending_key_vtype = None;
                    f.pending_key_width = None;
                    f.state = TableState::Key;
                }
            }
            TableState::Value => {
                sink.add_value(value, vt);
                if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                    f.state = TableState::Key;
                }
            }
        }
    }

    fn dispatch_scalar_value<S: EventSink>(
        &mut self, value: &str, vt: ValueType, tok: &Token, sink: &mut S,
    ) {
        let in_table = matches!(self.container_stack.last(), Some(Frame::Table(_)));
        let in_list  = matches!(self.container_stack.last(), Some(Frame::List));

        if in_table {
            self.dispatch_scalar_in_table(value, vt, tok, sink);
            return;
        }
        if in_list {
            sink.add_value(value, vt);
            return;
        }
        if self.in_loop {
            sink.add_value(value, vt);
            self.loop_has_values = true;
            return;
        }
        if self.active_tag.is_some() {
            sink.add_value(value, vt);
            self.active_tag = None;
            return;
        }

        // Orphan value
        self.emit(sink, "syntactic",
            &format!("value {value:?} has no preceding tag"),
            tok.line, tok.column, &tok.value, "attached to _cifflow_error_value");
        sink.add_tag("_cifflow_error_value");
        sink.add_value(value, vt);
    }

    // ── EOF ───────────────────────────────────────────────────────────────────

    pub fn handle_eof<S: EventSink>(&mut self, sink: &mut S) {
        let line = self.last_line;
        let col  = self.last_col;

        if self.in_loop {
            self.close_loop(line, col, "EOF", "EOF", sink);
        }

        while let Some(frame) = self.container_stack.pop() {
            match frame {
                Frame::List => {
                    sink.on_list_end();
                    self.emit(sink, "syntactic", "unterminated list at EOF",
                        line, col, "", "emitted on_list_end");
                }
                Frame::Table(tf) => {
                    self.cleanup_table_frame(tf, line, col, "EOF", sink);
                    sink.on_table_end();
                    self.emit(sink, "syntactic", "unterminated table at EOF",
                        line, col, "", "emitted on_table_end");
                }
            }
        }

        if let Some(tag) = self.active_tag.take() {
            self.emit(sink, "syntactic",
                &format!("tag {tag:?} has no value at EOF"),
                line, col, &tag, "inserted ? placeholder");
            sink.add_value("?", ValueType::Placeholder);
        }

        if self.in_save_frame {
            self.emit(sink, "syntactic", "unterminated save frame at EOF",
                line, col, "", "emitted on_save_frame_end");
            sink.on_save_frame_end();
            self.in_save_frame = false;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event_sink::EventSink;
    use crate::lexer::{Lexer, ValueType};
    use crate::version::detect_version;

    // ── Recording sink ────────────────────────────────────────────────────────

    #[derive(Debug, PartialEq, Clone)]
    enum Ev {
        DataBlock(String),
        SaveFrameStart(String),
        SaveFrameEnd,
        Tag(String),
        Value(String, ValueType),
        ListStart,
        ListEnd,
        TableStart,
        TableKey(String, ValueType),
        TableEnd,
        LoopStart(Vec<String>),
        LoopEnd,
        Err(String),
    }

    struct Rec(Vec<Ev>);

    impl EventSink for Rec {
        fn on_data_block(&mut self, n: &str) { self.0.push(Ev::DataBlock(n.to_string())); }
        fn on_save_frame_start(&mut self, n: &str) { self.0.push(Ev::SaveFrameStart(n.to_string())); }
        fn on_save_frame_end(&mut self) { self.0.push(Ev::SaveFrameEnd); }
        fn add_tag(&mut self, t: &str) { self.0.push(Ev::Tag(t.to_string())); }
        fn add_value(&mut self, v: &str, vt: ValueType) { self.0.push(Ev::Value(v.to_string(), vt)); }
        fn on_list_start(&mut self) { self.0.push(Ev::ListStart); }
        fn on_list_end(&mut self) { self.0.push(Ev::ListEnd); }
        fn on_table_start(&mut self) { self.0.push(Ev::TableStart); }
        fn on_table_key(&mut self, k: &str, vt: ValueType) { self.0.push(Ev::TableKey(k.to_string(), vt)); }
        fn on_table_end(&mut self) { self.0.push(Ev::TableEnd); }
        fn on_loop_start(&mut self, tags: &[String]) { self.0.push(Ev::LoopStart(tags.to_vec())); }
        fn on_loop_end(&mut self) { self.0.push(Ev::LoopEnd); }
        fn on_parse_error(&mut self, _e: &'static str, msg: &str, _l: u32, _c: u32, _ctx: &str, _r: &str) {
            self.0.push(Ev::Err(msg.to_string()));
        }
    }

    fn events(src: &str) -> Vec<Ev> {
        let vr = detect_version(src);
        let tokens = Lexer::new(&vr.remaining, vr.version, vr.line_offset).tokenise();
        let mut sink = Rec(Vec::new());
        Parser::new().parse(tokens, &mut sink);
        sink.0
    }

    fn ev_tags(evs: &[Ev]) -> Vec<&str> {
        evs.iter().filter_map(|e| if let Ev::Tag(t) = e { Some(t.as_str()) } else { None }).collect()
    }

    fn ev_values(evs: &[Ev]) -> Vec<&str> {
        evs.iter().filter_map(|e| if let Ev::Value(v, _) = e { Some(v.as_str()) } else { None }).collect()
    }

    fn ev_errors(evs: &[Ev]) -> Vec<&str> {
        evs.iter().filter_map(|e| if let Ev::Err(m) = e { Some(m.as_str()) } else { None }).collect()
    }

    fn count(evs: &[Ev], ev: &Ev) -> usize {
        evs.iter().filter(|e| *e == ev).count()
    }

    // ── basic structure ───────────────────────────────────────────────────────

    #[test]
    fn empty_source_no_events() {
        assert!(events("").is_empty());
    }

    #[test]
    fn single_data_block() {
        let evs = events("data_foo");
        assert_eq!(evs, vec![Ev::DataBlock("foo".to_string())]);
    }

    #[test]
    fn data_block_name_preserved() {
        let evs = events("data_My_Block_123");
        assert!(matches!(&evs[0], Ev::DataBlock(n) if n == "My_Block_123"));
    }

    #[test]
    fn tag_value_pair() {
        let evs = events("data_b _tag val");
        assert_eq!(ev_tags(&evs), vec!["_tag"]);
        assert_eq!(ev_values(&evs), vec!["val"]);
        assert!(ev_errors(&evs).is_empty());
    }

    #[test]
    fn tag_placeholder_dot() {
        let evs = events("data_b _tag .");
        assert!(matches!(&evs[2], Ev::Value(v, vt) if v == "." && *vt == ValueType::Placeholder));
    }

    #[test]
    fn tag_placeholder_question() {
        let evs = events("data_b _tag ?");
        assert!(matches!(&evs[2], Ev::Value(v, vt) if v == "?" && *vt == ValueType::Placeholder));
    }

    #[test]
    fn quoted_value_preserves_type() {
        let evs = events("data_b _tag 'hello'");
        assert!(matches!(&evs[2], Ev::Value(v, vt)
            if v == "hello" && *vt == ValueType::SingleQuoted));
    }

    #[test]
    fn multiple_tag_value_pairs() {
        let evs = events("data_b _a 1 _b 2 _c 3");
        assert_eq!(ev_tags(&evs), vec!["_a", "_b", "_c"]);
        assert_eq!(ev_values(&evs), vec!["1", "2", "3"]);
    }

    // ── loops ─────────────────────────────────────────────────────────────────

    #[test]
    fn loop_basic() {
        let evs = events("data_b loop_ _a _b 1 2 3 4");
        assert!(matches!(&evs[1], Ev::LoopStart(tags) if tags == &["_a", "_b"]));
        assert_eq!(ev_values(&evs), vec!["1", "2", "3", "4"]);
        assert!(evs.contains(&Ev::LoopEnd));
    }

    #[test]
    fn loop_terminated_by_eof() {
        let evs = events("data_b loop_ _a 1 2");
        assert!(evs.contains(&Ev::LoopEnd));
        assert!(ev_errors(&evs).is_empty());
    }

    #[test]
    fn loop_terminated_by_new_data_block() {
        let evs = events("data_a loop_ _x 1 2 data_b _y 3");
        assert!(evs.contains(&Ev::LoopEnd));
        assert_eq!(count(&evs, &Ev::DataBlock("a".to_string())), 1);
        assert_eq!(count(&evs, &Ev::DataBlock("b".to_string())), 1);
    }

    #[test]
    fn loop_terminated_by_new_tag() {
        let evs = events("data_b loop_ _a 1 2 _b 3");
        // Loop over _a is closed by _b; _b then becomes a standalone tag
        assert!(evs.contains(&Ev::LoopEnd));
        // _a is in LoopStart, not a Tag event; only _b appears as a Tag event
        assert_eq!(ev_tags(&evs), vec!["_b"]);
        assert_eq!(ev_values(&evs), vec!["1", "2", "3"]);
    }

    #[test]
    fn loop_terminated_by_stop() {
        let evs = events("data_b loop_ _a 1 2 stop_");
        assert!(evs.contains(&Ev::LoopEnd));
        assert!(ev_errors(&evs).is_empty());
    }

    #[test]
    fn loop_no_values_emits_error() {
        let evs = events("data_b loop_ _a");
        assert!(evs.contains(&Ev::LoopStart(vec!["_a".to_string()])));
        assert!(evs.contains(&Ev::LoopEnd));
        assert!(!ev_errors(&evs).is_empty());
    }

    #[test]
    fn loop_no_tags_is_error() {
        let evs = events("data_b loop_ 1 2 3");
        assert!(!ev_errors(&evs).is_empty());
        assert!(!evs.contains(&Ev::LoopEnd));
    }

    // ── save frames ───────────────────────────────────────────────────────────

    #[test]
    fn save_frame_basic() {
        let evs = events("data_b save_myframe _tag val save_");
        assert!(matches!(&evs[1], Ev::SaveFrameStart(n) if n == "myframe"));
        assert_eq!(ev_tags(&evs), vec!["_tag"]);
        assert!(evs.contains(&Ev::SaveFrameEnd));
        assert!(ev_errors(&evs).is_empty());
    }

    #[test]
    fn save_frame_terminated_by_eof() {
        let evs = events("data_b save_sf _tag val");
        assert!(evs.contains(&Ev::SaveFrameEnd));
        assert!(!ev_errors(&evs).is_empty());
    }

    #[test]
    fn save_frame_terminated_by_new_data_block() {
        let evs = events("data_a save_sf _tag val data_b _x 1");
        assert!(evs.contains(&Ev::SaveFrameEnd));
        assert_eq!(count(&evs, &Ev::DataBlock("a".to_string())), 1);
        assert_eq!(count(&evs, &Ev::DataBlock("b".to_string())), 1);
    }

    #[test]
    fn nested_save_frame_is_error() {
        let evs = events("data_b save_a _x 1 save_b _y 2 save_");
        let errs = ev_errors(&evs);
        assert!(errs.iter().any(|m| m.contains("nested save frame")));
    }

    #[test]
    fn save_close_outside_frame_is_error() {
        let evs = events("data_b save_");
        assert!(!ev_errors(&evs).is_empty());
    }

    #[test]
    fn save_frame_outside_data_block_is_error() {
        let evs = events("save_sf _tag val save_");
        assert!(!ev_errors(&evs).is_empty());
    }

    // ── orphan values and error recovery ─────────────────────────────────────

    #[test]
    fn orphan_value_attaches_to_error_tag() {
        let evs = events("data_b hello");
        assert!(!ev_errors(&evs).is_empty());
        assert!(ev_tags(&evs).iter().any(|t| t.contains("error_value")));
    }

    #[test]
    fn tag_without_value_at_eof_gets_placeholder() {
        let evs = events("data_b _tag");
        assert!(!ev_errors(&evs).is_empty());
        assert!(matches!(evs.last().unwrap(), Ev::Value(v, vt)
            if v == "?" && *vt == ValueType::Placeholder));
    }

    #[test]
    fn new_tag_while_previous_has_no_value() {
        let evs = events("data_b _tag1 _tag2 val");
        let errs = ev_errors(&evs);
        assert!(errs.iter().any(|m| m.contains("has no value")));
        assert_eq!(ev_values(&evs).iter().filter(|v| **v == "val").count(), 1);
    }

    // ── global_ ───────────────────────────────────────────────────────────────

    #[test]
    fn global_emits_error_and_halts() {
        let evs = events("data_b _tag 1 global_ _tag 2");
        let errs = ev_errors(&evs);
        assert!(errs.iter().any(|m| m.contains("global_")));
        // _tag 2 after global_ must not appear — parser halted
        let tags = ev_tags(&evs);
        assert_eq!(tags.iter().filter(|t| **t == "_tag").count(), 1);
    }

    // ── stop_ ────────────────────────────────────────────────────────────────

    #[test]
    fn stop_outside_loop_is_error() {
        let evs = events("data_b _tag 1 stop_");
        assert!(!ev_errors(&evs).is_empty());
    }

    // ── containers (CIF 2.0) ─────────────────────────────────────────────────

    #[test]
    fn list_basic() {
        let evs = events("#\\#CIF_2.0\ndata_b _tag [1 2 3]");
        assert!(evs.contains(&Ev::ListStart));
        assert_eq!(ev_values(&evs), vec!["1", "2", "3"]);
        assert!(evs.contains(&Ev::ListEnd));
        assert!(ev_errors(&evs).is_empty());
    }

    #[test]
    fn list_nested() {
        let evs = events("#\\#CIF_2.0\ndata_b _tag [[1 2] 3]");
        assert_eq!(count(&evs, &Ev::ListStart), 2);
        assert_eq!(count(&evs, &Ev::ListEnd), 2);
        assert!(ev_errors(&evs).is_empty());
    }

    #[test]
    fn table_basic() {
        let evs = events("#\\#CIF_2.0\ndata_b _tag {\"key\":val}");
        assert!(evs.contains(&Ev::TableStart));
        assert!(evs.iter().any(|e| matches!(e, Ev::TableKey(k, _) if k == "key")));
        assert_eq!(ev_values(&evs), vec!["val"]);
        assert!(evs.contains(&Ev::TableEnd));
    }

    #[test]
    fn table_unquoted_key_is_error() {
        let evs = events("#\\#CIF_2.0\ndata_b _tag {key:val}");
        assert!(!ev_errors(&evs).is_empty());
        // Despite error, key is still emitted
        assert!(evs.iter().any(|e| matches!(e, Ev::TableKey(_, _))));
    }

    #[test]
    fn unclosed_list_at_eof_closes_implicitly() {
        let evs = events("#\\#CIF_2.0\ndata_b _tag [1 2");
        assert!(evs.contains(&Ev::ListEnd));
        assert!(!ev_errors(&evs).is_empty());
    }

    #[test]
    fn unclosed_list_closed_by_new_tag() {
        let evs = events("#\\#CIF_2.0\ndata_b _tag [1 2 _next val");
        assert!(evs.contains(&Ev::ListEnd));
    }

    #[test]
    fn unmatched_close_list_is_error() {
        let evs = events("#\\#CIF_2.0\ndata_b _tag ]");
        assert!(!ev_errors(&evs).is_empty());
    }

    #[test]
    fn unmatched_close_table_is_error() {
        let evs = events("#\\#CIF_2.0\ndata_b _tag }");
        assert!(!ev_errors(&evs).is_empty());
    }

    // ── multi-block ───────────────────────────────────────────────────────────

    #[test]
    fn multi_block() {
        let evs = events("data_a _ta va data_b _tb vb");
        assert_eq!(count(&evs, &Ev::DataBlock("a".to_string())), 1);
        assert_eq!(count(&evs, &Ev::DataBlock("b".to_string())), 1);
        assert_eq!(ev_tags(&evs), vec!["_ta", "_tb"]);
        assert_eq!(ev_values(&evs), vec!["va", "vb"]);
    }

    #[test]
    fn tag_outside_data_block_is_error() {
        let evs = events("_tag val");
        assert!(!ev_errors(&evs).is_empty());
    }

    #[test]
    fn loop_outside_data_block_is_error() {
        let evs = events("loop_ _a 1 2");
        assert!(!ev_errors(&evs).is_empty());
    }

    // ── loop with containers ──────────────────────────────────────────────────

    #[test]
    fn loop_with_list_values() {
        let evs = events("#\\#CIF_2.0\ndata_b loop_ _a [1 2] [3 4]");
        assert!(evs.contains(&Ev::LoopStart(vec!["_a".to_string()])));
        assert_eq!(count(&evs, &Ev::ListStart), 2);
        assert!(evs.contains(&Ev::LoopEnd));
    }

    #[test]
    fn container_without_tag_is_error() {
        let evs = events("#\\#CIF_2.0\ndata_b [1 2]");
        assert!(!ev_errors(&evs).is_empty());
        assert!(ev_tags(&evs).iter().any(|t| t.contains("error_value")));
    }
}
