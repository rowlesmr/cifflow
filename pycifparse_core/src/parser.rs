// CIF Parser — generic over EventSink.
//
// Parser::parse<S: EventSink> calls sink methods directly; no Python I/O
// on the raw-build path.  PyEventSink wraps a Python handler for the
// callback path.

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

    pub fn parse<S: EventSink>(&mut self, tokens: Vec<Token>, sink: &mut S) -> pyo3::PyResult<()> {
        let n = tokens.len();
        let mut i = 0;
        while i < n && !self.halted {
            let tok = &tokens[i];
            self.flush_errors(tok, sink)?;
            self.last_line = tok.line;
            self.last_col  = tok.column;
            i = self.dispatch(&tokens, i, sink)?;
        }
        if !self.halted {
            self.handle_eof(sink)?;
        }
        Ok(())
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    fn flush_errors<S: EventSink>(&self, tok: &Token, sink: &mut S) -> pyo3::PyResult<()> {
        for le in &tok.errors {
            sink.on_parse_error("lexical", &le.message, le.line, le.column, &le.context, "lexer recovery")?;
        }
        Ok(())
    }

    fn emit<S: EventSink>(
        &self, sink: &mut S,
        etype: &'static str, msg: &str,
        line: u32, col: u32, context: &str, recovery: &str,
    ) -> pyo3::PyResult<()> {
        sink.on_parse_error(etype, msg, line, col, context, recovery)
    }

    // ── Container helpers ─────────────────────────────────────────────────────

    fn cleanup_table_frame<S: EventSink>(
        &mut self, frame: TableFrame,
        line: u32, col: u32, context: &str, sink: &mut S,
    ) -> pyo3::PyResult<()> {
        match frame.state {
            TableState::Colon => {
                if let Some(key) = frame.pending_key {
                    self.emit(sink, "syntactic",
                        &format!("table key {key:?} missing : separator"),
                        line, col, context,
                        "emitted on_table_key; inserted ? placeholder")?;
                    let vt = frame.pending_key_vtype.unwrap_or(ValueType::String);
                    sink.on_table_key(&key, vt)?;
                    sink.add_value("?", ValueType::Placeholder)?;
                }
            }
            TableState::Value => {
                self.emit(sink, "syntactic", "table key has no value",
                    line, col, context, "inserted ? placeholder")?;
                sink.add_value("?", ValueType::Placeholder)?;
            }
            TableState::Key => {}
        }
        Ok(())
    }

    fn close_all_containers<S: EventSink>(
        &mut self, line: u32, col: u32, context: &str, reason: &str, sink: &mut S,
    ) -> pyo3::PyResult<()> {
        while let Some(frame) = self.container_stack.pop() {
            match frame {
                Frame::List => {
                    sink.on_list_end()?;
                    self.emit(sink, "syntactic",
                        &format!("implicitly closed unclosed list ({reason})"),
                        line, col, context, "emitted on_list_end")?;
                }
                Frame::Table(tf) => {
                    self.cleanup_table_frame(tf, line, col, context, sink)?;
                    sink.on_table_end()?;
                    self.emit(sink, "syntactic",
                        &format!("implicitly closed unclosed table ({reason})"),
                        line, col, context, "emitted on_table_end")?;
                }
            }
        }
        self.active_tag = None;
        Ok(())
    }

    fn close_active_tag<S: EventSink>(
        &mut self, line: u32, col: u32, context: &str, reason: &str, sink: &mut S,
    ) -> pyo3::PyResult<()> {
        if let Some(tag) = self.active_tag.take() {
            self.emit(sink, "syntactic",
                &format!("tag {tag:?} has no value ({reason})"),
                line, col, context, "inserted ? placeholder")?;
            sink.add_value("?", ValueType::Placeholder)?;
        }
        Ok(())
    }

    fn close_loop<S: EventSink>(
        &mut self, line: u32, col: u32, context: &str, reason: &str, sink: &mut S,
    ) -> pyo3::PyResult<()> {
        if !self.container_stack.is_empty() {
            self.close_all_containers(line, col, context, reason, sink)?;
            self.emit(sink, "syntactic",
                &format!("unterminated container(s) in loop value ({reason})"),
                line, col, context, "containers implicitly closed")?;
        }
        if !self.loop_has_values {
            let tags_repr = format!("{:?}", self.loop_tags);
            self.emit(sink, "syntactic",
                &format!("loop has tags {tags_repr} but no values"),
                line, col, context, "loop emitted empty")?;
        }
        sink.on_loop_end()?;
        self.in_loop = false;
        self.loop_tags.clear();
        self.loop_has_values = false;
        Ok(())
    }

    fn prepare_for_keyword<S: EventSink>(
        &mut self, line: u32, col: u32, context: &str, keyword: &str, sink: &mut S,
    ) -> pyo3::PyResult<()> {
        if self.in_loop {
            let reason = format!("terminated by {keyword}");
            self.close_loop(line, col, context, &reason, sink)?;
        } else {
            if !self.container_stack.is_empty() {
                let reason = format!("terminated by {keyword}");
                self.close_all_containers(line, col, context, &reason, sink)?;
            }
            let reason = format!("terminated by {keyword}");
            self.close_active_tag(line, col, context, &reason, sink)?;
        }
        Ok(())
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
    ) -> pyo3::PyResult<usize> {
        let tok = &tokens[i];
        match tok.token_type {
            TokenType::Keyword => self.handle_keyword(tokens, i, sink),
            TokenType::Tag     => { self.handle_tag(tok, sink)?; Ok(i + 1) }
            TokenType::Value   => { self.handle_value(tok, sink)?; Ok(i + 1) }
        }
    }

    // ── Keyword handling ──────────────────────────────────────────────────────

    fn handle_keyword<S: EventSink>(
        &mut self, tokens: &[Token], i: usize, sink: &mut S,
    ) -> pyo3::PyResult<usize> {
        let tok = &tokens[i];
        let lower = tok.value.to_ascii_lowercase();

        if lower == "global_" {
            self.handle_global(tok, sink)?;
            return Ok(i + 1);
        }

        if lower == "stop_" {
            if self.in_loop {
                self.close_loop(tok.line, tok.column, &tok.value, "stop_", sink)?;
            } else {
                self.emit(sink, "syntactic", "stop_ outside loop",
                    tok.line, tok.column, &tok.value, "ignored")?;
            }
            return Ok(i + 1);
        }

        if lower == "loop_" {
            self.prepare_for_keyword(tok.line, tok.column, &tok.value, "loop_", sink)?;
            if !self.in_data_block {
                self.emit(sink, "syntactic", "loop_ outside data block",
                    tok.line, tok.column, &tok.value, "continuing")?;
            }
            return self.start_loop(tokens, i, sink);
        }

        // data_ / save_
        let tok_value = tok.value.clone();
        self.prepare_for_keyword(tok.line, tok.column, &tok.value, &tok_value, sink)?;

        if lower.starts_with("data_") {
            let name = tok.value[5..].to_string();
            if name.is_empty() {
                self.emit(sink, "syntactic", "data block with empty name",
                    tok.line, tok.column, &tok.value, "using empty string")?;
            }
            if self.in_save_frame {
                sink.on_save_frame_end()?;
                self.in_save_frame = false;
            }
            self.in_data_block = true;
            sink.on_data_block(&name)?;
        } else if lower.starts_with("save_") && lower.len() > 5 {
            let name = tok.value[5..].to_string();
            if !self.in_data_block {
                self.emit(sink, "syntactic", "save frame outside data block",
                    tok.line, tok.column, &tok.value, "continuing")?;
            }
            if self.in_save_frame {
                self.emit(sink, "syntactic", "nested save frame",
                    tok.line, tok.column, &tok.value,
                    "implicitly closed previous save frame")?;
                sink.on_save_frame_end()?;
            }
            self.in_save_frame = true;
            sink.on_save_frame_start(&name)?;
        } else if lower == "save_" {
            if self.in_save_frame {
                sink.on_save_frame_end()?;
                self.in_save_frame = false;
            } else {
                self.emit(sink, "syntactic", "save_ (frame close) outside save frame",
                    tok.line, tok.column, &tok.value, "ignored")?;
            }
        }

        Ok(i + 1)
    }

    fn handle_global<S: EventSink>(&mut self, tok: &Token, sink: &mut S) -> pyo3::PyResult<()> {
        if self.in_loop {
            self.close_loop(tok.line, tok.column, &tok.value, "global_", sink)?;
        } else {
            if !self.container_stack.is_empty() {
                self.close_all_containers(tok.line, tok.column, &tok.value, "global_", sink)?;
            }
            self.close_active_tag(tok.line, tok.column, &tok.value, "global_", sink)?;
        }
        if self.in_save_frame {
            sink.on_save_frame_end()?;
            self.in_save_frame = false;
        }
        self.emit(sink, "syntactic", "global_ is reserved and not permitted in CIF",
            tok.line, tok.column, &tok.value, "parsing halted")?;
        self.halted = true;
        Ok(())
    }

    fn start_loop<S: EventSink>(
        &mut self, tokens: &[Token], i: usize, sink: &mut S,
    ) -> pyo3::PyResult<usize> {
        let tok = &tokens[i];
        let mut j = i + 1;
        let mut tags: Vec<String> = Vec::new();

        while j < tokens.len() {
            let nxt = &tokens[j];
            if nxt.token_type != TokenType::Tag { break; }
            self.flush_errors(nxt, sink)?;
            tags.push(nxt.value.clone());
            j += 1;
        }

        if tags.is_empty() {
            self.emit(sink, "syntactic", "loop_ with no tags — loop skipped",
                tok.line, tok.column, &tok.value, "loop ignored")?;
            return Ok(j);
        }

        self.in_loop = true;
        self.loop_tags = tags.clone();
        self.loop_has_values = false;

        sink.on_loop_start(&tags)?;
        Ok(j)
    }

    // ── Tag handling ──────────────────────────────────────────────────────────

    fn handle_tag<S: EventSink>(&mut self, tok: &Token, sink: &mut S) -> pyo3::PyResult<()> {
        if self.in_loop {
            let reason = format!("new tag {:?}", tok.value);
            self.close_loop(tok.line, tok.column, &tok.value, &reason, sink)?;
        } else if !self.container_stack.is_empty() {
            self.emit(sink, "syntactic",
                &format!("tag {:?} encountered inside open container", tok.value),
                tok.line, tok.column, &tok.value, "implicitly closing containers")?;
            let reason = format!("tag {:?}", tok.value);
            self.close_all_containers(tok.line, tok.column, &tok.value, &reason, sink)?;
        }

        let reason = format!("new tag {:?}", tok.value);
        self.close_active_tag(tok.line, tok.column, &tok.value, &reason, sink)?;

        if !self.in_data_block {
            self.emit(sink, "syntactic",
                &format!("tag {:?} outside data block", tok.value),
                tok.line, tok.column, &tok.value, "continuing")?;
        }

        self.active_tag = Some(tok.value.clone());
        self.tag_base_depth = self.container_stack.len();
        sink.add_tag(&tok.value)?;
        Ok(())
    }

    // ── Value handling ────────────────────────────────────────────────────────

    fn handle_value<S: EventSink>(&mut self, tok: &Token, sink: &mut S) -> pyo3::PyResult<()> {
        let v = tok.value.as_str();
        let vt = tok.value_type.unwrap_or(ValueType::String);

        if v == "[" { return self.open_list(tok, sink); }
        if v == "]" { return self.close_list(tok, sink); }
        if v == "{" { return self.open_table(tok, sink); }
        if v == "}" { return self.close_table(tok, sink); }
        if v == ":" {
            let in_table = matches!(self.container_stack.last(), Some(Frame::Table(_)));
            if in_table {
                return self.handle_table_colon(tok, sink);
            }
        }

        self.dispatch_scalar_value(v, vt, tok, sink)
    }

    fn ensure_value_context<S: EventSink>(&mut self, tok: &Token, sink: &mut S) -> pyo3::PyResult<()> {
        if !self.in_loop && self.active_tag.is_none() && self.container_stack.is_empty() {
            self.emit(sink, "syntactic", "container without preceding tag",
                tok.line, tok.column, &tok.value, "attached to _pycifparse_error_value")?;
            sink.add_tag("_pycifparse_error_value")?;
            self.active_tag = Some("_pycifparse_error_value".to_string());
            self.tag_base_depth = 0;
        }
        Ok(())
    }

    fn notify_parent_table_of_container_open<S: EventSink>(
        &mut self, tok: &Token, sink: &mut S,
    ) -> pyo3::PyResult<()> {
        let table_action: Option<(TableState, Option<String>, Option<ValueType>)> =
            if let Some(Frame::Table(ref f)) = self.container_stack.last() {
                Some((f.state.clone(), f.pending_key.clone(), f.pending_key_vtype))
            } else {
                None
            };

        let Some((state, pending_key, pending_key_vtype)) = table_action else {
            return Ok(());
        };

        match state {
            TableState::Key => {
                self.emit(sink, "syntactic", "container in table key position",
                    tok.line, tok.column, &tok.value,
                    "treating container as table value (no key)")?;
                if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                    f.state = TableState::Value;
                }
            }
            TableState::Colon => {
                if let Some(key) = pending_key {
                    self.emit(sink, "syntactic",
                        &format!("table key {key:?} missing : separator"),
                        tok.line, tok.column, &tok.value,
                        "emitted on_table_key; treating container as value")?;
                    let vt = pending_key_vtype.unwrap_or(ValueType::String);
                    sink.on_table_key(&key, vt)?;
                    if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                        f.pending_key = None;
                        f.state = TableState::Value;
                    }
                }
            }
            TableState::Value => {}
        }
        Ok(())
    }

    fn open_list<S: EventSink>(&mut self, tok: &Token, sink: &mut S) -> pyo3::PyResult<()> {
        self.ensure_value_context(tok, sink)?;
        self.notify_parent_table_of_container_open(tok, sink)?;
        self.container_stack.push(Frame::List);
        sink.on_list_start()?;
        Ok(())
    }

    fn close_list<S: EventSink>(&mut self, tok: &Token, sink: &mut S) -> pyo3::PyResult<()> {
        if !matches!(self.container_stack.last(), Some(Frame::List)) {
            self.emit(sink, "syntactic", "unexpected ] — no open list",
                tok.line, tok.column, &tok.value, "ignored")?;
            return Ok(());
        }
        self.container_stack.pop();
        sink.on_list_end()?;
        self.after_close_container();
        Ok(())
    }

    fn open_table<S: EventSink>(&mut self, tok: &Token, sink: &mut S) -> pyo3::PyResult<()> {
        self.ensure_value_context(tok, sink)?;
        self.notify_parent_table_of_container_open(tok, sink)?;
        self.container_stack.push(Frame::Table(TableFrame {
            state: TableState::Key,
            pending_key: None,
            pending_key_vtype: None,
            pending_key_line: 0,
            pending_key_col: 0,
            pending_key_width: None,
        }));
        sink.on_table_start()?;
        Ok(())
    }

    fn close_table<S: EventSink>(&mut self, tok: &Token, sink: &mut S) -> pyo3::PyResult<()> {
        if !matches!(self.container_stack.last(), Some(Frame::Table(_))) {
            self.emit(sink, "syntactic", "unexpected } — no open table",
                tok.line, tok.column, &tok.value, "ignored")?;
            return Ok(());
        }
        let frame = self.container_stack.pop();
        if let Some(Frame::Table(tf)) = frame {
            self.cleanup_table_frame(tf, tok.line, tok.column, &tok.value, sink)?;
        }
        sink.on_table_end()?;
        self.after_close_container();
        Ok(())
    }

    fn handle_table_colon<S: EventSink>(
        &mut self, tok: &Token, sink: &mut S,
    ) -> pyo3::PyResult<()> {
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
        else { return Ok(()); };

        match state {
            TableState::Colon => {
                if let Some(width) = pk_width {
                    let adj_col = pk_col + width;
                    if tok.line != pk_line || tok.column != adj_col {
                        let key = pending_key.as_deref().unwrap_or("");
                        self.emit(sink, "syntactic",
                            &format!("whitespace between table key {key:?} and : separator"),
                            tok.line, tok.column, &tok.value, "accepted")?;
                    }
                }
                let key = pending_key.unwrap_or_default();
                let vt  = pending_key_vtype.unwrap_or(ValueType::String);
                sink.on_table_key(&key, vt)?;
                if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                    f.pending_key = None;
                    f.pending_key_vtype = None;
                    f.pending_key_width = None;
                    f.state = TableState::Value;
                }
            }
            TableState::Key => {
                self.emit(sink, "syntactic", "unexpected : in table — no pending key",
                    tok.line, tok.column, &tok.value, "ignored")?;
            }
            TableState::Value => {
                self.emit(sink, "syntactic", "unexpected : in table value position",
                    tok.line, tok.column, &tok.value, "ignored")?;
            }
        }
        Ok(())
    }

    fn dispatch_scalar_in_table<S: EventSink>(
        &mut self, value: &str, vt: ValueType, tok: &Token, sink: &mut S,
    ) -> pyo3::PyResult<()> {
        let state = if let Some(Frame::Table(ref f)) = self.container_stack.last() {
            f.state.clone()
        } else { return Ok(()); };

        match state {
            TableState::Key => {
                let quoted = matches!(vt,
                    ValueType::SingleQuoted | ValueType::DoubleQuoted |
                    ValueType::TripleSingleQuoted | ValueType::TripleDoubleQuoted);
                if !quoted {
                    self.emit(sink, "syntactic",
                        &format!("table key must be a quoted string, got unquoted: {value:?}"),
                        tok.line, tok.column, &tok.value, "treating as key anyway")?;
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
                    "emitted on_table_key; treating current token as value")?;
                let kv = kv.unwrap_or(ValueType::String);
                sink.on_table_key(&key, kv)?;
                sink.add_value(value, vt)?;
                if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                    f.pending_key = None;
                    f.pending_key_vtype = None;
                    f.pending_key_width = None;
                    f.state = TableState::Key;
                }
            }
            TableState::Value => {
                sink.add_value(value, vt)?;
                if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                    f.state = TableState::Key;
                }
            }
        }
        Ok(())
    }

    fn dispatch_scalar_value<S: EventSink>(
        &mut self, value: &str, vt: ValueType, tok: &Token, sink: &mut S,
    ) -> pyo3::PyResult<()> {
        let in_table = matches!(self.container_stack.last(), Some(Frame::Table(_)));
        let in_list  = matches!(self.container_stack.last(), Some(Frame::List));

        if in_table {
            return self.dispatch_scalar_in_table(value, vt, tok, sink);
        }
        if in_list {
            sink.add_value(value, vt)?;
            return Ok(());
        }
        if self.in_loop {
            sink.add_value(value, vt)?;
            self.loop_has_values = true;
            return Ok(());
        }
        if self.active_tag.is_some() {
            sink.add_value(value, vt)?;
            self.active_tag = None;
            return Ok(());
        }

        // Orphan value
        self.emit(sink, "syntactic",
            &format!("value {value:?} has no preceding tag"),
            tok.line, tok.column, &tok.value, "attached to _pycifparse_error_value")?;
        sink.add_tag("_pycifparse_error_value")?;
        sink.add_value(value, vt)?;
        Ok(())
    }

    // ── EOF ───────────────────────────────────────────────────────────────────

    fn handle_eof<S: EventSink>(&mut self, sink: &mut S) -> pyo3::PyResult<()> {
        let line = self.last_line;
        let col  = self.last_col;

        if self.in_loop {
            self.close_loop(line, col, "EOF", "EOF", sink)?;
        }

        while let Some(frame) = self.container_stack.pop() {
            match frame {
                Frame::List => {
                    sink.on_list_end()?;
                    self.emit(sink, "syntactic", "unterminated list at EOF",
                        line, col, "", "emitted on_list_end")?;
                }
                Frame::Table(tf) => {
                    self.cleanup_table_frame(tf, line, col, "EOF", sink)?;
                    sink.on_table_end()?;
                    self.emit(sink, "syntactic", "unterminated table at EOF",
                        line, col, "", "emitted on_table_end")?;
                }
            }
        }

        if let Some(tag) = self.active_tag.take() {
            self.emit(sink, "syntactic",
                &format!("tag {tag:?} has no value at EOF"),
                line, col, &tag, "inserted ? placeholder")?;
            sink.add_value("?", ValueType::Placeholder)?;
        }

        if self.in_save_frame {
            self.emit(sink, "syntactic", "unterminated save frame at EOF",
                line, col, "", "emitted on_save_frame_end")?;
            sink.on_save_frame_end()?;
            self.in_save_frame = false;
        }
        Ok(())
    }
}
