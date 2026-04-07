"""
Dictionary layer public API.

Provides DDLm dictionary loading, schema generation, schema application,
and tag resolution.

Typical usage::

    from pycifparse.dictionary import DictionaryLoader, directory_resolver
    from pycifparse.dictionary import generate_schema, apply_schema, resolve_tag

    loader = DictionaryLoader(resolver=directory_resolver('data/dictionaries'))
    dictionary = loader.load(open('data/dictionaries/cif_core.dic').read())
    schema = generate_schema(dictionary)

    import sqlite3
    conn = sqlite3.connect(':memory:')
    apply_schema(conn, schema)

    result = resolve_tag('_atom_site.fract_x', dictionary)
"""

from pycifparse.dictionary.ddlm_item import DdlmItem
from pycifparse.dictionary.ddlm_parser import DdlmDictionary
from pycifparse.dictionary.loader import (
    DictionaryLoader,
    SourceResolver,
    directory_resolver,
)
from pycifparse.dictionary.schema import (
    ColumnDef,
    ForeignKeyDef,
    SchemaSpec,
    TableDef,
    emit_create_statements,
    generate_schema,
)
from pycifparse.dictionary.schema_apply import apply_schema
from pycifparse.dictionary.resolver import ResolvedTag, resolve_tag
from pycifparse.dictionary.cache import save_dictionary, load_dictionary

__all__ = [
    'DdlmItem',
    'DdlmDictionary',
    'DictionaryLoader',
    'SourceResolver',
    'directory_resolver',
    'ForeignKeyDef',
    'ColumnDef',
    'TableDef',
    'SchemaSpec',
    'generate_schema',
    'emit_create_statements',
    'apply_schema',
    'ResolvedTag',
    'resolve_tag',
    'save_dictionary',
    'load_dictionary',
]
