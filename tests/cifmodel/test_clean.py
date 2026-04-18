"""Tests for clean() and CleanWarning."""

import pytest

from pycifparse.types import CifVersion, ValueType
from pycifparse.cifmodel.builder import build as parse_build
from pycifparse.cifmodel.model import CifFile
from pycifparse.cifmodel.scalar import CifScalar
from pycifparse.cifmodel.clean import clean, CleanWarning


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_cif_with_error_value() -> CifFile:
    # Orphan value (no preceding tag) → stored under _error_value
    src = "data_b\n_tag value\norphan\n"
    cif, _ = parse_build(src)
    return cif


def _make_cif_with_duplicate_blocks() -> CifFile:
    src = "data_b\n_t1 v1\ndata_b\n_t2 v2\n"
    cif, _ = parse_build(src)
    return cif


def _make_cif_with_duplicate_save_frames() -> CifFile:
    src = "data_b\nsave_sf\n_t1 v1\nsave_\nsave_sf\n_t2 v2\nsave_\n"
    cif, _ = parse_build(src)
    return cif


def _make_cif_with_duplicate_tags() -> CifFile:
    # Two assignments to _tag (parser appends both)
    from pycifparse.cifmodel.writer import CifWriter
    from pycifparse.cifmodel.model import CifFile
    cif = CifFile(version=CifVersion.CIF_1_1)
    from pycifparse.cifmodel.model import CifBlock
    from pycifparse.cifmodel.scalar import CifScalar
    block = CifBlock("b")
    cif._add_block(block)
    block._append_value("_x", CifScalar("first", ValueType.STRING))
    block._append_value("_x", CifScalar("second", ValueType.STRING))
    return cif


def _make_cif_with_padded_loop() -> CifFile:
    # Directly build a 2-column loop where the last row is all PLACEHOLDERs,
    # simulating what CifBuilder pad mode produces when the incomplete final row
    # happens to have all missing values filled with '?'.
    from pycifparse.cifmodel.model import CifBlock
    cif = CifFile(version=CifVersion.CIF_1_1)
    block = CifBlock("b")
    cif._add_block(block)
    ph = CifScalar("?", ValueType.PLACEHOLDER)
    block._add_loop(
        ["_a", "_b"],
        {
            "_a": [CifScalar("1", ValueType.STRING), ph],
            "_b": [CifScalar("x", ValueType.STRING), ph],
        },
    )
    return cif


# ─────────────────────────────────────────────────────────────────────────────
# remove_error_values
# ─────────────────────────────────────────────────────────────────────────────

class TestRemoveErrorValues:
    def test_removes_error_tag(self):
        cif = _make_cif_with_error_value()
        result, warnings = clean(cif, deduplicate_blocks=False,
                                  deduplicate_save_frames=False,
                                  deduplicate_tags=False,
                                  strip_loop_padding=False)
        assert "_error_value" not in result["b"]._tags

    def test_warning_emitted(self):
        cif = _make_cif_with_error_value()
        _, warnings = clean(cif, deduplicate_blocks=False,
                             deduplicate_save_frames=False,
                             deduplicate_tags=False,
                             strip_loop_padding=False)
        cats = [w.category for w in warnings]
        assert 'remove_error_values' in cats

    def test_disabled_leaves_tag(self):
        cif = _make_cif_with_error_value()
        result, warnings = clean(cif, remove_error_values=False,
                                  deduplicate_blocks=False,
                                  deduplicate_save_frames=False,
                                  deduplicate_tags=False,
                                  strip_loop_padding=False)
        assert "_error_value" in result["b"]._tags
        assert not warnings


# ─────────────────────────────────────────────────────────────────────────────
# deduplicate_blocks
# ─────────────────────────────────────────────────────────────────────────────

class TestDeduplicateBlocks:
    def test_keeps_first(self):
        cif = _make_cif_with_duplicate_blocks()
        result, warnings = clean(cif, remove_error_values=False,
                                  deduplicate_blocks='first',
                                  deduplicate_save_frames=False,
                                  deduplicate_tags=False,
                                  strip_loop_padding=False)
        assert len(result._block_list) == 1
        assert "_t1" in result["b"]._tags

    def test_keeps_last(self):
        cif = _make_cif_with_duplicate_blocks()
        result, warnings = clean(cif, remove_error_values=False,
                                  deduplicate_blocks='last',
                                  deduplicate_save_frames=False,
                                  deduplicate_tags=False,
                                  strip_loop_padding=False)
        assert len(result._block_list) == 1
        assert "_t2" in result["b"]._tags

    def test_warning_emitted(self):
        cif = _make_cif_with_duplicate_blocks()
        _, warnings = clean(cif, remove_error_values=False,
                             deduplicate_save_frames=False,
                             deduplicate_tags=False,
                             strip_loop_padding=False)
        cats = [w.category for w in warnings]
        assert 'deduplicate_blocks' in cats

    def test_disabled_leaves_duplicates(self):
        cif = _make_cif_with_duplicate_blocks()
        result, warnings = clean(cif, remove_error_values=False,
                                  deduplicate_blocks=False,
                                  deduplicate_save_frames=False,
                                  deduplicate_tags=False,
                                  strip_loop_padding=False)
        assert len(result._block_list) == 2


# ─────────────────────────────────────────────────────────────────────────────
# deduplicate_save_frames
# ─────────────────────────────────────────────────────────────────────────────

class TestDeduplicateSaveFrames:
    def test_keeps_first(self):
        cif = _make_cif_with_duplicate_save_frames()
        result, warnings = clean(cif, remove_error_values=False,
                                  deduplicate_blocks=False,
                                  deduplicate_save_frames='first',
                                  deduplicate_tags=False,
                                  strip_loop_padding=False)
        assert len(result["b"]._save_frame_list) == 1
        assert "_t1" in result["b"]["sf"]._tags

    def test_keeps_last(self):
        cif = _make_cif_with_duplicate_save_frames()
        result, warnings = clean(cif, remove_error_values=False,
                                  deduplicate_blocks=False,
                                  deduplicate_save_frames='last',
                                  deduplicate_tags=False,
                                  strip_loop_padding=False)
        assert len(result["b"]._save_frame_list) == 1
        assert "_t2" in result["b"]["sf"]._tags

    def test_warning_emitted(self):
        cif = _make_cif_with_duplicate_save_frames()
        _, warnings = clean(cif, remove_error_values=False,
                             deduplicate_blocks=False,
                             deduplicate_tags=False,
                             strip_loop_padding=False)
        cats = [w.category for w in warnings]
        assert 'deduplicate_save_frames' in cats

    def test_disabled_leaves_duplicates(self):
        cif = _make_cif_with_duplicate_save_frames()
        result, warnings = clean(cif, remove_error_values=False,
                                  deduplicate_blocks=False,
                                  deduplicate_save_frames=False,
                                  deduplicate_tags=False,
                                  strip_loop_padding=False)
        assert len(result["b"]._save_frame_list) == 2


# ─────────────────────────────────────────────────────────────────────────────
# deduplicate_tags
# ─────────────────────────────────────────────────────────────────────────────

class TestDeduplicateTags:
    def test_keeps_first(self):
        cif = _make_cif_with_duplicate_tags()
        result, warnings = clean(cif, remove_error_values=False,
                                  deduplicate_blocks=False,
                                  deduplicate_save_frames=False,
                                  deduplicate_tags='first',
                                  strip_loop_padding=False)
        assert result["b"]["_x"] == [CifScalar("first", ValueType.STRING)]

    def test_keeps_last(self):
        cif = _make_cif_with_duplicate_tags()
        result, warnings = clean(cif, remove_error_values=False,
                                  deduplicate_blocks=False,
                                  deduplicate_save_frames=False,
                                  deduplicate_tags='last',
                                  strip_loop_padding=False)
        assert result["b"]["_x"] == [CifScalar("second", ValueType.STRING)]

    def test_warning_emitted(self):
        cif = _make_cif_with_duplicate_tags()
        _, warnings = clean(cif, remove_error_values=False,
                             deduplicate_blocks=False,
                             deduplicate_save_frames=False,
                             strip_loop_padding=False)
        cats = [w.category for w in warnings]
        assert 'deduplicate_tags' in cats

    def test_disabled_leaves_duplicates(self):
        cif = _make_cif_with_duplicate_tags()
        result, warnings = clean(cif, remove_error_values=False,
                                  deduplicate_blocks=False,
                                  deduplicate_save_frames=False,
                                  deduplicate_tags=False,
                                  strip_loop_padding=False)
        assert len(result["b"]["_x"]) == 2

    def test_loop_columns_not_touched(self):
        src = "data_b\nloop_\n_a\n_b\n1 x\n2 y\n"
        cif, _ = parse_build(src)
        result, warnings = clean(cif, remove_error_values=False,
                                  deduplicate_blocks=False,
                                  deduplicate_save_frames=False,
                                  deduplicate_tags='first',
                                  strip_loop_padding=False)
        assert len(result["b"]["_a"]) == 2
        assert len(result["b"]["_b"]) == 2
        assert not any(w.category == 'deduplicate_tags' for w in warnings)


# ─────────────────────────────────────────────────────────────────────────────
# strip_loop_padding
# ─────────────────────────────────────────────────────────────────────────────

class TestStripLoopPadding:
    def test_strips_padding(self):
        cif = _make_cif_with_padded_loop()
        result, warnings = clean(cif, remove_error_values=False,
                                  deduplicate_blocks=False,
                                  deduplicate_save_frames=False,
                                  deduplicate_tags=False)
        # After stripping the phantom row, each column should have 1 row
        assert len(result["b"]["_a"]) == 1
        assert len(result["b"]["_b"]) == 1

    def test_warning_emitted(self):
        cif = _make_cif_with_padded_loop()
        _, warnings = clean(cif, remove_error_values=False,
                             deduplicate_blocks=False,
                             deduplicate_save_frames=False,
                             deduplicate_tags=False)
        cats = [w.category for w in warnings]
        assert 'strip_loop_padding' in cats

    def test_legitimate_middle_placeholder_preserved(self):
        # Loop where ? appears in the middle row — must not be stripped.
        # k = min(trailing PLACEHOLDERs per column): _a has 0 trailing, _b has 0 trailing → k=0.
        from pycifparse.cifmodel.model import CifBlock
        cif = CifFile(version=CifVersion.CIF_1_1)
        block = CifBlock("b")
        cif._add_block(block)
        ph = CifScalar("?", ValueType.PLACEHOLDER)
        block._add_loop(
            ["_a", "_b"],
            {
                "_a": [CifScalar("1"), ph, CifScalar("3")],
                "_b": [CifScalar("x"), CifScalar("y"), CifScalar("z")],
            },
        )
        result, warnings = clean(cif, remove_error_values=False,
                                  deduplicate_blocks=False,
                                  deduplicate_save_frames=False,
                                  deduplicate_tags=False)
        assert len(result["b"]["_a"]) == 3
        assert not any(w.category == 'strip_loop_padding' for w in warnings)


# ─────────────────────────────────────────────────────────────────────────────
# copy semantics
# ─────────────────────────────────────────────────────────────────────────────

class TestCopySemantics:
    def test_copy_true_original_unmodified(self):
        cif = _make_cif_with_error_value()
        assert "_error_value" in cif["b"]._tags
        clean(cif)
        assert "_error_value" in cif["b"]._tags  # original untouched

    def test_copy_false_returns_same_object(self):
        cif = _make_cif_with_error_value()
        result, _ = clean(cif, copy=False)
        assert result is cif

    def test_copy_true_returns_different_object(self):
        cif = _make_cif_with_error_value()
        result, _ = clean(cif, copy=True)
        assert result is not cif
