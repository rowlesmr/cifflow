"""
Unit tests for ingest().
"""

import sqlite3

import pytest

from pycifparse import ingest
from pycifparse.cifmodel.builder import build
from pycifparse.dictionary.schema_apply import apply_fallback_schema


class TestImport:
    def test_ingest_importable_from_top_level(self):
        from pycifparse import ingest as _ingest
        assert callable(_ingest)

    def test_ingest_importable_from_ingestion(self):
        from pycifparse.ingestion import ingest as _ingest
        assert callable(_ingest)
