"""
SQLite ingestion for CifFile objects.

See prompts/Stage4_Ingestion_Prompt.md for the full specification.
"""

from __future__ import annotations

import sqlite3
from typing import Callable

from pycifparse.cifmodel.model import CifFile
from pycifparse.dictionary.ddlm_parser import DdlmDictionary
from pycifparse.dictionary.schema import SchemaSpec


def ingest(
    cif: CifFile,
    conn: sqlite3.Connection,
    schema: SchemaSpec | None = None,
    dictionary: DdlmDictionary | None = None,
    *,
    propagate_fk: bool = False,
    dataset_id: str | None = None,
    on_error: Callable[[str], None] | None = None,
) -> list[str]:
    """Ingest a parsed CifFile into a SQLite database.

    Parameters
    ----------
    cif:
        Parsed CifFile from build(). May contain one or more blocks.
    conn:
        Open sqlite3.Connection with the schema already applied.
    schema:
        SchemaSpec used to route tags to structured tables. If None, all tags
        are routed to _cif_fallback.
    dictionary:
        DdlmDictionary for alias resolution. May be None when schema is None.
    propagate_fk:
        When True, non-key FK columns absent from the CIF data inherit their
        value from the FK target already known in the same block.
    dataset_id:
        The _audit_dataset.id value identifying the dataset to ingest.
        When None, the common dataset ID is auto-detected from the blocks.
    on_error:
        Optional callback for non-fatal semantic errors.

    Returns
    -------
    list[str]
        Semantic error/warning strings in emission order.
    """
    raise NotImplementedError
