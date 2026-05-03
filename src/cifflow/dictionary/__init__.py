"""
Dictionary layer public API.

Provides DDLm dictionary loading, schema generation, schema application,
and tag resolution.

Typical usage::

    from cifflow.dictionary import DictionaryLoader, directory_resolver
    from cifflow.dictionary import generate_schema, apply_schema, resolve_tag

    loader = DictionaryLoader(resolver=directory_resolver('data/dictionaries'))
    dictionary = loader.load(open('data/dictionaries/cif_core.dic').read())
    schema = generate_schema(dictionary)

    import sqlite3
    conn = sqlite3.connect(':memory:')
    apply_schema(conn, schema)

    result = resolve_tag('_atom_site.fract_x', dictionary)
"""

from cifflow.dictionary.ddlm_item import DdlmItem
from cifflow.dictionary.ddlm_parser import DdlmDictionary
from cifflow.dictionary.loader import (
    DictionaryLoader,
    SourceResolver,
    directory_resolver,
    directory_path_resolver,
)
from cifflow.dictionary.schema import (
    ColumnDef,
    ForeignKeyDef,
    SchemaSpec,
    TableDef,
    emit_create_statements,
    emit_fallback_create_statements,
    generate_schema,
)
from cifflow.dictionary.schema_apply import apply_schema, apply_fallback_schema
from cifflow.dictionary.resolver import ResolvedTag, resolve_tag
from cifflow.dictionary.cache import save_dictionary, load_dictionary
from cifflow.dictionary.visualise import visualise_schema, visualise_schema_html

__all__ = [
    'DdlmItem',
    'DdlmDictionary',
    'DictionaryLoader',
    'SourceResolver',
    'directory_resolver',
    'directory_path_resolver',
    'ForeignKeyDef',
    'ColumnDef',
    'TableDef',
    'SchemaSpec',
    'generate_schema',
    'emit_create_statements',
    'emit_fallback_create_statements',
    'apply_schema',
    'apply_fallback_schema',
    'ResolvedTag',
    'resolve_tag',
    'save_dictionary',
    'load_dictionary',
    'visualise_schema',
    'visualise_schema_html',
]
