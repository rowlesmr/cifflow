"""
SQLite schema generation from a loaded DDLm dictionary.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from pycifparse.dictionary.ddlm_item import DdlmItem
from pycifparse.dictionary.ddlm_parser import DdlmDictionary


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BridgeColumnDef:
    """
    A column whose value is derived transitively through another table.

    When populating ``table_name``, the column ``column_name`` has no direct
    CIF source.  Its value must be fetched from
    ``bridge_table.bridge_value_column`` by looking up the row where
    ``bridge_table.bridge_pk_column = row[via_column]``.

    Attributes
    ----------
    table_name:
        Table that gains the derived column (e.g. ``'geom_angle'``).
    column_name:
        Name of the derived column (e.g. ``'structure_id'``).
    via_column:
        Column in *table_name* used as the lookup key (e.g. ``'model_id'``).
    bridge_table:
        Intermediate table to query (e.g. ``'model'``).
    bridge_pk_column:
        PK column of *bridge_table* matched against *via_column*
        (e.g. ``'id'``).
    bridge_value_column:
        Column in *bridge_table* whose value is copied (e.g. ``'structure_id'``).
    """

    table_name: str
    column_name: str
    via_column: str
    bridge_table: str
    bridge_pk_column: str
    bridge_value_column: str


@dataclass
class ForeignKeyDef:
    """
    A ``FOREIGN KEY`` constraint between two tables (single- or multi-column).

    Always emitted with ``DEFERRABLE INITIALLY DEFERRED`` to handle cyclic
    category graphs correctly within a transaction.

    Attributes
    ----------
    source_table:
        Name of the table that holds the foreign key column(s).
    source_columns:
        Ordered list of foreign key column names in *source_table*.
    target_table:
        Name of the table being referenced.
    target_columns:
        Ordered list of column names being referenced in *target_table*,
        corresponding positionally to *source_columns*.
    """

    source_table: str
    source_columns: list[str]
    target_table: str
    target_columns: list[str]


@dataclass
class ColumnDef:
    """
    Definition of a single column in a generated SQLite table.

    Attributes
    ----------
    name:
        SQL column name, equal to the DDLm ``_name.object_id``, lowercased.
        For synthetic columns the name is ``_block_id``, ``_row_id``, or
        ``_pycifparse_id``.
    definition_id:
        The current canonical ``_definition.id`` for this column's DDLm item.
        Empty string for synthetic columns.
    type_contents:
        DDLm ``_type.contents`` value (e.g. ``"Text"``, ``"Integer"``,
        ``"Real"``, ``"List"``); ``None`` if absent from the dictionary or for
        synthetic columns.  Informational only — DDL always emits ``TEXT`` for
        all value columns; ``_row_id`` always emits ``INTEGER``.
    nullable:
        ``False`` for synthetic and primary-key columns; ``True`` for all
        other domain columns.
    is_primary_key:
        ``True`` if this column is part of the table's ``PRIMARY KEY``.
    is_synthetic:
        ``True`` for the ``_block_id``, ``_row_id``, and ``_pycifparse_id``
        infrastructure columns, which have no corresponding DDLm item
        definition.
    linked_item_id:
        For ``SU`` items only: the ``_definition.id`` of the associated
        measurand item, lowercased.  ``None`` for all other column types.
        Does not produce a ``FOREIGN KEY`` constraint; used by the ingestion
        and output layers.
    """

    name: str
    definition_id: str
    type_contents: str | None
    nullable: bool
    is_primary_key: bool
    is_synthetic: bool
    linked_item_id: str | None


@dataclass
class TableDef:
    """
    Definition of a single SQLite table generated from a DDLm category.

    Attributes
    ----------
    name:
        SQL table name, derived from the category's ``_definition.id``
        (lowercased, leading ``_`` stripped, ``.`` replaced with ``_``).
    definition_id:
        The ``_definition.id`` of the category save frame that produced
        this table.
    category_class:
        DDLm class of the source category: ``"Set"`` or ``"Loop"``.
    columns:
        Ordered list of column definitions.  Order follows the column-ordering
        rule: ``_block_id``, ``_pycifparse_id`` (keyless Set only),
        ``_row_id``, primary-key domain columns, remaining domain columns
        alphabetically.
    primary_keys:
        Column names forming the ``PRIMARY KEY``, in declaration order.
    foreign_keys:
        ``FOREIGN KEY`` constraints on this table; empty when none exist.
    """

    name: str
    definition_id: str
    category_class: str
    columns: list[ColumnDef]
    primary_keys: list[str]
    foreign_keys: list[ForeignKeyDef] = field(default_factory=list)


@dataclass
class SchemaSpec:
    """
    Complete SQLite schema derived from a ``DdlmDictionary``.

    Produced by :func:`generate_schema` and consumed by
    :func:`emit_create_statements` and
    :func:`~pycifparse.dictionary.schema_apply.apply_schema`.

    Attributes
    ----------
    tables:
        Mapping from SQL table name to its :class:`TableDef`.
    column_to_tag:
        Reverse mapping from ``(table_name, column_name)`` to the canonical
        ``_definition.id`` of the corresponding DDLm item.  Synthetic
        columns (``_block_id``, ``_row_id``, ``_pycifparse_id``) are excluded.
    alias_to_definition_id:
        Old tag name → canonical ``_definition.id``.  Copied from
        ``DdlmDictionary.alias_to_definition_id`` by ``generate_schema``.
        Used by ``ingest()`` for alias resolution without retaining a
        dictionary reference.
    deprecated_ids:
        Set of ``_definition.id`` values marked as deprecated.  Copied from
        ``DdlmDictionary.deprecated_ids`` by ``generate_schema``.  Used by
        ``ingest()`` to emit deprecation warnings.
    warnings:
        Non-fatal issues encountered during schema generation, in emission
        order.
    """

    tables: dict[str, TableDef]
    column_to_tag: dict[tuple[str, str], str]
    alias_to_definition_id: dict[str, str] = field(default_factory=dict)
    deprecated_ids: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    bridge_columns: list[BridgeColumnDef] = field(default_factory=list)
    propagation_links: dict[str, list[tuple[str, str, str | None]]] = field(default_factory=dict)
    """Mapping from table name to ``[(column_name, target_def_id, default), ...]``.

    For PK columns that are DDLm ``Link`` items but whose ``FOREIGN KEY``
    constraint was skipped at schema generation time (e.g. because the FK
    target is not a PK of the target table), the ingest layer still needs to
    propagate the value from the ``fk_accumulator`` or the current loop row.
    Each entry records the target ``_definition.id`` to look up.
    """


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_SYNTHETIC_NAMES: frozenset[str] = frozenset({'_block_id', '_row_id', '_pycifparse_id'})


def _table_name(category_id: str) -> str:
    """Derive a SQL table name from a lowercased ``_name.category_id`` value."""
    return category_id.lstrip('_').replace('.', '_')


def _qi(name: str) -> str:
    """Quote a SQL identifier with double quotes, escaping embedded quotes."""
    return '"' + name.replace('"', '""') + '"'


def _ddl_type(col: ColumnDef) -> str:
    """Return the SQL type to emit in DDL for *col*.

    ``_row_id`` is always ``INTEGER``; every other column (synthetic or domain)
    is ``TEXT``.  Domain columns store all CIF values as raw strings regardless
    of ``type_contents`` (Lesson 27).
    """
    return 'INTEGER' if col.name == '_row_id' else 'TEXT'


# ---------------------------------------------------------------------------
# Private helpers (continued)
# ---------------------------------------------------------------------------

def _find_transitive_bridge(
    src_tbl: str,
    tgt_tbl: str,
    missing_pk_col: str,
    tables: dict,
    dictionary: 'DdlmDictionary',
    link_groups: dict,
) -> 'tuple[str, str, str, str] | None':
    """Return ``(src_bridge_col, bridge_tbl, bridge_pk_col, bridge_val_col)`` or None.

    Searches for an intermediate table *bridge_tbl* such that:

    - *src_tbl* has a clean single-column Link to the sole PK of *bridge_tbl*
      via column *src_bridge_col*.
    - *bridge_tbl* has a column *bridge_val_col* whose ``linked_item_id``
      equals that of *tgt_tbl*.*missing_pk_col* — meaning both columns draw
      their values from the same parent item (e.g. both link to
      ``_structure.id``).

    When such a bridge exists, the value of *missing_pk_col* in a *src_tbl*
    row can be derived by looking up
    ``bridge_tbl[bridge_pk_col = row[src_bridge_col]].bridge_val_col``.
    """
    # Find the anchor: what definition_id does tgt_tbl.missing_pk_col link to?
    tgt_col_def = next(
        (c for c in tables[tgt_tbl].columns if c.name == missing_pk_col), None
    )
    if tgt_col_def is None or not tgt_col_def.definition_id:
        return None
    tgt_item = dictionary.tag_to_item.get(tgt_col_def.definition_id)
    if tgt_item is None or tgt_item.linked_item_id is None:
        return None
    anchor = tgt_item.linked_item_id  # e.g. '_structure.id'

    # Scan all Link groups from src_tbl for a viable bridge table
    for (s, bridge_tbl), b_pairs in sorted(link_groups.items()):
        if s != src_tbl or bridge_tbl == tgt_tbl or bridge_tbl not in tables:
            continue

        # Bridge table must have exactly one non-synthetic PK column
        bridge_ns_pks = [
            pk for pk in tables[bridge_tbl].primary_keys
            if pk not in _SYNTHETIC_NAMES
        ]
        if len(bridge_ns_pks) != 1:
            continue
        bridge_pk_col = bridge_ns_pks[0]

        # The link group must cleanly map one src column → bridge_pk_col (1:1)
        b_tgt_to_srcs: dict[str, list[str]] = defaultdict(list)
        for sc, tc, _ in b_pairs:
            b_tgt_to_srcs[tc].append(sc)
        if bridge_pk_col not in b_tgt_to_srcs:
            continue
        if len(b_tgt_to_srcs[bridge_pk_col]) != 1:
            continue
        src_bridge_col = b_tgt_to_srcs[bridge_pk_col][0]

        # Bridge table must have a column sharing the same linked_item_id as anchor
        for col in tables[bridge_tbl].columns:
            if col.is_synthetic or not col.definition_id:
                continue
            b_item = dictionary.tag_to_item.get(col.definition_id)
            if (b_item is not None
                    and b_item.type_purpose == 'Link'
                    and b_item.linked_item_id == anchor):
                return (src_bridge_col, bridge_tbl, bridge_pk_col, col.name)

    return None


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def generate_schema(dictionary: DdlmDictionary) -> SchemaSpec:
    """
    Derive a :class:`SchemaSpec` from a loaded ``DdlmDictionary``.

    Iterates over all categories in *dictionary*, creating one
    :class:`TableDef` for each ``Set`` or ``Loop`` category.  ``Head`` and
    ``Functions`` categories are silently skipped (they never appear in data
    instance files); any other unrecognised class emits a warning and is also
    skipped.

    Foreign-key constraints are built in a second pass over all items whose
    ``type_purpose`` is ``"Link"``.  ``SU`` items populate
    :attr:`ColumnDef.linked_item_id` but do not produce
    :class:`ForeignKeyDef` entries.

    ``alias_to_definition_id`` and ``deprecated_ids`` are copied directly from
    *dictionary* so that ``ingest()`` can perform alias resolution and
    deprecation checking without retaining a reference to the dictionary.

    Parameters
    ----------
    dictionary:
        The loaded dictionary returned by
        :meth:`~pycifparse.dictionary.loader.DictionaryLoader.load`.

    Returns
    -------
    SchemaSpec
        The complete schema specification including all tables, column
        definitions, primary keys, foreign keys, the reverse
        ``column_to_tag`` mapping, and alias/deprecation metadata.
    """
    warnings: list[str] = []
    tables: dict[str, TableDef] = {}
    column_to_tag: dict[tuple[str, str], str] = {}

    for cat_id, cat_item in dictionary.categories.items():
        cat_class = cat_item.definition_class
        if cat_class not in ('Set', 'Loop'):
            if cat_class not in ('Head', 'Functions'):
                warnings.append(
                    f"category {cat_id!r} has unsupported class {cat_class!r} — skipped"
                )
            continue

        # Table name is derived from the category's own definition_id.
        tbl_name = _table_name(cat_item.definition_id)

        # Domain items: those whose _name.category_id points to this category.
        domain_items: dict[str, DdlmItem] = {
            item.object_id: item
            for item in dictionary.items.values()
            if item.category_id == cat_item.definition_id
            and item.object_id is not None
        }

        # --- Determine primary key column names ---
        non_synthetic_pks: list[str] = []
        for key_tag in cat_item.category_keys:
            key_item = dictionary.tag_to_item.get(key_tag)
            if key_item is None:
                warnings.append(
                    f"category {cat_id!r}: category key {key_tag!r} not found "
                    f"in dictionary — skipped"
                )
                continue
            if key_item.object_id is None:
                warnings.append(
                    f"category {cat_id!r}: category key {key_tag!r} has no "
                    f"object_id — skipped"
                )
                continue
            non_synthetic_pks.append(key_item.object_id)

        use_fallback_pk = not non_synthetic_pks
        if use_fallback_pk:
            if cat_class == 'Set':
                warnings.append(
                    f"category {cat_id!r} (Set) has no _category_key.name — "
                    f"using _pycifparse_id as primary key"
                )
                primary_keys = ['_pycifparse_id']
            else:  # Loop
                warnings.append(
                    f"category {cat_id!r} (Loop) has no _category_key.name — "
                    f"using _block_id + _row_id as primary key"
                )
                primary_keys = ['_block_id', '_row_id']
        else:
            primary_keys = list(non_synthetic_pks)

        # --- Build columns in specified order ---
        columns: list[ColumnDef] = []

        # 1. _block_id (always first; informational only for keyed tables)
        block_id_is_pk = '_block_id' in primary_keys
        columns.append(ColumnDef(
            name='_block_id',
            definition_id='',
            type_contents=None,
            nullable=False,
            is_primary_key=block_id_is_pk,
            is_synthetic=True,
            linked_item_id=None,
        ))

        # 2. _pycifparse_id (keyless Set tables only)
        if use_fallback_pk and cat_class == 'Set':
            columns.append(ColumnDef(
                name='_pycifparse_id',
                definition_id='',
                type_contents=None,
                nullable=False,
                is_primary_key=True,
                is_synthetic=True,
                linked_item_id=None,
            ))

        # 3. _row_id (all Set and Loop tables)
        row_id_is_pk = '_row_id' in primary_keys
        columns.append(ColumnDef(
            name='_row_id',
            definition_id='',
            type_contents=None,
            nullable=False,
            is_primary_key=row_id_is_pk,
            is_synthetic=True,
            linked_item_id=None,
        ))

        # 4. Non-synthetic primary-key columns (in category_keys order)
        for obj_id in non_synthetic_pks:
            item = domain_items.get(obj_id)
            if item is None:
                warnings.append(
                    f"table {tbl_name!r}: primary key column {obj_id!r} not "
                    f"found in category items — using TEXT"
                )
                col = ColumnDef(
                    name=obj_id,
                    definition_id='',
                    type_contents=None,
                    nullable=False,
                    is_primary_key=True,
                    is_synthetic=False,
                    linked_item_id=None,
                )
            else:
                col = ColumnDef(
                    name=obj_id,
                    definition_id=item.definition_id,
                    type_contents=item.type_contents,
                    nullable=False,
                    is_primary_key=True,
                    is_synthetic=False,
                    linked_item_id=(
                        item.linked_item_id if item.type_purpose == 'SU' else None
                    ),
                )
                column_to_tag[(tbl_name, obj_id)] = item.definition_id
            columns.append(col)

        # 5. Remaining domain columns (alphabetically, excluding PKs)
        pk_set = set(non_synthetic_pks)
        for obj_id, item in sorted(domain_items.items()):
            if obj_id in pk_set:
                continue
            col = ColumnDef(
                name=obj_id,
                definition_id=item.definition_id,
                type_contents=item.type_contents,
                nullable=True,
                is_primary_key=False,
                is_synthetic=False,
                linked_item_id=(
                    item.linked_item_id if item.type_purpose == 'SU' else None
                ),
            )
            columns.append(col)
            column_to_tag[(tbl_name, obj_id)] = item.definition_id

        tables[tbl_name] = TableDef(
            name=tbl_name,
            definition_id=cat_item.definition_id,
            category_class=cat_class,
            columns=columns,
            primary_keys=primary_keys,
            foreign_keys=[],
        )

    # --- Second pass: foreign-key detection ---
    # Collect all Link items grouped by (src_tbl, tgt_tbl).  When multiple
    # source columns all link to columns that together cover the target table's
    # full composite PK, emit one composite FOREIGN KEY constraint.  Single-
    # column FKs targeting a sole PK are handled as the degenerate case.
    #
    # SQLite requires the FK target to have a UNIQUE index.  For a sole-PK
    # table SQLite creates one automatically; for a composite PK it does NOT
    # create per-column UNIQUE indices.  Therefore a valid FK must reference
    # EITHER the sole PK (single-column FK) OR the full composite PK (multi-
    # column FK).  Partial or non-PK references are warned and skipped.

    bridge_columns: list[BridgeColumnDef] = []

    _link_groups: dict[
        tuple[str, str], list[tuple[str, str, DdlmItem]]
    ] = defaultdict(list)   # (src_tbl, tgt_tbl) → [(src_col, tgt_col, item)]

    for item in dictionary.items.values():
        if item.type_purpose != 'Link' or item.linked_item_id is None:
            continue

        target_item = dictionary.tag_to_item.get(item.linked_item_id)
        if target_item is None:
            warnings.append(
                f"FK: linked_item_id {item.linked_item_id!r} for "
                f"{item.definition_id!r} not found in dictionary — skipped"
            )
            continue

        if item.category_id is None or item.object_id is None:
            continue
        if target_item.category_id is None or target_item.object_id is None:
            continue

        src_tbl = _table_name(item.category_id)
        tgt_tbl = _table_name(target_item.category_id)

        if src_tbl not in tables:
            continue  # source category not schema-generating (Head etc.)
        if tgt_tbl not in tables:
            warnings.append(
                f"FK: target table {tgt_tbl!r} for {item.definition_id!r} "
                f"not in schema — skipped"
            )
            continue

        # Warn if linked item is not a category key of the target.
        tgt_cat = dictionary.categories.get(target_item.category_id)
        if tgt_cat and item.linked_item_id not in tgt_cat.category_keys:
            warnings.append(
                f"FK: {item.linked_item_id!r} is not declared as a category "
                f"key of {target_item.category_id!r} — recording FK anyway"
            )

        _link_groups[(src_tbl, tgt_tbl)].append(
            (item.object_id, target_item.object_id, item)
        )

    for (src_tbl, tgt_tbl), pairs in sorted(_link_groups.items()):
        tgt_pks: list[str] = tables[tgt_tbl].primary_keys
        tgt_pks_set = set(tgt_pks)

        # tgt_col → [src_col, ...]: detect full coverage and duplicate targets
        tgt_to_srcs: dict[str, list[str]] = defaultdict(list)
        for src_col, tgt_col, _ in pairs:
            tgt_to_srcs[tgt_col].append(src_col)

        tgt_cols_covered = set(tgt_to_srcs.keys())
        non_pk_tgt_cols  = tgt_cols_covered - tgt_pks_set
        missing_pk_cols  = tgt_pks_set - tgt_cols_covered
        has_conflicts    = any(len(v) > 1 for v in tgt_to_srcs.values())

        if has_conflicts and not missing_pk_cols and not non_pk_tgt_cols:
            # Multiple source columns each independently reference the full PK
            # (e.g. bond.atom_1 and bond.atom_2 both → atom.number).
            # Emit one separate single/composite FK per source column.
            for tgt_col, src_list in tgt_to_srcs.items():
                for src_col in src_list:
                    tables[src_tbl].foreign_keys.append(ForeignKeyDef(
                        source_table=src_tbl,
                        source_columns=[src_col],
                        target_table=tgt_tbl,
                        target_columns=[tgt_col],
                    ))
        elif not non_pk_tgt_cols and len(missing_pk_cols) == 1:
            # All covered columns are PKs; exactly one PK column is missing.
            # Sub-case A: the missing column already exists in src_tbl (self-ref
            #   or previously bridged) — use it directly.
            # Sub-case B: try to derive it via a transitive bridge table.
            [missing_pk_col] = missing_pk_cols
            src_col_names = {c.name for c in tables[src_tbl].columns}
            bridge_col_in_src: str | None = (
                missing_pk_col if missing_pk_col in src_col_names else None
            )

            if bridge_col_in_src is None:
                found = _find_transitive_bridge(
                    src_tbl, tgt_tbl, missing_pk_col,
                    tables, dictionary, _link_groups,
                )
                if found is not None:
                    src_bridge_col, bridge_tbl, bridge_pk_col, bridge_val_col = found
                    # Add derived column once per (src_tbl, col) pair
                    tables[src_tbl].columns.append(ColumnDef(
                        name=missing_pk_col,
                        definition_id='',
                        type_contents=None,
                        nullable=True,
                        is_primary_key=False,
                        is_synthetic=False,
                        linked_item_id=None,
                    ))
                    bridge_columns.append(BridgeColumnDef(
                        table_name=src_tbl,
                        column_name=missing_pk_col,
                        via_column=src_bridge_col,
                        bridge_table=bridge_tbl,
                        bridge_pk_column=bridge_pk_col,
                        bridge_value_column=bridge_val_col,
                    ))
                    bridge_col_in_src = missing_pk_col

            if bridge_col_in_src is not None:
                # Emit one composite FK per conflicting src column (or one if
                # no conflicts), with tgt_pks ordering throughout.
                if has_conflicts:
                    for tgt_col, src_list in tgt_to_srcs.items():
                        for src_col in src_list:
                            src_ordered = [
                                src_col if pk == tgt_col else bridge_col_in_src
                                for pk in tgt_pks
                            ]
                            tables[src_tbl].foreign_keys.append(ForeignKeyDef(
                                source_table=src_tbl,
                                source_columns=src_ordered,
                                target_table=tgt_tbl,
                                target_columns=list(tgt_pks),
                            ))
                else:
                    src_ordered = [
                        tgt_to_srcs[pk][0] if pk in tgt_to_srcs else bridge_col_in_src
                        for pk in tgt_pks
                    ]
                    tables[src_tbl].foreign_keys.append(ForeignKeyDef(
                        source_table=src_tbl,
                        source_columns=src_ordered,
                        target_table=tgt_tbl,
                        target_columns=list(tgt_pks),
                    ))
            else:
                # No bridge found — warn per pair
                for src_col, tgt_col, item in pairs:
                    warnings.append(
                        f"FK: {item.definition_id!r} -> {item.linked_item_id!r}: "
                        f"partial FK to '{tgt_tbl}' — covers "
                        f"{sorted(tgt_cols_covered)} of PKs={tgt_pks}, "
                        f"no transitive bridge found — skipping FK constraint"
                    )
        elif non_pk_tgt_cols or missing_pk_cols or has_conflicts:
            # Cannot form a complete, unambiguous (composite) FK.
            # Emit one warning per failing pair so each source item is named.
            for src_col, tgt_col, item in pairs:
                if len(tgt_to_srcs.get(tgt_col, [])) > 1:
                    msg = (
                        f"ambiguous composite FK — multiple source columns "
                        f"link to '{tgt_tbl}'.'{tgt_col}'"
                    )
                elif tgt_col not in tgt_pks_set:
                    msg = (
                        f"target column '{tgt_col}' is not a PK of "
                        f"'{tgt_tbl}' (PKs={tgt_pks})"
                    )
                else:
                    msg = (
                        f"partial FK to '{tgt_tbl}' — covers "
                        f"{sorted(tgt_cols_covered)} of PKs={tgt_pks}"
                    )
                warnings.append(
                    f"FK: {item.definition_id!r} -> {item.linked_item_id!r}: "
                    f"{msg} — skipping FK constraint"
                )
        else:
            # All PKs covered, no non-PK targets, no duplicate targets.
            # Order source columns to match the target PK column order.
            src_ordered = [tgt_to_srcs[tc][0] for tc in tgt_pks]
            tables[src_tbl].foreign_keys.append(ForeignKeyDef(
                source_table=src_tbl,
                source_columns=src_ordered,
                target_table=tgt_tbl,
                target_columns=list(tgt_pks),
            ))

    # --- Third pass: propagation links ---
    # For every PK column that is a Link item, record the target definition_id
    # so that _apply_fk can still fill the column from the fk_accumulator or
    # loop values even when no formal FK constraint was emitted.
    #
    # Additionally, PK Link columns with skipped FKs are made nullable: the
    # database cannot enforce referential integrity for them, and NULL is the
    # correct representation of an absent/default value.
    propagation_links: dict[str, list[tuple[str, str, str | None]]] = {}
    _seen_prop: set[tuple[str, str]] = set()
    for item in dictionary.items.values():
        if item.type_purpose != 'Link' or item.linked_item_id is None:
            continue
        if item.category_id is None or item.object_id is None:
            continue
        src_tbl = _table_name(item.category_id)
        if src_tbl not in tables:
            continue
        src_col_def = next(
            (c for c in tables[src_tbl].columns if c.name == item.object_id),
            None,
        )
        if src_col_def is None or not src_col_def.is_primary_key:
            continue
        key = (src_tbl, item.object_id)
        if key in _seen_prop:
            continue
        _seen_prop.add(key)
        propagation_links.setdefault(src_tbl, []).append(
            (item.object_id, item.linked_item_id, item.enumeration_default)
        )
        # Make the column nullable: FK was skipped, so NULL is valid here.
        src_col_def.nullable = True

    return SchemaSpec(
        tables=tables,
        column_to_tag=column_to_tag,
        alias_to_definition_id=dict(dictionary.alias_to_definition_id),
        deprecated_ids=set(dictionary.deprecated_ids),
        warnings=warnings,
        bridge_columns=bridge_columns,
        propagation_links=propagation_links,
    )


def emit_fallback_create_statements() -> list[str]:
    """
    Return the fixed DDL statements for the schema-less fallback tier.

    Returns four SQL strings: ``CREATE TABLE IF NOT EXISTS`` for
    ``_cif_fallback``, its lookup index, ``CREATE TABLE IF NOT EXISTS`` for
    ``_block_dataset_membership``, and ``CREATE TABLE IF NOT EXISTS`` for
    ``_validation_result``.
    """
    fallback = (
        f"CREATE TABLE IF NOT EXISTS {_qi('_cif_fallback')} (\n"
        f"    {_qi('_block_id')}   TEXT     NOT NULL,\n"
        f"    {_qi('_row_id')}     INTEGER  NOT NULL,\n"
        f"    {_qi('tag')}         TEXT     NOT NULL,\n"
        f"    {_qi('value')}       TEXT,\n"
        f"    {_qi('value_type')}  TEXT     NOT NULL,\n"
        f"    {_qi('loop_id')}     INTEGER,\n"
        f"    {_qi('col_index')}   INTEGER,\n"
        f"    PRIMARY KEY ({_qi('_block_id')}, {_qi('_row_id')}, {_qi('tag')})\n"
        f")"
    )
    index = (
        f"CREATE INDEX IF NOT EXISTS {_qi('idx_cif_fallback_tag_block')} "
        f"ON {_qi('_cif_fallback')} ({_qi('tag')}, {_qi('_block_id')})"
    )
    membership = (
        f"CREATE TABLE IF NOT EXISTS {_qi('_block_dataset_membership')} (\n"
        f"    {_qi('_block_id')}            TEXT  NOT NULL,\n"
        f"    {_qi('_audit_dataset_id')}    TEXT  NOT NULL,\n"
        f"    {_qi('id_regime')}            TEXT  NOT NULL,\n"
        f"    PRIMARY KEY ({_qi('_block_id')}, {_qi('_audit_dataset_id')})\n"
        f")"
    )
    validation = (
        f"CREATE TABLE IF NOT EXISTS {_qi('_validation_result')} (\n"
        f"    {_qi('check_name')}  TEXT  NOT NULL,\n"
        f"    {_qi('severity')}    TEXT  NOT NULL,\n"
        f"    {_qi('block_id')}    TEXT,\n"
        f"    {_qi('detail')}      TEXT,\n"
        f"    {_qi('id_regime')}   TEXT\n"
        f")"
    )
    return [fallback, index, membership, validation]


def emit_create_statements(schema: SchemaSpec) -> list[str]:
    """
    Render each :class:`TableDef` in *schema* as a ``CREATE TABLE`` statement.

    Returns one SQL string per table.  The statements use
    ``CREATE TABLE IF NOT EXISTS`` and include inline ``PRIMARY KEY`` and
    ``FOREIGN KEY`` clauses.  All FK constraints carry
    ``DEFERRABLE INITIALLY DEFERRED``.

    All value columns are declared ``TEXT`` regardless of
    ``ColumnDef.type_contents``; ``_row_id`` is always ``INTEGER``.

    Parameters
    ----------
    schema:
        The schema specification produced by :func:`generate_schema`.

    Returns
    -------
    list[str]
        One ``CREATE TABLE IF NOT EXISTS ...`` statement per table.
    """
    stmts: list[str] = []

    for table in schema.tables.values():
        parts: list[str] = []

        row_id_col = next((c for c in table.columns if c.name == '_row_id'), None)
        for col in table.columns:
            line = f"    {_qi(col.name)}  {_ddl_type(col)}"
            if not col.nullable:
                line += "  NOT NULL"
            parts.append(line)

        pk_clause = ', '.join(_qi(k) for k in table.primary_keys)
        parts.append(f"    PRIMARY KEY ({pk_clause})")

        # Composite UNIQUE on (_block_id, _row_id) when _row_id is not already
        # part of the PRIMARY KEY.
        if row_id_col is not None and not row_id_col.is_primary_key:
            parts.append(
                f"    UNIQUE ({_qi('_block_id')}, {_qi('_row_id')})"
            )

        for fk in table.foreign_keys:
            src_cols = ', '.join(_qi(c) for c in fk.source_columns)
            tgt_cols = ', '.join(_qi(c) for c in fk.target_columns)
            parts.append(
                f"    FOREIGN KEY ({src_cols})\n"
                f"        REFERENCES {_qi(fk.target_table)}({tgt_cols})\n"
                f"        DEFERRABLE INITIALLY DEFERRED"
            )

        body = ',\n'.join(parts)
        stmts.append(
            f"CREATE TABLE IF NOT EXISTS {_qi(table.name)} (\n{body}\n)"
        )

    return stmts
