// RawBuilder — pure-Rust EventSink that accumulates parser events into a
// ParsedCif tree, then converts it to Python dicts/lists in one shot.
//
// No Python calls are made during parsing; all PyO3 work happens in to_python().
// This eliminates the per-token Rust→Python boundary cost.

use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString};

use crate::error::RustParseError;
use crate::event_sink::EventSink;
use crate::lexer::ValueType;
use crate::textfield::transform_multiline;
use crate::version::CifVersion;

// ─────────────────────────────────────────────────────────────────────────────
// Internal value tree
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug)]
pub enum RawValue {
    Str(String),
    List(Vec<RawValue>),
    Table(Vec<(String, RawValue)>), // ordered key-value pairs
}

// ─────────────────────────────────────────────────────────────────────────────
// Frame data (shared between blocks and save frames)
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Default)]
pub struct FrameData {
    pub tag_order: Vec<String>,
    pub tags: HashMap<String, Vec<RawValue>>,
    pub loops: Vec<Vec<String>>,
}

impl FrameData {
    fn append_value(&mut self, tag: &str, value: RawValue) {
        if !self.tags.contains_key(tag) {
            self.tag_order.push(tag.to_string());
            self.tags.insert(tag.to_string(), Vec::new());
        }
        self.tags.get_mut(tag).unwrap().push(value);
    }

    fn add_loop(&mut self, tags: &[String], buffers: &mut HashMap<String, Vec<RawValue>>) {
        self.loops.push(tags.to_vec());
        for tag in tags {
            if !self.tags.contains_key(tag.as_str()) {
                self.tag_order.push(tag.clone());
            }
            let values = buffers.remove(tag).unwrap_or_default();
            self.tags.insert(tag.clone(), values);
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Completed structures
// ─────────────────────────────────────────────────────────────────────────────

pub struct ParsedSaveFrame {
    pub name: String,
    pub data: FrameData,
}

pub struct ParsedBlock {
    pub name: String,
    pub data: FrameData,
    pub save_frames: Vec<ParsedSaveFrame>,
}

pub struct ParsedCif {
    pub version: CifVersion,
    pub blocks: Vec<ParsedBlock>,
    pub errors: Vec<RustParseError>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Container stack
// ─────────────────────────────────────────────────────────────────────────────

enum ContainerFrame {
    List(Vec<RawValue>),
    Table {
        data: Vec<(String, RawValue)>,
        current_key: Option<String>,
    },
}

// ─────────────────────────────────────────────────────────────────────────────
// RawBuilder
// ─────────────────────────────────────────────────────────────────────────────

pub struct RawBuilder {
    version: CifVersion,
    errors: Vec<RustParseError>,
    mode_strict: bool,
    stopped: bool,

    // Block / save-frame state
    current_block: Option<(String, FrameData, Vec<ParsedSaveFrame>)>,
    current_save_frame: Option<(String, FrameData)>,

    // Duplicate-name tracking
    seen_block_names: HashSet<String>,
    seen_save_frame_names: HashSet<String>,

    // Active tag
    active_tag: Option<String>,

    // Loop state
    in_loop: bool,
    loop_tags: Vec<String>,
    loop_value_index: usize,
    loop_buffers: HashMap<String, Vec<RawValue>>,

    // Container stack
    container_stack: Vec<ContainerFrame>,

    // Output
    completed_blocks: Vec<ParsedBlock>,
}

impl RawBuilder {
    pub fn new(version: CifVersion, mode_strict: bool) -> Self {
        RawBuilder {
            version,
            errors: Vec::new(),
            mode_strict,
            stopped: false,
            current_block: None,
            current_save_frame: None,
            seen_block_names: HashSet::new(),
            seen_save_frame_names: HashSet::new(),
            active_tag: None,
            in_loop: false,
            loop_tags: Vec::new(),
            loop_value_index: 0,
            loop_buffers: HashMap::new(),
            container_stack: Vec::new(),
            completed_blocks: Vec::new(),
        }
    }

    pub fn push_error(&mut self, e: &RustParseError) {
        self.errors.push(e.clone());
    }

    fn semantic_error(&mut self, message: String, recovery: String) {
        self.errors.push(RustParseError {
            error_type: "semantic",
            message,
            line: 0,
            column: 0,
            context: "RawBuilder".to_string(),
            recovery_action: recovery,
        });
        if self.mode_strict {
            self.stopped = true;
        }
    }

    fn finish_current_block(&mut self) {
        if let Some((name, data, save_frames)) = self.current_block.take() {
            self.completed_blocks.push(ParsedBlock { name, data, save_frames });
        }
    }

    fn dispatch_value(&mut self, value: RawValue) {
        if let Some(frame) = self.container_stack.last_mut() {
            match frame {
                ContainerFrame::List(list) => {
                    list.push(value);
                }
                ContainerFrame::Table { data, current_key } => {
                    if let Some(key) = current_key.take() {
                        data.push((key, value));
                    }
                }
            }
            return;
        }

        if self.in_loop {
            let n = self.loop_tags.len();
            if n > 0 {
                let tag = self.loop_tags[self.loop_value_index % n].clone();
                self.loop_buffers.entry(tag).or_default().push(value);
                self.loop_value_index += 1;
            }
            return;
        }

        if let Some(tag) = self.active_tag.take() {
            let tag_str = tag.as_str().to_string();
            if self.current_save_frame.is_some() {
                if let Some((_, ref mut data)) = self.current_save_frame {
                    data.append_value(&tag_str, value);
                }
            } else if let Some((_, ref mut data, _)) = self.current_block {
                data.append_value(&tag_str, value);
            }
        }
    }

    pub fn finish(mut self) -> ParsedCif {
        self.finish_current_block();
        ParsedCif {
            version: self.version,
            blocks: self.completed_blocks,
            errors: self.errors,
        }
    }

    pub fn to_python<'py>(self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let parsed = self.finish();
        let result = PyDict::new(py);

        let version_str = match parsed.version {
            CifVersion::Cif1_1 => "CIF_1_1",
            CifVersion::Cif2_0 => "CIF_2_0",
        };
        result.set_item("version", version_str)?;

        // Errors
        let errors = PyList::empty(py);
        for e in &parsed.errors {
            let err_dict = PyDict::new(py);
            err_dict.set_item("error_type", e.error_type)?;
            err_dict.set_item("message", e.message.as_str())?;
            err_dict.set_item("line", e.line)?;
            err_dict.set_item("column", e.column)?;
            err_dict.set_item("context", e.context.as_str())?;
            err_dict.set_item("recovery_action", e.recovery_action.as_str())?;
            errors.append(&err_dict)?;
        }
        result.set_item("errors", &errors)?;

        // Blocks
        let blocks = PyList::empty(py);
        for block in &parsed.blocks {
            let block_dict = block_to_python(py, &block.name, &block.data, &block.save_frames)?;
            blocks.append(&block_dict)?;
        }
        result.set_item("blocks", &blocks)?;

        Ok(result)
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Python conversion helpers
// ─────────────────────────────────────────────────────────────────────────────

pub(crate) fn raw_value_to_python<'py>(py: Python<'py>, v: &RawValue) -> PyResult<Bound<'py, PyAny>> {
    match v {
        RawValue::Str(s) => Ok(PyString::new(py, s).into_any()),
        RawValue::List(items) => {
            let list = PyList::empty(py);
            for item in items {
                list.append(&raw_value_to_python(py, item)?)?;
            }
            Ok(list.into_any())
        }
        RawValue::Table(pairs) => {
            let dict = PyDict::new(py);
            for (k, v) in pairs {
                dict.set_item(k.as_str(), raw_value_to_python(py, v)?)?;
            }
            Ok(dict.into_any())
        }
    }
}

fn frame_data_to_python<'py>(
    py: Python<'py>,
    name: &str,
    data: &FrameData,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", name)?;

    // tag_order
    let tag_order = PyList::empty(py);
    for t in &data.tag_order {
        tag_order.append(PyString::new(py, t))?;
    }
    d.set_item("tag_order", &tag_order)?;

    // loops (list of lists of tag names)
    let loops = PyList::empty(py);
    for loop_tags in &data.loops {
        let lt = PyList::empty(py);
        for t in loop_tags {
            lt.append(PyString::new(py, t))?;
        }
        loops.append(&lt)?;
    }
    d.set_item("loops", &loops)?;

    // tags dict: tag -> list of values
    let tags_dict = PyDict::new(py);
    for tag in &data.tag_order {
        if let Some(values) = data.tags.get(tag.as_str()) {
            let vals = PyList::empty(py);
            for v in values {
                vals.append(&raw_value_to_python(py, v)?)?;
            }
            tags_dict.set_item(tag.as_str(), &vals)?;
        }
    }
    d.set_item("tags", &tags_dict)?;

    Ok(d)
}

fn block_to_python<'py>(
    py: Python<'py>,
    name: &str,
    data: &FrameData,
    save_frames: &[ParsedSaveFrame],
) -> PyResult<Bound<'py, PyDict>> {
    let d = frame_data_to_python(py, name, data)?;

    let sfs = PyList::empty(py);
    for sf in save_frames {
        sfs.append(&frame_data_to_python(py, &sf.name, &sf.data)?)?;
    }
    d.set_item("save_frames", &sfs)?;

    Ok(d)
}

// ─────────────────────────────────────────────────────────────────────────────
// Unicode canonical caseless matching
// ─────────────────────────────────────────────────────────────────────────────

fn casefold(s: &str) -> String {
    s.to_lowercase()
}

// ─────────────────────────────────────────────────────────────────────────────
// EventSink implementation
// ─────────────────────────────────────────────────────────────────────────────

impl EventSink for RawBuilder {
    fn on_data_block(&mut self, name: &str) {
        if self.stopped { return; }
        self.finish_current_block();
        let norm = casefold(name);
        let is_dup = !self.seen_block_names.insert(norm.clone());
        if is_dup {
            self.semantic_error(
                format!("duplicate data block name: {:?}", name),
                "duplicate block stored with distinct internal id".to_string(),
            );
        }
        self.current_save_frame = None;
        self.active_tag = None;
        self.in_loop = false;
        self.loop_tags.clear();
        self.loop_value_index = 0;
        self.loop_buffers.clear();
        self.container_stack.clear();
        self.seen_save_frame_names.clear();
        self.current_block = Some((norm, FrameData::default(), Vec::new()));
    }

    fn on_save_frame_start(&mut self, name: &str) {
        if self.stopped || self.current_block.is_none() { return; }
        let norm = casefold(name);
        let is_dup = !self.seen_save_frame_names.insert(norm.clone());
        if is_dup {
            self.semantic_error(
                format!("duplicate save frame name: {:?}", name),
                "duplicate save frame stored with distinct internal id".to_string(),
            );
        }
        self.current_save_frame = Some((norm, FrameData::default()));
    }

    fn on_save_frame_end(&mut self) {
        if self.stopped || self.current_block.is_none() { return; }
        if let Some((sf_name, sf_data)) = self.current_save_frame.take() {
            if let Some((_, _, ref mut sfs)) = self.current_block {
                sfs.push(ParsedSaveFrame { name: sf_name, data: sf_data });
            }
        }
    }

    fn add_tag(&mut self, tag: &str) {
        if self.stopped { return; }
        self.active_tag = Some(casefold(tag));
    }

    fn add_value(&mut self, value: &str, vtype: ValueType) {
        if self.stopped { return; }
        let stored = match vtype {
            ValueType::MultilineString => transform_multiline(value),
            ValueType::Placeholder => value.to_string(),
            _ if value == "." || value == "?" => format!("\"{}\"", value),
            _ => value.to_string(),
        };
        self.dispatch_value(RawValue::Str(stored));
    }

    fn on_list_start(&mut self) {
        if self.stopped { return; }
        self.container_stack.push(ContainerFrame::List(Vec::new()));
    }

    fn on_list_end(&mut self) {
        if self.stopped { return; }
        if let Some(ContainerFrame::List(items)) = self.container_stack.pop() {
            self.dispatch_value(RawValue::List(items));
        }
    }

    fn on_table_start(&mut self) {
        if self.stopped { return; }
        self.container_stack.push(ContainerFrame::Table {
            data: Vec::new(),
            current_key: None,
        });
    }

    fn on_table_key(&mut self, key: &str, _vtype: ValueType) {
        if self.stopped { return; }
        if let Some(ContainerFrame::Table { ref mut current_key, .. }) =
            self.container_stack.last_mut()
        {
            *current_key = Some(key.to_string());
        }
    }

    fn on_table_end(&mut self) {
        if self.stopped { return; }
        if let Some(ContainerFrame::Table { data, .. }) = self.container_stack.pop() {
            self.dispatch_value(RawValue::Table(data));
        }
    }

    fn on_loop_start(&mut self, tags: &[String]) {
        if self.stopped { return; }
        let normed: Vec<String> = tags.iter().map(|t| casefold(t)).collect();
        self.in_loop = true;
        self.loop_value_index = 0;
        self.loop_buffers.clear();
        for tag in &normed {
            self.loop_buffers.entry(tag.clone()).or_default();
        }
        self.loop_tags = normed;
    }

    fn on_loop_end(&mut self) {
        if self.stopped { return; }
        let n = self.loop_tags.len();
        let total = self.loop_value_index;

        if n == 0 {
            self.in_loop = false;
            return;
        }

        if total % n != 0 {
            let missing = n - (total % n);
            let tag_list = self.loop_tags.join(", ");
            let recovery = if self.mode_strict {
                "stopped".to_string()
            } else {
                format!("padded {} placeholder(s)", missing)
            };
            self.semantic_error(
                format!(
                    "loop value count {} is not divisible by tag count {} \
                     ({} value(s) missing from final row); tags: {}",
                    total, n, missing, tag_list
                ),
                recovery,
            );

            if self.stopped {
                self.in_loop = false;
                self.loop_tags.clear();
                self.loop_value_index = 0;
                self.loop_buffers.clear();
                return;
            }

            // Pad mode: fill incomplete row with '?'
            for _ in 0..missing {
                let tag = self.loop_tags[self.loop_value_index % n].clone();
                self.loop_buffers.entry(tag).or_default()
                    .push(RawValue::Str("?".to_string()));
                self.loop_value_index += 1;
            }
        }

        let tags = std::mem::take(&mut self.loop_tags);
        let mut buffers = std::mem::take(&mut self.loop_buffers);

        if self.current_save_frame.is_some() {
            if let Some((_, ref mut data)) = self.current_save_frame {
                data.add_loop(&tags, &mut buffers);
            }
        } else if let Some((_, ref mut data, _)) = self.current_block {
            data.add_loop(&tags, &mut buffers);
        }

        self.in_loop = false;
        self.loop_value_index = 0;
    }

    fn on_parse_error(
        &mut self,
        etype: &'static str,
        msg: &str,
        line: u32,
        col: u32,
        context: &str,
        recovery: &str,
    ) {
        self.errors.push(RustParseError {
            error_type: etype,
            message: msg.to_string(),
            line,
            column: col,
            context: context.to_string(),
            recovery_action: recovery.to_string(),
        });
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Arrow IPC export
// ─────────────────────────────────────────────────────────────────────────────

use arrow::array::{ArrayRef, Int32Array, StringArray};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::record_batch::RecordBatch;
use std::sync::Arc;

fn raw_to_str(v: &RawValue) -> String {
    match v {
        RawValue::Str(s) => s.clone(),
        RawValue::List(items) => {
            let parts: Vec<String> = items.iter().map(json_val).collect();
            format!("\x00[{}]", parts.join(","))
        }
        RawValue::Table(pairs) => {
            let parts: Vec<String> = pairs.iter()
                .map(|(k, v)| format!("{}:{}", json_str(k), json_val(v)))
                .collect();
            format!("\x00{{{}}}", parts.join(","))
        }
    }
}

fn json_str(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"'  => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c    => out.push(c),
        }
    }
    out.push('"');
    out
}

fn json_val(v: &RawValue) -> String {
    match v {
        RawValue::Str(s) => json_str(s),
        RawValue::List(items) => {
            let parts: Vec<String> = items.iter().map(json_val).collect();
            format!("[{}]", parts.join(","))
        }
        RawValue::Table(pairs) => {
            let parts: Vec<String> = pairs.iter()
                .map(|(k, v)| format!("{}:{}", json_str(k), json_val(v)))
                .collect();
            format!("{{{}}}", parts.join(","))
        }
    }
}

fn make_batch(
    block_idx: i32,
    block_name: &str,
    frame_idx: Option<i32>,
    frame_name: Option<&str>,
    loop_id: &str,
    tags: &[String],
    tag_data: &HashMap<String, Vec<Option<String>>>,
    n_rows: usize,
) -> RecordBatch {
    let mut fields = vec![
        Field::new("_block_idx",  DataType::Int32, false),
        Field::new("_block_name", DataType::Utf8,  false),
        Field::new("_frame_idx",  DataType::Int32, true),
        Field::new("_frame_name", DataType::Utf8,  true),
        Field::new("_loop_id",    DataType::Utf8,  false),
    ];
    for tag in tags {
        fields.push(Field::new(tag.as_str(), DataType::Utf8, true));
    }
    let schema = Arc::new(Schema::new(fields));

    let mut arrays: Vec<ArrayRef> = vec![
        Arc::new(Int32Array::from(vec![block_idx; n_rows])),
        Arc::new(StringArray::from(vec![block_name; n_rows])),
        Arc::new(Int32Array::from(vec![frame_idx; n_rows])),
        Arc::new(StringArray::from(vec![frame_name; n_rows])),
        Arc::new(StringArray::from(vec![loop_id; n_rows])),
    ];
    for tag in tags {
        if let Some(col) = tag_data.get(tag.as_str()) {
            arrays.push(Arc::new(StringArray::from(
                col.iter().map(|o| o.as_deref()).collect::<Vec<_>>(),
            )));
        } else {
            arrays.push(Arc::new(StringArray::from(vec![None::<&str>; n_rows])));
        }
    }

    RecordBatch::try_new(schema, arrays).expect("schema/array length mismatch")
}

fn frame_to_batches(
    data: &FrameData,
    block_idx: i32,
    block_name: &str,
    frame_idx: Option<i32>,
    frame_name: Option<&str>,
) -> Vec<RecordBatch> {
    let loop_tag_set: HashSet<&str> = data.loops.iter()
        .flat_map(|l| l.iter().map(String::as_str))
        .collect();

    let scalar_tags: Vec<String> = data.tag_order.iter()
        .filter(|t| !loop_tag_set.contains(t.as_str()))
        .cloned()
        .collect();

    let mut batches = Vec::new();

    // Scalar batch
    if !scalar_tags.is_empty() {
        let n_rows = scalar_tags.iter()
            .map(|t| data.tags.get(t.as_str()).map_or(0, |v| v.len()))
            .max()
            .unwrap_or(0);

        if n_rows > 0 {
            let mut tag_data: HashMap<String, Vec<Option<String>>> = HashMap::new();
            for tag in &scalar_tags {
                let vals = data.tags.get(tag.as_str()).map_or(&[][..], |v| v.as_slice());
                let col = (0..n_rows)
                    .map(|i| vals.get(i).map(raw_to_str))
                    .collect();
                tag_data.insert(tag.clone(), col);
            }
            batches.push(make_batch(
                block_idx, block_name, frame_idx, frame_name,
                "__scalars__", &scalar_tags, &tag_data, n_rows,
            ));
        }
    }

    // Loop batches
    for (loop_idx, loop_tags) in data.loops.iter().enumerate() {
        if loop_tags.is_empty() {
            continue;
        }
        let n_rows = data.tags.get(loop_tags[0].as_str()).map_or(0, |v| v.len());
        if n_rows == 0 {
            continue;
        }
        let mut tag_data: HashMap<String, Vec<Option<String>>> = HashMap::new();
        for tag in loop_tags {
            let vals = data.tags.get(tag.as_str()).map_or(&[][..], |v| v.as_slice());
            let col = vals.iter().map(|v| Some(raw_to_str(v))).collect();
            tag_data.insert(tag.clone(), col);
        }
        let loop_id = format!("__loop_{}__", loop_idx);
        batches.push(make_batch(
            block_idx, block_name, frame_idx, frame_name,
            &loop_id, loop_tags, &tag_data, n_rows,
        ));
    }

    batches
}

impl ParsedCif {
    pub fn to_py_batches<'py>(&self, py: Python<'py>) -> PyResult<Vec<PyObject>> {
        use arrow::pyarrow::ToPyArrow;
        let mut result = Vec::new();
        for (block_idx, block) in self.blocks.iter().enumerate() {
            let bi = block_idx as i32;
            for batch in frame_to_batches(&block.data, bi, &block.name, None, None) {
                result.push(batch.to_pyarrow(py)?);
            }
            let mut fi = 0i32;
            for sf in &block.save_frames {
                for batch in frame_to_batches(&sf.data, bi, &block.name, Some(fi), Some(&sf.name)) {
                    result.push(batch.to_pyarrow(py)?);
                }
                fi += 1;
            }
        }
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::Lexer;
    use crate::parser::Parser;
    use crate::version::{detect_version, CifVersion};

    // ── test infrastructure ───────────────────────────────────────────────────

    fn parse(src: &str) -> ParsedCif {
        let vr = detect_version(src);
        let mut b = RawBuilder::new(vr.version, false);
        for e in &vr.errors { b.push_error(e); }
        let tokens = Lexer::new(&vr.remaining, vr.version, vr.line_offset).tokenise();
        Parser::new().parse(tokens, &mut b);
        b.finish()
    }

    fn parse_strict(src: &str) -> ParsedCif {
        let vr = detect_version(src);
        let mut b = RawBuilder::new(vr.version, true);
        for e in &vr.errors { b.push_error(e); }
        let tokens = Lexer::new(&vr.remaining, vr.version, vr.line_offset).tokenise();
        Parser::new().parse(tokens, &mut b);
        b.finish()
    }

    fn str_val(v: &RawValue) -> &str {
        if let RawValue::Str(s) = v { s } else { panic!("not a Str: {:?}", v) }
    }

    // ── version detection ─────────────────────────────────────────────────────

    #[test]
    fn version_cif2_magic() {
        assert_eq!(parse("#\\#CIF_2.0\ndata_b _t v").version, CifVersion::Cif2_0);
    }

    #[test]
    fn version_cif1_no_magic() {
        assert_eq!(parse("data_b _t v").version, CifVersion::Cif1_1);
    }

    // ── block structure ───────────────────────────────────────────────────────

    #[test]
    fn single_block_name() {
        let p = parse("data_myblock _t v");
        assert_eq!(p.blocks.len(), 1);
        assert_eq!(p.blocks[0].name, "myblock");
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn two_blocks() {
        let p = parse("data_a _ta va data_b _tb vb");
        assert_eq!(p.blocks.len(), 2);
        assert_eq!(p.blocks[0].name, "a");
        assert_eq!(p.blocks[1].name, "b");
    }

    #[test]
    fn empty_source_no_blocks() {
        let p = parse("");
        assert_eq!(p.blocks.len(), 0);
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn duplicate_block_name_is_error() {
        let p = parse("data_foo _t 1 data_foo _t 2");
        assert_eq!(p.blocks.len(), 2);
        assert!(!p.errors.is_empty());
        assert!(p.errors.iter().any(|e| e.message.contains("duplicate")));
    }

    // ── scalar values ─────────────────────────────────────────────────────────

    #[test]
    fn scalar_string_stored() {
        let p = parse("data_b _tag hello");
        let vals = &p.blocks[0].data.tags["_tag"];
        assert_eq!(vals.len(), 1);
        assert_eq!(str_val(&vals[0]), "hello");
    }

    #[test]
    fn placeholder_dot_stored_as_dot() {
        let p = parse("data_b _tag .");
        let v = str_val(&p.blocks[0].data.tags["_tag"][0]);
        assert_eq!(v, ".");
    }

    #[test]
    fn placeholder_question_stored_as_question() {
        let p = parse("data_b _tag ?");
        let v = str_val(&p.blocks[0].data.tags["_tag"][0]);
        assert_eq!(v, "?");
    }

    #[test]
    fn quoted_dot_stored_as_sentinel() {
        // Quoted '.' should be stored as '"."' (sentinel encoding)
        let p = parse("data_b _tag '.'");
        let v = str_val(&p.blocks[0].data.tags["_tag"][0]);
        assert_eq!(v, "\".\"");
    }

    #[test]
    fn quoted_question_stored_as_sentinel() {
        let p = parse("data_b _tag \"?\"");
        let v = str_val(&p.blocks[0].data.tags["_tag"][0]);
        assert_eq!(v, "\"?\"");
    }

    #[test]
    fn tag_order_preserved() {
        let p = parse("data_b _c 3 _a 1 _b 2");
        assert_eq!(p.blocks[0].data.tag_order, vec!["_c", "_a", "_b"]);
    }

    #[test]
    fn duplicate_tag_values_both_stored() {
        let p = parse("data_b _tag val1 _tag val2");
        let vals = &p.blocks[0].data.tags["_tag"];
        assert_eq!(vals.len(), 2);
        assert_eq!(str_val(&vals[0]), "val1");
        assert_eq!(str_val(&vals[1]), "val2");
    }

    // ── loops ─────────────────────────────────────────────────────────────────

    #[test]
    fn loop_values_stored_per_tag() {
        let p = parse("data_b loop_ _a _b 1 2 3 4");
        let data = &p.blocks[0].data;
        assert_eq!(data.loops, vec![vec!["_a".to_string(), "_b".to_string()]]);
        assert_eq!(data.tags["_a"].iter().map(|v| str_val(v)).collect::<Vec<_>>(), vec!["1", "3"]);
        assert_eq!(data.tags["_b"].iter().map(|v| str_val(v)).collect::<Vec<_>>(), vec!["2", "4"]);
    }

    #[test]
    fn loop_tag_order_in_loops_vec() {
        let p = parse("data_b loop_ _x _y 1 2");
        assert_eq!(p.blocks[0].data.loops[0], vec!["_x", "_y"]);
    }

    #[test]
    fn loop_imbalanced_pad_mode() {
        // 3 values for a 2-tag loop → pad mode adds 1 placeholder, emits error
        let p = parse("data_b loop_ _a _b 1 2 3");
        assert!(!p.errors.is_empty());
        assert!(p.errors.iter().any(|e| e.message.contains("not divisible")));
        let data = &p.blocks[0].data;
        // padded: _a=[1,3], _b=[2,?]
        assert_eq!(data.tags["_a"].len(), 2);
        assert_eq!(data.tags["_b"].len(), 2);
        assert_eq!(str_val(&data.tags["_b"][1]), "?");
    }

    #[test]
    fn loop_imbalanced_strict_mode_stops() {
        let p = parse_strict("data_b loop_ _a _b 1 2 3");
        assert!(!p.errors.is_empty());
        // In strict mode, partial loop data may still be in blocks but stopped flag is set
    }

    #[test]
    fn scalar_then_loop_then_scalar() {
        let p = parse("data_b _x 1 loop_ _y 2 3 _z 4");
        let data = &p.blocks[0].data;
        assert_eq!(str_val(&data.tags["_x"][0]), "1");
        assert_eq!(data.tags["_y"].len(), 2);
        assert_eq!(str_val(&data.tags["_z"][0]), "4");
    }

    // ── save frames ───────────────────────────────────────────────────────────

    #[test]
    fn save_frame_stored() {
        let p = parse("data_b save_sf _tag val save_");
        assert_eq!(p.blocks[0].save_frames.len(), 1);
        assert_eq!(p.blocks[0].save_frames[0].name, "sf");
        assert_eq!(str_val(&p.blocks[0].save_frames[0].data.tags["_tag"][0]), "val");
    }

    #[test]
    fn duplicate_save_frame_is_error() {
        let p = parse("data_b save_sf _t 1 save_ save_sf _t 2 save_");
        assert!(!p.errors.is_empty());
    }

    // ── containers ───────────────────────────────────────────────────────────

    #[test]
    fn list_value_stored() {
        let p = parse("#\\#CIF_2.0\ndata_b _tag [1 2 3]");
        let v = &p.blocks[0].data.tags["_tag"][0];
        assert!(matches!(v, RawValue::List(_)));
        if let RawValue::List(items) = v {
            assert_eq!(items.len(), 3);
            assert_eq!(str_val(&items[0]), "1");
        }
    }

    #[test]
    fn table_value_stored() {
        let p = parse("#\\#CIF_2.0\ndata_b _tag {\"key\":val}");
        let v = &p.blocks[0].data.tags["_tag"][0];
        assert!(matches!(v, RawValue::Table(_)));
        if let RawValue::Table(pairs) = v {
            assert_eq!(pairs.len(), 1);
            assert_eq!(pairs[0].0, "key");
            assert_eq!(str_val(&pairs[0].1), "val");
        }
    }

    #[test]
    fn nested_list() {
        let p = parse("#\\#CIF_2.0\ndata_b _tag [[1 2] 3]");
        let v = &p.blocks[0].data.tags["_tag"][0];
        if let RawValue::List(outer) = v {
            assert_eq!(outer.len(), 2);
            assert!(matches!(&outer[0], RawValue::List(_)));
        } else { panic!("expected List"); }
    }

    // ── multiline text field transform ────────────────────────────────────────

    #[test]
    fn multiline_content_transformed() {
        // ;text\n; → raw = "text" → transform_multiline("text") = "text"
        let p = parse("data_b _tag\n;text\n;");
        assert_eq!(str_val(&p.blocks[0].data.tags["_tag"][0]), "text");
    }

    #[test]
    fn multiline_with_fold_prefix() {
        // fold: first line is "P>\ ", remaining lines have prefix "P>" stripped + unfolded
        let cif = "data_b _tag\n;P>\\  \nP>part1\\\nP>part2\n;";
        let p = parse(cif);
        assert_eq!(str_val(&p.blocks[0].data.tags["_tag"][0]), "part1part2");
    }

    // ── error propagation ─────────────────────────────────────────────────────

    #[test]
    fn no_errors_on_clean_input() {
        let p = parse("#\\#CIF_2.0\ndata_b _tag val");
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn version_error_propagated() {
        // Unknown CIF version produces an error from detect_version
        let p = parse("#\\#CIF_9.9\ndata_b _tag val");
        assert!(!p.errors.is_empty());
    }

    #[test]
    fn global_error_propagated() {
        let p = parse("data_b global_");
        assert!(p.errors.iter().any(|e| e.message.contains("global_")));
    }

    // ── fixture files ─────────────────────────────────────────────────────────

    // Helper: parse fixture, assert no panic, return ParsedCif
    macro_rules! fixture {
        ($path:literal) => { parse(include_str!($path)) };
    }

    #[test]
    fn fixture_empty() {
        let p = fixture!("../../tests/cif_files/comcifs/empty.cif");
        assert_eq!(p.blocks.len(), 0);
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_comment_only() {
        let p = fixture!("../../tests/cif_files/comcifs/comment_only.cif");
        assert_eq!(p.blocks.len(), 0);
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_simple_data() {
        let p = fixture!("../../tests/cif_files/comcifs/simple_data.cif");
        assert_eq!(p.blocks.len(), 1);
        assert_eq!(p.blocks[0].name, "simple_data");
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_simple_loops() {
        let p = fixture!("../../tests/cif_files/comcifs/simple_loops.cif");
        assert_eq!(p.errors.len(), 0);
        assert!(!p.blocks[0].data.loops.is_empty());
    }

    #[test]
    fn fixture_simple_containers() {
        let p = fixture!("../../tests/cif_files/comcifs/simple_containers.cif");
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_complex_data() {
        let p = fixture!("../../tests/cif_files/comcifs/complex_data.cif");
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_nested() {
        // nested.cif contains nested save frames (illegal) → parser + semantic errors
        let p = fixture!("../../tests/cif_files/comcifs/nested.cif");
        assert_eq!(p.blocks.len(), 1);
        assert!(!p.errors.is_empty());
    }

    #[test]
    fn fixture_list_data() {
        let p = fixture!("../../tests/cif_files/comcifs/list_data.cif");
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_table_data() {
        let p = fixture!("../../tests/cif_files/comcifs/table_data.cif");
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_strings() {
        let p = fixture!("../../tests/cif_files/comcifs/strings.cif");
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_text_fields() {
        let p = fixture!("../../tests/cif_files/comcifs/text_fields.cif");
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_triple() {
        let p = fixture!("../../tests/cif_files/comcifs/triple.cif");
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_unicode() {
        let p = fixture!("../../tests/cif_files/comcifs/unicode.cif");
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_ver1() {
        let p = fixture!("../../tests/cif_files/comcifs/ver1.cif");
        assert_eq!(p.version, CifVersion::Cif1_1);
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_ver2() {
        let p = fixture!("../../tests/cif_files/comcifs/ver2.cif");
        assert_eq!(p.version, CifVersion::Cif2_0);
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_cif11_unquoted() {
        let p = fixture!("../../tests/cif_files/comcifs/cif11_unquoted.cif");
        assert_eq!(p.version, CifVersion::Cif1_1);
    }

    #[test]
    fn fixture_cif1_quoting() {
        let p = fixture!("../../tests/cif_files/comcifs/cif1_quoting.cif");
        assert_eq!(p.version, CifVersion::Cif1_1);
    }

    #[test]
    fn fixture_bom() {
        // Single BOM byte — no data blocks, orphan-value error for the BOM char
        let p = fixture!("../../tests/cif_files/comcifs/bom.cif");
        assert_eq!(p.blocks.len(), 0);
    }

    #[test]
    fn fixture_bom_ver2() {
        let p = fixture!("../../tests/cif_files/comcifs/bom_ver2.cif");
        assert_eq!(p.version, CifVersion::Cif2_0);
    }

    #[test]
    fn fixture_container_names() {
        let p = fixture!("../../tests/cif_files/comcifs/container_names.cif");
        assert_eq!(p.errors.len(), 0);
    }

    #[test]
    fn fixture_10() {
        let _ = fixture!("../../tests/cif_files/comcifs/10.cif");
    }

    #[test]
    fn fixture_cif1_invalid() {
        let p = fixture!("../../tests/cif_files/comcifs/cif1_invalid.cif");
        // Invalid CIF 1.x constructs produce errors but must not panic
        assert!(!p.errors.is_empty());
    }

    #[test]
    fn fixture_malformed_loops() {
        let p = fixture!("../../tests/cif_files/malformed/loops.cif");
        assert!(!p.errors.is_empty());
    }

    #[test]
    fn fixture_malformed_strings_cif1() {
        let p = fixture!("../../tests/cif_files/malformed/strings1-1.cif");
        assert!(!p.errors.is_empty());
    }

    #[test]
    fn fixture_malformed_strings_cif2() {
        let p = fixture!("../../tests/cif_files/malformed/strings2-0.cif");
        assert!(!p.errors.is_empty());
    }

    #[test]
    fn fixture_malformed_containers() {
        let p = fixture!("../../tests/cif_files/malformed/containers.cif");
        assert!(!p.errors.is_empty());
    }

    #[test]
    fn fixture_malformed_multiline() {
        let p = fixture!("../../tests/cif_files/malformed/multiline.cif");
        assert!(!p.errors.is_empty());
    }

    #[test]
    fn fixture_single_one() { let _ = fixture!("../../tests/cif_files/single_one.cif"); }
    #[test]
    fn fixture_single_many_1() { let _ = fixture!("../../tests/cif_files/single_many_1.cif"); }
    #[test]
    fn fixture_single_many_2() { let _ = fixture!("../../tests/cif_files/single_many_2.cif"); }
    #[test]
    fn fixture_single_list() { let _ = fixture!("../../tests/cif_files/single_list.cif"); }
    #[test]
    fn fixture_multi_one() { let _ = fixture!("../../tests/cif_files/multi_one.cif"); }
    #[test]
    fn fixture_multi_many() { let _ = fixture!("../../tests/cif_files/multi_many.cif"); }
    #[test]
    fn fixture_multi_list() { let _ = fixture!("../../tests/cif_files/multi_list.cif"); }
    #[test]
    fn fixture_multi_one_as_oneblock() { let _ = fixture!("../../tests/cif_files/multi_one_as_oneblock.cif"); }
    #[test]
    fn fixture_second_short() { let _ = fixture!("../../tests/cif_files/second_short.cif"); }
    #[test]
    fn fixture_one_structure() { let _ = fixture!("../../tests/cif_files/one_structure.cif"); }
    #[test]
    fn fixture_transitive_01() { let _ = fixture!("../../tests/cif_files/transitive_01.cif"); }
    #[test]
    fn fixture_transitive_02() { let _ = fixture!("../../tests/cif_files/transitive_02.cif"); }
    #[test]
    fn fixture_transitive_03() { let _ = fixture!("../../tests/cif_files/transitive_03.cif"); }
    #[test]
    fn fixture_enumeration_range() { let _ = fixture!("../../tests/cif_files/enumeration_range.cif"); }
    #[test]
    fn fixture_pathological_key_block() { let _ = fixture!("../../tests/cif_files/pathological_key_block.cif"); }

    #[test]
    fn fixture_pycifparse_core_cell_only() {
        let p = fixture!("../../tests/cif_files/pycifparse/core_cell_only.cif");
        assert_eq!(p.errors.len(), 0);
        assert_eq!(p.blocks.len(), 1);
    }
    #[test]
    fn fixture_pycifparse_core_cell_su() { let _ = fixture!("../../tests/cif_files/pycifparse/core_cell_su.cif"); }
    #[test]
    fn fixture_pycifparse_core_atom_site_no_atom_type() { let _ = fixture!("../../tests/cif_files/pycifparse/core_atom_site_no_atom_type.cif"); }
    #[test]
    fn fixture_pycifparse_core_alias_tag() { let _ = fixture!("../../tests/cif_files/pycifparse/core_alias_tag.cif"); }
    #[test]
    fn fixture_pycifparse_core_placeholder_in_loop() { let _ = fixture!("../../tests/cif_files/pycifparse/core_placeholder_in_loop.cif"); }
    #[test]
    fn fixture_pycifparse_core_quoted_sentinel() { let _ = fixture!("../../tests/cif_files/pycifparse/core_quoted_sentinel.cif"); }
    #[test]
    fn fixture_pycifparse_core_unknown_tag() { let _ = fixture!("../../tests/cif_files/pycifparse/core_unknown_tag.cif"); }
    #[test]
    fn fixture_pycifparse_core_multiple_blocks() { let _ = fixture!("../../tests/cif_files/pycifparse/core_multiple_blocks.cif"); }
    #[test]
    fn fixture_pycifparse_core_multiline_formula() { let _ = fixture!("../../tests/cif_files/pycifparse/core_multiline_formula.cif"); }
    #[test]
    fn fixture_pycifparse_core_repeated_loop_key() { let _ = fixture!("../../tests/cif_files/pycifparse/core_repeated_loop_key.cif"); }
    #[test]
    fn fixture_pycifparse_core_keyless_sets() { let _ = fixture!("../../tests/cif_files/pycifparse/core_keyless_sets.cif"); }
    #[test]
    fn fixture_pycifparse_fallback_scalars() { let _ = fixture!("../../tests/cif_files/pycifparse/fallback_scalars.cif"); }
    #[test]
    fn fixture_pycifparse_fallback_loop() { let _ = fixture!("../../tests/cif_files/pycifparse/fallback_loop.cif"); }
    #[test]
    fn fixture_pycifparse_fallback_value_types() { let _ = fixture!("../../tests/cif_files/pycifparse/fallback_value_types.cif"); }
    #[test]
    fn fixture_pycifparse_fallback_containers() { let _ = fixture!("../../tests/cif_files/pycifparse/fallback_containers.cif"); }
    #[test]
    fn fixture_pycifparse_fallback_multiblock() { let _ = fixture!("../../tests/cif_files/pycifparse/fallback_multiblock.cif"); }
    #[test]
    fn fixture_pycifparse_pow_wavelength_propagation() { let _ = fixture!("../../tests/cif_files/pycifparse/pow_wavelength_propagation.cif"); }
    #[test]
    fn fixture_pycifparse_pow_enumeration_default() { let _ = fixture!("../../tests/cif_files/pycifparse/pow_enumeration_default.cif"); }
    #[test]
    fn fixture_pycifparse_pow_small_pd_data_meas() { let _ = fixture!("../../tests/cif_files/pycifparse/pow_small_pd_data_meas.cif"); }
    #[test]
    fn fixture_pycifparse_pow_small_pd_meas_proc() { let _ = fixture!("../../tests/cif_files/pycifparse/pow_small_pd_meas_proc.cif"); }

    #[test]
    fn fixture_pycifparse_pow_multiple_blocks_canonical_case() {
        let p = fixture!("../../tests/cif_files/pycifparse/pow_multiple_blocks_canonical_case.cif");
        // All 3 blocks casefold to "abc"
        assert_eq!(p.blocks.len(), 3);
        assert!(p.blocks.iter().all(|b| b.name == "abc"));
        // 2 errors: data_abc and data_aBc duplicate data_ABC
        assert_eq!(
            p.errors.iter().filter(|e| e.message.contains("duplicate data block")).count(),
            2
        );
        // Block 0 has _cell.length_a with 2 values (_cell.LENGTH_A casefolded to same tag)
        assert_eq!(
            p.blocks[0].data.tags.get("_cell.length_a").map(|v| v.len()),
            Some(2)
        );
    }
}
