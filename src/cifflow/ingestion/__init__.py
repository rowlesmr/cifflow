"""CIF ingestion: load a CifFile into a DuckDB database."""

from cifflow.ingestion.ingest import ingest, IngestionError

__all__ = ['ingest', 'IngestionError']
