"""DuckDB post-processing: type coercion and default filling for ingested CIF databases."""

from cifflow.database.compact import convert_database
from cifflow.database.defaults import generate_defaults

__all__ = ['convert_database', 'generate_defaults']
