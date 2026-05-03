"""Tests for CifWriter, BlockWriter, SaveFrameWriter."""

import pytest
import warnings

from cifflow.types import CifVersion, ValueType
from cifflow.cifmodel.model import CifFile, CifBlock, CifSaveFrame
from cifflow.cifmodel.scalar import CifScalar
from cifflow.cifmodel.writer import CifWriter, BlockWriter, SaveFrameWriter


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_writer() -> CifWriter:
    return CifWriter(CifVersion.CIF_2_0)


def scalar(s: str, vt: ValueType = ValueType.STRING) -> CifScalar:
    return CifScalar(s, vt)


# ─────────────────────────────────────────────────────────────────────────────
# CifWriter — basic construction
# ─────────────────────────────────────────────────────────────────────────────

class TestCifWriterBasic:
    def test_version_readable(self):
        w = CifWriter(CifVersion.CIF_2_0)
        assert w.version == CifVersion.CIF_2_0

    def test_version_readonly(self):
        w = CifWriter(CifVersion.CIF_2_0)
        with pytest.raises(AttributeError):
            w.version = CifVersion.CIF_1_1

    def test_contains_false_initially(self):
        w = make_writer()
        assert "block1" not in w

    def test_add_block_contains(self):
        w = make_writer()
        w.add_block("block1")
        assert "block1" in w

    def test_getitem_returns_cifblock(self):
        w = make_writer()
        w.add_block("b")
        assert isinstance(w["b"], CifBlock)

    def test_getitem_missing_raises(self):
        w = make_writer()
        with pytest.raises(KeyError):
            _ = w["missing"]

    def test_get_returns_block(self):
        w = make_writer()
        w.add_block("b")
        assert w.get("b") is w["b"]

    def test_get_missing_returns_default(self):
        w = make_writer()
        assert w.get("missing") is None
        assert w.get("missing", None) is None

    def test_add_block_duplicate_raises(self):
        w = make_writer()
        w.add_block("b")
        with pytest.raises(ValueError):
            w.add_block("b")

    def test_add_block_illegal_name_raises(self):
        w = make_writer()
        with pytest.raises(ValueError):
            w.add_block("")
        with pytest.raises(ValueError):
            w.add_block("has space")

    def test_add_block_cif11_nonascii_raises(self):
        w = CifWriter(CifVersion.CIF_1_1)
        with pytest.raises(ValueError):
            w.add_block("blöck")

    def test_add_block_cif20_unicode_ok(self):
        w = make_writer()
        w.add_block("blöck")
        assert "blöck" in w

    def test_build_empty_raises(self):
        w = make_writer()
        with pytest.raises(ValueError):
            w.build()

    def test_build_returns_ciffile(self):
        w = make_writer()
        w.add_block("b")
        result = w.build()
        assert isinstance(result, CifFile)

    def test_build_returns_same_object(self):
        w = make_writer()
        w.add_block("b")
        assert w.build() is w.build()


# ─────────────────────────────────────────────────────────────────────────────
# CifWriter — wrap existing CifFile
# ─────────────────────────────────────────────────────────────────────────────

class TestCifWriterWrap:
    def test_wrap_returns_same_object_on_build(self):
        from cifflow.cifmodel.builder import build as parse_build
        cif, _ = parse_build("data_test\n_tag value\n")
        w = CifWriter(CifVersion.CIF_1_1, cif=cif)
        assert w.build() is cif

    def test_wrap_mutations_visible_on_original(self):
        from cifflow.cifmodel.builder import build as parse_build
        cif, _ = parse_build("data_test\n_tag value\n")
        w = CifWriter(CifVersion.CIF_1_1, cif=cif)
        bw = w.get_block("test")
        bw.set_tag("_new", "hello")
        assert "_new" in cif["test"]

    def test_wrap_20_with_11_warns(self):
        cif = CifFile(version=CifVersion.CIF_2_0)
        with pytest.warns(UserWarning, match="CIF 2.0"):
            CifWriter(CifVersion.CIF_1_1, cif=cif)

    def test_wrap_11_with_20_no_warn(self):
        cif = CifFile(version=CifVersion.CIF_1_1)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            CifWriter(CifVersion.CIF_2_0, cif=cif)  # should not warn


# ─────────────────────────────────────────────────────────────────────────────
# CifWriter — get_block with index
# ─────────────────────────────────────────────────────────────────────────────

class TestGetBlockIndex:
    def test_get_block_default(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", "1")
        bw2 = w.get_block("b")
        assert w["b"]["_x"][0] == CifScalar("1", ValueType.STRING)

    def test_get_block_missing_raises(self):
        w = make_writer()
        with pytest.raises(KeyError):
            w.get_block("missing")

    def test_get_block_index_out_of_range_raises(self):
        from cifflow.cifmodel.builder import build as parse_build
        cif, _ = parse_build("data_b\n_t1 v1\ndata_b\n_t2 v2\n")
        w = CifWriter(CifVersion.CIF_1_1, cif=cif)
        with pytest.raises(IndexError):
            w.get_block("b", index=5)

    def test_get_block_index_1_accesses_duplicate(self):
        from cifflow.cifmodel.builder import build as parse_build
        cif, _ = parse_build("data_b\n_t1 v1\ndata_b\n_t2 v2\n")
        w = CifWriter(CifVersion.CIF_1_1, cif=cif)
        bw1 = w.get_block("b", index=0)
        bw2 = w.get_block("b", index=1)
        assert bw1._block is not bw2._block
        assert "_t1" in bw1._ns._tags
        assert "_t2" in bw2._ns._tags


# ─────────────────────────────────────────────────────────────────────────────
# CifWriter — rename_block, remove_block
# ─────────────────────────────────────────────────────────────────────────────

class TestBlockManagement:
    def test_rename_block(self):
        w = make_writer()
        w.add_block("old")
        w.rename_block("old", "new")
        assert "new" in w
        assert "old" not in w
        assert w["new"].name == "new"

    def test_rename_block_missing_raises(self):
        w = make_writer()
        with pytest.raises(KeyError):
            w.rename_block("missing", "new")

    def test_rename_block_duplicate_raises(self):
        w = make_writer()
        w.add_block("a")
        w.add_block("b")
        with pytest.raises(ValueError):
            w.rename_block("a", "b")

    def test_rename_block_illegal_raises(self):
        w = make_writer()
        w.add_block("a")
        with pytest.raises(ValueError):
            w.rename_block("a", "")

    def test_remove_block(self):
        w = make_writer()
        w.add_block("b")
        w.remove_block("b")
        assert "b" not in w
        assert w._file._block_list == []

    def test_remove_block_missing_raises(self):
        w = make_writer()
        with pytest.raises(KeyError):
            w.remove_block("missing")

    def test_remove_block_from_end(self):
        from cifflow.cifmodel.builder import build as parse_build
        cif, _ = parse_build("data_b\n_t1 v1\ndata_b\n_t2 v2\n")
        w = CifWriter(CifVersion.CIF_1_1, cif=cif)
        w.remove_block("b", from_end=True)
        remaining = [b for b in cif._block_list if b.name == "b"]
        assert len(remaining) == 1
        assert "_t1" in remaining[0]._tags


# ─────────────────────────────────────────────────────────────────────────────
# BlockWriter — set_tag
# ─────────────────────────────────────────────────────────────────────────────

class TestSetTag:
    def test_set_tag_string(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_cell_a", "5.0")
        assert w["b"]["_cell_a"] == [CifScalar("5.0", ValueType.STRING)]

    def test_set_tag_int(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_n", 42)
        assert w["b"]["_n"] == [CifScalar("42", ValueType.STRING)]

    def test_set_tag_float(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_n", 3.14)
        assert w["b"]["_n"] == [CifScalar("3.14", ValueType.STRING)]

    def test_set_tag_placeholder_dot(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", ".")
        assert w["b"]["_x"][0].value_type == ValueType.PLACEHOLDER

    def test_set_tag_placeholder_question(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", "?")
        assert w["b"]["_x"][0].value_type == ValueType.PLACEHOLDER

    def test_set_tag_preserves_cifscalar(self):
        w = make_writer()
        bw = w.add_block("b")
        s = CifScalar("hello", ValueType.DOUBLE_QUOTED)
        bw.set_tag("_x", s)
        assert w["b"]["_x"][0] is s

    def test_set_tag_list_container(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", ["a", "b"])
        val = w["b"]["_x"][0]
        assert isinstance(val, list)
        assert val[0] == CifScalar("a", ValueType.STRING)

    def test_set_tag_missing_underscore_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        with pytest.raises(ValueError):
            bw.set_tag("notag", "v")

    def test_set_tag_duplicate_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", "1")
        with pytest.raises(ValueError):
            bw.set_tag("_x", "2")

    def test_set_tag_duplicate_loop_column_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_x": ["1", "2"]})
        with pytest.raises(ValueError):
            bw.set_tag("_x", "3")

    def test_set_tag_chaining(self):
        w = make_writer()
        bw = w.add_block("b")
        result = bw.set_tag("_x", "1")
        assert result is bw


# ─────────────────────────────────────────────────────────────────────────────
# BlockWriter — add_loop
# ─────────────────────────────────────────────────────────────────────────────

class TestAddLoop:
    def test_add_loop_basic(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1", "2"], "_b": ["x", "y"]})
        block = w["b"]
        assert block.loops == [["_a", "_b"]]
        assert block["_a"] == [CifScalar("1", ValueType.STRING), CifScalar("2", ValueType.STRING)]

    def test_add_loop_empty_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        with pytest.raises(ValueError):
            bw.add_loop({})

    def test_add_loop_unequal_lengths_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        with pytest.raises(ValueError):
            bw.add_loop({"_a": ["1", "2"], "_b": ["x"]})

    def test_add_loop_duplicate_tag_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_a", "v")
        with pytest.raises(ValueError):
            bw.add_loop({"_a": ["1"]})

    def test_add_loop_zero_length_accepted(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": [], "_b": []})
        assert w["b"].loops == [["_a", "_b"]]

    def test_add_loop_zero_length_build_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": [], "_b": []})
        with pytest.raises(ValueError):
            w.build()


# ─────────────────────────────────────────────────────────────────────────────
# BlockWriter — add_loop_column
# ─────────────────────────────────────────────────────────────────────────────

class TestAddLoopColumn:
    def test_add_loop_column_adjacent_in_tag_order(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_before", "x")
        bw.add_loop({"_a": ["1", "2"], "_b": ["x", "y"]})
        bw.set_tag("_after", "z")
        bw.add_loop_column("_a", "_c", ["p", "q"])
        tag_order = w["b"]._tag_order
        b_pos = tag_order.index("_b")
        c_pos = tag_order.index("_c")
        after_pos = tag_order.index("_after")
        assert c_pos == b_pos + 1
        assert c_pos < after_pos

    def test_add_loop_column_appended_to_loop(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"], "_b": ["x"]})
        bw.add_loop_column("_a", "_c", ["p"])
        assert w["b"].loops[0] == ["_a", "_b", "_c"]

    def test_add_loop_column_wrong_length_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1", "2"]})
        with pytest.raises(ValueError):
            bw.add_loop_column("_a", "_b", ["x"])

    def test_add_loop_column_unknown_loop_tag_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"]})
        with pytest.raises(KeyError):
            bw.add_loop_column("_missing", "_b", ["x"])

    def test_add_loop_column_duplicate_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"]})
        with pytest.raises(ValueError):
            bw.add_loop_column("_a", "_a", ["x"])


# ─────────────────────────────────────────────────────────────────────────────
# BlockWriter — reorder_loop_tags
# ─────────────────────────────────────────────────────────────────────────────

class TestReorderLoopTags:
    def test_reorder_updates_loops_and_tag_order(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"], "_b": ["x"], "_c": ["p"]})
        bw.reorder_loop_tags("_a", ["_c", "_a", "_b"])
        block = w["b"]
        assert block.loops[0] == ["_c", "_a", "_b"]
        tag_order = block._tag_order
        c_pos = tag_order.index("_c")
        a_pos = tag_order.index("_a")
        b_pos = tag_order.index("_b")
        assert c_pos < a_pos < b_pos

    def test_reorder_values_unchanged(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1", "2"], "_b": ["x", "y"]})
        bw.reorder_loop_tags("_a", ["_b", "_a"])
        assert w["b"]["_a"] == [CifScalar("1"), CifScalar("2")]
        assert w["b"]["_b"] == [CifScalar("x"), CifScalar("y")]

    def test_reorder_non_permutation_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"], "_b": ["x"]})
        with pytest.raises(ValueError):
            bw.reorder_loop_tags("_a", ["_a", "_c"])

    def test_reorder_unknown_loop_tag_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"]})
        with pytest.raises(KeyError):
            bw.reorder_loop_tags("_missing", ["_a"])

    def test_get_loop_tags_reflects_reorder(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"], "_b": ["x"]})
        bw.reorder_loop_tags("_a", ["_b", "_a"])
        assert bw.get_loop_tags("_a") == ["_b", "_a"]


# ─────────────────────────────────────────────────────────────────────────────
# BlockWriter — get_loop_tags
# ─────────────────────────────────────────────────────────────────────────────

class TestGetLoopTags:
    def test_returns_current_order(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"], "_b": ["x"]})
        assert bw.get_loop_tags("_a") == ["_a", "_b"]

    def test_unknown_tag_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"]})
        with pytest.raises(KeyError):
            bw.get_loop_tags("_missing")


# ─────────────────────────────────────────────────────────────────────────────
# BlockWriter — add_loop_row
# ─────────────────────────────────────────────────────────────────────────────

class TestAddLoopRow:
    def test_add_row_appends(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"], "_b": ["x"]})
        bw.add_loop_row("_a", ["2", "y"])
        assert w["b"]["_a"] == [CifScalar("1"), CifScalar("2")]
        assert w["b"]["_b"] == [CifScalar("x"), CifScalar("y")]

    def test_add_row_wrong_length_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"], "_b": ["x"]})
        with pytest.raises(ValueError):
            bw.add_loop_row("_a", ["2"])

    def test_add_row_unknown_tag_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"]})
        with pytest.raises(KeyError):
            bw.add_loop_row("_missing", ["2"])


# ─────────────────────────────────────────────────────────────────────────────
# BlockWriter — save frame management
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveFrameManagement:
    def test_add_save_frame_returns_writer(self):
        w = make_writer()
        bw = w.add_block("b")
        sfw = bw.add_save_frame("sf1")
        assert isinstance(sfw, SaveFrameWriter)

    def test_add_save_frame_duplicate_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_save_frame("sf1")
        with pytest.raises(ValueError):
            bw.add_save_frame("sf1")

    def test_get_save_frame(self):
        w = make_writer()
        bw = w.add_block("b")
        sfw = bw.add_save_frame("sf1")
        sfw.set_tag("_x", "v")
        sfw2 = bw.get_save_frame("sf1")
        assert sfw2._ns is sfw._ns

    def test_get_save_frame_missing_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        with pytest.raises(KeyError):
            bw.get_save_frame("missing")

    def test_get_save_frame_index_1_accesses_duplicate(self):
        from cifflow.cifmodel.builder import build as parse_build
        cif, _ = parse_build("data_b\nsave_sf\n_t1 v1\nsave_\nsave_sf\n_t2 v2\nsave_\n")
        w = CifWriter(CifVersion.CIF_1_1, cif=cif)
        bw = w.get_block("b")
        sfw1 = bw.get_save_frame("sf", index=0)
        sfw2 = bw.get_save_frame("sf", index=1)
        assert sfw1._ns is not sfw2._ns
        assert "_t1" in sfw1._ns._tags
        assert "_t2" in sfw2._ns._tags

    def test_get_save_frame_index_out_of_range_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_save_frame("sf1")
        with pytest.raises(IndexError):
            bw.get_save_frame("sf1", index=5)

    def test_mutations_through_get_save_frame_visible_on_block(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_save_frame("sf1").set_tag("_x", "v")
        sfw2 = bw.get_save_frame("sf1")
        sfw2.set_tag("_y", "z")
        assert "_y" in w["b"]["sf1"]

    def test_remove_save_frame(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_save_frame("sf1")
        bw.remove_save_frame("sf1")
        assert "sf1" not in w["b"]._save_frames
        assert w["b"]._save_frame_list == []

    def test_remove_save_frame_missing_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        with pytest.raises(KeyError):
            bw.remove_save_frame("missing")

    def test_rename_save_frame(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_save_frame("sf1")
        bw.rename_save_frame("sf1", "sf2")
        assert "sf2" in w["b"]._save_frames
        assert "sf1" not in w["b"]._save_frames
        assert w["b"]._save_frame_list[0].name == "sf2"

    def test_rename_save_frame_missing_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        with pytest.raises(KeyError):
            bw.rename_save_frame("missing", "new")

    def test_rename_save_frame_duplicate_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_save_frame("sf1")
        bw.add_save_frame("sf2")
        with pytest.raises(ValueError):
            bw.rename_save_frame("sf1", "sf2")


# ─────────────────────────────────────────────────────────────────────────────
# BlockWriter — reassign_tag
# ─────────────────────────────────────────────────────────────────────────────

class TestReassignTag:
    def test_reassign_scalar(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", "old")
        bw.reassign_tag("_x", "new")
        assert w["b"]["_x"] == [CifScalar("new")]

    def test_reassign_scalar_with_list_container(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", "old")
        bw.reassign_tag("_x", ["a", "b"])
        assert isinstance(w["b"]["_x"][0], list)

    def test_reassign_loop_column(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1", "2"], "_b": ["x", "y"]})
        bw.reassign_tag("_a", ["3", "4"])
        assert w["b"]["_a"] == [CifScalar("3"), CifScalar("4")]
        assert w["b"]["_b"] == [CifScalar("x"), CifScalar("y")]

    def test_reassign_loop_column_different_length_build_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1", "2"], "_b": ["x", "y"]})
        bw.reassign_tag("_a", ["3"])  # length 1, _b still length 2 → inconsistent
        with pytest.raises(ValueError):
            w.build()

    def test_reassign_missing_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        with pytest.raises(KeyError):
            bw.reassign_tag("_missing", "v")

    def test_reassign_scalar_duplicate_detected_by_build(self):
        # Directly inject duplicate via _append_value to simulate parser output
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", "1")
        w["b"]._append_value("_x", CifScalar("2", ValueType.STRING))
        with pytest.raises(ValueError):
            w.build()


# ─────────────────────────────────────────────────────────────────────────────
# BlockWriter — delete_tag
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteTag:
    def test_delete_scalar(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", "v")
        bw.delete_tag("_x")
        assert "_x" not in w["b"]._tags
        assert "_x" not in w["b"]._tag_order

    def test_delete_loop_column(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"], "_b": ["x"]})
        bw.delete_tag("_a")
        assert "_a" not in w["b"]._tags
        assert w["b"].loops == [["_b"]]

    def test_delete_last_loop_column_removes_loop(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"]})
        bw.delete_tag("_a")
        assert w["b"].loops == []

    def test_delete_missing_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        with pytest.raises(KeyError):
            bw.delete_tag("_missing")


# ─────────────────────────────────────────────────────────────────────────────
# BlockWriter — remove_loop_tag
# ─────────────────────────────────────────────────────────────────────────────

class TestRemoveLoopTag:
    def test_remove_loop_tag(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"], "_b": ["x"], "_c": ["p"]})
        bw.remove_loop_tag("_a", "_b")
        assert "_b" not in w["b"]._tags
        assert w["b"].loops == [["_a", "_c"]]

    def test_remove_loop_tag_empties_loop(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"]})
        bw.remove_loop_tag("_a", "_a")
        assert w["b"].loops == []

    def test_remove_loop_tag_unknown_loop_tag_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"]})
        with pytest.raises(KeyError):
            bw.remove_loop_tag("_missing", "_a")

    def test_remove_loop_tag_not_in_loop_raises(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.add_loop({"_a": ["1"], "_b": ["x"]})
        bw.add_loop({"_c": ["p"]})
        with pytest.raises(KeyError):
            bw.remove_loop_tag("_a", "_c")


# ─────────────────────────────────────────────────────────────────────────────
# CIF version rules — deferred to build()
# ─────────────────────────────────────────────────────────────────────────────

class TestCifVersionRules:
    def test_cif20_allows_list(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", ["a", "b"])
        w.build()  # should not raise

    def test_cif20_allows_dict(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", {"key": "val"})
        w.build()  # should not raise

    def test_cif11_list_accepted_by_mutation(self):
        w = CifWriter(CifVersion.CIF_1_1)
        bw = w.add_block("b")
        bw.set_tag("_x", ["a", "b"])  # should not raise here

    def test_cif11_list_rejected_by_build(self):
        w = CifWriter(CifVersion.CIF_1_1)
        bw = w.add_block("b")
        bw.set_tag("_x", ["a", "b"])
        with pytest.raises(ValueError):
            w.build()

    def test_cif11_dict_rejected_by_build(self):
        w = CifWriter(CifVersion.CIF_1_1)
        bw = w.add_block("b")
        bw.set_tag("_x", {"key": "val"})
        with pytest.raises(ValueError):
            w.build()

    def test_cif11_nested_list_in_dict_rejected_by_build(self):
        w = CifWriter(CifVersion.CIF_1_1)
        bw = w.add_block("b")
        bw.set_tag("_x", {"key": ["a", "b"]})
        with pytest.raises(ValueError):
            w.build()


# ─────────────────────────────────────────────────────────────────────────────
# ValueType inference
# ─────────────────────────────────────────────────────────────────────────────

class TestValueTypeInference:
    def test_dot_is_placeholder(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", ".")
        assert w["b"]["_x"][0].value_type == ValueType.PLACEHOLDER

    def test_question_is_placeholder(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", "?")
        assert w["b"]["_x"][0].value_type == ValueType.PLACEHOLDER

    def test_int_is_string(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", 5)
        v = w["b"]["_x"][0]
        assert v == CifScalar("5", ValueType.STRING)
        assert v.value_type == ValueType.STRING

    def test_float_is_string(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", 2.5)
        assert w["b"]["_x"][0] == CifScalar("2.5", ValueType.STRING)

    def test_other_str_is_string(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", "hello")
        assert w["b"]["_x"][0].value_type == ValueType.STRING

    def test_cifscalar_preserved(self):
        w = make_writer()
        bw = w.add_block("b")
        s = CifScalar("hello", ValueType.SINGLE_QUOTED)
        bw.set_tag("_x", s)
        assert w["b"]["_x"][0] is s
        assert w["b"]["_x"][0].value_type == ValueType.SINGLE_QUOTED

    def test_nested_list_inferred_recursively(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", [".", 5])
        inner = w["b"]["_x"][0]
        assert isinstance(inner, list)
        assert inner[0].value_type == ValueType.PLACEHOLDER
        assert inner[1].value_type == ValueType.STRING

    def test_nested_dict_inferred_recursively(self):
        w = make_writer()
        bw = w.add_block("b")
        bw.set_tag("_x", {"k": "?"})
        inner = w["b"]["_x"][0]
        assert isinstance(inner, dict)
        assert inner["k"].value_type == ValueType.PLACEHOLDER


# ─────────────────────────────────────────────────────────────────────────────
# SaveFrameWriter — representative cases
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveFrameWriter:
    def test_set_tag_in_save_frame(self):
        w = make_writer()
        bw = w.add_block("b")
        sfw = bw.add_save_frame("sf")
        sfw.set_tag("_x", "v")
        assert w["b"]["sf"]["_x"] == [CifScalar("v")]

    def test_add_loop_in_save_frame(self):
        w = make_writer()
        bw = w.add_block("b")
        sfw = bw.add_save_frame("sf")
        sfw.add_loop({"_a": ["1", "2"]})
        assert w["b"]["sf"].loops == [["_a"]]

    def test_delete_tag_in_save_frame(self):
        w = make_writer()
        bw = w.add_block("b")
        sfw = bw.add_save_frame("sf")
        sfw.set_tag("_x", "v")
        sfw.delete_tag("_x")
        assert "_x" not in w["b"]["sf"]._tags

    def test_reassign_tag_in_save_frame(self):
        w = make_writer()
        bw = w.add_block("b")
        sfw = bw.add_save_frame("sf")
        sfw.set_tag("_x", "old")
        sfw.reassign_tag("_x", "new")
        assert w["b"]["sf"]["_x"] == [CifScalar("new")]

    def test_add_loop_row_in_save_frame(self):
        w = make_writer()
        bw = w.add_block("b")
        sfw = bw.add_save_frame("sf")
        sfw.add_loop({"_a": ["1"]})
        sfw.add_loop_row("_a", ["2"])
        assert len(w["b"]["sf"]["_a"]) == 2

    def test_remove_loop_tag_in_save_frame(self):
        w = make_writer()
        bw = w.add_block("b")
        sfw = bw.add_save_frame("sf")
        sfw.add_loop({"_a": ["1"], "_b": ["x"]})
        sfw.remove_loop_tag("_a", "_b")
        assert w["b"]["sf"].loops == [["_a"]]

    def test_add_loop_column_in_save_frame(self):
        w = make_writer()
        bw = w.add_block("b")
        sfw = bw.add_save_frame("sf")
        sfw.add_loop({"_a": ["1"]})
        sfw.add_loop_column("_a", "_b", ["x"])
        assert w["b"]["sf"].loops == [["_a", "_b"]]

    def test_reorder_loop_tags_in_save_frame(self):
        w = make_writer()
        bw = w.add_block("b")
        sfw = bw.add_save_frame("sf")
        sfw.add_loop({"_a": ["1"], "_b": ["x"]})
        sfw.reorder_loop_tags("_a", ["_b", "_a"])
        assert w["b"]["sf"].loops == [["_b", "_a"]]

    def test_get_loop_tags_in_save_frame(self):
        w = make_writer()
        bw = w.add_block("b")
        sfw = bw.add_save_frame("sf")
        sfw.add_loop({"_a": ["1"], "_b": ["x"]})
        assert sfw.get_loop_tags("_a") == ["_a", "_b"]


# ─────────────────────────────────────────────────────────────────────────────
# Full build verification
# ─────────────────────────────────────────────────────────────────────────────

class TestFullBuild:
    def test_complex_file(self):
        w = make_writer()
        b1 = w.add_block("block1")
        b1.set_tag("_title", "Test")
        b1.add_loop({"_atom": ["C", "N"], "_x": ["1.0", "2.0"]})
        sf = b1.add_save_frame("frame1")
        sf.set_tag("_def", "something")

        b2 = w.add_block("block2")
        b2.set_tag("_note", "hello")

        cif = w.build()
        assert cif.blocks == ["block1", "block2"]
        assert cif["block1"]["_title"] == [CifScalar("Test")]
        assert cif["block1"].loops == [["_atom", "_x"]]
        assert cif["block1"]["frame1"]["_def"] == [CifScalar("something")]
        assert cif["block2"]["_note"] == [CifScalar("hello")]
