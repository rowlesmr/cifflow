"""
Output plan dataclasses and EmitMode enum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EmitMode(Enum):
    """Controls how the database is partitioned into CIF blocks.

    ONE_BLOCK
        All data collapsed into a single CIF block named ``'output'``.

    ALL_BLOCKS
        One CIF block per schema category, plus one block per original
        ``_block_id`` from ``_cif_fallback``.

    ORIGINAL
        Rows are grouped into blocks by their original ``_block_id`` value,
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
          those tables fall back to ``_block_id`` grouping, and their output
          blocks are absorbed into any co-located Set-anchored block sharing
          the same ``_block_id``.
        - Keyless Set categories (those whose primary key is ``_pycifparse_id``
          rather than a domain key) carry no cross-block identity; they also
          fall back to ``_block_id`` grouping.

        All tables that share the same Set anchor and the same anchor key
        values are emitted in a single output block, merging rows from
        multiple original data blocks that carry the same Set-level identity.
        Block names are taken from the first anchor row's ``_block_id`` value.
    """

    ONE_BLOCK = "one_block"
    ALL_BLOCKS = "all_blocks"
    ORIGINAL = "original"
    GROUPED = "grouped"


@dataclass
class BlockSpec:
    """Emission ordering for a single output block.

    Attributes
    ----------
    categories:
        Category (table) names in emission order.  Categories not listed are
        appended alphabetically after those listed.
    column_order:
        Mapping from category name to an ordered list of column names.  Listed
        columns are emitted first; remaining columns follow alphabetically.
    """

    categories: list[str] = field(default_factory=list)
    column_order: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class OutputPlan:
    """Optional ordering specification for :func:`emit`.

    Attributes
    ----------
    blocks:
        List of :class:`BlockSpec` objects, one per output block.  If fewer
        specs are provided than there are output blocks, the last spec is
        reused for remaining blocks; if none are provided the default ordering
        applies to all blocks.
    """

    blocks: list[BlockSpec] = field(default_factory=list)

    def spec_for(self, index: int) -> BlockSpec | None:
        """Return the BlockSpec for block *index*, or ``None`` if no plan."""
        if not self.blocks:
            return None
        return self.blocks[min(index, len(self.blocks) - 1)]
