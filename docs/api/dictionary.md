# Dictionary

::: cifflow.dictionary.ddlm_item
    options:
      members: [DdlmItem]

::: cifflow.dictionary.ddlm_parser
    options:
      members: [DdlmDictionary]

::: cifflow.dictionary.loader
    options:
      members: [DictionaryLoader, SourceResolver, directory_resolver, directory_path_resolver]

::: cifflow.dictionary.schema
    options:
      members: [BridgeColumnDef, ForeignKeyDef, ColumnDef, TableDef, SchemaSpec, generate_schema, emit_create_statements, emit_fallback_create_statements]

::: cifflow.dictionary.resolver
    options:
      members: [ResolvedTag, resolve_tag]

::: cifflow.dictionary.cache
    options:
      members: [save_dictionary, load_dictionary]

::: cifflow.dictionary.visualise
    options:
      members: [visualise_schema, visualise_schema_html]
