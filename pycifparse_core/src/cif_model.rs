// PyO3-backed CIF model types.
//
// PyCifSaveFrame, PyCifBlock, PyCifFile are #[pyclass] types that implement
// the full public CIF model API.  Internal data (_tags, _tag_order, _loops,
// _save_frames, _save_frame_list, _blocks, _block_list) is stored as live
// Python objects (Py<PyAny>) so that writer.py and clean.py can mutate those
// dicts and lists directly without any Rust involvement.
//
// build_py_cif() converts a ParsedCif into a PyCifFile in one pass, with no
// intermediate Python dict.

use pyo3::exceptions::PyKeyError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::raw_builder::{raw_value_to_python, FrameData, ParsedCif};
use crate::version::CifVersion;

fn casefold(s: &str) -> String {
    s.to_lowercase()
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared helper — convert FrameData to (tags_dict, tag_order_list, loops_list)
// ─────────────────────────────────────────────────────────────────────────────

fn frame_data_to_py<'py>(
    py: Python<'py>,
    data: &FrameData,
) -> PyResult<(Bound<'py, PyDict>, Bound<'py, PyList>, Bound<'py, PyList>)> {
    let tags_dict  = PyDict::new(py);
    let tag_order  = PyList::empty(py);
    let loops_list = PyList::empty(py);

    for tag in &data.tag_order {
        tag_order.append(tag.as_str())?;
        if let Some(values) = data.tags.get(tag.as_str()) {
            let py_vals = PyList::empty(py);
            for v in values {
                py_vals.append(raw_value_to_python(py, v)?)?;
            }
            tags_dict.set_item(tag.as_str(), py_vals)?;
        }
    }
    for loop_tags in &data.loops {
        let py_loop = PyList::empty(py);
        for t in loop_tags {
            py_loop.append(t.as_str())?;
        }
        loops_list.append(py_loop)?;
    }
    Ok((tags_dict, tag_order, loops_list))
}

// ─────────────────────────────────────────────────────────────────────────────
// PyCifSaveFrame
// ─────────────────────────────────────────────────────────────────────────────

#[pyclass(name = "CifSaveFrame", module = "pycifparse.cifmodel.model")]
pub struct PyCifSaveFrame {
    #[pyo3(get, set)]
    pub name: String,
    #[pyo3(get, set)]
    pub _id: usize,
    // Python dict[str, list]  — mutable from Python
    #[pyo3(get, set)]
    pub _tags: PyObject,
    // Python list[str]  — mutable from Python
    #[pyo3(get, set)]
    pub _tag_order: PyObject,
    // Python list[list[str]]  — mutable from Python
    #[pyo3(get, set)]
    pub _loops: PyObject,
}

#[pymethods]
impl PyCifSaveFrame {
    #[new]
    #[pyo3(signature = (name, id = 0))]
    fn new(py: Python<'_>, name: String, id: usize) -> PyResult<Self> {
        Ok(PyCifSaveFrame {
            name,
            _id: id,
            _tags:      PyDict::new(py).into_any().unbind(),
            _tag_order: PyList::empty(py).into_any().unbind(),
            _loops:     PyList::empty(py).into_any().unbind(),
        })
    }

    fn __getitem__<'py>(&self, py: Python<'py>, key: &str) -> PyResult<Bound<'py, PyAny>> {
        let norm = casefold(key);
        let tags = self._tags.bind(py).downcast::<PyDict>()?;
        match tags.get_item(norm.as_str())? {
            Some(v) => Ok(v),
            None    => Err(PyKeyError::new_err(key.to_string())),
        }
    }

    fn __contains__(&self, py: Python<'_>, key: &str) -> PyResult<bool> {
        self._tags.bind(py).downcast::<PyDict>()?.contains(casefold(key).as_str())
    }

    #[getter]
    fn tags<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let order = self._tag_order.bind(py).downcast::<PyList>()?;
        let copy = PyList::empty(py);
        for item in order.iter() {
            copy.append(item)?;
        }
        Ok(copy)
    }

    #[getter]
    fn loops<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let src = self._loops.bind(py).downcast::<PyList>()?;
        let copy = PyList::empty(py);
        for inner in src.iter() {
            let inner_copy = PyList::empty(py);
            for tag in inner.downcast::<PyList>()?.iter() {
                inner_copy.append(tag)?;
            }
            copy.append(inner_copy)?;
        }
        Ok(copy)
    }

    fn _append_value<'py>(
        &self,
        py: Python<'py>,
        tag: &str,
        value: Bound<'py, PyAny>,
    ) -> PyResult<()> {
        let tags  = self._tags.bind(py).downcast::<PyDict>()?;
        let order = self._tag_order.bind(py).downcast::<PyList>()?;
        if !tags.contains(tag)? {
            order.append(tag)?;
            tags.set_item(tag, PyList::empty(py))?;
        }
        let val_list: &Bound<'_, PyList> = &tags.get_item(tag)?.unwrap().downcast_into()?;
        val_list.append(value)?;
        Ok(())
    }

    fn _add_loop<'py>(
        &self,
        py: Python<'py>,
        tags: Vec<String>,
        buffers: Bound<'py, PyDict>,
    ) -> PyResult<()> {
        let self_tags = self._tags.bind(py).downcast::<PyDict>()?;
        let order     = self._tag_order.bind(py).downcast::<PyList>()?;
        let loops     = self._loops.bind(py).downcast::<PyList>()?;

        let loop_tags_py = PyList::empty(py);
        for tag in &tags {
            if !self_tags.contains(tag.as_str())? {
                order.append(tag.as_str())?;
            }
            let vals = match buffers.get_item(tag.as_str())? {
                Some(v) => v,
                None    => PyList::empty(py).into_any(),
            };
            self_tags.set_item(tag.as_str(), vals)?;
            loop_tags_py.append(tag.as_str())?;
        }
        loops.append(loop_tags_py)?;
        Ok(())
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// PyCifBlock
// ─────────────────────────────────────────────────────────────────────────────

#[pyclass(name = "CifBlock", module = "pycifparse.cifmodel.model")]
pub struct PyCifBlock {
    #[pyo3(get, set)]
    pub name: String,
    #[pyo3(get, set)]
    pub _id: usize,
    #[pyo3(get, set)]
    pub _tags: PyObject,
    #[pyo3(get, set)]
    pub _tag_order: PyObject,
    #[pyo3(get, set)]
    pub _loops: PyObject,
    // Save frame lookup dict: name → first PyCifSaveFrame  (mutable from Python)
    #[pyo3(get, set)]
    pub _save_frames: PyObject,
    // Save frame ordered list  (mutable from Python)
    #[pyo3(get, set)]
    pub _save_frame_list: PyObject,
}

#[pymethods]
impl PyCifBlock {
    #[new]
    #[pyo3(signature = (name, id = 0))]
    fn new(py: Python<'_>, name: String, id: usize) -> PyResult<Self> {
        Ok(PyCifBlock {
            name,
            _id: id,
            _tags:            PyDict::new(py).into_any().unbind(),
            _tag_order:       PyList::empty(py).into_any().unbind(),
            _loops:           PyList::empty(py).into_any().unbind(),
            _save_frames:     PyDict::new(py).into_any().unbind(),
            _save_frame_list: PyList::empty(py).into_any().unbind(),
        })
    }

    fn __getitem__<'py>(&self, py: Python<'py>, key: &str) -> PyResult<Bound<'py, PyAny>> {
        let norm = casefold(key);
        if norm.starts_with('_') {
            let tags = self._tags.bind(py).downcast::<PyDict>()?;
            match tags.get_item(norm.as_str())? {
                Some(v) => Ok(v),
                None    => Err(PyKeyError::new_err(key.to_string())),
            }
        } else {
            let sfs = self._save_frames.bind(py).downcast::<PyDict>()?;
            match sfs.get_item(norm.as_str())? {
                Some(v) => Ok(v),
                None    => Err(PyKeyError::new_err(key.to_string())),
            }
        }
    }

    fn __contains__(&self, py: Python<'_>, key: &str) -> PyResult<bool> {
        let norm = casefold(key);
        if norm.starts_with('_') {
            self._tags.bind(py).downcast::<PyDict>()?.contains(norm.as_str())
        } else {
            self._save_frames.bind(py).downcast::<PyDict>()?.contains(norm.as_str())
        }
    }

    #[getter]
    fn tags<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let order = self._tag_order.bind(py).downcast::<PyList>()?;
        let copy = PyList::empty(py);
        for item in order.iter() {
            copy.append(item)?;
        }
        Ok(copy)
    }

    #[getter]
    fn loops<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let src = self._loops.bind(py).downcast::<PyList>()?;
        let copy = PyList::empty(py);
        for inner in src.iter() {
            let inner_copy = PyList::empty(py);
            for tag in inner.downcast::<PyList>()?.iter() {
                inner_copy.append(tag)?;
            }
            copy.append(inner_copy)?;
        }
        Ok(copy)
    }

    #[getter]
    fn save_frames<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let sf_list = self._save_frame_list.bind(py).downcast::<PyList>()?;
        let names = PyList::empty(py);
        for sf in sf_list.iter() {
            let name = sf.getattr("name")?;
            names.append(name)?;
        }
        Ok(names)
    }

    fn get_all<'py>(&self, py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyList>> {
        let norm = casefold(name);
        let sf_list = self._save_frame_list.bind(py).downcast::<PyList>()?;
        let result = PyList::empty(py);
        for sf in sf_list.iter() {
            let sf_name: String = sf.getattr("name")?.extract()?;
            if sf_name == norm {
                result.append(sf)?;
            }
        }
        Ok(result)
    }

    fn _append_value<'py>(
        &self,
        py: Python<'py>,
        tag: &str,
        value: Bound<'py, PyAny>,
    ) -> PyResult<()> {
        let tags  = self._tags.bind(py).downcast::<PyDict>()?;
        let order = self._tag_order.bind(py).downcast::<PyList>()?;
        if !tags.contains(tag)? {
            order.append(tag)?;
            tags.set_item(tag, PyList::empty(py))?;
        }
        let val_list: &Bound<'_, PyList> = &tags.get_item(tag)?.unwrap().downcast_into()?;
        val_list.append(value)?;
        Ok(())
    }

    fn _add_loop<'py>(
        &self,
        py: Python<'py>,
        tags: Vec<String>,
        buffers: Bound<'py, PyDict>,
    ) -> PyResult<()> {
        let self_tags = self._tags.bind(py).downcast::<PyDict>()?;
        let order     = self._tag_order.bind(py).downcast::<PyList>()?;
        let loops     = self._loops.bind(py).downcast::<PyList>()?;

        let loop_tags_py = PyList::empty(py);
        for tag in &tags {
            if !self_tags.contains(tag.as_str())? {
                order.append(tag.as_str())?;
            }
            let vals = match buffers.get_item(tag.as_str())? {
                Some(v) => v,
                None    => PyList::empty(py).into_any(),
            };
            self_tags.set_item(tag.as_str(), vals)?;
            loop_tags_py.append(tag.as_str())?;
        }
        loops.append(loop_tags_py)?;
        Ok(())
    }

    fn _add_save_frame<'py>(
        &self,
        py: Python<'py>,
        frame: Bound<'py, PyAny>,
    ) -> PyResult<bool> {
        let sf_list = self._save_frame_list.bind(py).downcast::<PyList>()?;
        let sfs     = self._save_frames.bind(py).downcast::<PyDict>()?;
        let new_id  = sf_list.len();
        frame.setattr("_id", new_id)?;
        sf_list.append(&frame)?;
        let name: String = frame.getattr("name")?.extract()?;
        let dup = sfs.contains(&name)?;
        if !dup {
            sfs.set_item(&name, &frame)?;
        }
        Ok(dup)
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// PyCifFile
// ─────────────────────────────────────────────────────────────────────────────

#[pyclass(name = "CifFile", module = "pycifparse.cifmodel.model")]
pub struct PyCifFile {
    version: CifVersion,
    #[pyo3(get, set)]
    pub _blocks: PyObject,
    #[pyo3(get, set)]
    pub _block_list: PyObject,
}

#[pymethods]
impl PyCifFile {
    #[new]
    #[pyo3(signature = (version = None))]
    fn new(py: Python<'_>, version: Option<Bound<'_, PyAny>>) -> PyResult<Self> {
        let rust_version = match version {
            None    => CifVersion::Cif2_0,
            Some(v) => {
                let name: String = v.getattr("name")?.extract()?;
                if name == "CIF_1_1" { CifVersion::Cif1_1 } else { CifVersion::Cif2_0 }
            }
        };
        Ok(PyCifFile {
            version: rust_version,
            _blocks:     PyDict::new(py).into_any().unbind(),
            _block_list: PyList::empty(py).into_any().unbind(),
        })
    }

    fn __getitem__<'py>(&self, py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
        let norm = casefold(name);
        let blocks = self._blocks.bind(py).downcast::<PyDict>()?;
        match blocks.get_item(norm.as_str())? {
            Some(v) => Ok(v),
            None    => Err(PyKeyError::new_err(name.to_string())),
        }
    }

    fn __contains__(&self, py: Python<'_>, name: &str) -> PyResult<bool> {
        self._blocks.bind(py).downcast::<PyDict>()?.contains(casefold(name).as_str())
    }

    #[getter]
    fn version<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let types_mod = py.import("pycifparse.types")?;
        let cls = types_mod.getattr("CifVersion")?;
        let attr = match self.version {
            CifVersion::Cif2_0 => "CIF_2_0",
            CifVersion::Cif1_1 => "CIF_1_1",
        };
        cls.getattr(attr)
    }

    #[setter]
    fn set_version(&mut self, v: Bound<'_, PyAny>) -> PyResult<()> {
        let name: String = v.getattr("name")?.extract()?;
        self.version = if name == "CIF_1_1" { CifVersion::Cif1_1 } else { CifVersion::Cif2_0 };
        Ok(())
    }

    #[getter]
    fn blocks<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let bl = self._block_list.bind(py).downcast::<PyList>()?;
        let names = PyList::empty(py);
        for b in bl.iter() {
            names.append(b.getattr("name")?)?;
        }
        Ok(names)
    }

    fn get_all<'py>(&self, py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyList>> {
        let norm = casefold(name);
        let bl = self._block_list.bind(py).downcast::<PyList>()?;
        let result = PyList::empty(py);
        for b in bl.iter() {
            let b_name: String = b.getattr("name")?.extract()?;
            if b_name == norm {
                result.append(b)?;
            }
        }
        Ok(result)
    }

    fn _add_block<'py>(&self, py: Python<'py>, block: Bound<'py, PyAny>) -> PyResult<bool> {
        let bl     = self._block_list.bind(py).downcast::<PyList>()?;
        let blocks = self._blocks.bind(py).downcast::<PyDict>()?;
        let new_id = bl.len();
        block.setattr("_id", new_id)?;
        bl.append(&block)?;
        let name: String = block.getattr("name")?.extract()?;
        let dup = blocks.contains(&name)?;
        if !dup {
            blocks.set_item(&name, &block)?;
        }
        Ok(dup)
    }

    fn deepcopy<'py>(&self, py: Python<'py>) -> PyResult<Py<PyCifFile>> {
        let copy_mod = py.import("copy")?;

        let new_bl     = PyList::empty(py);
        let new_blocks = PyDict::new(py);

        let src_bl = self._block_list.bind(py).downcast::<PyList>()?;
        for b_obj in src_bl.iter() {
            // Deep-copy per-block Python containers
            let b_tags     = copy_mod.call_method1("deepcopy", (b_obj.getattr("_tags")?,))?;
            let b_order    = copy_mod.call_method1("deepcopy", (b_obj.getattr("_tag_order")?,))?;
            let b_loops    = copy_mod.call_method1("deepcopy", (b_obj.getattr("_loops")?,))?;

            // Deep-copy save frames
            let new_sf_list = PyList::empty(py);
            let new_sfs     = PyDict::new(py);
            for sf_obj in b_obj.getattr("_save_frame_list")?.downcast::<PyList>()?.iter() {
                let sf_id: usize  = sf_obj.getattr("_id")?.extract()?;
                let sf_name: String = sf_obj.getattr("name")?.extract()?;
                let new_sf = Py::new(py, PyCifSaveFrame {
                    name:       sf_name.clone(),
                    _id:        sf_id,
                    _tags:      copy_mod.call_method1("deepcopy", (sf_obj.getattr("_tags")?,))?.into_any().unbind(),
                    _tag_order: copy_mod.call_method1("deepcopy", (sf_obj.getattr("_tag_order")?,))?.into_any().unbind(),
                    _loops:     copy_mod.call_method1("deepcopy", (sf_obj.getattr("_loops")?,))?.into_any().unbind(),
                })?;
                new_sf_list.append(&new_sf)?;
                if !new_sfs.contains(&sf_name)? {
                    new_sfs.set_item(&sf_name, &new_sf)?;
                }
            }

            let b_name: String = b_obj.getattr("name")?.extract()?;
            let b_id: usize    = b_obj.getattr("_id")?.extract()?;
            let new_b = Py::new(py, PyCifBlock {
                name:             b_name.clone(),
                _id:              b_id,
                _tags:            b_tags.into_any().unbind(),
                _tag_order:       b_order.into_any().unbind(),
                _loops:           b_loops.into_any().unbind(),
                _save_frames:     new_sfs.into_any().unbind(),
                _save_frame_list: new_sf_list.into_any().unbind(),
            })?;
            new_bl.append(&new_b)?;
            if !new_blocks.contains(&b_name)? {
                new_blocks.set_item(&b_name, &new_b)?;
            }
        }

        Py::new(py, PyCifFile {
            version:     self.version.clone(),
            _blocks:     new_blocks.into_any().unbind(),
            _block_list: new_bl.into_any().unbind(),
        })
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Build PyCifFile from a ParsedCif
// ─────────────────────────────────────────────────────────────────────────────

pub(crate) fn build_py_cif<'py>(
    py: Python<'py>,
    parsed: &ParsedCif,
) -> PyResult<Py<PyCifFile>> {
    let block_list = PyList::empty(py);
    let blocks     = PyDict::new(py);

    for (block_idx, block) in parsed.blocks.iter().enumerate() {
        let sf_list = PyList::empty(py);
        let sfs     = PyDict::new(py);

        for (sf_idx, sf) in block.save_frames.iter().enumerate() {
            let py_sf = build_py_save_frame(py, &sf.name, sf_idx, &sf.data)?;
            sf_list.append(&py_sf)?;
            if !sfs.contains(&sf.name)? {
                sfs.set_item(&sf.name, &py_sf)?;
            }
        }

        let (tags, tag_order, loops) = frame_data_to_py(py, &block.data)?;
        let py_block = Py::new(py, PyCifBlock {
            name:             block.name.clone(),
            _id:              block_idx,
            _tags:            tags.into_any().unbind(),
            _tag_order:       tag_order.into_any().unbind(),
            _loops:           loops.into_any().unbind(),
            _save_frames:     sfs.into_any().unbind(),
            _save_frame_list: sf_list.into_any().unbind(),
        })?;

        block_list.append(&py_block)?;
        if !blocks.contains(&block.name)? {
            blocks.set_item(&block.name, &py_block)?;
        }
    }

    Py::new(py, PyCifFile {
        version:     parsed.version.clone(),
        _blocks:     blocks.into_any().unbind(),
        _block_list: block_list.into_any().unbind(),
    })
}

fn build_py_save_frame<'py>(
    py: Python<'py>,
    name: &str,
    id: usize,
    data: &FrameData,
) -> PyResult<Py<PyCifSaveFrame>> {
    let (tags, tag_order, loops) = frame_data_to_py(py, data)?;
    Py::new(py, PyCifSaveFrame {
        name:       name.to_string(),
        _id:        id,
        _tags:      tags.into_any().unbind(),
        _tag_order: tag_order.into_any().unbind(),
        _loops:     loops.into_any().unbind(),
    })
}
