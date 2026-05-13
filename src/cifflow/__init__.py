"""cifflow — CIF parsing, ingestion, emission, and validation library."""

from cifflow.types import CifVersion
from cifflow.database import convert_database
from cifflow.cifmodel.model import CifFile, CifBlock, CifSaveFrame
from cifflow.cifmodel.builder import build, build_arrow, build_arrow_file, cif_to_arrow, CifBuilder
from cifflow.cifmodel.writer import CifWriter, BlockWriter, SaveFrameWriter, CifInput
from cifflow.cifmodel.clean import clean, CleanWarning
from cifflow.ingestion import ingest
from cifflow.ingestion.ingest import IngestionError
from cifflow.dictionary import (
    DdlmItem,
    DdlmDictionary,
    DictionaryLoader,
    SourceResolver,
    directory_resolver,
    directory_path_resolver,
    ForeignKeyDef,
    ColumnDef,
    TableDef,
    SchemaSpec,
    generate_schema,
    emit_create_statements,
    emit_fallback_create_statements,
    ResolvedTag,
    resolve_tag,
    save_dictionary,
    load_dictionary,
    visualise_schema,
    visualise_schema_html,
)
from cifflow.output import emit, quote, EmitMode, OutputPlan, BlockSpec, only, any_of, all_of, has, namer
from cifflow.fidelity import check_fidelity, FidelityReport, FidelityMismatch
from cifflow.validation import validate, validate_database, ValidationReport, ValidationIssue, DbValidationResult
from cifflow.inspect import (
    inspect_lexer,
    inspect_parse,
    ParseHandler,
    inspect_model,
    inspect_schema,
    inspect_ingest,
    TraceEvent,
)

__all__ = [
    # CIF model
    'CifFile', 'CifBlock', 'CifSaveFrame', 'CifBuilder', 'build', 'build_arrow', 'build_arrow_file', 'cif_to_arrow',
    # Version
    'CifVersion',
    # Writer
    'CifWriter', 'BlockWriter', 'SaveFrameWriter', 'CifInput',
    # Clean
    'clean', 'CleanWarning',
    # Ingestion
    'ingest', 'IngestionError',
    # Database
    'convert_database',
    # Dictionary
    'DdlmItem', 'DdlmDictionary',
    'DictionaryLoader', 'SourceResolver', 'directory_resolver', 'directory_path_resolver',
    'ForeignKeyDef', 'ColumnDef', 'TableDef', 'SchemaSpec',
    'generate_schema', 'emit_create_statements', 'emit_fallback_create_statements',
    'ResolvedTag', 'resolve_tag',
    'save_dictionary', 'load_dictionary',
    'visualise_schema', 'visualise_schema_html',
    # Output
    'emit', 'quote', 'EmitMode', 'OutputPlan', 'BlockSpec', 'only', 'any_of', 'all_of', 'has', 'namer',
    # Fidelity
    'check_fidelity', 'FidelityReport', 'FidelityMismatch',
    # Validation
    'validate', 'validate_database', 'ValidationReport', 'ValidationIssue', 'DbValidationResult',
    # Inspect
    'inspect_lexer', 'inspect_parse', 'ParseHandler',
    'inspect_model', 'inspect_schema', 'inspect_ingest', 'TraceEvent',
]
