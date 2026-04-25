// CIF Parser — direct port of src/pycifparse/parser/parser.py.
//
// Calls methods on a Python CifParserEvents handler via PyO3.
// ParseError Python dataclass instances are created from Rust for each error.
//
// Borrow-checker note: wherever we need to both read from container_stack
// and call &mut self methods, we extract the needed data (clone) into locals
// first, drop the borrow, then act. This is the standard NLL-safe pattern.

use pyo3::prelude::*;

use crate::error::RustParseError;
use crate::lexer::{Token, TokenType, ValueType};

// ─────────────────────────────────────────────────────────────────────────────
// Pre-fetched Python objects
// ─────────────────────────────────────────────────────────────────────────────

pub struct PyCtx<'py> {
    pub handler: &'py Bound<'py, PyAny>,
    pub parse_error_cls: Bound<'py, PyAny>,
    pub vt_multiline_string:     Bound<'py, PyAny>,
    pub vt_triple_double_quoted: Bound<'py, PyAny>,
    pub vt_triple_single_quoted: Bound<'py, PyAny>,
    pub vt_double_quoted:        Bound<'py, PyAny>,
    pub vt_single_quoted:        Bound<'py, PyAny>,
    pub vt_string:               Bound<'py, PyAny>,
    pub vt_placeholder:          Bound<'py, PyAny>,
}

impl<'py> PyCtx<'py> {
    pub fn make_parse_error(
        &self,
        error_type: &str,
        message: &str,
        line: u32,
        column: u32,
        context: &str,
        recovery_action: &str,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.parse_error_cls.call1((
            error_type, message, line as usize, column as usize, context, recovery_action,
        ))
    }

    pub fn make_parse_error_from_rust(&self, e: &RustParseError) -> PyResult<Bound<'py, PyAny>> {
        self.make_parse_error(e.error_type, &e.message, e.line, e.column, &e.context, &e.recovery_action)
    }

    pub fn value_type_obj(&self, vt: ValueType) -> &Bound<'py, PyAny> {
        match vt {
            ValueType::MultilineString    => &self.vt_multiline_string,
            ValueType::TripleDoubleQuoted => &self.vt_triple_double_quoted,
            ValueType::TripleSingleQuoted => &self.vt_triple_single_quoted,
            ValueType::DoubleQuoted       => &self.vt_double_quoted,
            ValueType::SingleQuoted       => &self.vt_single_quoted,
            ValueType::String             => &self.vt_string,
            ValueType::Placeholder        => &self.vt_placeholder,
        }
    }
}

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
    // token width for adjacency check; None means "unknown / skip check"
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

    pub fn parse(&mut self, tokens: Vec<Token>, ctx: &PyCtx) -> PyResult<()> {
        let n = tokens.len();
        let mut i = 0;
        while i < n && !self.halted {
            let tok = &tokens[i];
            self.flush_errors(tok, ctx)?;
            self.last_line = tok.line;
            self.last_col  = tok.column;
            i = self.dispatch(&tokens, i, ctx)?;
        }
        if !self.halted {
            self.handle_eof(ctx)?;
        }
        Ok(())
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    fn flush_errors(&self, tok: &Token, ctx: &PyCtx) -> PyResult<()> {
        for le in &tok.errors {
            let pe = ctx.make_parse_error("lexical", &le.message, le.line, le.column, &le.context, "lexer recovery")?;
            ctx.handler.call_method1("on_error", (pe,))?;
        }
        Ok(())
    }

    /// Emit a parse error via handler.on_error.
    fn emit(&self, ctx: &PyCtx, etype: &str, msg: &str, line: u32, col: u32, context: &str, recovery: &str) -> PyResult<()> {
        let pe = ctx.make_parse_error(etype, msg, line, col, context, recovery)?;
        ctx.handler.call_method1("on_error", (pe,))?;
        Ok(())
    }

    // ── Container helpers ─────────────────────────────────────────────────────

    /// Consume an already-popped TableFrame, emitting any needed corrections.
    /// Takes the frame by value so there is no borrow-of-self conflict.
    fn cleanup_table_frame(&mut self, frame: TableFrame, line: u32, col: u32, context: &str, ctx: &PyCtx) -> PyResult<()> {
        match frame.state {
            TableState::Colon => {
                if let Some(key) = frame.pending_key {
                    self.emit(ctx, "syntactic",
                        &format!("table key {key:?} missing : separator"),
                        line, col, context,
                        "emitted on_table_key; inserted ? placeholder")?;
                    let vt = frame.pending_key_vtype.map(|v| ctx.value_type_obj(v).clone())
                        .unwrap_or_else(|| ctx.vt_string.clone());
                    ctx.handler.call_method1("on_table_key", (key, vt))?;
                    ctx.handler.call_method1("add_value", ("?", ctx.vt_placeholder.clone()))?;
                }
            }
            TableState::Value => {
                self.emit(ctx, "syntactic", "table key has no value",
                    line, col, context, "inserted ? placeholder")?;
                ctx.handler.call_method1("add_value", ("?", ctx.vt_placeholder.clone()))?;
            }
            TableState::Key => {}
        }
        Ok(())
    }

    fn close_all_containers(&mut self, line: u32, col: u32, context: &str, reason: &str, ctx: &PyCtx) -> PyResult<()> {
        while let Some(frame) = self.container_stack.pop() {
            match frame {
                Frame::List => {
                    ctx.handler.call_method1("on_list_end", ())?;
                    self.emit(ctx, "syntactic",
                        &format!("implicitly closed unclosed list ({reason})"),
                        line, col, context, "emitted on_list_end")?;
                }
                Frame::Table(tf) => {
                    self.cleanup_table_frame(tf, line, col, context, ctx)?;
                    ctx.handler.call_method1("on_table_end", ())?;
                    self.emit(ctx, "syntactic",
                        &format!("implicitly closed unclosed table ({reason})"),
                        line, col, context, "emitted on_table_end")?;
                }
            }
        }
        self.active_tag = None;
        Ok(())
    }

    fn close_active_tag(&mut self, line: u32, col: u32, context: &str, reason: &str, ctx: &PyCtx) -> PyResult<()> {
        if let Some(tag) = self.active_tag.take() {
            self.emit(ctx, "syntactic",
                &format!("tag {tag:?} has no value ({reason})"),
                line, col, context, "inserted ? placeholder")?;
            ctx.handler.call_method1("add_value", ("?", ctx.vt_placeholder.clone()))?;
        }
        Ok(())
    }

    fn close_loop(&mut self, line: u32, col: u32, context: &str, reason: &str, ctx: &PyCtx) -> PyResult<()> {
        if !self.container_stack.is_empty() {
            self.close_all_containers(line, col, context, reason, ctx)?;
            self.emit(ctx, "syntactic",
                &format!("unterminated container(s) in loop value ({reason})"),
                line, col, context, "containers implicitly closed")?;
        }
        if !self.loop_has_values {
            let tags_repr = format!("{:?}", self.loop_tags);
            self.emit(ctx, "syntactic",
                &format!("loop has tags {tags_repr} but no values"),
                line, col, context, "loop emitted empty")?;
        }
        ctx.handler.call_method1("on_loop_end", ())?;
        self.in_loop = false;
        self.loop_tags.clear();
        self.loop_has_values = false;
        Ok(())
    }

    fn prepare_for_keyword(&mut self, line: u32, col: u32, context: &str, keyword: &str, ctx: &PyCtx) -> PyResult<()> {
        if self.in_loop {
            let reason = format!("terminated by {keyword}");
            self.close_loop(line, col, context, &reason, ctx)?;
        } else {
            if !self.container_stack.is_empty() {
                let reason = format!("terminated by {keyword}");
                self.close_all_containers(line, col, context, &reason, ctx)?;
            }
            let reason = format!("terminated by {keyword}");
            self.close_active_tag(line, col, context, &reason, ctx)?;
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

    fn dispatch(&mut self, tokens: &[Token], i: usize, ctx: &PyCtx) -> PyResult<usize> {
        let tok = &tokens[i];
        match tok.token_type {
            TokenType::Keyword => self.handle_keyword(tokens, i, ctx),
            TokenType::Tag     => { self.handle_tag(tok, ctx)?; Ok(i + 1) }
            TokenType::Value   => { self.handle_value(tok, ctx)?; Ok(i + 1) }
        }
    }

    // ── Keyword handling ──────────────────────────────────────────────────────

    fn handle_keyword(&mut self, tokens: &[Token], i: usize, ctx: &PyCtx) -> PyResult<usize> {
        let tok = &tokens[i];
        let lower = tok.value.to_ascii_lowercase();

        if lower == "global_" {
            self.handle_global(tok, ctx)?;
            return Ok(i + 1);
        }

        if lower == "stop_" {
            if self.in_loop {
                self.close_loop(tok.line, tok.column, &tok.value, "stop_", ctx)?;
            } else {
                self.emit(ctx, "syntactic", "stop_ outside loop",
                    tok.line, tok.column, &tok.value, "ignored")?;
            }
            return Ok(i + 1);
        }

        if lower == "loop_" {
            self.prepare_for_keyword(tok.line, tok.column, &tok.value, "loop_", ctx)?;
            if !self.in_data_block {
                self.emit(ctx, "syntactic", "loop_ outside data block",
                    tok.line, tok.column, &tok.value, "continuing")?;
            }
            return self.start_loop(tokens, i, ctx);
        }

        // data_ / save_
        self.prepare_for_keyword(tok.line, tok.column, &tok.value, &tok.value.clone(), ctx)?;

        if lower.starts_with("data_") {
            let name = tok.value[5..].to_string();
            if name.is_empty() {
                self.emit(ctx, "syntactic", "data block with empty name",
                    tok.line, tok.column, &tok.value, "using empty string")?;
            }
            if self.in_save_frame {
                ctx.handler.call_method1("on_save_frame_end", ())?;
                self.in_save_frame = false;
            }
            self.in_data_block = true;
            ctx.handler.call_method1("on_data_block", (name.as_str(),))?;
        } else if lower.starts_with("save_") && lower.len() > 5 {
            let name = tok.value[5..].to_string();
            if !self.in_data_block {
                self.emit(ctx, "syntactic", "save frame outside data block",
                    tok.line, tok.column, &tok.value, "continuing")?;
            }
            if self.in_save_frame {
                self.emit(ctx, "syntactic", "nested save frame",
                    tok.line, tok.column, &tok.value,
                    "implicitly closed previous save frame")?;
                ctx.handler.call_method1("on_save_frame_end", ())?;
            }
            self.in_save_frame = true;
            ctx.handler.call_method1("on_save_frame_start", (name.as_str(),))?;
        } else if lower == "save_" {
            if self.in_save_frame {
                ctx.handler.call_method1("on_save_frame_end", ())?;
                self.in_save_frame = false;
            } else {
                self.emit(ctx, "syntactic", "save_ (frame close) outside save frame",
                    tok.line, tok.column, &tok.value, "ignored")?;
            }
        }

        Ok(i + 1)
    }

    fn handle_global(&mut self, tok: &Token, ctx: &PyCtx) -> PyResult<()> {
        if self.in_loop {
            self.close_loop(tok.line, tok.column, &tok.value, "global_", ctx)?;
        } else {
            if !self.container_stack.is_empty() {
                self.close_all_containers(tok.line, tok.column, &tok.value, "global_", ctx)?;
            }
            self.close_active_tag(tok.line, tok.column, &tok.value, "global_", ctx)?;
        }
        if self.in_save_frame {
            ctx.handler.call_method1("on_save_frame_end", ())?;
            self.in_save_frame = false;
        }
        self.emit(ctx, "syntactic", "global_ is reserved and not permitted in CIF",
            tok.line, tok.column, &tok.value, "parsing halted")?;
        self.halted = true;
        Ok(())
    }

    fn start_loop(&mut self, tokens: &[Token], i: usize, ctx: &PyCtx) -> PyResult<usize> {
        let tok = &tokens[i];
        let mut j = i + 1;
        let mut tags: Vec<String> = Vec::new();

        while j < tokens.len() {
            let nxt = &tokens[j];
            if nxt.token_type != TokenType::Tag { break; }
            self.flush_errors(nxt, ctx)?;
            tags.push(nxt.value.clone());
            j += 1;
        }

        if tags.is_empty() {
            self.emit(ctx, "syntactic", "loop_ with no tags — loop skipped",
                tok.line, tok.column, &tok.value, "loop ignored")?;
            return Ok(j);
        }

        self.in_loop = true;
        self.loop_tags = tags.clone();
        self.loop_has_values = false;

        let py_tags: Vec<&str> = tags.iter().map(String::as_str).collect();
        ctx.handler.call_method1("on_loop_start", (py_tags,))?;
        Ok(j)
    }

    // ── Tag handling ──────────────────────────────────────────────────────────

    fn handle_tag(&mut self, tok: &Token, ctx: &PyCtx) -> PyResult<()> {
        if self.in_loop {
            let reason = format!("new tag {:?}", tok.value);
            self.close_loop(tok.line, tok.column, &tok.value, &reason, ctx)?;
        } else if !self.container_stack.is_empty() {
            self.emit(ctx, "syntactic",
                &format!("tag {:?} encountered inside open container", tok.value),
                tok.line, tok.column, &tok.value, "implicitly closing containers")?;
            let reason = format!("tag {:?}", tok.value);
            self.close_all_containers(tok.line, tok.column, &tok.value, &reason, ctx)?;
        }

        let reason = format!("new tag {:?}", tok.value);
        self.close_active_tag(tok.line, tok.column, &tok.value, &reason, ctx)?;

        if !self.in_data_block {
            self.emit(ctx, "syntactic",
                &format!("tag {:?} outside data block", tok.value),
                tok.line, tok.column, &tok.value, "continuing")?;
        }

        self.active_tag = Some(tok.value.clone());
        self.tag_base_depth = self.container_stack.len();
        ctx.handler.call_method1("add_tag", (tok.value.as_str(),))?;
        Ok(())
    }

    // ── Value handling ────────────────────────────────────────────────────────

    fn handle_value(&mut self, tok: &Token, ctx: &PyCtx) -> PyResult<()> {
        let v = tok.value.as_str();
        let vt = tok.value_type.unwrap_or(ValueType::String);

        // CIF 2.0 structural tokens
        if v == "[" { return self.open_list(tok, ctx); }
        if v == "]" { return self.close_list(tok, ctx); }
        if v == "{" { return self.open_table(tok, ctx); }
        if v == "}" { return self.close_table(tok, ctx); }
        if v == ":" {
            let in_table = matches!(self.container_stack.last(), Some(Frame::Table(_)));
            if in_table {
                return self.handle_table_colon(tok, ctx);
            }
        }

        self.dispatch_scalar_value(v, vt, tok, ctx)
    }

    fn ensure_value_context(&mut self, tok: &Token, ctx: &PyCtx) -> PyResult<()> {
        if !self.in_loop && self.active_tag.is_none() && self.container_stack.is_empty() {
            self.emit(ctx, "syntactic", "container without preceding tag",
                tok.line, tok.column, &tok.value, "attached to _pycifparse_error_value")?;
            ctx.handler.call_method1("add_tag", ("_pycifparse_error_value",))?;
            self.active_tag = Some("_pycifparse_error_value".to_string());
            self.tag_base_depth = 0;
        }
        Ok(())
    }

    /// Prepare the parent table (if any) for a child container opening.
    /// Extracts state before any method calls to avoid borrow conflicts.
    fn notify_parent_table_of_container_open(&mut self, tok: &Token, ctx: &PyCtx) -> PyResult<()> {
        // Step 1: extract state — no borrow held after this block
        let table_action: Option<(TableState, Option<String>, Option<ValueType>)> =
            if let Some(Frame::Table(ref f)) = self.container_stack.last() {
                Some((f.state.clone(), f.pending_key.clone(), f.pending_key_vtype))
            } else {
                None
            };

        let Some((state, pending_key, pending_key_vtype)) = table_action else {
            return Ok(());
        };

        // Step 2: act (no active borrow)
        match state {
            TableState::Key => {
                self.emit(ctx, "syntactic", "container in table key position",
                    tok.line, tok.column, &tok.value,
                    "treating container as table value (no key)")?;
                if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                    f.state = TableState::Value;
                }
            }
            TableState::Colon => {
                if let Some(key) = pending_key {
                    self.emit(ctx, "syntactic",
                        &format!("table key {key:?} missing : separator"),
                        tok.line, tok.column, &tok.value,
                        "emitted on_table_key; treating container as value")?;
                    let vt = pending_key_vtype
                        .map(|v| ctx.value_type_obj(v).clone())
                        .unwrap_or_else(|| ctx.vt_string.clone());
                    ctx.handler.call_method1("on_table_key", (key, vt))?;
                    if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                        f.pending_key = None;
                        f.state = TableState::Value;
                    }
                }
            }
            TableState::Value => {} // normal path
        }
        Ok(())
    }

    fn open_list(&mut self, tok: &Token, ctx: &PyCtx) -> PyResult<()> {
        self.ensure_value_context(tok, ctx)?;
        self.notify_parent_table_of_container_open(tok, ctx)?;
        self.container_stack.push(Frame::List);
        ctx.handler.call_method1("on_list_start", ())?;
        Ok(())
    }

    fn close_list(&mut self, tok: &Token, ctx: &PyCtx) -> PyResult<()> {
        if !matches!(self.container_stack.last(), Some(Frame::List)) {
            self.emit(ctx, "syntactic", "unexpected ] — no open list",
                tok.line, tok.column, &tok.value, "ignored")?;
            return Ok(());
        }
        self.container_stack.pop();
        ctx.handler.call_method1("on_list_end", ())?;
        self.after_close_container();
        Ok(())
    }

    fn open_table(&mut self, tok: &Token, ctx: &PyCtx) -> PyResult<()> {
        self.ensure_value_context(tok, ctx)?;
        self.notify_parent_table_of_container_open(tok, ctx)?;
        self.container_stack.push(Frame::Table(TableFrame {
            state: TableState::Key,
            pending_key: None,
            pending_key_vtype: None,
            pending_key_line: 0,
            pending_key_col: 0,
            pending_key_width: None,
        }));
        ctx.handler.call_method1("on_table_start", ())?;
        Ok(())
    }

    fn close_table(&mut self, tok: &Token, ctx: &PyCtx) -> PyResult<()> {
        if !matches!(self.container_stack.last(), Some(Frame::Table(_))) {
            self.emit(ctx, "syntactic", "unexpected } — no open table",
                tok.line, tok.column, &tok.value, "ignored")?;
            return Ok(());
        }
        let frame = self.container_stack.pop();
        if let Some(Frame::Table(tf)) = frame {
            self.cleanup_table_frame(tf, tok.line, tok.column, &tok.value, ctx)?;
        }
        ctx.handler.call_method1("on_table_end", ())?;
        self.after_close_container();
        Ok(())
    }

    /// Handle ':' inside a table — extract state first to avoid borrow conflicts.
    fn handle_table_colon(&mut self, tok: &Token, ctx: &PyCtx) -> PyResult<()> {
        // Step 1: extract state
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

        let Some((state, pending_key, pending_key_vtype, pk_line, pk_col, pk_width)) = info else {
            return Ok(());
        };

        // Step 2: act
        match state {
            TableState::Colon => {
                // Adjacency check
                if let Some(width) = pk_width {
                    let adj_col = pk_col + width;
                    if tok.line != pk_line || tok.column != adj_col {
                        let key = pending_key.as_deref().unwrap_or("");
                        self.emit(ctx, "syntactic",
                            &format!("whitespace between table key {key:?} and : separator"),
                            tok.line, tok.column, &tok.value, "accepted")?;
                    }
                }
                let key = pending_key.unwrap_or_default();
                let vt  = pending_key_vtype
                    .map(|v| ctx.value_type_obj(v).clone())
                    .unwrap_or_else(|| ctx.vt_string.clone());
                ctx.handler.call_method1("on_table_key", (key, vt))?;
                // Step 3: update
                if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                    f.pending_key = None;
                    f.pending_key_vtype = None;
                    f.pending_key_width = None;
                    f.state = TableState::Value;
                }
            }
            TableState::Key => {
                self.emit(ctx, "syntactic", "unexpected : in table — no pending key",
                    tok.line, tok.column, &tok.value, "ignored")?;
            }
            TableState::Value => {
                self.emit(ctx, "syntactic", "unexpected : in table value position",
                    tok.line, tok.column, &tok.value, "ignored")?;
            }
        }
        Ok(())
    }

    /// Route a scalar value inside a table — extract state first.
    fn dispatch_scalar_in_table(&mut self, value: &str, vt: ValueType, tok: &Token, ctx: &PyCtx) -> PyResult<()> {
        // Step 1: extract state
        let state = if let Some(Frame::Table(ref f)) = self.container_stack.last() {
            f.state.clone()
        } else { return Ok(()); };

        match state {
            TableState::Key => {
                let quoted = matches!(vt,
                    ValueType::SingleQuoted | ValueType::DoubleQuoted |
                    ValueType::TripleSingleQuoted | ValueType::TripleDoubleQuoted);
                if !quoted {
                    self.emit(ctx, "syntactic",
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
                // Extract pending key info
                let (key, kv) = if let Some(Frame::Table(ref f)) = self.container_stack.last() {
                    (f.pending_key.clone().unwrap_or_default(), f.pending_key_vtype)
                } else { (String::new(), None) };

                self.emit(ctx, "syntactic",
                    &format!("table key {key:?} not followed by : separator"),
                    tok.line, tok.column, &tok.value,
                    "emitted on_table_key; treating current token as value")?;
                let kv_py = kv.map(|v| ctx.value_type_obj(v).clone())
                    .unwrap_or_else(|| ctx.vt_string.clone());
                ctx.handler.call_method1("on_table_key", (key, kv_py))?;
                let vt_py = ctx.value_type_obj(vt).clone();
                ctx.handler.call_method1("add_value", (value, vt_py))?;
                if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                    f.pending_key = None;
                    f.pending_key_vtype = None;
                    f.pending_key_width = None;
                    f.state = TableState::Key;
                }
            }
            TableState::Value => {
                let vt_py = ctx.value_type_obj(vt).clone();
                ctx.handler.call_method1("add_value", (value, vt_py))?;
                if let Some(Frame::Table(ref mut f)) = self.container_stack.last_mut() {
                    f.state = TableState::Key;
                }
            }
        }
        Ok(())
    }

    fn dispatch_scalar_value(&mut self, value: &str, vt: ValueType, tok: &Token, ctx: &PyCtx) -> PyResult<()> {
        // Check top of container stack first (without holding a borrow)
        let in_table = matches!(self.container_stack.last(), Some(Frame::Table(_)));
        let in_list  = matches!(self.container_stack.last(), Some(Frame::List));

        if in_table {
            return self.dispatch_scalar_in_table(value, vt, tok, ctx);
        }
        if in_list {
            let vt_py = ctx.value_type_obj(vt).clone();
            ctx.handler.call_method1("add_value", (value, vt_py))?;
            return Ok(());
        }
        if self.in_loop {
            let vt_py = ctx.value_type_obj(vt).clone();
            ctx.handler.call_method1("add_value", (value, vt_py))?;
            self.loop_has_values = true;
            return Ok(());
        }
        if self.active_tag.is_some() {
            let vt_py = ctx.value_type_obj(vt).clone();
            ctx.handler.call_method1("add_value", (value, vt_py))?;
            self.active_tag = None;
            return Ok(());
        }

        // Orphan value
        self.emit(ctx, "syntactic",
            &format!("value {value:?} has no preceding tag"),
            tok.line, tok.column, &tok.value, "attached to _pycifparse_error_value")?;
        ctx.handler.call_method1("add_tag", ("_pycifparse_error_value",))?;
        let vt_py = ctx.value_type_obj(vt).clone();
        ctx.handler.call_method1("add_value", (value, vt_py))?;
        Ok(())
    }

    // ── EOF ───────────────────────────────────────────────────────────────────

    fn handle_eof(&mut self, ctx: &PyCtx) -> PyResult<()> {
        let line = self.last_line;
        let col  = self.last_col;

        if self.in_loop {
            self.close_loop(line, col, "EOF", "EOF", ctx)?;
        }

        while let Some(frame) = self.container_stack.pop() {
            match frame {
                Frame::List => {
                    ctx.handler.call_method1("on_list_end", ())?;
                    self.emit(ctx, "syntactic", "unterminated list at EOF",
                        line, col, "", "emitted on_list_end")?;
                }
                Frame::Table(tf) => {
                    self.cleanup_table_frame(tf, line, col, "EOF", ctx)?;
                    ctx.handler.call_method1("on_table_end", ())?;
                    self.emit(ctx, "syntactic", "unterminated table at EOF",
                        line, col, "", "emitted on_table_end")?;
                }
            }
        }

        if let Some(tag) = self.active_tag.take() {
            self.emit(ctx, "syntactic",
                &format!("tag {tag:?} has no value at EOF"),
                line, col, &tag, "inserted ? placeholder")?;
            ctx.handler.call_method1("add_value", ("?", ctx.vt_placeholder.clone()))?;
        }

        if self.in_save_frame {
            self.emit(ctx, "syntactic", "unterminated save frame at EOF",
                line, col, "", "emitted on_save_frame_end")?;
            ctx.handler.call_method1("on_save_frame_end", ())?;
            self.in_save_frame = false;
        }
        Ok(())
    }
}
