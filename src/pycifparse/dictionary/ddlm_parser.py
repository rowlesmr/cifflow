"""
DDLm dictionary data container produced by DictionaryLoader.
"""

from dataclasses import dataclass, field

from pycifparse.dictionary.ddlm_item import DdlmItem


@dataclass
class DdlmDictionary:
    """
    In-memory representation of a loaded DDLm dictionary.

    Produced by ``DictionaryLoader.load()``.  Contains all category and item
    definitions extracted from the dictionary's save frames, together with
    pre-built lookup tables for fast tag resolution.

    Attributes
    ----------
    name:
        The ``data_`` block name from the parsed CIF file (e.g. ``"CIF_CORE"``).
    title:
        Value of ``_dictionary.title``, or ``None`` if absent.
    version:
        Value of ``_dictionary.version``, or ``None`` if absent.
    categories:
        Mapping from lowercased ``definition_id`` to ``DdlmItem`` for every
        ``"Category"``-scope frame.
    items:
        Mapping from lowercased ``definition_id`` to ``DdlmItem`` for every
        ``"Item"``-scope frame.
    tag_to_item:
        Combined lookup covering every ``definition_id`` (both categories and
        items) plus all declared aliases.  Keys are lowercased.
    alias_to_definition_id:
        Maps each lowercased alias tag name to the current lowercased
        ``definition_id``.
    deprecated_ids:
        Set of lowercased ``definition_id`` values whose definitions have been
        replaced (``is_deprecated == True``).
    warnings:
        Non-fatal issues encountered during loading, in emission order.
    """

    name: str
    title: str | None
    version: str | None
    categories: dict[str, DdlmItem]
    items: dict[str, DdlmItem]
    tag_to_item: dict[str, DdlmItem]
    alias_to_definition_id: dict[str, str]
    deprecated_ids: set[str]
    warnings: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    uri: str | None = None
