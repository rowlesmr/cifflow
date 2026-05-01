"""
Integration tests: ddl.dic and cif_core.dic end-to-end
(load → schema → create tables via emit_create_statements).
"""

import pathlib
import tempfile

import duckdb
import pytest

from pycifparse.dictionary import (
    DictionaryLoader,
    directory_resolver,
    emit_create_statements,
    generate_schema,
    load_dictionary,
    resolve_tag,
    save_dictionary,
)

_DATA_DIR = pathlib.Path(__file__).parents[2] / 'data' / 'dictionaries'


def _apply_schema_duckdb(db: duckdb.DuckDBPyConnection, schema) -> None:
    """Create structured tables in *db* from *schema* using DuckDB."""
    for stmt in emit_create_statements(schema):
        db.execute(stmt)


def _db_tables(db: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        row[0]
        for row in db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    }


def _db_columns(db: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [
        row[0]
        for row in db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=? ORDER BY ordinal_position",
            [table],
        ).fetchall()
    ]


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
        c = duckdb.connect()
        _apply_schema_duckdb(c, schema)
        yield c
        c.close()

    def test_has_tables(self, schema):
        assert len(schema.tables) > 0

    def test_table_names_in_duckdb(self, conn, schema):
        db_tables = _db_tables(conn)
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
        fk_stmts = [s for s in emit_create_statements(schema)
                    if 'FOREIGN KEY' in s]
        for stmt in fk_stmts:
            assert 'DEFERRABLE INITIALLY DEFERRED' in stmt

    def test_column_to_tag_non_empty(self, schema):
        assert len(schema.column_to_tag) > 0

    def test_column_to_tag_round_trip(self, schema, conn):
        for (tbl, col), tag in list(schema.column_to_tag.items())[:5]:
            col_names = _db_columns(conn, tbl)
            assert col in col_names, (
                f"column {col!r} not found in table {tbl!r}"
            )


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
        c = duckdb.connect()
        _apply_schema_duckdb(c, schema)
        yield c
        c.close()

    def test_no_load_exceptions(self, dictionary):
        assert dictionary.name != ''

    def test_type_attributes_populated_on_imported_items(self, dictionary):
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
        r = resolve_tag('_atom_site.fract_x', dictionary)
        assert r is not None
        assert r.category_id == 'atom_site'
        assert r.object_id == 'fract_x'

    def test_resolve_tag_unknown_returns_none(self, dictionary):
        assert resolve_tag('_totally_unknown.tag', dictionary) is None

    def test_schema_tables_created(self, conn, schema):
        db_tables = _db_tables(conn)
        for name in schema.tables:
            assert name in db_tables

    def test_columns_present_on_loop_tables(self, conn, schema):
        loop_tables = [t for t in schema.tables.values()
                       if t.category_class == 'Loop']
        assert loop_tables, "expected at least one Loop table"
        for table in loop_tables[:3]:
            col_names = _db_columns(conn, table.name)
            assert '_row_id' in col_names, (
                f"Loop table {table.name!r} missing _row_id column"
            )


# ---------------------------------------------------------------------------
# Stage 3C: metadictionary loading and cache round-trip
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestMetadictionary:
    @pytest.fixture(scope='class')
    def multi_block(self):
        resolver = directory_resolver(_DATA_DIR)
        src = (_DATA_DIR / 'multi_block_core.dic').read_text(encoding='utf-8')
        return DictionaryLoader(resolver=resolver).load(src)

    @pytest.fixture(scope='class')
    def cif_img(self):
        resolver = directory_resolver(_DATA_DIR)
        src = (_DATA_DIR / 'cif_img.dic').read_text(encoding='utf-8')
        return DictionaryLoader(resolver=resolver).load(src)

    @pytest.fixture(scope='class')
    def cif_pow(self):
        resolver = directory_resolver(_DATA_DIR)
        src = (_DATA_DIR / 'cif_pow.dic').read_text(encoding='utf-8')
        return DictionaryLoader(resolver=resolver).load(src)

    def test_multi_block_contains_cif_core_definitions(self, multi_block):
        r = resolve_tag('_atom_site.fract_x', multi_block)
        assert r is not None
        assert r.category_id == 'atom_site'

    def test_cif_img_contains_multi_block_and_core_definitions(self, cif_img):
        assert resolve_tag('_atom_site.fract_x', cif_img) is not None

    def test_cif_pow_definition_count_exceeds_cif_core(self, cif_pow):
        resolver = directory_resolver(_DATA_DIR)
        core_src = (_DATA_DIR / 'cif_core.dic').read_text(encoding='utf-8')
        core = DictionaryLoader(resolver=resolver).load(core_src)
        assert len(cif_pow.items) > len(core.items)

    def test_cif_pow_contains_cif_core_definition(self, cif_pow):
        assert resolve_tag('_atom_site.fract_x', cif_pow) is not None

    def test_cif_pow_contains_cif_img_definition(self, cif_pow):
        r = resolve_tag('_array_data.data', cif_pow)
        assert r is not None

    def test_shared_transitive_dependency_no_spurious_warnings(self, cif_pow):
        warnings = []
        resolver = directory_resolver(_DATA_DIR)
        src = (_DATA_DIR / 'cif_pow.dic').read_text(encoding='utf-8')
        DictionaryLoader(resolver=resolver, on_warning=warnings.append).load(src)
        conflict_warnings = [w for w in warnings if 'dupl=Exit' in w]
        assert conflict_warnings == []


@pytest.mark.slow
class TestDictionaryCache:
    @pytest.fixture(scope='class')
    def cif_core_dict(self):
        resolver = directory_resolver(_DATA_DIR)
        src = (_DATA_DIR / 'cif_core.dic').read_text(encoding='utf-8')
        return DictionaryLoader(resolver=resolver).load(src)

    def test_round_trip_preserves_item_count(self, cif_core_dict):
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            path = pathlib.Path(f.name)
        try:
            save_dictionary(cif_core_dict, path)
            loaded = load_dictionary(path)
            assert len(loaded.items) == len(cif_core_dict.items)
            assert len(loaded.categories) == len(cif_core_dict.categories)
        finally:
            path.unlink(missing_ok=True)

    def test_round_trip_tag_to_item_resolves_correctly(self, cif_core_dict):
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            path = pathlib.Path(f.name)
        try:
            save_dictionary(cif_core_dict, path)
            loaded = load_dictionary(path)
            r = resolve_tag('_atom_site.fract_x', loaded)
            assert r is not None
            assert r.object_id == 'fract_x'
        finally:
            path.unlink(missing_ok=True)

    def test_round_trip_alias_survives(self, cif_core_dict):
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            path = pathlib.Path(f.name)
        try:
            save_dictionary(cif_core_dict, path)
            loaded = load_dictionary(path)
            for alias, canonical in loaded.alias_to_definition_id.items():
                assert loaded.tag_to_item[alias] is loaded.tag_to_item[canonical]
                break
        finally:
            path.unlink(missing_ok=True)

    def test_round_trip_deprecated_ids_is_set(self, cif_core_dict):
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            path = pathlib.Path(f.name)
        try:
            save_dictionary(cif_core_dict, path)
            loaded = load_dictionary(path)
            assert isinstance(loaded.deprecated_ids, set)
            assert len(loaded.deprecated_ids) == len(cif_core_dict.deprecated_ids)
        finally:
            path.unlink(missing_ok=True)
