"""DDLm dictionary loader — parses a DDLm CIF and resolves _import.get directives."""

import json
import pathlib
from collections.abc import Callable
from typing import Any

from cifflow.cifmodel.builder import build
from cifflow.cifmodel.model import CifFile, CifSaveFrame
from cifflow.dictionary.ddlm_item import DdlmItem
from cifflow.dictionary.ddlm_parser import DdlmDictionary


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
    '_enumeration.default',
    '_enumeration.range',
    '_enumeration.def_index_ids',
    '_enumeration.def_index_id',    # deprecated alias
    '_enumeration_defaults.index',
    '_enumeration_defaults.value',
    '_enumeration_default.index',   # deprecated alias
    '_enumeration_default.value',   # deprecated alias
    '_type.dimension',
    '_category_key.name',
    '_alias.definition_id',
    '_definition_replaced.id',
    '_definition_replaced.by',
    '_import.get',
})

def _apply_tag_aliases(frame_data: dict[str, list]) -> None:
    """Rename deprecated DDLm tag keys in *frame_data* to their canonical forms.

    Also normalises values so the canonical tag always holds the expected type:

    - ``_enumeration.def_index_id`` (bare string) → ``_enumeration.def_index_ids``
      (CIF2 list): wraps the string values in a list so the canonical form is
      ``[['tag1', ...]]`` rather than ``['tag1']``.
    - ``_enumeration_default.index`` (bare strings) → ``_enumeration_defaults.index``
      (CIF2 lists): wraps each string element in a list so the canonical form is
      ``[['H'], ['D'], ...]`` rather than ``['H', 'D', ...]``.
    - ``_enumeration_default.value`` → ``_enumeration_defaults.value``: directly
      comparable; values are moved without transformation.
    """
    if '_enumeration.def_index_id' in frame_data and '_enumeration.def_index_ids' not in frame_data:
        old_vals = frame_data.pop('_enumeration.def_index_id')
        # old_vals = ['_atom_type.symbol']  →  canonical = [['_atom_type.symbol']]
        frame_data['_enumeration.def_index_ids'] = [old_vals]

    if '_enumeration_default.index' in frame_data and '_enumeration_defaults.index' not in frame_data:
        old_vals = frame_data.pop('_enumeration_default.index')
        # old_vals = ['H', 'D', ...]  →  canonical = [['H'], ['D'], ...]
        frame_data['_enumeration_defaults.index'] = [[v] for v in old_vals]

    if '_enumeration_default.value' in frame_data and '_enumeration_defaults.value' not in frame_data:
        frame_data['_enumeration_defaults.value'] = frame_data.pop('_enumeration_default.value')


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


def directory_path_resolver(path: str | pathlib.Path) -> 'Callable[[str], str | None]':
    """
    Return a path resolver that maps a URI to its absolute file path.

    Companion to :func:`directory_resolver`.  Pass to
    ``DictionaryLoader(path_resolver=...)`` so that ``source_files`` in the
    resulting ``DdlmDictionary`` contains absolute paths rather than bare URIs.

    Parameters
    ----------
    path:
        Directory to search for dictionary files.

    Returns
    -------
    Callable[[str], str | None]
        Maps URI strings to absolute path strings, or ``None`` if not found.
    """
    directory = pathlib.Path(path)

    def _resolve_path(uri: str) -> str | None:
        filename = pathlib.PurePosixPath(uri).name
        candidate = directory / filename
        if candidate.exists():
            return str(candidate.resolve())
        return None

    return _resolve_path


def _scalar(
    data: dict[str, list],
    tag: str,
    default: str | None = None,
    *,
    keep_dot: bool = False,
) -> str | None:
    """Return the first string value for *tag*, or *default* if absent/placeholder.

    When *keep_dot* is True, the CIF inapplicable placeholder ``'.'`` is
    returned as-is rather than being replaced by *default*.
    """
    vals = data.get(tag)
    if not vals:
        return default
    v = vals[0]
    if not isinstance(v, str):
        return default
    if v == '?' or (v == '.' and not keep_dot):
        return default
    return v


def _str_list(data: dict[str, list], tag: str) -> list[str]:
    """Return all string values for *tag*, excluding placeholders."""
    return [v for v in data.get(tag, []) if isinstance(v, str) and v != '?']


def _extract_item(data: dict[str, list], warn: Callable[[str], None]) -> DdlmItem | None:
    """Extract a DdlmItem from a working dict, or return None if the frame should be skipped."""
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
    enumeration_default = _scalar(data, '_enumeration.default', keep_dot=True)
    enumeration_range = _scalar(data, '_enumeration.range')
    type_dimension    = _scalar(data, '_type.dimension')

    # _enumeration.def_index_ids — a single CIF2 list value
    raw_index_ids = data.get('_enumeration.def_index_ids', [])
    if raw_index_ids and isinstance(raw_index_ids[0], list):
        enumeration_def_index_ids = [
            v.lower() for v in raw_index_ids[0]
            if isinstance(v, str) and v not in ('.', '?')
        ]
    else:
        enumeration_def_index_ids = []

    # _enumeration_defaults loop
    index_raw = data.get('_enumeration_defaults.index', [])
    value_raw = data.get('_enumeration_defaults.value', [])
    enumeration_defaults: list[tuple[list[str], str]] = []
    for idx_val, def_val in zip(index_raw, value_raw):
        if isinstance(idx_val, list):
            key = [str(k) for k in idx_val if isinstance(k, str) and k not in ('.', '?')]
        elif isinstance(idx_val, str) and idx_val not in ('.', '?'):
            key = [idx_val]
        else:
            continue
        if isinstance(def_val, (list, dict)):
            val_str = json.dumps(def_val, separators=(',', ':'), ensure_ascii=False)
        elif isinstance(def_val, str) and def_val != '?':
            val_str = def_val
        else:
            continue
        enumeration_defaults.append((key, val_str))

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
        enumeration_default=enumeration_default,
        category_keys=category_keys,
        aliases=aliases,
        replaced_by=replaced_by,
        is_deprecated=is_deprecated,
        enumeration_range=enumeration_range,
        type_dimension=type_dimension,
        enumeration_def_index_ids=enumeration_def_index_ids,
        enumeration_defaults=enumeration_defaults,
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


def _merge_constituent(
    pool: dict[str, DdlmItem],
    constituent: DdlmDictionary,
    dupl: str,
    warn: Callable[[str], None],
) -> bool:
    """
    Merge all items from *constituent* into *pool*.

    Iterates over all categories and items in *constituent*, applying the *dupl*
    policy to resolve conflicts with items already in *pool*.

    Returns ``True`` if the load should be aborted (``dupl == "Exit"`` and a
    conflict was found), ``False`` otherwise.
    """
    all_items = {**constituent.categories, **constituent.items}
    for def_id, item in all_items.items():
        if def_id not in pool:
            pool[def_id] = item
        elif dupl == 'Ignore':
            pass
        elif dupl == 'Replace':
            pool[def_id] = item
        else:  # 'Exit'
            warn(
                f"_import.get dupl=Exit: constituent definition {def_id!r} "
                f"conflicts with existing pool entry — aborting"
            )
            return True
    return False


class DictionaryLoader:
    """
    Loads a DDLm dictionary from a CIF 2.0 source string.

    Resolves ``_import.get`` directives using the supplied ``SourceResolver``.
    Both ``mode="Contents"`` (frame-level attribute merge) and ``mode="Full"``
    (constituent dictionary incorporation) are supported.  File access is fully
    delegated to the resolver; this class never accesses the filesystem or
    network directly.

    Parsed files are cached for the lifetime of the loader instance.  To
    invalidate the cache, create a new instance.

    Parameters
    ----------
    resolver
        Callable that maps a URI string to a raw CIF source string, or ``None``
        if the file is unavailable.  If ``None``, import directives that require
        an external file will trigger the ``if_miss`` policy.
    path_resolver
        Optional companion to *resolver* that maps the same URI to an absolute
        filesystem path.  When provided, the resolved paths are recorded in
        :attr:`~cifflow.dictionary.ddlm_parser.DdlmDictionary.source_files`.
    on_warning
        Optional callback for non-fatal warnings.  If ``None``, warnings are
        silently discarded.
    ignore_head_imports
        When ``True``, ``_import.get`` directives in save frames with
        ``_definition.class = Head`` are silently skipped.  Only the save
        frames physically present in the file being loaded are parsed.
        Applies to all files loaded by this instance, including constituents
        loaded via ``mode="Full"`` recursion.  Defaults to ``False``.
    """

    def __init__(
        self,
        resolver: SourceResolver | None = None,
        *,
        path_resolver: 'Callable[[str], str | None] | None' = None,
        on_warning: Callable[[str], None] | None = None,
        ignore_head_imports: bool = False,
    ) -> None:
        self._resolver = resolver
        self._path_resolver = path_resolver
        self._on_warning = on_warning if on_warning is not None else lambda msg: None
        self._ignore_head_imports = ignore_head_imports
        self._source_cache: dict[str, str] = {}
        self._parse_cache: dict[str, CifFile] = {}

    def load(self, source: str, *, base_uri: str | None = None) -> DdlmDictionary:
        """
        Parse a DDLm dictionary source string and resolve all ``_import.get`` directives.

        Both ``mode="Contents"`` (frame-level attribute merge) and
        ``mode="Full"`` (constituent dictionary incorporation) are supported.
        When a ``mode="Full"`` import targets a Head category, the entire
        constituent dictionary is loaded recursively and its definitions are
        merged into the result, with local definitions taking precedence.

        Circular imports are detected and skipped with a warning.

        Parameters
        ----------
        source:
            Raw CIF 2.0 source string of the dictionary to parse.
        base_uri:
            URI of the dictionary being parsed, used as the base for resolving
            relative import URIs.  If ``None`` and ``_dictionary.uri`` is
            present in the dictionary, that value is used.  If neither is
            available, relative URIs are passed to the resolver as-is.

        Returns
        -------
        DdlmDictionary
            The fully loaded dictionary with all imports resolved.
        """
        collected: list[str] = []
        if base_uri:
            resolved = self._path_resolver(base_uri) if self._path_resolver else None
            collected.append(resolved or base_uri)
        return self._load_recursive(source, base_uri, set(), collected)

    def _load_recursive(
        self,
        source: str,
        base_uri: str | None,
        loading: set[str],
        collected: list[str],
    ) -> DdlmDictionary:
        """Parse and resolve one dictionary, tracking *loading* for cycle detection."""
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

        # Read the canonical dictionary URI unconditionally.
        uri_vals = block['_dictionary.uri'] if '_dictionary.uri' in block else []
        dict_uri = uri_vals[0] if uri_vals and isinstance(uri_vals[0], str) and uri_vals[0] not in ('.', '?') else None

        # Resolve base_uri for import resolution if not supplied by caller.
        if base_uri is None:
            base_uri = dict_uri

        title = block['_dictionary.title'][0] if '_dictionary.title' in block else None
        if isinstance(title, str) and title in ('.', '?'):
            title = None
        version = block['_dictionary.version'][0] if '_dictionary.version' in block else None
        if isinstance(version, str) and version in ('.', '?'):
            version = None

        # pool accumulates DdlmItems from mode="Full" constituent imports.
        # Primary items (from this file's frames) are appended afterwards so
        # they overwrite constituent definitions with the same definition_id.
        pool: dict[str, DdlmItem] = {}
        primary_items: list[DdlmItem] = []

        for sf_name in block.save_frames:
            sf = block[sf_name]
            frame_data = {tag: sf[tag] for tag in sf.tags if tag in _FRAME_TAGS}
            _apply_tag_aliases(frame_data)

            frame_class = (_scalar(frame_data, '_definition.class') or '').lower()
            is_head = frame_class == 'head'
            if '_import.get' in frame_data and not (self._ignore_head_imports and is_head):
                directives_val = frame_data['_import.get']
                if directives_val and isinstance(directives_val[0], list):
                    directives = directives_val[0]
                    self._resolve_imports(
                        frame_data, directives, base_uri, loading, pool, warn, collected
                    )

            item = _extract_item(frame_data, warn)
            if item is not None:
                primary_items.append(item)

        # Merge: constituents first (pool), then primary overwrites.
        all_items = list(pool.values()) + primary_items

        categories, items, tag_to_item, alias_to_def_id, deprecated_ids = (
            _build_lookup_tables(all_items, warn)
        )

        return DdlmDictionary(
            name=block_name,
            title=title,
            version=version,
            uri=dict_uri,
            categories=categories,
            items=items,
            tag_to_item=tag_to_item,
            alias_to_definition_id=alias_to_def_id,
            deprecated_ids=deprecated_ids,
            warnings=warnings,
            source_files=list(collected),
        )

    def _load_constituent(
        self,
        uri: str,
        loading: set[str],
        warn: Callable[[str], None],
        collected: list[str] | None = None,
    ) -> DdlmDictionary | None:
        """
        Load and return the dictionary at *uri*, or ``None`` on failure.

        Checks *loading* for circular imports before proceeding.  Adds *uri*
        to *loading* for the duration of the recursive call.
        """
        if uri in loading:
            warn(f'circular import detected for {uri!r} — skipped')
            return None
        src = self._get_source(uri)
        if src is None:
            return None
        if collected is not None:
            resolved = self._path_resolver(uri) if self._path_resolver else None
            entry = resolved or uri
            if entry not in collected:
                collected.append(entry)
        loading.add(uri)
        try:
            return self._load_recursive(src, uri, loading, collected if collected is not None else [])
        finally:
            loading.discard(uri)

    def _resolve_imports(
        self,
        frame_data: dict[str, list],
        directives: list[Any],
        base_uri: str | None,
        loading: set[str],
        pool: dict[str, DdlmItem],
        warn: Callable[[str], None],
        collected: list[str] | None = None,
    ) -> None:
        """Apply ``_import.get`` directives to *frame_data* and/or *pool*."""
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

            if mode not in ('Contents', 'Full'):
                warn(
                    f"_import.get mode {mode!r} is not supported "
                    f"(file={file_uri!r}, save={save_id!r}) — skipped"
                )
                continue

            # Resolve the URI relative to base_uri if needed.
            resolved_uri = self._resolve_uri(file_uri, base_uri)

            if mode == 'Full':
                # Look up the named save frame first to determine whether the
                # target is a Head category (dictionary-level import) or an
                # ordinary frame (frame-level attribute merge like Contents).
                source_cif = self._get_parsed(resolved_uri)

                if source_cif is None:
                    msg = (
                        f"_import.get could not load {resolved_uri!r} "
                        f"(save={save_id!r})"
                    )
                    if miss == 'Ignore':
                        warn(msg + ' — ignored')
                        continue
                    else:
                        warn(msg + ' — aborting dictionary load')
                        return

                source_frame_data = self._find_frame_by_definition_id(
                    source_cif, save_id, lambda _: None
                )

                if source_frame_data is None:
                    msg = (
                        f"_import.get save frame {save_id!r} not found "
                        f"in {resolved_uri!r}"
                    )
                    if miss == 'Ignore':
                        warn(msg + ' — ignored')
                        continue
                    else:
                        warn(msg + ' — aborting dictionary load')
                        return

                target_class = (
                    _scalar(source_frame_data, '_definition.class') or ''
                ).lower()

                if target_class == 'head':
                    # Dictionary-level import: load the entire constituent
                    # dictionary and merge all its definitions into pool.
                    constituent = self._load_constituent(resolved_uri, loading, warn, collected)
                    if constituent is None:
                        msg = (
                            f"_import.get could not load constituent "
                            f"{resolved_uri!r} (save={save_id!r})"
                        )
                        if miss == 'Ignore':
                            warn(msg + ' — ignored')
                            continue
                        else:
                            warn(msg + ' — aborting dictionary load')
                            return

                    # Surface constituent warnings prefixed with their source.
                    for w in constituent.warnings:
                        warn(f'[{resolved_uri}] {w}')

                    abort = _merge_constituent(pool, constituent, dupl, warn)
                    if abort:
                        return
                    continue

                # Non-Head target: frame-level attribute merge (same as Contents).
                # Fall through to the shared frame-merge path below.
                # source_cif and source_frame_data are already resolved above.

            else:
                # mode == 'Contents': frame-level attribute merge.
                source_cif = self._get_parsed(resolved_uri)

                if source_cif is None:
                    msg = (
                        f"_import.get could not load {resolved_uri!r} "
                        f"(save={save_id!r})"
                    )
                    if miss == 'Ignore':
                        warn(msg + ' — ignored')
                        continue
                    else:
                        warn(msg + ' — aborting dictionary load')
                        return

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

            # Shared frame-level merge path (mode="Contents" or mode="Full" non-Head).
            # source_cif and source_frame_data are already resolved above.
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
        Search all save frames in *cif* for one matching *definition_id*.

        Match strategy (case-insensitive, in priority order):

        1. ``_definition.id`` value — used by full dictionary frames.
        2. Save frame label — used by template files (e.g. ``templ_attr.cif``)
           that declare no ``_definition.id``.

        Returns the frame's working dict filtered to ``_FRAME_TAGS``, or
        ``None`` if no match is found.
        """
        if not cif.blocks:
            return None
        block = cif[cif.blocks[0]]
        target = definition_id.lower()
        for sf_name in block.save_frames:
            sf = block[sf_name]
            if '_definition.id' in sf:
                raw_id = sf['_definition.id'][0]
                if isinstance(raw_id, str) and raw_id.lower() == target:
                    fd = {tag: sf[tag] for tag in sf.tags if tag in _FRAME_TAGS}
                    _apply_tag_aliases(fd)
                    return fd
            elif sf_name.lower() == target:
                # Template files carry no _definition.id; match by frame label.
                fd = {tag: sf[tag] for tag in sf.tags if tag in _FRAME_TAGS}
                _apply_tag_aliases(fd)
                return fd
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
        """If *tag* belongs to a Loop category in *source_cif*, remove all tags from that category in *frame_data* before the caller inserts the new value."""
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
