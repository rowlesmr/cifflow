"""
Cleaning API for parser-produced CifFile objects.

clean() removes well-known parse-time artefacts:
  - orphan error values (_pycifparse_error_value synthetic tag)
  - duplicate blocks, save frames, and scalar tags
  - loop padding added by CifBuilder in pad mode

Returns a new CifFile (copy=True, default) or mutates in place (copy=False).
Every removal produces a CleanWarning — nothing is silently discarded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pycifparse.cifmodel.model import CifFile, CifSaveFrame
from pycifparse.cifmodel.writer import CifWriter, BlockWriter, SaveFrameWriter

Keep = Literal['first', 'last']

_ERROR_TAG = '_pycifparse_error_value'


@dataclass
class CleanWarning:
    category: str        # step name, e.g. 'deduplicate_blocks'
    block: str | None
    save_frame: str | None
    message: str


def clean(
    cif: CifFile,
    *,
    copy: bool = True,
    remove_error_values: bool = True,
    deduplicate_blocks: Keep | Literal[False] = 'first',
    deduplicate_save_frames: Keep | Literal[False] = 'first',
    deduplicate_tags: Keep | Literal[False] = 'first',
    strip_loop_padding: bool = True,
) -> tuple[CifFile, list[CleanWarning]]:
    """
    Clean parse-time artefacts from a CifFile.

    Returns (cleaned_cif, warnings).  When copy=True (default) the input is
    not modified.  When copy=False the input is mutated in place and returned.
    """
    target = cif.deepcopy() if copy else cif
    out: list[CleanWarning] = []
    writer = CifWriter(version=target.version, cif=target)

    if remove_error_values:
        _step_remove_error_values(target, writer, out)

    if deduplicate_blocks is not False:
        _step_deduplicate_blocks(target, writer, deduplicate_blocks, out)

    if deduplicate_save_frames is not False:
        _step_deduplicate_save_frames(target, writer, deduplicate_save_frames, out)

    if deduplicate_tags is not False:
        _step_deduplicate_tags(target, writer, deduplicate_tags, out)

    if strip_loop_padding:
        _step_strip_loop_padding(target, writer, out)

    return target, out


# ─────────────────────────────────────────────────────────────────────────────
# Step implementations
# ─────────────────────────────────────────────────────────────────────────────

def _step_remove_error_values(
    cif: CifFile,
    writer: CifWriter,
    out: list[CleanWarning],
) -> None:
    for block in cif._block_list:
        bw = writer.get_block(block.name)
        _remove_error_from_ns(block, bw, block.name, None, out)
        for sf in block._save_frame_list:
            sfw = bw.get_save_frame(sf.name, index=block._save_frame_list.index(sf))
            _remove_error_from_ns(sf, sfw, block.name, sf.name, out)


def _remove_error_from_ns(
    ns: CifSaveFrame,
    nsw: SaveFrameWriter,
    block_name: str,
    save_frame_name: str | None,
    out: list[CleanWarning],
) -> None:
    if _ERROR_TAG in ns:
        count = len(ns[_ERROR_TAG])
        nsw.delete_tag(_ERROR_TAG)
        out.append(CleanWarning(
            category='remove_error_values',
            block=block_name,
            save_frame=save_frame_name,
            message=f"Removed {count} orphan error value(s) tagged {_ERROR_TAG!r}",
        ))


def _step_deduplicate_blocks(
    cif: CifFile,
    writer: CifWriter,
    keep: Keep,
    out: list[CleanWarning],
) -> None:
    # Collect counts first — do not iterate _block_list while removing
    seen: dict[str, int] = {}
    for b in cif._block_list:
        seen[b.name] = seen.get(b.name, 0) + 1
    for name, count in seen.items():
        if count > 1:
            removed = 0
            while sum(1 for b in cif._block_list if b.name == name) > 1:
                writer.remove_block(name, from_end=(keep == 'first'))
                removed += 1
            out.append(CleanWarning(
                category='deduplicate_blocks',
                block=name,
                save_frame=None,
                message=f"Removed {removed} duplicate(s) of block {name!r}",
            ))


def _step_deduplicate_save_frames(
    cif: CifFile,
    writer: CifWriter,
    keep: Keep,
    out: list[CleanWarning],
) -> None:
    for block in cif._block_list:
        bw = writer.get_block(block.name)
        seen: dict[str, int] = {}
        for sf in block._save_frame_list:
            seen[sf.name] = seen.get(sf.name, 0) + 1
        for name, count in seen.items():
            if count > 1:
                removed = 0
                while sum(1 for sf in block._save_frame_list if sf.name == name) > 1:
                    bw.remove_save_frame(name, from_end=(keep == 'first'))
                    removed += 1
                out.append(CleanWarning(
                    category='deduplicate_save_frames',
                    block=block.name,
                    save_frame=name,
                    message=(
                        f"Removed {removed} duplicate(s) of save frame {name!r} "
                        f"in block {block.name!r}"
                    ),
                ))


def _step_deduplicate_tags(
    cif: CifFile,
    writer: CifWriter,
    keep: Keep,
    out: list[CleanWarning],
) -> None:
    for block in cif._block_list:
        bw = writer.get_block(block.name)
        _dedup_tags_in_ns(block, bw, block.name, None, keep, out)
        for sf in block._save_frame_list:
            sfw = bw.get_save_frame(sf.name, index=block._save_frame_list.index(sf))
            _dedup_tags_in_ns(sf, sfw, block.name, sf.name, keep, out)


def _dedup_tags_in_ns(
    ns: CifSaveFrame,
    nsw: SaveFrameWriter,
    block_name: str,
    save_frame_name: str | None,
    keep: Keep,
    out: list[CleanWarning],
) -> None:
    loop_tags: set[str] = {t for loop in ns._loops for t in loop}
    for tag in list(ns._tag_order):
        if tag in loop_tags:
            continue
        values = ns._tags.get(tag, [])
        if len(values) > 1:
            kept = values[0] if keep == 'first' else values[-1]
            dropped = len(values) - 1
            nsw.reassign_tag(tag, kept)
            out.append(CleanWarning(
                category='deduplicate_tags',
                block=block_name,
                save_frame=save_frame_name,
                message=(
                    f"Tag {tag!r}: kept {keep!r} of {len(values)} values, "
                    f"dropped {dropped}"
                ),
            ))


def _step_strip_loop_padding(
    cif: CifFile,
    writer: CifWriter,
    out: list[CleanWarning],
) -> None:
    for block in cif._block_list:
        bw = writer.get_block(block.name)
        _strip_padding_in_ns(block, bw, block.name, None, out)
        for sf in block._save_frame_list:
            sfw = bw.get_save_frame(sf.name, index=block._save_frame_list.index(sf))
            _strip_padding_in_ns(sf, sfw, block.name, sf.name, out)


def _trailing_placeholder_count(values: list) -> int:
    count = 0
    for v in reversed(values):
        if v == '?':
            count += 1
        else:
            break
    return count


def _strip_padding_in_ns(
    ns: CifSaveFrame,
    nsw: SaveFrameWriter,
    block_name: str,
    save_frame_name: str | None,
    out: list[CleanWarning],
) -> None:
    for loop in list(ns._loops):
        n = len(loop)
        if n == 0:
            continue
        columns = [ns._tags.get(tag, []) for tag in loop]
        if not columns or not columns[0]:
            continue
        trailing = [_trailing_placeholder_count(col) for col in columns]
        k = min(trailing)
        k = min(k, n - 1)
        if k <= 0:
            continue
        for tag in loop:
            nsw.reassign_tag(tag, list(ns._tags[tag][:-k]))
        out.append(CleanWarning(
            category='strip_loop_padding',
            block=block_name,
            save_frame=save_frame_name,
            message=f"Stripped {k} padding row(s) from loop {loop!r}",
        ))
