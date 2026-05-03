"""
Integration tests: ddl.dic and cif_core.dic end-to-end
(load → schema → apply_schema).
"""

import pathlib
import sqlite3

import pytest

import tempfile

from cifflow.dictionary import (
    DictionaryLoader,
    apply_schema,
    directory_resolver,
    generate_schema,
    load_dictionary,
    resolve_tag,
    save_dictionary,
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

    def test_synthetic_cifflow_block_id_present(self, schema):
        for table in schema.tables.values():
            col_names = [c.name for c in table.columns]
            assert '_cifflow_block_id' in col_names, (
                f"table {table.name!r} missing _cifflow_block_id"
            )

    def test_loop_tables_have_cifflow_row_id(self, schema):
        for table in schema.tables.values():
            if table.category_class == 'Loop':
                col_names = [c.name for c in table.columns]
                assert '_cifflow_row_id' in col_names, (
                    f"Loop table {table.name!r} missing _cifflow_row_id"
                )

    def test_fk_deferrable_in_ddl_if_present(self, schema):
        # ddl.dic may have zero FKs (Link items whose targets are non-schema
        # categories).  If any FK exists, it must carry DEFERRABLE.
        from cifflow.dictionary.schema import emit_create_statements
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

    def test_cifflow_row_id_unique_on_loop_tables(self, conn, schema):
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
            assert '_cifflow_row_id' in unique_cols, (
                f"Loop table {table.name!r} missing UNIQUE on _cifflow_row_id"
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
        # multi_block_core imports cif_core; _atom_site.fract_x should be present.
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
        # _array_data.data is defined in cif_img.
        r = resolve_tag('_array_data.data', cif_pow)
        assert r is not None

    def test_shared_transitive_dependency_no_spurious_warnings(self, cif_pow):
        # cif_core is reachable via both cif_img and multi_block_core.
        # With dupl=Ignore, no duplicate-conflict warnings should appear.
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
            # Any alias should resolve to the same item as the canonical tag.
            for alias, canonical in loaded.alias_to_definition_id.items():
                assert loaded.tag_to_item[alias] is loaded.tag_to_item[canonical]
                break  # one is enough
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
