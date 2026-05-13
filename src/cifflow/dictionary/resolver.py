"""Tag resolution — maps a CIF tag name to its current DDLm definition."""

from dataclasses import dataclass

from cifflow.dictionary.ddlm_parser import DdlmDictionary


@dataclass
class ResolvedTag:
    """
    Result of resolving a tag name against a loaded DDLm dictionary.

    Produced by :func:`resolve_tag`.

    Attributes
    ----------
    definition_id:
        The current canonical tag name (``_definition.id``), lowercased.
    category_id:
        The SQL table name for this definition (``_name.category_id``),
        lowercased.
    object_id:
        The SQL column name for this definition (``_name.object_id``),
        lowercased.
    was_alias:
        ``True`` if the input tag was an old alias that maps to
        *definition_id*; ``False`` if it matched the canonical name directly.
    is_deprecated:
        ``True`` if this definition has been superseded by one or more
        replacements (``_definition_replaced`` records exist).
    """

    definition_id: str
    category_id: str
    object_id: str
    was_alias: bool
    is_deprecated: bool


def resolve_tag(tag: str, dictionary: DdlmDictionary) -> ResolvedTag | None:
    """
    Resolve a tag name from a CIF data file to its current definition.

    Looks up *tag* (case-insensitive) in ``dictionary.tag_to_item``,
    following alias chains transparently.  Returns ``None`` if the tag is
    not known to this dictionary; this is the signal that the tag belongs
    to the fallback tier, not an error condition.

    Does not emit warnings.  The caller is responsible for acting on the
    ``was_alias`` and ``is_deprecated`` flags of the returned value.

    Parameters
    ----------
    tag:
        The tag name to resolve, as it appears in a CIF data file.
        Lookup is case-insensitive.
    dictionary:
        The loaded ``DdlmDictionary`` to resolve against.

    Returns
    -------
    ResolvedTag | None
        Resolution result including canonical name, table, column, and
        alias/deprecation flags; ``None`` if the tag is not known to this
        dictionary.
    """
    item = dictionary.tag_to_item.get(tag.lower())
    if item is None:
        return None

    was_alias = tag.lower() in dictionary.alias_to_definition_id

    if item.category_id is None or item.object_id is None:
        return None

    return ResolvedTag(
        definition_id=item.definition_id,
        category_id=item.category_id,
        object_id=item.object_id,
        was_alias=was_alias,
        is_deprecated=item.is_deprecated,
    )
