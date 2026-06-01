"""Output plan dataclasses and EmitMode enum."""

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

    STRUCTURE
        Identical to GROUPED except that blocks whose primary Set anchor is
        ``structure`` absorb related satellite blocks:

        - The ``pd_phase`` block matching ``structure.phase_id`` is merged in.
        - The ``space_group`` block matching ``structure.space_group_id`` is
          merged in (repeated in full for every structure that references it).
        - If exactly one ``model`` block has ``model.structure_id`` equal to
          this ``structure.id``, it is merged in; if more than one, each model
          block is emitted as its own separate block (unchanged from GROUPED).

        Satellite blocks that are not referenced by any ``structure`` row are
        emitted as their own blocks (unchanged from GROUPED).
    """

    ONE_BLOCK = "one_block"
    ALL_BLOCKS = "all_blocks"
    ORIGINAL = "original"
    GROUPED = "grouped"
    STRUCTURE = "structure"


# ---------------------------------------------------------------------------
# _Matcher and helper functions
# ---------------------------------------------------------------------------

class _Matcher:
    """Callable predicate wrapper returned by the routing helper functions.

    Supports ``.excluding()``, ``|``, and ``&`` for composition.

    The ``specificity`` attribute is used by :meth:`OutputPlan.match` to pick
    the best match when multiple specs match the same block.  Higher specificity
    wins regardless of spec order in the plan.  Within equal specificity, plan
    order (first-match) is preserved.

    Specificity values assigned by the factory functions:

    - ``only``: 10000 + len(cats)  — exact match always beats subset match
    - ``all_of``: len(cats)        — more required anchors = more specific
    - ``any_of``, ``has``: 1       — at-least-one match
    - composed (``|``, ``&``, ``.excluding()``): derived from operands
    """

    def __init__(
        self,
        fn: Callable[[frozenset[str], frozenset[str]], bool],
        specificity: int = 0,
    ) -> None:
        self._fn = fn
        self.specificity = specificity

    def __call__(self, anchors: frozenset[str], tables: frozenset[str]) -> bool:
        return self._fn(anchors, tables)

    def excluding(self, *categories: str) -> _Matcher:
        """Return a new :class:`_Matcher` excluding any of the given categories.

        Additionally requires none of the given category names appear in either
        the anchor or tables frozenset.
        Chainable: ``.excluding('a').excluding('b')`` ≡ ``.excluding('a', 'b')``.
        """
        excluded = frozenset(categories)
        original = self._fn
        def _fn(anchors: frozenset[str], tables: frozenset[str]) -> bool:
            if excluded & (anchors | tables):
                return False
            return original(anchors, tables)
        return _Matcher(_fn, specificity=self.specificity)

    def __or__(self, other: _Matcher) -> _Matcher:
        """Match if either *self* or *other* matches.

        Specificity is the minimum of the two operands — an OR is only as
        specific as its least specific branch.
        """
        a, b = self._fn, other._fn
        return _Matcher(
            lambda anchors, tables: a(anchors, tables) or b(anchors, tables),
            specificity=min(self.specificity, other.specificity),
        )

    def __and__(self, other: _Matcher) -> _Matcher:
        """Match if both *self* and *other* match.

        Specificity is the sum of the two operands — an AND is more specific
        than either operand alone.
        """
        a, b = self._fn, other._fn
        return _Matcher(
            lambda anchors, tables: a(anchors, tables) and b(anchors, tables),
            specificity=self.specificity + other.specificity,
        )


def only(*categories: str) -> _Matcher:
    """Match blocks whose anchor set is exactly the given set — no more, no less."""
    cats = frozenset(categories)
    return _Matcher(lambda anchors, tables: anchors == cats, specificity=10000 + len(cats))


def any_of(*categories: str) -> _Matcher:
    """Match blocks containing at least one of *categories* in the anchor frozenset."""
    cats = frozenset(categories)
    return _Matcher(lambda anchors, tables: bool(cats & anchors), specificity=1)


def all_of(*categories: str) -> _Matcher:
    """Match blocks containing all of *categories* in the anchor frozenset.

    More anchors required = higher specificity, so ``all_of('a', 'b', 'c')``
    automatically beats ``all_of('a', 'b')`` without needing to order the specs.
    """
    cats = frozenset(categories)
    return _Matcher(lambda anchors, tables: cats <= anchors, specificity=len(cats))


def has(*categories: str) -> _Matcher:
    """Match blocks containing at least one of *categories* in the full tables frozenset.

    Checks the Set **or** Loop tables frozenset.  Use this to route loop-only
    blocks that have no Set anchor without writing a lambda.
    """
    cats = frozenset(categories)
    return _Matcher(lambda anchors, tables: bool(cats & tables), specificity=1)


# ---------------------------------------------------------------------------
# block_namer helper
# ---------------------------------------------------------------------------

def namer(*keys: str, prefix: str = '', suffix: str = '', sep: str = '_', fallback: str = '?') -> Callable[[dict[str, list[str]]], str]:
    """
    Return a block_namer that builds a name from anchor key values.

    Parameters
    ----------
    *keys
        Anchor key identifiers in ``'{category}.{object_id}'`` form.  The
        first value of each key is extracted from the ``kd`` dict passed by
        the emitter.  Keys absent from ``kd`` contribute *fallback*.

          For example, a block anchored to diffrn with id='D1' would receive: {'diffrn.id': ['D1']}
          A bridge block with both pd_phase and pd_diffractogram: {'pd_diffractogram.id': ['D1'], 'pd_phase.id': ['Al2O3']}
    prefix
        String prepended to the result.
    suffix
        String appended to the result.
    sep
        Separator inserted between the extracted values.  Default ``'_'``.
    fallback
        Value used when a key is absent from ``kd``.  Default ``'?'``.

    Returns
    -------
    Callable[[dict[str, list[str]]], str]
        A ``block_namer`` compatible with :class:`BlockSpec` and
        :class:`OutputPlan`.

    Examples
    --------
    Single key with prefix:

    >>> plan = OutputPlan(specs=[BlockSpec(matches='diffrn',
    ...                                   block_namer=namer('diffrn.id', prefix='structure_'))])
    'structure_

    Multi-key bridge block:

    >>> namer('pd_phase.id', 'pd_diffractogram.id')({'pd_phase.id': ['Al2O3'], 'pd_diffractogram.id': ['D1']})
    'Al2O3_D1'
    """
    def _fn(kd: dict[str, list[str]]) -> str:
        parts = [kd.get(k, [fallback])[0] for k in keys]
        return prefix + sep.join(parts) + suffix
    return _fn


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

MatchPredicate = (
    str                                                    # 'diffrn' → any_of('diffrn')
    | set[str] | frozenset[str]                            # {'a','b'} → all_of('a','b')
    | Callable[[frozenset[str], frozenset[str]], bool]     # plain two-arg callable
    | _Matcher                                             # helper return value
    | None                                                 # catch-all
)


# ---------------------------------------------------------------------------
# BlockSpec / OutputPlan
# ---------------------------------------------------------------------------

@dataclass
class BlockSpec:
    """Emission specification for a group of output blocks.

    Attributes
    ----------
    matches:
        Predicate for block routing.  Accepted forms:

        ``None``
            Catch-all; matches any block.
        ``str``
            Equivalent to ``any_of(name)`` — matches if the name is in the
            anchor frozenset (Set-category tables with rows).
        ``set[str]`` / ``frozenset[str]``
            Equivalent to ``all_of(*names)`` — matches if every listed name
            is in the anchor frozenset.
        Two-argument callable ``(anchors, tables) -> bool``
            *anchors* is the frozenset of Set-category table names with rows;
            *tables* is the frozenset of all table names present (Set + Loop).
        :class:`_Matcher`
            Returned by :func:`only`, :func:`any_of`, :func:`all_of`,
            :func:`has`; supports ``.excluding()``, ``|``, ``&``.

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
        suppression does not apply.  Mutually exclusive with ``attach_to``.
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
    attach_to:
        When set, this block is not emitted standalone.  Instead its table
        rows are merged into the first already-resolved output block whose
        anchor and tables frozensets satisfy this predicate (same forms as
        ``matches``).  If no target is found, the block is emitted standalone
        with a ``UserWarning``.  Mutually exclusive with ``single_block``.
    """

    matches: MatchPredicate = None
    category_order: list[str | list[str]] = field(default_factory=list)
    single_block: bool = False
    column_order: dict[str, list[str]] = field(default_factory=dict)
    block_namer: Callable[[dict[str, list[str]]], str] | None = None
    attach_to: MatchPredicate = None

    def __post_init__(self) -> None:
        """Normalise and validate fields after dataclass initialisation.

        Raises
        ------
        ValueError
            If both ``single_block=True`` and ``attach_to`` are set.
        """
        if isinstance(self.matches, str):
            self.matches = any_of(self.matches)
        elif isinstance(self.matches, (set, frozenset)):
            self.matches = all_of(*self.matches)
        if isinstance(self.attach_to, str):
            self.attach_to = any_of(self.attach_to)
        elif isinstance(self.attach_to, (set, frozenset)):
            self.attach_to = all_of(*self.attach_to)
        if self.single_block and self.attach_to is not None:
            raise ValueError("BlockSpec: 'attach_to' and 'single_block' are mutually exclusive")


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

    def match(
        self,
        anchors: frozenset[str],
        tables: frozenset[str],
    ) -> tuple[int, BlockSpec] | tuple[None, None]:
        """Return ``(index, spec)`` of the first matching spec, or ``(None, None)``.

        Parameters
        ----------
        anchors
            Frozenset of Set-category table names that have rows in the block.
        tables
            Frozenset of all table names present in the block (Set + Loop).

        Returns
        -------
        tuple[int, BlockSpec] | tuple[None, None]
            ``(index, spec)`` of the first matching spec, or ``(None, None)``
            if no spec matches.
        """
        best_idx: int | None = None
        best_spec: BlockSpec | None = None
        best_specificity: int = -1
        for i, spec in enumerate(self.specs):
            if spec.matches is None:
                if best_idx is None:
                    best_idx, best_spec = i, spec
            elif spec.matches(anchors, tables):
                sp = getattr(spec.matches, 'specificity', 0)
                if sp > best_specificity:
                    best_specificity = sp
                    best_idx = i
                    best_spec = spec
        return best_idx, best_spec
