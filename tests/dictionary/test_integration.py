"""
Integration tests: ddl.dic and cif_core.dic end-to-end
(load → schema → apply_schema).
"""

import pathlib
import sqlite3

import pytest

from pycifparse.dictionary import (
    DictionaryLoader,
    apply_schema,
    directory_resolver,
    generate_schema,
    resolve_tag,
)

_DATA_DIR = pathlib.Path(__file__).parents[2] / 'data' / 'dictionaries'


# ---------------------------------------------------------------------------
# ddl.dic — the DDLm self-defining dictionary
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestDdlDic:
    @pytest.fixture(scope='class')
    def schema(self):
        resolver = directory_resolver(_DATA_DIR)
        source = (_DATA_DIR / 'ddl.dic').read_text(encoding='utf-8')
        d = DictionaryLoader(resolver=resolver).load(source)
        return generate_schema(d)

    @pytest.fixture(scope='class')
    def conn(self, schema):
        c = sqlite3.connect(':memory:')
        apply_schema(c, schema)
        return c

    def test_has_tables(self, schema):
        assert len(schema.tables) > 0

    def test_table_names_in_sqlite(self, conn, schema):
        db_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for name in schema.tables:
            assert name in db_tables

    def test_synthetic_block_id_present(self, schema):
        for table in schema.tables.values():
            col_names = [c.name for c in table.columns]
            assert '_block_id' in col_names, (
                f"table {table.name!r} missing _block_id"
            )

    def test_loop_tables_have_row_id(self, schema):
        for table in schema.tables.values():
            if table.category_class == 'Loop':
                col_names = [c.name for c in table.columns]
                assert '_row_id' in col_names, (
                    f"Loop table {table.name!r} missing _row_id"
                )

    def test_fk_deferrable_in_ddl_if_present(self, schema):
        # ddl.dic may have zero FKs (Link items whose targets are non-schema
        # categories).  If any FK exists, it must carry DEFERRABLE.
        from pycifparse.dictionary.schema import emit_create_statements
        fk_stmts = [s for s in emit_create_statements(schema)
                    if 'FOREIGN KEY' in s]
        for stmt in fk_stmts:
            assert 'DEFERRABLE INITIALLY DEFERRED' in stmt

    def test_column_to_tag_non_empty(self, schema):
        assert len(schema.column_to_tag) > 0

    def test_column_to_tag_round_trip(self, schema, conn):
        # Pick up to 5 entries and verify the column exists in the table.
        for (tbl, col), tag in list(schema.column_to_tag.items())[:5]:
            rows = list(conn.execute(
                f'PRAGMA table_info("{tbl}")'
            ))
            col_names = [r[1] for r in rows]
            assert col in col_names, (
                f"column {col!r} not found in table {tbl!r}"
            )

    def test_fk_via_pragma(self, conn, schema):
        for table in schema.tables.values():
            if not table.foreign_keys:
                continue
            fk_list = list(conn.execute(
                f'PRAGMA foreign_key_list("{table.name}")'
            ))
            assert len(fk_list) >= len(table.foreign_keys), (
                f"table {table.name!r}: expected {len(table.foreign_keys)} FKs, "
                f"got {len(fk_list)}"
            )
            break  # one table is enough for the integration check


# ---------------------------------------------------------------------------
# cif_core.dic — a domain dictionary that uses _import.get
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestCifCoreDic:
    @pytest.fixture(scope='class')
    def dictionary(self):
        resolver = directory_resolver(_DATA_DIR)
        source = (_DATA_DIR / 'cif_core.dic').read_text(encoding='utf-8')
        return DictionaryLoader(resolver=resolver).load(source)

    @pytest.fixture(scope='class')
    def schema(self, dictionary):
        return generate_schema(dictionary)

    @pytest.fixture(scope='class')
    def conn(self, schema):
        c = sqlite3.connect(':memory:')
        apply_schema(c, schema)
        return c

    def test_no_load_exceptions(self, dictionary):
        # Fixture itself verifies no exception was raised; just check name.
        assert dictionary.name != ''

    def test_type_attributes_populated_on_imported_items(self, dictionary):
        # Items that use _import.get should have type_purpose/type_contents.
        su_items = [
            item for item in dictionary.items.values()
            if item.type_purpose == 'SU'
        ]
        assert len(su_items) > 0

    def test_alias_resolution(self, dictionary):
        assert len(dictionary.alias_to_definition_id) > 0
        for alias, canon in list(dictionary.alias_to_definition_id.items())[:3]:
            assert alias in dictionary.tag_to_item
            assert dictionary.tag_to_item[alias].definition_id == canon

    def test_deprecated_ids_non_empty(self, dictionary):
        assert len(dictionary.deprecated_ids) > 0

    def test_resolve_tag_known(self, dictionary):
        # _atom_site.fract_x is in cif_core
        r = resolve_tag('_atom_site.fract_x', dictionary)
        assert r is not None
        assert r.category_id == 'atom_site'
        assert r.object_id == 'fract_x'

    def test_resolve_tag_unknown_returns_none(self, dictionary):
        assert resolve_tag('_totally_unknown.tag', dictionary) is None

    def test_schema_tables_created(self, conn, schema):
        db_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for name in schema.tables:
            assert name in db_tables

    def test_row_id_unique_on_loop_tables(self, conn, schema):
        loop_tables = [t for t in schema.tables.values()
                       if t.category_class == 'Loop']
        assert loop_tables, "expected at least one Loop table"
        for table in loop_tables[:3]:
            indexes = list(conn.execute(f'PRAGMA index_list("{table.name}")'))
            unique_cols: set[str] = set()
            for idx in indexes:
                if idx[2] == 1:  # unique flag
                    for info in conn.execute(
                        f'PRAGMA index_info("{idx[1]}")'
                    ):
                        unique_cols.add(info[2])
            assert '_row_id' in unique_cols, (
                f"Loop table {table.name!r} missing UNIQUE on _row_id"
            )
