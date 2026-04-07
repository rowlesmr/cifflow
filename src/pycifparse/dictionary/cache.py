"""
JSON serialisation and deserialisation of DdlmDictionary.

Allows a fully resolved dictionary (including metadictionary imports) to be
saved to disk and reloaded without re-parsing constituent CIF files.

Cache invalidation is the caller's responsibility.  These functions make no
attempt to detect whether the source dictionary files have changed.
"""

import dataclasses
import json
import pathlib

from pycifparse.dictionary.ddlm_item import DdlmItem
from pycifparse.dictionary.ddlm_parser import DdlmDictionary


def save_dictionary(
    dictionary: DdlmDictionary,
    path: str | pathlib.Path,
) -> None:
    """
    Serialise *dictionary* to a JSON file at *path*.

    The file is written atomically from the perspective of a single process
    (standard ``open`` + ``json.dump``).  Existing files are overwritten.

    ``tag_to_item`` is stored as a ``dict[str, str]`` mapping (tag name →
    ``definition_id``) to avoid duplicating ``DdlmItem`` objects for every
    alias.  It is reconstructed on load.

    Parameters
    ----------
    dictionary:
        The ``DdlmDictionary`` to serialise.
    path:
        Destination file path.  Parent directories must already exist.
    """
    data = {
        'name': dictionary.name,
        'title': dictionary.title,
        'version': dictionary.version,
        'categories': {
            k: dataclasses.asdict(v)
            for k, v in dictionary.categories.items()
        },
        'items': {
            k: dataclasses.asdict(v)
            for k, v in dictionary.items.items()
        },
        # Store as tag → definition_id to avoid duplicating DdlmItem objects.
        'tag_to_item': {
            tag: item.definition_id
            for tag, item in dictionary.tag_to_item.items()
        },
        'alias_to_definition_id': dictionary.alias_to_definition_id,
        'deprecated_ids': sorted(dictionary.deprecated_ids),
        'warnings': dictionary.warnings,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_dictionary(path: str | pathlib.Path) -> DdlmDictionary:
    """
    Deserialise a ``DdlmDictionary`` from a JSON file at *path*.

    Raises ``ValueError`` if the file does not exist, contains malformed JSON,
    is missing required keys, or references an unknown ``definition_id`` in
    ``tag_to_item``.  The caller should respond by falling back to
    ``DictionaryLoader.load()``.

    Parameters
    ----------
    path:
        Path to a JSON file previously written by :func:`save_dictionary`.

    Returns
    -------
    DdlmDictionary
        The deserialised dictionary.

    Raises
    ------
    ValueError
        If the file cannot be read or the contents are invalid.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        raise ValueError(f'dictionary cache file not found: {path}')
    except json.JSONDecodeError as e:
        raise ValueError(f'malformed JSON in dictionary cache {path}: {e}')

    try:
        categories = {
            k: DdlmItem(**v) for k, v in data['categories'].items()
        }
        items = {
            k: DdlmItem(**v) for k, v in data['items'].items()
        }
    except (KeyError, TypeError) as e:
        raise ValueError(f'invalid dictionary cache structure in {path}: {e}')

    # Reconstruct tag_to_item from the stored tag → definition_id mapping.
    all_by_id: dict[str, DdlmItem] = {**categories, **items}
    tag_to_item: dict[str, DdlmItem] = {}
    try:
        for tag, def_id in data['tag_to_item'].items():
            if def_id not in all_by_id:
                raise ValueError(
                    f'tag_to_item entry {tag!r} references unknown '
                    f'definition_id {def_id!r} in {path}'
                )
            tag_to_item[tag] = all_by_id[def_id]
    except (KeyError, TypeError) as e:
        raise ValueError(f'invalid tag_to_item in dictionary cache {path}: {e}')

    try:
        return DdlmDictionary(
            name=data['name'],
            title=data['title'],
            version=data['version'],
            categories=categories,
            items=items,
            tag_to_item=tag_to_item,
            alias_to_definition_id=data['alias_to_definition_id'],
            deprecated_ids=set(data['deprecated_ids']),
            warnings=data['warnings'],
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f'invalid dictionary cache structure in {path}: {e}')
