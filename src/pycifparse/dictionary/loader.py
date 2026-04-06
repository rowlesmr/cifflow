"""
DDLm dictionary loader — parses a DDLm CIF and resolves _import.get directives.
"""

import pathlib
from collections.abc import Callable
from typing import Any

from pycifparse.cifmodel.builder import build
from pycifparse.cifmodel.model import CifFile, CifSaveFrame
from pycifparse.dictionary.ddlm_item import DdlmItem
from pycifparse.dictionary.ddlm_parser import DdlmDictionary


SourceResolver = Callable[[str], str | None]
"""Callable that maps a URI string to a raw CIF source string, or None."""

# Tags read from each save frame into the working dict.
# Tags that define a save frame's own identity — never imported from a source frame.
_IMPORT_IDENTITY_TAGS = frozenset({
    '_definition.id',
    '_definition.scope',
    '_definition.class',
    '_name.category_id',
    '_name.object_id',
    '_name.linked_item_id',
    '_import.get',
})

_FRAME_TAGS = frozenset({
    '_definition.id',
    '_definition.scope',
    '_definition.class',
    '_name.category_id',
    '_name.object_id',
    '_name.linked_item_id',
    '_type.purpose',
    '_type.source',
    '_type.container',
    '_type.contents',
    '_units.code',
    '_description.text',
    '_enumeration_set.state',
    '_category_key.name',
    '_alias.definition_id',
    '_definition_replaced.id',
    '_definition_replaced.by',
    '_import.get',
})


def directory_resolver(path: str | pathlib.Path) -> SourceResolver:
    """
    Return a SourceResolver that reads files by filename from a local directory.

    The last path component of the URI is used as the filename.  Returns
    ``None`` if the file is not found in the directory.

    Parameters
    ----------
    path:
        Directory to search for dictionary files.

    Returns
    -------
    SourceResolver
        A callable mapping URI strings to raw CIF source strings.
    """
    directory = pathlib.Path(path)

    def _resolve(uri: str) -> str | None:
        filename = pathlib.PurePosixPath(uri).name
        candidate = directory / filename
        if candidate.exists():
            return candidate.read_text(encoding='utf-8')
        return None

    return _resolve


def _scalar(data: dict[str, list], tag: str, default: str | None = None) -> str | None:
    """Return the first string value for *tag*, or *default* if absent/placeholder."""
    vals = data.get(tag)
    if not vals:
        return default
    v = vals[0]
    if not isinstance(v, str) or v in ('.', '?'):
        return default
    return v


def _str_list(data: dict[str, list], tag: str) -> list[str]:
    """Return all string values for *tag*, excluding placeholders."""
    return [v for v in data.get(tag, []) if isinstance(v, str) and v != '?']


def _extract_item(data: dict[str, list], warn: Callable[[str], None]) -> DdlmItem | None:
    """
    Extract a DdlmItem from a working dict, or return None if the frame should
    be skipped.
    """
    raw_id = _scalar(data, '_definition.id')
    if raw_id is None:
        warn('save frame missing _definition.id — skipped')
        return None

    definition_id = raw_id.lower()
    scope = (_scalar(data, '_definition.scope') or 'Item').capitalize()

    # Normalise known scope values; default unknown to "Item" with warning.
    if scope not in ('Item', 'Category', 'Dictionary'):
        warn(f'unknown _definition.scope {scope!r} for {definition_id!r} — treating as Item')
        scope = 'Item'

    if scope == 'Dictionary':
        return None  # skip silently

    definition_class = _scalar(data, '_definition.class') or 'Datum'

    category_id_raw = _scalar(data, '_name.category_id')
    if scope == 'Item' and category_id_raw is None:
        warn(f'item frame {definition_id!r} missing _name.category_id — skipped')
        return None

    category_id = category_id_raw.lower() if category_id_raw else None
    object_id_raw = _scalar(data, '_name.object_id')
    object_id = object_id_raw.lower() if object_id_raw else None

    linked_raw = _scalar(data, '_name.linked_item_id')
    linked_item_id = linked_raw.lower() if linked_raw else None

    # _definition_replaced.by: '.' → '' (deprecated with no replacement)
    replaced_by_raw = _str_list(data, '_definition_replaced.by')
    replaced_by = ['' if v == '.' else v.lower() for v in replaced_by_raw]
    is_deprecated = bool(data.get('_definition_replaced.id'))

    aliases = [v.lower() for v in _str_list(data, '_alias.definition_id')]
    category_keys = [v.lower() for v in _str_list(data, '_category_key.name')]
    enumeration_states = _str_list(data, '_enumeration_set.state')

    return DdlmItem(
        definition_id=definition_id,
        scope=scope,
        definition_class=definition_class,
        category_id=category_id,
        object_id=object_id,
        type_purpose=_scalar(data, '_type.purpose'),
        type_source=_scalar(data, '_type.source'),
        type_container=_scalar(data, '_type.container') or 'Single',
        type_contents=_scalar(data, '_type.contents'),
        linked_item_id=linked_item_id,
        units_code=_scalar(data, '_units.code'),
        description=_scalar(data, '_description.text'),
        enumeration_states=enumeration_states,
        category_keys=category_keys,
        aliases=aliases,
        replaced_by=replaced_by,
        is_deprecated=is_deprecated,
    )


def _build_lookup_tables(
    all_items: list[DdlmItem],
    warn: Callable[[str], None],
) -> tuple[
    dict[str, DdlmItem],   # categories
    dict[str, DdlmItem],   # items
    dict[str, DdlmItem],   # tag_to_item
    dict[str, str],        # alias_to_definition_id
    set[str],              # deprecated_ids
]:
    """Build the DdlmDictionary lookup tables from a flat list of DdlmItems."""
    categories: dict[str, DdlmItem] = {}
    items: dict[str, DdlmItem] = {}
    tag_to_item: dict[str, DdlmItem] = {}
    alias_to_definition_id: dict[str, str] = {}
    deprecated_ids: set[str] = set()

    for item in all_items:
        if item.scope == 'Category':
            categories[item.definition_id] = item
        else:
            items[item.definition_id] = item

        tag_to_item[item.definition_id] = item

        if item.is_deprecated:
            deprecated_ids.add(item.definition_id)

    # Register aliases
    for item in all_items:
        for alias in item.aliases:
            if alias in tag_to_item:
                warn(
                    f'alias {alias!r} for {item.definition_id!r} collides with '
                    f'existing entry {tag_to_item[alias].definition_id!r} — skipped'
                )
                continue
            tag_to_item[alias] = item
            alias_to_definition_id[alias] = item.definition_id

    return categories, items, tag_to_item, alias_to_definition_id, deprecated_ids


class DictionaryLoader:
    """
    Loads a DDLm dictionary from a CIF 2.0 source string.

    Resolves ``_import.get`` directives (mode ``"Contents"`` only) using the
    supplied ``SourceResolver``.  File access is fully delegated to the resolver;
    this class never accesses the filesystem or network directly.

    Parsed files are cached for the lifetime of the loader instance.  To
    invalidate the cache, create a new instance.

    Parameters
    ----------
    resolver:
        Callable that maps a URI string to a raw CIF source string, or ``None``
        if the file is unavailable.  If ``None``, import directives that require
        an external file will trigger the ``if_miss`` policy.
    on_warning:
        Optional callback for non-fatal warnings.  If ``None``, warnings are
        silently discarded.
    """

    def __init__(
        self,
        resolver: SourceResolver | None = None,
        *,
        on_warning: Callable[[str], None] | None = None,
    ) -> None:
        self._resolver = resolver
        self._on_warning = on_warning if on_warning is not None else lambda msg: None
        self._source_cache: dict[str, str] = {}
        self._parse_cache: dict[str, CifFile] = {}

    def load(self, source: str, *, base_uri: str | None = None) -> DdlmDictionary:
        """
        Parse a DDLm dictionary source string and resolve all ``_import.get``
        directives.

        Parameters
        ----------
        source:
            Raw CIF 2.0 source string of the dictionary to parse.
        base_uri:
            URI of the dictionary being parsed, used as the base for resolving
            relative import URIs.  If ``None`` and ``_dictionary.uri`` is present
            in the dictionary, that value is used.  If neither is available,
            relative URIs are passed to the resolver as-is.

        Returns
        -------
        DdlmDictionary
            The fully loaded dictionary with all imports resolved.
        """
        warnings: list[str] = []

        def warn(msg: str) -> None:
            warnings.append(msg)
            self._on_warning(msg)

        cif, parse_errors = build(source)
        for e in parse_errors:
            warn(f'parse error in dictionary: {e.message} (line {e.line})')

        if not cif.blocks:
            warn('dictionary CIF contains no data blocks')
            return DdlmDictionary(
                name='', title=None, version=None,
                categories={}, items={}, tag_to_item={},
                alias_to_definition_id={}, deprecated_ids=set(),
                warnings=warnings,
            )

        if len(cif.blocks) > 1:
            warn(f'dictionary CIF has {len(cif.blocks)} data blocks — using first')

        block_name = cif.blocks[0]
        block = cif[block_name]

        # Resolve base_uri from _dictionary.uri if not supplied by caller.
        if base_uri is None:
            uri_vals = block['_dictionary.uri'] if '_dictionary.uri' in block else []
            if uri_vals and isinstance(uri_vals[0], str) and uri_vals[0] not in ('.', '?'):
                base_uri = uri_vals[0]

        title = block['_dictionary.title'][0] if '_dictionary.title' in block else None
        if isinstance(title, str) and title in ('.', '?'):
            title = None
        version = block['_dictionary.version'][0] if '_dictionary.version' in block else None
        if isinstance(version, str) and version in ('.', '?'):
            version = None

        all_items: list[DdlmItem] = []

        for sf_name in block.save_frames:
            sf = block[sf_name]
            frame_data = {tag: sf[tag] for tag in sf.tags if tag in _FRAME_TAGS}

            if '_import.get' in frame_data:
                directives_val = frame_data['_import.get']
                if directives_val and isinstance(directives_val[0], list):
                    directives = directives_val[0]
                    self._resolve_imports(frame_data, directives, base_uri, warn)

            item = _extract_item(frame_data, warn)
            if item is not None:
                all_items.append(item)

        categories, items, tag_to_item, alias_to_def_id, deprecated_ids = (
            _build_lookup_tables(all_items, warn)
        )

        return DdlmDictionary(
            name=block_name,
            title=title,
            version=version,
            categories=categories,
            items=items,
            tag_to_item=tag_to_item,
            alias_to_definition_id=alias_to_def_id,
            deprecated_ids=deprecated_ids,
            warnings=warnings,
        )

    def _resolve_imports(
        self,
        frame_data: dict[str, list],
        directives: list[Any],
        base_uri: str | None,
        warn: Callable[[str], None],
    ) -> None:
        """Apply ``_import.get`` directives to *frame_data* in place."""
        # Sort by 'order' if present; fall back to list order.
        def _order_key(d: Any) -> int:
            if not isinstance(d, dict):
                return 0
            v = d.get('order')
            try:
                return int(v) if v is not None else 0
            except (TypeError, ValueError):
                return 0

        sorted_directives = sorted(directives, key=_order_key)

        for directive in sorted_directives:
            if not isinstance(directive, dict):
                warn(f'_import.get directive is not a table — skipped: {directive!r}')
                continue

            file_uri = directive.get('file', '')
            save_id = directive.get('save', '')
            mode = directive.get('mode', 'Contents')
            dupl = directive.get('dupl', 'Exit')
            miss = directive.get('miss', 'Exit')

            if not isinstance(file_uri, str) or not file_uri:
                warn("_import.get directive missing 'file' key — skipped")
                continue
            if not isinstance(save_id, str) or not save_id:
                warn("_import.get directive missing 'save' key — skipped")
                continue

            if mode != 'Contents':
                warn(
                    f"_import.get mode {mode!r} is not supported in Phase 1 "
                    f"(file={file_uri!r}, save={save_id!r}) — skipped"
                )
                continue

            # Resolve the URI relative to base_uri if needed.
            resolved_uri = self._resolve_uri(file_uri, base_uri)
            source_cif = self._get_parsed(resolved_uri)

            if source_cif is None:
                msg = (
                    f"_import.get could not load {resolved_uri!r} "
                    f"(save={save_id!r})"
                )
                if miss == 'Ignore':
                    warn(msg + ' — ignored')
                    continue
                else:  # 'Exit' (default)
                    warn(msg + ' — aborting dictionary load')
                    return

            # Locate the named save frame by _definition.id match.
            source_frame_data = self._find_frame_by_definition_id(
                source_cif, save_id, warn
            )

            if source_frame_data is None:
                msg = (
                    f"_import.get save frame with _definition.id={save_id!r} "
                    f"not found in {resolved_uri!r}"
                )
                if miss == 'Ignore':
                    warn(msg + ' — ignored')
                    continue
                else:
                    warn(msg + ' — aborting dictionary load')
                    return

            # Merge source tags into frame_data per dupl policy.
            abort = self._merge_frame(
                frame_data, source_frame_data, source_cif, dupl, warn
            )
            if abort:
                return

    def _resolve_uri(self, uri: str, base_uri: str | None) -> str:
        """Return the URI to pass to the resolver."""
        # If the URI looks absolute or base_uri is absent, use it as-is.
        return uri

    def _get_source(self, uri: str) -> str | None:
        """Return raw CIF source for *uri*, using cache then resolver."""
        if uri in self._source_cache:
            return self._source_cache[uri]
        if self._resolver is None:
            return None
        src = self._resolver(uri)
        if src is not None:
            self._source_cache[uri] = src
        return src

    def _get_parsed(self, uri: str) -> CifFile | None:
        """Return a parsed CifFile for *uri*, using cache then resolver."""
        if uri in self._parse_cache:
            return self._parse_cache[uri]
        src = self._get_source(uri)
        if src is None:
            return None
        cif, _ = build(src)
        self._parse_cache[uri] = cif
        return cif

    def _find_frame_by_definition_id(
        self,
        cif: CifFile,
        definition_id: str,
        warn: Callable[[str], None],
    ) -> dict[str, list] | None:
        """
        Search all save frames in *cif* for one whose ``_definition.id``
        matches *definition_id* (case-insensitive).  Returns its working dict
        or ``None`` if not found.
        """
        if not cif.blocks:
            return None
        block = cif[cif.blocks[0]]
        target = definition_id.lower()
        for sf_name in block.save_frames:
            sf = block[sf_name]
            if '_definition.id' not in sf:
                continue
            raw_id = sf['_definition.id'][0]
            if isinstance(raw_id, str) and raw_id.lower() == target:
                return {tag: sf[tag] for tag in sf.tags if tag in _FRAME_TAGS}
        return None

    def _merge_frame(
        self,
        frame_data: dict[str, list],
        source_data: dict[str, list],
        source_cif: CifFile,
        dupl: str,
        warn: Callable[[str], None],
    ) -> bool:
        """
        Merge *source_data* tags into *frame_data* according to *dupl* policy.

        Returns ``True`` if the load should be aborted (``dupl == "Exit"``
        and a conflict was found), ``False`` otherwise.
        """
        for tag, values in source_data.items():
            if tag in _IMPORT_IDENTITY_TAGS:
                # Never import frame-identity tags from a source frame.
                continue
            if tag not in frame_data:
                frame_data[tag] = values
            else:
                if dupl == 'Ignore':
                    pass  # Keep existing value.
                elif dupl == 'Replace':
                    # If the tag belongs to a Loop category, remove all tags
                    # from that category in frame_data before inserting.
                    self._replace_loop_category_tags(
                        frame_data, tag, source_cif
                    )
                    frame_data[tag] = values
                else:  # 'Exit' (default)
                    warn(
                        f"_import.get dupl=Exit: conflict on tag {tag!r} — "
                        f"aborting dictionary load"
                    )
                    return True
        return False

    def _replace_loop_category_tags(
        self,
        frame_data: dict[str, list],
        tag: str,
        source_cif: CifFile,
    ) -> None:
        """
        If *tag* belongs to a Loop category in *source_cif*, remove all tags
        from that category in *frame_data* before the caller inserts the new
        value.
        """
        if not source_cif.blocks:
            return
        block = source_cif[source_cif.blocks[0]]

        # Find the tag's save frame to get its _name.category_id.
        tag_lower = tag.lower()
        category_id: str | None = None
        for sf_name in block.save_frames:
            sf = block[sf_name]
            if '_definition.id' not in sf:
                continue
            raw_id = sf['_definition.id'][0]
            if not isinstance(raw_id, str) or raw_id.lower() != tag_lower:
                continue
            cat_vals = sf['_name.category_id'] if '_name.category_id' in sf else []
            if cat_vals and isinstance(cat_vals[0], str):
                category_id = cat_vals[0].lower()
            break

        if category_id is None:
            return

        # Check if that category is a Loop class.
        for sf_name in block.save_frames:
            sf = block[sf_name]
            if '_definition.id' not in sf:
                continue
            raw_id = sf['_definition.id'][0]
            if not isinstance(raw_id, str) or raw_id.lower() != category_id:
                continue
            class_vals = sf['_definition.class'] if '_definition.class' in sf else []
            if class_vals and isinstance(class_vals[0], str):
                if class_vals[0].lower() == 'loop':
                    # Remove all tags in frame_data that belong to this category.
                    # Look up each tag's category via its save frame in source_cif.
                    self._remove_category_tags(frame_data, category_id, block)
            break

    def _remove_category_tags(
        self,
        frame_data: dict[str, list],
        category_id: str,
        block: Any,
    ) -> None:
        """Remove all tags from *frame_data* whose category is *category_id*."""
        to_remove = []
        for existing_tag in list(frame_data):
            for sf_name in block.save_frames:
                sf = block[sf_name]
                if '_definition.id' not in sf:
                    continue
                raw_id = sf['_definition.id'][0]
                if not isinstance(raw_id, str) or raw_id.lower() != existing_tag.lower():
                    continue
                cat_vals = sf['_name.category_id'] if '_name.category_id' in sf else []
                if cat_vals and isinstance(cat_vals[0], str):
                    if cat_vals[0].lower() == category_id:
                        to_remove.append(existing_tag)
                break
        for t in to_remove:
            del frame_data[t]
