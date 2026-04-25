// RawBuilder — pure-Rust EventSink that accumulates parser events into a
// ParsedCif tree, then converts it to Python dicts/lists in one shot.
//
// No Python calls are made during parsing; all PyO3 work happens in to_python().
// This eliminates the per-token Rust→Python boundary cost.

use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString, PyTuple};

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
    Str(String, ValueType),
    List(Vec<RawValue>),
    Table(Vec<(String, RawValue)>), // ordered key-value pairs
}

// ─────────────────────────────────────────────────────────────────────────────
// Frame data (shared between blocks and save frames)
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Default)]
struct FrameData {
    tag_order: Vec<String>,
    tags: HashMap<String, Vec<RawValue>>,
    loops: Vec<Vec<String>>, // list of loop tag groups, in file order
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

struct ParsedSaveFrame {
    name: String,
    data: FrameData,
}

struct ParsedBlock {
    name: String,
    data: FrameData,
    save_frames: Vec<ParsedSaveFrame>,
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

// toplevel=true: scalars become (value_str, vt_name) tuples for lazy CifScalar creation.
// toplevel=false (inside container): scalars become plain strings — containers are
// accessed as Python str/list/dict already and don't need lazy CifScalar conversion.
fn raw_value_to_python<'py>(py: Python<'py>, v: &RawValue, toplevel: bool) -> PyResult<Bound<'py, PyAny>> {
    match v {
        RawValue::Str(s, vt) => {
            if toplevel {
                let tuple = PyTuple::new(py, [
                    PyString::new(py, s).into_any(),
                    PyString::new(py, vt.python_attr()).into_any(),
                ])?;
                Ok(tuple.into_any())
            } else {
                Ok(PyString::new(py, s).into_any())
            }
        }
        RawValue::List(items) => {
            let list = PyList::empty(py);
            for item in items {
                list.append(&raw_value_to_python(py, item, false)?)?;
            }
            Ok(list.into_any())
        }
        RawValue::Table(pairs) => {
            let dict = PyDict::new(py);
            for (k, v) in pairs {
                dict.set_item(k.as_str(), raw_value_to_python(py, v, false)?)?;
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
                vals.append(&raw_value_to_python(py, v, true)?)?;
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
// EventSink implementation
// ─────────────────────────────────────────────────────────────────────────────

impl EventSink for RawBuilder {
    fn on_data_block(&mut self, name: &str) -> PyResult<()> {
        if self.stopped {
            return Ok(());
        }
        self.finish_current_block();
        let is_dup = !self.seen_block_names.insert(name.to_string());
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
        self.current_block = Some((name.to_string(), FrameData::default(), Vec::new()));
        Ok(())
    }

    fn on_save_frame_start(&mut self, name: &str) -> PyResult<()> {
        if self.stopped || self.current_block.is_none() {
            return Ok(());
        }
        let is_dup = !self.seen_save_frame_names.insert(name.to_string());
        if is_dup {
            self.semantic_error(
                format!("duplicate save frame name: {:?}", name),
                "duplicate save frame stored with distinct internal id".to_string(),
            );
        }
        self.current_save_frame = Some((name.to_string(), FrameData::default()));
        Ok(())
    }

    fn on_save_frame_end(&mut self) -> PyResult<()> {
        if self.stopped || self.current_block.is_none() {
            return Ok(());
        }
        if let Some((sf_name, sf_data)) = self.current_save_frame.take() {
            if let Some((_, _, ref mut sfs)) = self.current_block {
                sfs.push(ParsedSaveFrame { name: sf_name, data: sf_data });
            }
        }
        Ok(())
    }

    fn add_tag(&mut self, tag: &str) -> PyResult<()> {
        if self.stopped {
            return Ok(());
        }
        self.active_tag = Some(tag.to_string());
        Ok(())
    }

    fn add_value(&mut self, value: &str, vtype: ValueType) -> PyResult<()> {
        if self.stopped {
            return Ok(());
        }
        let processed = if vtype == ValueType::MultilineString {
            transform_multiline(value)
        } else {
            value.to_string()
        };
        self.dispatch_value(RawValue::Str(processed, vtype));
        Ok(())
    }

    fn on_list_start(&mut self) -> PyResult<()> {
        if self.stopped {
            return Ok(());
        }
        self.container_stack.push(ContainerFrame::List(Vec::new()));
        Ok(())
    }

    fn on_list_end(&mut self) -> PyResult<()> {
        if self.stopped {
            return Ok(());
        }
        if let Some(ContainerFrame::List(items)) = self.container_stack.pop() {
            self.dispatch_value(RawValue::List(items));
        }
        Ok(())
    }

    fn on_table_start(&mut self) -> PyResult<()> {
        if self.stopped {
            return Ok(());
        }
        self.container_stack.push(ContainerFrame::Table {
            data: Vec::new(),
            current_key: None,
        });
        Ok(())
    }

    fn on_table_key(&mut self, key: &str, _vtype: ValueType) -> PyResult<()> {
        if self.stopped {
            return Ok(());
        }
        if let Some(ContainerFrame::Table { ref mut current_key, .. }) =
            self.container_stack.last_mut()
        {
            *current_key = Some(key.to_string());
        }
        Ok(())
    }

    fn on_table_end(&mut self) -> PyResult<()> {
        if self.stopped {
            return Ok(());
        }
        if let Some(ContainerFrame::Table { data, .. }) = self.container_stack.pop() {
            self.dispatch_value(RawValue::Table(data));
        }
        Ok(())
    }

    fn on_loop_start(&mut self, tags: &[String]) -> PyResult<()> {
        if self.stopped {
            return Ok(());
        }
        self.in_loop = true;
        self.loop_tags = tags.to_vec();
        self.loop_value_index = 0;
        self.loop_buffers.clear();
        for tag in tags {
            self.loop_buffers.entry(tag.clone()).or_default();
        }
        Ok(())
    }

    fn on_loop_end(&mut self) -> PyResult<()> {
        if self.stopped {
            return Ok(());
        }
        let n = self.loop_tags.len();
        let total = self.loop_value_index;

        if n == 0 {
            self.in_loop = false;
            return Ok(());
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
                return Ok(());
            }

            // Pad mode: fill incomplete row with '?'
            for _ in 0..missing {
                let tag = self.loop_tags[self.loop_value_index % n].clone();
                self.loop_buffers.entry(tag).or_default()
                    .push(RawValue::Str("?".to_string(), ValueType::Placeholder));
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
        Ok(())
    }

    fn on_parse_error(
        &mut self,
        etype: &'static str,
        msg: &str,
        line: u32,
        col: u32,
        context: &str,
        recovery: &str,
    ) -> PyResult<()> {
        self.errors.push(RustParseError {
            error_type: etype,
            message: msg.to_string(),
            line,
            column: col,
            context: context.to_string(),
            recovery_action: recovery.to_string(),
        });
        Ok(())
    }
}
