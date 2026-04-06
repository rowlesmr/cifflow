"""
SQLite schema generation from a loaded DDLm dictionary.
"""

from dataclasses import dataclass, field
from collections.abc import Callable

from pycifparse.dictionary.ddlm_item import DdlmItem
from pycifparse.dictionary.ddlm_parser import DdlmDictionary


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ForeignKeyDef:
    """
    A single ``FOREIGN KEY`` constraint between two tables.

    Always emitted with ``DEFERRABLE INITIALLY DEFERRED`` to handle cyclic
    category graphs correctly within a transaction.

    Attributes
    ----------
    source_table:
        Name of the table that holds the foreign key column.
    source_column:
        Name of the foreign key column in *source_table*.
    target_table:
        Name of the table being referenced.
    target_column:
        Name of the column being referenced in *target_table*.
    """

    source_table: str
    source_column: str
    target_table: str
    target_column: str


@dataclass
class ColumnDef:
    """
    Definition of a single column in a generated SQLite table.

    Attributes
    ----------
    name:
        SQL column name, equal to the DDLm ``_name.object_id``, lowercased.
        For synthetic columns the name is ``_block_id`` or ``_row_id``.
    definition_id:
        The current canonical ``_definition.id`` for this column's DDLm item.
        Empty string for synthetic columns (``_block_id``, ``_row_id``).
    sql_type:
        SQLite type affinity: ``"TEXT"``, ``"INTEGER"``, or ``"REAL"``.
    nullable:
        ``False`` for synthetic and primary-key columns; ``True`` for all
        other domain columns.
    is_primary_key:
        ``True`` if this column is part of the table's ``PRIMARY KEY``.
    is_synthetic:
        ``True`` for the ``_block_id`` and ``_row_id`` infrastructure columns,
        which have no corresponding DDLm item definition.
    linked_item_id:
        For ``SU`` items only: the ``_definition.id`` of the associated
        measurand item, lowercased.  ``None`` for all other column types.
        Does not produce a ``FOREIGN KEY`` constraint; used by the output
        layer.
    is_status_column:
        ``True`` for the ``{name}_status`` companion columns that record the
        CIF data-presence state (``"value"``, ``"inapplicable"``, ``"unknown"``,
        or ``"absent"``).  ``False`` for all value columns and infrastructure
        columns.  Status columns are never included in ``column_to_tag``.
    """

    name: str
    definition_id: str
    sql_type: str
    nullable: bool
    is_primary_key: bool
    is_synthetic: bool
    linked_item_id: str | None
    is_status_column: bool = False


@dataclass
class TableDef:
    """
    Definition of a single SQLite table generated from a DDLm category.

    Attributes
    ----------
    name:
        SQL table name, derived from the category's ``_name.category_id``
        (lowercased, leading ``_`` stripped, ``.`` replaced with ``_``).
    definition_id:
        The ``_definition.id`` of the category save frame that produced
        this table.
    category_class:
        DDLm class of the source category: ``"Set"`` or ``"Loop"``.
    columns:
        Ordered list of column definitions.  Order follows the column-ordering
        rule: ``_block_id``, ``_row_id`` (Loop only), primary-key domain
        columns, remaining domain columns alphabetically.
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
        columns (``_block_id``, ``_row_id``) are excluded.
    warnings:
        Non-fatal issues encountered during schema generation, in emission
        order.
    """

    tables: dict[str, TableDef]
    column_to_tag: dict[tuple[str, str], str]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_SYNTHETIC_NAMES: frozenset[str] = frozenset({'_block_id', '_row_id'})


def _sql_type(type_contents: str | None) -> str:
    """Map a DDLm ``_type.contents`` value to a SQLite type affinity string."""
    if type_contents is None:
        return 'TEXT'
    tc = type_contents.lower()
    if tc == 'integer':
        return 'INTEGER'
    if tc == 'real':
        return 'REAL'
    return 'TEXT'


def _table_name(category_id: str) -> str:
    """Derive a SQL table name from a lowercased ``_name.category_id`` value."""
    return category_id.lstrip('_').replace('.', '_')


def _qi(name: str) -> str:
    """Quote a SQL identifier with double quotes, escaping embedded quotes."""
    return '"' + name.replace('"', '""') + '"'


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

    Parameters
    ----------
    dictionary:
        The loaded dictionary returned by
        :meth:`~pycifparse.dictionary.loader.DictionaryLoader.load`.

    Returns
    -------
    SchemaSpec
        The complete schema specification including all tables, column
        definitions, primary keys, foreign keys, and the reverse
        ``column_to_tag`` mapping.
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
        # cat_item.category_id is the PARENT category in the DDLm hierarchy —
        # not the table name.  Items belonging to this category carry
        # _name.category_id == cat_item.definition_id.
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
                    f"using _block_id as primary key"
                )
                primary_keys = ['_block_id']
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

        # Collect all domain object_ids for collision detection.
        # A true collision occurs when one domain column's object_id equals
        # another's object_id + "_status".  In that case the companion for
        # the first column is renamed to {name}_cif_status.
        all_domain_ids: frozenset[str] = frozenset(domain_items)

        def _status_col(value_col: ColumnDef) -> ColumnDef:
            """Return the status companion for *value_col*."""
            candidate = f"{value_col.name}_status"
            if candidate in all_domain_ids:
                warnings.append(
                    f"table {tbl_name!r}: status column name {candidate!r} "
                    f"collides with domain column — using "
                    f"{value_col.name!r}_cif_status instead"
                )
                candidate = f"{value_col.name}_cif_status"
            return ColumnDef(
                name=candidate,
                definition_id='',
                sql_type='TEXT',
                nullable=False,
                is_primary_key=False,
                is_synthetic=False,
                linked_item_id=None,
                is_status_column=True,
            )

        # 1. _block_id (always first; no status companion)
        block_id_is_pk = '_block_id' in primary_keys
        columns.append(ColumnDef(
            name='_block_id',
            definition_id='',
            sql_type='TEXT',
            nullable=False,
            is_primary_key=block_id_is_pk,
            is_synthetic=True,
            linked_item_id=None,
        ))

        # 2. _row_id (Loop tables only; no status companion)
        if cat_class == 'Loop':
            row_id_is_pk = '_row_id' in primary_keys
            columns.append(ColumnDef(
                name='_row_id',
                definition_id='',
                sql_type='INTEGER',
                nullable=False,
                is_primary_key=row_id_is_pk,
                is_synthetic=True,
                linked_item_id=None,
            ))

        # 3. Non-synthetic primary-key columns (in category_keys order)
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
                    sql_type='TEXT',
                    nullable=False,
                    is_primary_key=True,
                    is_synthetic=False,
                    linked_item_id=None,
                )
            else:
                col = ColumnDef(
                    name=obj_id,
                    definition_id=item.definition_id,
                    sql_type=_sql_type(item.type_contents),
                    nullable=False,
                    is_primary_key=True,
                    is_synthetic=False,
                    linked_item_id=(
                        item.linked_item_id if item.type_purpose == 'SU' else None
                    ),
                )
                column_to_tag[(tbl_name, obj_id)] = item.definition_id
            columns.append(col)
            columns.append(_status_col(col))

        # 4. Remaining domain columns (alphabetically, excluding PKs)
        pk_set = set(non_synthetic_pks)
        for obj_id, item in sorted(domain_items.items()):
            if obj_id in pk_set:
                continue
            col = ColumnDef(
                name=obj_id,
                definition_id=item.definition_id,
                sql_type=_sql_type(item.type_contents),
                nullable=True,
                is_primary_key=False,
                is_synthetic=False,
                linked_item_id=(
                    item.linked_item_id if item.type_purpose == 'SU' else None
                ),
            )
            columns.append(col)
            columns.append(_status_col(col))
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

        tables[src_tbl].foreign_keys.append(ForeignKeyDef(
            source_table=src_tbl,
            source_column=item.object_id,
            target_table=tgt_tbl,
            target_column=target_item.object_id,
        ))

    return SchemaSpec(
        tables=tables,
        column_to_tag=column_to_tag,
        warnings=warnings,
    )


def emit_create_statements(schema: SchemaSpec) -> list[str]:
    """
    Render each :class:`TableDef` in *schema* as a ``CREATE TABLE`` statement.

    Returns one SQL string per table.  The statements use
    ``CREATE TABLE IF NOT EXISTS`` and include inline ``PRIMARY KEY`` and
    ``FOREIGN KEY`` clauses.  All foreign-key constraints carry
    ``DEFERRABLE INITIALLY DEFERRED``.  The ``_row_id`` column on Loop tables
    includes an additional ``UNIQUE`` constraint.

    The returned strings are valid SQLite DDL and can be executed directly
    against a ``sqlite3.Connection``.

    Parameters
    ----------
    schema:
        The schema specification produced by :func:`generate_schema`.

    Returns
    -------
    list[str]
        One ``CREATE TABLE IF NOT EXISTS ...`` statement per table, in
        iteration order of ``schema.tables``.
    """
    stmts: list[str] = []

    for table in schema.tables.values():
        parts: list[str] = []

        for col in table.columns:
            line = f"    {_qi(col.name)}  {col.sql_type}"
            if not col.nullable:
                line += "  NOT NULL"
            if col.is_status_column:
                line += "  DEFAULT 'absent'"
            if col.is_synthetic and col.name == '_row_id':
                line += "  UNIQUE"
            parts.append(line)

        pk_clause = ', '.join(_qi(k) for k in table.primary_keys)
        parts.append(f"    PRIMARY KEY ({pk_clause})")

        for fk in table.foreign_keys:
            parts.append(
                f"    FOREIGN KEY ({_qi(fk.source_column)})\n"
                f"        REFERENCES {_qi(fk.target_table)}({_qi(fk.target_column)})\n"
                f"        DEFERRABLE INITIALLY DEFERRED"
            )

        body = ',\n'.join(parts)
        stmts.append(
            f"CREATE TABLE IF NOT EXISTS {_qi(table.name)} (\n{body}\n)"
        )

    return stmts
