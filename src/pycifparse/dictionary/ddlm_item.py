"""
DDLm item definition — one save frame extracted from a DDLm dictionary.
"""

from dataclasses import dataclass, field


@dataclass
class DdlmItem:
    """
    Represents a single definition extracted from a DDLm dictionary save frame.

    Each save frame in a DDLm dictionary defines either a data item or a
    category.  After import resolution, all relevant attributes are collected
    into this dataclass.

    Attributes
    ----------
    definition_id:
        Canonical tag name as it appears in CIF data files, normalised to
        lowercase.  Corresponds to ``_definition.id``.
    scope:
        ``"Item"``, ``"Category"``, or ``"Dictionary"``.  Defaults to
        ``"Item"`` when ``_definition.scope`` is absent from the save frame.
    definition_class:
        DDLm class of this definition: ``"Datum"``, ``"Attribute"``,
        ``"Loop"``, ``"Set"``, ``"Head"``, or ``"Functions"``.  Defaults to
        ``"Datum"`` when ``_definition.class`` is absent.
    category_id:
        SQLite table name derived from ``_name.category_id``, lowercased.
        ``None`` for ``"Dictionary"``-scope frames and items missing this tag.
    object_id:
        SQLite column name derived from ``_name.object_id``, lowercased.
        ``None`` for category frames and items missing this tag.
    type_purpose:
        Value of ``_type.purpose`` (e.g. ``"Key"``, ``"Link"``, ``"SU"``,
        ``"Measurand"``).  ``None`` if absent.
    type_source:
        Value of ``_type.source`` (e.g. ``"Assigned"``, ``"Recorded"``).
        ``None`` if absent.
    type_container:
        Value of ``_type.container`` (e.g. ``"Single"``, ``"List"``).
        Defaults to ``"Single"`` when absent.
    type_contents:
        Value of ``_type.contents`` (e.g. ``"Text"``, ``"Integer"``,
        ``"Real"``).  ``None`` if absent.
    linked_item_id:
        For ``Link`` and ``SU`` items: the ``_definition.id`` of the linked
        item, lowercased.  ``None`` for all other items.
    units_code:
        Value of ``_units.code``.  ``None`` if absent.
    description:
        Human-readable description from ``_description.text``.  ``None`` if
        absent.
    enumeration_states:
        Allowed enumeration values from ``_enumeration_set.state``.  Empty
        list when not present.  Item-scope frames only.
    enumeration_default:
        Default value from ``_enumeration.default``.  ``None`` if absent.
        The CIF inapplicable placeholder ``'.'`` is preserved as-is.
    category_keys:
        Lowercased fully-qualified tag names from ``_category_key.name``.
        Empty list when not present.  Category-scope frames only.
    aliases:
        Old tag names from ``_alias.definition_id``, each mapping 1:1 to
        this ``definition_id``.  Empty list when none are declared.
    replaced_by:
        Preferred replacement tag names from ``_definition_replaced.by``,
        lowercased.  An empty string represents a ``PLACEHOLDER`` (``"."``),
        meaning deprecated with no replacement.  Empty list when not present.
    is_deprecated:
        ``True`` if any ``_definition_replaced`` row exists for this item,
        regardless of the replacement value.
    """

    definition_id: str
    scope: str
    definition_class: str
    category_id: str | None
    object_id: str | None
    type_purpose: str | None
    type_source: str | None
    type_container: str
    type_contents: str | None
    linked_item_id: str | None
    units_code: str | None
    description: str | None
    enumeration_states: list[str] = field(default_factory=list)
    enumeration_default: str | None = None
    category_keys: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    replaced_by: list[str] = field(default_factory=list)
    is_deprecated: bool = False
    enumeration_range: str | None = None
    type_dimension: str | None = None
