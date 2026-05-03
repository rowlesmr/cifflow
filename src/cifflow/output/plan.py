"""
Output plan dataclasses and EmitMode enum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class EmitMode(Enum):
    """Controls how the database is partitioned into CIF blocks.

    ONE_BLOCK
        All data collapsed into a single CIF block named ``'output'``.

    ALL_BLOCKS
        One CIF block per schema category, plus one block per original
        ``_cifflow_block_id`` from ``_cif_fallback``.

    ORIGINAL
        Rows are grouped into blocks by their original ``_cifflow_block_id`` value,
        reconstructing the CIF blocks as they were before ingestion.  This is
        the simple inverse of ingestion and the default.

    GROUPED
        Rows are grouped by Set-category anchor key values.  For each table
        the FK graph is searched (BFS) for the nearest Set-class ancestor:

        - If a Set is reachable, that Set is the anchor.  Tables with
          composite keys — where some FK paths lead to Loop tables and others
          lead to a Set — are correctly anchored to the Set even when the Set
          path is not the first FK in the list.
        - If no Set is reachable (the FK chain terminates at Loop tables only),
          those tables fall back to ``_cifflow_block_id`` grouping (equivalent to
          ORIGINAL for those tables).
        - Keyless Set categories (those whose primary key is ``_cifflow_id``
          rather than a domain key) carry no cross-block identity; they also
          fall back to ``_cifflow_block_id`` grouping.

        All tables that share the same Set anchor and the same anchor key
        values are emitted in a single output block, merging rows from
        multiple original data blocks that carry the same Set-level identity.
    """

    ONE_BLOCK = "one_block"
    ALL_BLOCKS = "all_blocks"
    ORIGINAL = "original"
    GROUPED = "grouped"


@dataclass
class BlockSpec:
    """Emission specification for a group of output blocks.

    Attributes
    ----------
    matches:
        Predicate receiving the ``frozenset`` of Set-category table names
        present in a candidate block; returns ``True`` if this spec applies.
        ``None`` is treated as a catch-all (matches any block).
        First-match wins across the ordered list in ``OutputPlan.specs``.
    category_order:
        Categories in emission order within a block.  A plain ``str`` names a
        single category.  A ``str`` ending with ``'*'`` expands to that
        category plus all schema descendants, alphabetically.  An inner
        ``list[str]`` is a merge group: compatible categories (sharing
        identical non-synthetic PK columns) are emitted as a single
        ``loop_`` via a FULL OUTER JOIN; incompatible categories fall back to
        plain loops in the listed order.  Categories not listed are appended
        alphabetically (Set-class first) after those listed.
    single_block:
        When ``False`` (default), one output block is produced per unique
        combination of anchor key values matching this spec.  When ``True``,
        all data matching this spec is collapsed into a single output block;
        Set-category key columns are emitted as loop columns and FK-PK
        suppression does not apply.
    column_order:
        ``category_name → [col_name, ...]``.  Listed columns appear first
        within their category; remaining columns follow alphabetically.
    block_namer:
        Optional per-spec block name override.  Receives a dict mapping
        ``'{category}.{object_id}'`` → ``[key_value, ...]`` (single-element
        list when ``single_block=False``; all values when ``single_block=True``)
        and returns the desired block name as a plain string.  Sanitization
        and disambiguation are still applied by the emitter.  Falls back to
        ``OutputPlan.block_namer``, then to the default construction rule.
    """

    matches: Callable[[frozenset[str]], bool] | None = None
    category_order: list[str | list[str]] = field(default_factory=list)
    single_block: bool = False
    column_order: dict[str, list[str]] = field(default_factory=dict)
    block_namer: Callable[[dict[str, list[str]]], str] | None = None


@dataclass
class OutputPlan:
    """Optional ordering and grouping specification for :func:`emit`.

    Attributes
    ----------
    specs:
        Ordered list of :class:`BlockSpec` objects.  For each output block
        the emitter evaluates specs in order and assigns the first matching
        spec (first-match wins).  Blocks with no matching spec use default
        alphabetical category ordering.

        Emission order: all blocks assigned to ``specs[0]`` are emitted
        first, then ``specs[1]``, etc.  Unmatched blocks are emitted last
        in alphabetical order by block name.  Within a single spec, multiple
        matching blocks are emitted in alphabetical order by block name.

        An empty list (default) means all blocks use default ordering.
    block_namer:
        Global fallback block_namer (same signature as
        ``BlockSpec.block_namer``) used when the matched ``BlockSpec`` has no
        ``block_namer`` of its own.  When ``None``, the default construction
        rule applies.
    """

    specs: list[BlockSpec] = field(default_factory=list)
    block_namer: Callable[[dict[str, list[str]]], str] | None = None

    def match(self, anchor_frozenset: frozenset[str]) -> tuple[int, BlockSpec] | tuple[None, None]:
        """Return ``(index, spec)`` of the first matching spec, or ``(None, None)``."""
        for i, spec in enumerate(self.specs):
            if spec.matches is None or spec.matches(anchor_frozenset):
                return i, spec
        return None, None
