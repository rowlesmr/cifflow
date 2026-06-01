"""DuckDB post-processing: type coercion and default filling for ingested CIF databases."""

from cifflow.database.atom_types import standardise_atom_type_symbols
from cifflow.database.compact import convert_database
from cifflow.database.component_intensities import consolidate_component_intensities
from cifflow.database.defaults import generate_defaults

__all__ = [
    'consolidate_component_intensities',
    'convert_database',
    'generate_defaults',
    'standardise_atom_type_symbols',
]
