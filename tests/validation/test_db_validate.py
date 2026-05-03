"""Tests for validate_database() and the per-check helpers."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

import pytest

from cifflow.dictionary.schema import ColumnDef, SchemaSpec, TableDef
from cifflow.dictionary.schema_apply import apply_fallback_schema
from cifflow.validation._db_checks import (
    _NULL_LEAF,
    check_enumeration_range_leaf,
    check_enumeration_states_leaf,
    check_type_container,
    check_type_contents_leaf,
    check_type_dimension,
    extract_leaves,
    parse_type_dimension,
)
from cifflow.validation._db_validate import DbValidationResult, validate_database


# ---------------------------------------------------------------------------
# Schema / DB builder helpers
# ---------------------------------------------------------------------------

def _col(
    name: str,
    *,
    type_contents: str | None = 'Text',
    type_container: str | None = 'Single',
    nullable: bool = True,
    is_primary_key: bool = False,
    is_synthetic: bool = False,
    enumeration_states: list[str] | None = None,
    enumeration_range: str | None = None,
    type_dimension: str | None = None,
) -> ColumnDef:
    return ColumnDef(
        name=name,
        definition_id=f'_{name}.{name}' if not is_synthetic else '',
        type_contents=type_contents,
        nullable=nullable,
        is_primary_key=is_primary_key,
        is_synthetic=is_synthetic,
        linked_item_id=None,
        type_container=type_container,
        enumeration_states=enumeration_states or [],
        enumeration_range=enumeration_range,
        type_dimension=type_dimension,
    )


def _synthetic(name: str, *, is_primary_key: bool = False) -> ColumnDef:
    return ColumnDef(
        name=name,
        definition_id='',
        type_contents=None,
        nullable=False if not is_primary_key else False,
        is_primary_key=is_primary_key,
        is_synthetic=True,
        linked_item_id=None,
        type_container=None,
    )


def _make_table(
    table_name: str,
    domain_cols: list[ColumnDef],
    *,
    pks: list[str] | None = None,
    category_class: str = 'Loop',
    keyless_set: bool = False,
) -> TableDef:
    """Create a TableDef with standard synthetic prefix columns."""
    block_id_col = _synthetic('_cifflow_block_id', is_primary_key='_cifflow_block_id' in (pks or []))
    row_id_col = _synthetic('_cifflow_row_id', is_primary_key='_cifflow_row_id' in (pks or []))

    if keyless_set:
        cifflow_id = _synthetic('_cifflow_id', is_primary_key=True)
        all_cols = [block_id_col, cifflow_id, row_id_col] + domain_cols
        primary_keys = ['_cifflow_id']
    else:
        all_cols = [block_id_col, row_id_col] + domain_cols
        primary_keys = pks if pks is not None else ['_cifflow_block_id', '_cifflow_row_id']

    return TableDef(
        name=table_name,
        definition_id=f'_{table_name}',
        category_class=category_class,
        columns=all_cols,
        primary_keys=primary_keys,
    )


def _make_schema(
    table_def: TableDef,
    *,
    extra_tables: dict[str, TableDef] | None = None,
) -> SchemaSpec:
    tables = {table_def.name: table_def}
    if extra_tables:
        tables.update(extra_tables)
    column_to_tag: dict[tuple[str, str], str] = {}
    for t in tables.values():
        for c in t.columns:
            if not c.is_synthetic and c.definition_id:
                column_to_tag[(t.name, c.name)] = c.definition_id
    return SchemaSpec(tables=tables, column_to_tag=column_to_tag)


def _setup_db(
    table_def: TableDef,
    rows: list[dict],
    *,
    extra_tables: dict[str, TableDef] | None = None,
    fallback_rows: list[dict] | None = None,
) -> tuple[sqlite3.Connection, SchemaSpec]:
    """Create an in-memory SQLite DB with the given table populated."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    apply_fallback_schema(conn)

    schema = _make_schema(table_def, extra_tables=extra_tables)

    # Create the table.
    all_tables = {table_def.name: table_def}
    if extra_tables:
        all_tables.update(extra_tables)
    for tbl_name, tbl_def in all_tables.items():
        col_parts = []
        for c in tbl_def.columns:
            typ = 'INTEGER' if c.name == '_cifflow_row_id' else 'TEXT'
            null_clause = '' if c.nullable else ' NOT NULL'
            col_parts.append(f'"{c.name}" {typ}{null_clause}')
        pk_clause = ', '.join(f'"{pk}"' for pk in tbl_def.primary_keys)
        col_parts.append(f'PRIMARY KEY ({pk_clause})')
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{tbl_name}" ({", ".join(col_parts)})')

    # Insert rows.
    tbl_cols = [c.name for c in table_def.columns]
    for row in rows:
        cols = list(row.keys())
        placeholders = ', '.join('?' for _ in cols)
        col_str = ', '.join(f'"{c}"' for c in cols)
        conn.execute(
            f'INSERT INTO "{table_def.name}" ({col_str}) VALUES ({placeholders})',
            [row[c] for c in cols],
        )

    # Insert fallback rows if requested.
    if fallback_rows:
        for fr in fallback_rows:
            conn.execute(
                'INSERT INTO "_cif_fallback" ("_cifflow_block_id", "_cifflow_row_id", "tag", "value", "value_type") '
                'VALUES (?, ?, ?, ?, ?)',
                (fr['_cifflow_block_id'], fr['_cifflow_row_id'], fr['tag'], fr.get('value'), fr.get('value_type', 'string')),
            )

    conn.commit()
    return conn, schema


# ---------------------------------------------------------------------------
# TestTypeContainer
# ---------------------------------------------------------------------------

class TestTypeContainer:
    def _col_single(self, tc='Text'):
        return _col('val', type_contents=tc, type_container='Single')

    def _col_list(self, tc='Text'):
        return _col('val', type_contents=tc, type_container='List')

    def _col_table(self, tc='Text'):
        return _col('val', type_contents=tc, type_container='Table')

    def _col_implied(self):
        return _col('val', type_contents='Text', type_container='Implied')

    def _run(self, col, value, strict=True):
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': value}])
        results = validate_database(conn, schema, strict_container_nulls=strict)
        conn.close()
        return results

    def test_single_plain_string_no_result(self):
        assert self._run(self._col_single(), 'hello') == []

    def test_single_json_array_gives_error(self):
        results = self._run(self._col_single(), '[1,2,3]')
        assert len(results) == 1
        assert results[0].check == 'type_container'
        assert results[0].severity == 'Error'
        assert results[0].message == 'Expected scalar, got JSON array'

    def test_single_json_object_gives_error(self):
        results = self._run(self._col_single(), '{"a": 1}')
        assert len(results) == 1
        assert results[0].message == 'Expected scalar, got JSON object'

    def test_list_json_array_no_result(self):
        assert self._run(self._col_list(), '[1,2,3]') == []

    def test_list_plain_string_gives_error(self):
        results = self._run(self._col_list(), 'hello')
        assert len(results) == 1
        assert results[0].check == 'type_container'
        assert 'Expected JSON array, got scalar' in results[0].message

    def test_list_json_object_gives_error(self):
        results = self._run(self._col_list(), '{"a": 1}')
        assert results[0].message == 'Expected JSON array, got JSON object'

    def test_array_column_json_array_no_result(self):
        col = _col('val', type_container='Array')
        assert self._run(col, '[1,2]') == []

    def test_array_column_plain_string_gives_error(self):
        col = _col('val', type_container='Array')
        results = self._run(col, 'hello')
        assert results[0].check == 'type_container'

    def test_matrix_column_nested_array_no_result(self):
        col = _col('val', type_container='Matrix')
        assert self._run(col, '[[1,2],[3,4]]') == []

    def test_matrix_column_plain_string_gives_error(self):
        col = _col('val', type_container='Matrix')
        results = self._run(col, 'hello')
        assert results[0].check == 'type_container'

    def test_matrix_column_empty_array_no_result(self):
        col = _col('val', type_container='Matrix')
        assert self._run(col, '[]') == []

    def test_table_empty_object_no_result(self):
        assert self._run(self._col_table(), '{}') == []

    def test_table_json_array_gives_error(self):
        results = self._run(self._col_table(), '[1,2]')
        assert results[0].message == 'Expected JSON object, got JSON array'

    def test_table_plain_string_gives_error(self):
        results = self._run(self._col_table(), 'hello')
        assert results[0].message == 'Expected JSON object, got scalar'

    def test_table_unquotable_key_gives_error(self):
        # A key that contains both ''' and """ forces semicolon delimiter.
        bad_key = "''' and \"\"\""
        results = self._run(self._col_table(), json.dumps({bad_key: 'v'}))
        type_container_results = [r for r in results if r.check == 'type_container']
        assert len(type_container_results) >= 1
        # value field carries the raw key string
        assert any(r.value == bad_key for r in type_container_results)

    def test_table_unquotable_does_not_block_cde(self):
        # Bad key fails Check A but Check C should still run on values.
        bad_key = "''' and \"\"\""
        col = _col('val', type_contents='Integer', type_container='Table')
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': json.dumps({bad_key: 'not_int'})}])
        results = validate_database(conn, schema)
        conn.close()
        checks = {r.check for r in results}
        assert 'type_container' in checks
        assert 'type_contents' in checks

    def test_implied_container_gives_error(self):
        results = self._run(self._col_implied(), 'anything')
        assert len(results) == 1
        assert results[0].check == 'type_container'
        assert "'Implied' container" in results[0].message

    def test_type_container_failure_blocks_bce(self):
        # List column given a plain string → Check A fails; C/D/E not run.
        col = _col('val', type_contents='Integer', type_container='List',
                   enumeration_states=['1', '2'])
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': 'hello'}])
        results = validate_database(conn, schema)
        conn.close()
        assert all(r.check == 'type_container' for r in results)

    def test_null_value_skipped(self):
        assert self._run(self._col_single(), None) == []

    def test_placeholder_dot_skipped(self):
        assert self._run(self._col_single(), '.') == []

    def test_placeholder_question_skipped(self):
        assert self._run(self._col_single(), '?') == []


# ---------------------------------------------------------------------------
# TestTypeDimension
# ---------------------------------------------------------------------------

class TestTypeDimension:
    def _run(self, dim: str, value, tc='List', strict=True):
        col = _col('val', type_contents='Text', type_container=tc, type_dimension=dim)
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': json.dumps(value)}])
        results = validate_database(conn, schema, strict_container_nulls=strict)
        conn.close()
        return [r for r in results if r.check == 'type_dimension']

    def test_list_correct_length_no_result(self):
        assert self._run('[3]', [1, 2, 3]) == []

    def test_list_wrong_length_gives_warning(self):
        results = self._run('[3]', [1, 2])
        assert len(results) == 1
        assert results[0].severity == 'Warning'
        assert 'Expected 3 elements at dimension 1, got 2' in results[0].message

    def test_list_variable_size_no_result(self):
        assert self._run('[]', [1, 2, 3, 4, 5]) == []

    def test_matrix_correct_shape_no_result(self):
        assert self._run('[2,3]', [[1, 2, 3], [4, 5, 6]], tc='Matrix') == []

    def test_matrix_wrong_outer_length_gives_warning(self):
        results = self._run('[2,3]', [[1, 2, 3], [4, 5, 6], [7, 8, 9]], tc='Matrix')
        assert len(results) == 1
        assert 'Expected 2 elements at dimension 1, got 3' in results[0].message

    def test_matrix_wrong_inner_length_gives_warning(self):
        results = self._run('[2,3]', [[1, 2], [3, 4, 5]], tc='Matrix')
        assert len(results) == 1
        assert 'Expected 3 elements at dimension 2, got 2' in results[0].message
        assert results[0].value == json.dumps([1, 2])

    def test_matrix_1d_when_2d_expected_gives_warning(self):
        results = self._run('[2,3]', [1, 2], tc='Matrix')
        assert len(results) == 1
        assert 'Expected 2-D container, got 1-D' in results[0].message

    def test_matrix_one_row_correct(self):
        assert self._run('[1,3]', [[1, 2, 3]], tc='Matrix') == []

    def test_matrix_one_column_correct(self):
        assert self._run('[3,1]', [[1], [2], [3]], tc='Matrix') == []

    def test_matrix_one_row_wrong_outer(self):
        results = self._run('[1,3]', [[1, 2, 3], [4, 5, 6]], tc='Matrix')
        assert len(results) == 1
        assert 'Expected 1 elements at dimension 1, got 2' in results[0].message

    def test_matrix_two_rows_both_wrong_one_warning_only(self):
        # Stop after first failing row.
        results = self._run('[2,3]', [[1, 2], [3, 4]], tc='Matrix')
        assert len(results) == 1

    def test_multidimensional_list_valid(self):
        assert self._run('[2,3]', [[1, 2, 3], [4, 5, 6]], tc='List') == []

    def test_multidimensional_list_1d_for_2d_gives_warning(self):
        results = self._run('[2,3]', [1, 2], tc='List')
        assert len(results) == 1

    def test_array_3d_correct(self):
        val = [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]
        assert self._run('[2,2,2]', val, tc='Array') == []

    def test_array_3d_wrong_gives_warning(self):
        val = [[1, 2], [3, 4]]  # 2-D not 3-D
        results = self._run('[2,2,2]', val, tc='Array')
        assert len(results) == 1

    def test_null_row_strict_gives_warning(self):
        col = _col('val', type_contents='Text', type_container='Matrix', type_dimension='[2,3]')
        tbl = _make_table('t', [col])
        v = json.dumps([[1, 2, 3], None])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': v}])
        results = validate_database(conn, schema, strict_container_nulls=True)
        conn.close()
        dim_results = [r for r in results if r.check == 'type_dimension']
        assert len(dim_results) == 1
        assert 'Expected 2-D container, got 1-D' in dim_results[0].message

    def test_null_row_not_strict_no_result(self):
        col = _col('val', type_contents='Text', type_container='Matrix', type_dimension='[2,3]')
        tbl = _make_table('t', [col])
        v = json.dumps([[1, 2, 3], None])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': v}])
        results = validate_database(conn, schema, strict_container_nulls=False)
        conn.close()
        dim_results = [r for r in results if r.check == 'type_dimension']
        assert dim_results == []

    def test_zero_dimension_skipped(self):
        assert self._run('[0,3]', [[1, 2, 3]], tc='Matrix') == []

    def test_table_with_type_dimension_no_result(self):
        col = _col('val', type_contents='Text', type_container='Table', type_dimension='[3]')
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': '{"a":1}'}])
        results = validate_database(conn, schema)
        conn.close()
        assert all(r.check != 'type_dimension' for r in results)


# ---------------------------------------------------------------------------
# TestTypeContents
# ---------------------------------------------------------------------------

class TestTypeContents:
    def _run(self, type_contents: str, value: str) -> list[DbValidationResult]:
        col = _col('val', type_contents=type_contents, type_container='Single')
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': value}])
        results = validate_database(conn, schema)
        conn.close()
        return [r for r in results if r.check == 'type_contents']

    def test_text_always_valid(self):
        assert self._run('Text', 'anything goes here') == []

    def test_word_no_whitespace_valid(self):
        assert self._run('Word', 'hello') == []

    def test_word_with_space_warning(self):
        results = self._run('Word', 'hello world')
        assert len(results) == 1
        assert results[0].severity == 'Warning'

    def test_code_no_whitespace_valid(self):
        assert self._run('Code', 'P21/c') == []

    def test_code_with_space_warning(self):
        results = self._run('Code', 'P 21/c')
        assert len(results) == 1
        assert results[0].severity == 'Warning'

    def test_name_alphanumeric_underscore_valid(self):
        assert self._run('Name', 'C1') == []

    def test_name_with_hyphen_warning(self):
        results = self._run('Name', 'C-1')
        assert len(results) == 1
        assert results[0].severity == 'Warning'

    def test_tag_starts_underscore_valid(self):
        assert self._run('Tag', '_cell.length_a') == []

    def test_tag_no_underscore_warning(self):
        results = self._run('Tag', 'cell.length_a')
        assert len(results) == 1

    def test_uri_valid(self):
        assert self._run('Uri', 'https://example.com') == []

    def test_uri_whitespace_warning(self):
        results = self._run('Uri', 'not a\turi')
        assert len(results) == 1
        assert results[0].severity == 'Warning'

    def test_iri_valid_unicode(self):
        assert self._run('Iri', 'https://example.com/caf\u00e9') == []

    def test_iri_tab_warning(self):
        results = self._run('Iri', 'has\ttab')
        assert len(results) == 1
        assert results[0].severity == 'Warning'

    def test_date_valid(self):
        assert self._run('Date', '2024-01-15') == []

    def test_date_invalid_warning(self):
        results = self._run('Date', '15/01/2024')
        assert len(results) == 1
        assert results[0].severity == 'Warning'

    def test_datetime_valid(self):
        assert self._run('DateTime', '2024-01-15T12:00:00') == []

    def test_datetime_invalid_warning(self):
        results = self._run('DateTime', 'not-a-date')
        assert len(results) == 1

    def test_version_valid(self):
        assert self._run('Version', '1.2.3') == []

    def test_version_two_parts_warning(self):
        results = self._run('Version', '1.2')
        assert len(results) == 1

    def test_dimension_valid(self):
        assert self._run('Dimension', '[3,3]') == []

    def test_dimension_invalid_warning(self):
        results = self._run('Dimension', '3,3')
        assert len(results) == 1

    def test_range_open_lower_valid(self):
        assert self._run('Range', ':10') == []

    def test_range_closed_valid(self):
        assert self._run('Range', '0:10') == []

    def test_range_non_numeric_side_warning(self):
        results = self._run('Range', 'abc:10')
        assert len(results) == 1

    def test_integer_valid(self):
        assert self._run('Integer', '42') == []
        assert self._run('Integer', '-7') == []

    def test_integer_float_error(self):
        results = self._run('Integer', '3.5')
        assert len(results) == 1
        assert results[0].severity == 'Error'

    def test_integer_alpha_error(self):
        results = self._run('Integer', 'abc')
        assert len(results) == 1
        assert results[0].severity == 'Error'

    def test_real_valid(self):
        assert self._run('Real', '3.14') == []
        assert self._run('Real', '-1.0e-3') == []

    def test_real_alpha_error(self):
        results = self._run('Real', 'abc')
        assert len(results) == 1
        assert results[0].severity == 'Error'

    def test_symop_valid(self):
        assert self._run('Symop', '1') == []
        assert self._run('Symop', '1_555') == []

    def test_symop_alpha_warning(self):
        results = self._run('Symop', 'abc')
        assert len(results) == 1
        assert results[0].severity == 'Warning'

    def test_imag_no_result(self):
        assert self._run('Imag', 'anything') == []

    def test_complex_no_result(self):
        assert self._run('Complex', 'anything') == []

    def test_implied_skipped(self):
        assert self._run('Implied', 'anything') == []

    def test_by_reference_skipped(self):
        assert self._run('ByReference', 'anything') == []

    def test_inherited_skipped(self):
        assert self._run('Inherited', 'anything') == []


# ---------------------------------------------------------------------------
# TestTypeContentsLeafValues
# ---------------------------------------------------------------------------

class TestTypeContentsLeafValues:
    def _run(self, value_json: str, tc: str = 'Integer', strict: bool = True):
        col = _col('val', type_contents=tc, type_container='List')
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': value_json}])
        results = validate_database(conn, schema, strict_container_nulls=strict)
        conn.close()
        return [r for r in results if r.check == 'type_contents']

    def test_all_valid_leaves_no_result(self):
        assert self._run(json.dumps([1, 2, 3])) == []

    def test_one_invalid_leaf_gives_one_result(self):
        results = self._run(json.dumps([1, 'abc', 3]))
        assert len(results) == 1
        assert results[0].value == 'abc'

    def test_multiple_invalid_leaves_gives_multiple_results(self):
        results = self._run(json.dumps(['x', 'y', 3]))
        assert len(results) == 2

    def test_null_leaf_strict_gives_error(self):
        results = self._run(json.dumps([1, None, 3]), strict=True)
        null_results = [r for r in results if r.value == 'null']
        assert len(null_results) == 1
        assert null_results[0].severity == 'Error'

    def test_null_leaf_not_strict_no_result(self):
        assert self._run(json.dumps([1, None, 3]), strict=False) == []

    def test_json_number_coerced_to_string(self):
        # JSON stores 3.5 as a number; coerced to '3.5' which isn't a valid Integer.
        results = self._run(json.dumps([3.5]))
        assert len(results) == 1

    def test_placeholder_dot_in_list_skipped(self):
        assert self._run(json.dumps(['.', '?', '1'])) == []

    def test_quoted_sentinel_validated_normally(self):
        # "'.'" stored as a string — not a sentinel.
        results = self._run(json.dumps(["'.'"]))
        assert len(results) == 1  # "'.'" is not a valid Integer

    def test_table_column_valid_value_no_result(self):
        col = _col('val', type_contents='Integer', type_container='Table')
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': json.dumps({'k': '42'})}])
        results = validate_database(conn, schema)
        conn.close()
        assert [r for r in results if r.check == 'type_contents'] == []

    def test_table_column_invalid_value_gives_error(self):
        col = _col('val', type_contents='Integer', type_container='Table')
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': json.dumps({'k': 'abc'})}])
        results = validate_database(conn, schema)
        conn.close()
        tc_results = [r for r in results if r.check == 'type_contents']
        assert len(tc_results) == 1

    def test_list_of_lists_invalid_leaf(self):
        results = self._run(json.dumps([[1, 2], [3, 'bad']]))
        assert len(results) == 1
        assert results[0].value == 'bad'


# ---------------------------------------------------------------------------
# TestEnumerationRange
# ---------------------------------------------------------------------------

class TestEnumerationRange:
    def _run(self, value: str, range_str: str, tc: str = 'Real', strict: bool = True):
        col = _col('val', type_contents=tc, type_container='Single',
                   enumeration_range=range_str)
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': value}])
        results = validate_database(conn, schema, strict_container_nulls=strict)
        conn.close()
        return [r for r in results if r.check == 'enumeration_range']

    def test_value_within_range_no_result(self):
        assert self._run('5.0', '0:10') == []

    def test_value_at_lower_bound_no_result(self):
        assert self._run('0.0', '0:10') == []

    def test_value_at_upper_bound_no_result(self):
        assert self._run('10.0', '0:10') == []

    def test_value_below_lower_bound_error(self):
        results = self._run('-1.0', '0:')
        assert len(results) == 1
        assert results[0].severity == 'Error'
        assert 'below lower bound' in results[0].message

    def test_value_above_upper_bound_error(self):
        results = self._run('4.0', ':3.1415')
        assert len(results) == 1
        assert 'above upper bound' in results[0].message

    def test_open_upper_large_value_no_result(self):
        assert self._run('1000.0', '0:') == []

    def test_non_numeric_value_no_range_result(self):
        # type_contents Error is raised, but no enumeration_range result.
        results = self._run('abc', '0:10')
        assert results == []

    def test_list_leaves_in_range_no_result(self):
        col = _col('val', type_contents='Real', type_container='List',
                   enumeration_range='0:10')
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': '[1.0, 5.0, 9.9]'}])
        results = validate_database(conn, schema)
        conn.close()
        assert [r for r in results if r.check == 'enumeration_range'] == []

    def test_list_one_leaf_out_of_range_gives_error(self):
        col = _col('val', type_contents='Real', type_container='List',
                   enumeration_range='0:10')
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': '[5.0, 15.0]'}])
        results = validate_database(conn, schema)
        conn.close()
        range_results = [r for r in results if r.check == 'enumeration_range']
        assert len(range_results) == 1
        assert results[0].value == '15.0'

    def test_list_null_not_strict_no_result(self):
        col = _col('val', type_contents='Real', type_container='List',
                   enumeration_range='0:10')
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': '[1.0, null]'}])
        results = validate_database(conn, schema, strict_container_nulls=False)
        conn.close()
        assert [r for r in results if r.check == 'enumeration_range'] == []


# ---------------------------------------------------------------------------
# TestEnumerationStates
# ---------------------------------------------------------------------------

class TestEnumerationStates:
    def _run(self, value: str, states: list[str], tc: str = 'Text'):
        col = _col('val', type_contents=tc, type_container='Single',
                   enumeration_states=states)
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': value}])
        results = validate_database(conn, schema)
        conn.close()
        return [r for r in results if r.check == 'enumeration_states']

    def test_value_in_set_no_result(self):
        assert self._run('P1', ['P1', 'P21', 'C2']) == []

    def test_value_not_in_set_error(self):
        results = self._run('Pm', ['P1', 'P21', 'C2'])
        assert len(results) == 1
        assert results[0].severity == 'Error'
        assert 'Pm' in results[0].message

    def test_more_than_10_states_truncated(self):
        states = [str(i) for i in range(15)]
        results = self._run('bad', states)
        assert '... and 5 more' in results[0].message

    def test_code_case_insensitive_no_result(self):
        assert self._run('p21', ['P21', 'P1'], tc='Code') == []

    def test_name_case_insensitive_no_result(self):
        assert self._run('CARBON', ['Carbon'], tc='Name') == []

    def test_text_case_sensitive_error(self):
        results = self._run('p21', ['P21', 'P1'], tc='Text')
        assert len(results) == 1

    def test_list_all_valid_no_result(self):
        col = _col('val', type_contents='Text', type_container='List',
                   enumeration_states=['a', 'b', 'c'])
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': json.dumps(['a', 'b'])}])
        results = validate_database(conn, schema)
        conn.close()
        assert [r for r in results if r.check == 'enumeration_states'] == []

    def test_list_one_invalid_leaf_gives_error(self):
        col = _col('val', type_contents='Text', type_container='List',
                   enumeration_states=['a', 'b'])
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': json.dumps(['a', 'x'])}])
        results = validate_database(conn, schema)
        conn.close()
        es_results = [r for r in results if r.check == 'enumeration_states']
        assert len(es_results) == 1
        assert es_results[0].value == 'x'

    def test_list_null_not_strict_no_result(self):
        col = _col('val', type_contents='Text', type_container='List',
                   enumeration_states=['a', 'b'])
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': json.dumps(['a', None])}])
        results = validate_database(conn, schema, strict_container_nulls=False)
        conn.close()
        assert [r for r in results if r.check == 'enumeration_states'] == []


# ---------------------------------------------------------------------------
# TestSentinels
# ---------------------------------------------------------------------------

class TestSentinels:
    def _col_with_all_constraints(self):
        return _col(
            'val', type_contents='Integer', type_container='Single',
            enumeration_states=['1', '2', '3'],
            enumeration_range='1:3',
        )

    def test_null_skips_all_checks(self):
        col = self._col_with_all_constraints()
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': None}])
        results = validate_database(conn, schema)
        conn.close()
        assert results == []

    def test_dot_skips_all_checks(self):
        col = self._col_with_all_constraints()
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': '.'}])
        results = validate_database(conn, schema)
        conn.close()
        assert results == []

    def test_question_skips_all_checks(self):
        col = self._col_with_all_constraints()
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': '?'}])
        results = validate_database(conn, schema)
        conn.close()
        assert results == []

    def test_dot_question_in_list_skipped(self):
        col = _col('val', type_contents='Integer', type_container='List',
                   enumeration_states=['1'], enumeration_range='0:1')
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': json.dumps(['.', '?', '1'])}])
        results = validate_database(conn, schema)
        conn.close()
        assert results == []

    def test_quoted_sentinel_not_skipped(self):
        # "'.'" as a stored string is NOT the placeholder '.'.
        col = _col('val', type_contents='Integer', type_container='List')
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'val': json.dumps(["'.'"]) }])
        results = validate_database(conn, schema)
        conn.close()
        tc = [r for r in results if r.check == 'type_contents']
        assert len(tc) == 1


# ---------------------------------------------------------------------------
# TestKeyValues
# ---------------------------------------------------------------------------

class TestKeyValues:
    def test_single_pk_column_in_key_values(self):
        pk_col = _col('id', type_contents='Text', type_container='Single',
                      is_primary_key=True, nullable=False)
        val_col = _col('num', type_contents='Integer', type_container='Single')
        tbl = TableDef(
            name='t',
            definition_id='_t',
            category_class='Loop',
            columns=[
                _synthetic('_cifflow_block_id'),
                _synthetic('_cifflow_row_id'),
                pk_col, val_col,
            ],
            primary_keys=['id'],
        )
        schema = _make_schema(tbl)
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute('CREATE TABLE "t" ("_cifflow_block_id" TEXT NOT NULL, "_cifflow_row_id" INTEGER NOT NULL, "id" TEXT, "num" TEXT)')
        conn.execute('INSERT INTO "t" VALUES (?, ?, ?, ?)', ('b', 1, 'row1', 'bad'))
        conn.commit()

        results = validate_database(conn, schema)
        conn.close()

        tc_results = [r for r in results if r.check == 'type_contents']
        assert len(tc_results) == 1
        assert tc_results[0].key_values == {'_id.id': 'row1'}

    def test_keyless_set_empty_key_values(self):
        col = _col('val', type_contents='Integer')
        tbl = _make_table('t', [col], keyless_set=True, category_class='Set')

        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute(
            'CREATE TABLE "t" ("_cifflow_block_id" TEXT NOT NULL, "_cifflow_id" TEXT NOT NULL, '
            '"_cifflow_row_id" INTEGER NOT NULL, "val" TEXT, PRIMARY KEY ("_cifflow_id"))'
        )
        conn.execute('INSERT INTO "t" VALUES (?, ?, ?, ?)', ('b', 'id1', 1, 'bad'))
        conn.commit()

        schema = _make_schema(tbl)
        results = validate_database(conn, schema)
        conn.close()

        tc = [r for r in results if r.check == 'type_contents']
        assert len(tc) == 1
        assert tc[0].key_values == {}


# ---------------------------------------------------------------------------
# TestBlockIdFilter
# ---------------------------------------------------------------------------

class TestBlockIdFilter:
    def test_no_filter_checks_all_blocks(self):
        col = _col('val', type_contents='Integer')
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [
            {'_cifflow_block_id': 'b1', '_cifflow_row_id': 1, 'val': 'bad1'},
            {'_cifflow_block_id': 'b2', '_cifflow_row_id': 2, 'val': 'bad2'},
        ])
        results = validate_database(conn, schema)
        conn.close()
        blocks = {r.block_id for r in results if r.check == 'type_contents'}
        assert blocks == {'b1', 'b2'}

    def test_filter_to_one_block(self):
        col = _col('val', type_contents='Integer')
        tbl = _make_table('t', [col])
        conn, schema = _setup_db(tbl, [
            {'_cifflow_block_id': 'b1', '_cifflow_row_id': 1, 'val': 'bad1'},
            {'_cifflow_block_id': 'b2', '_cifflow_row_id': 2, 'val': 'bad2'},
        ])
        results = validate_database(conn, schema, block_id='b1')
        conn.close()
        blocks = {r.block_id for r in results if r.check == 'type_contents'}
        assert blocks == {'b1'}


# ---------------------------------------------------------------------------
# TestUnknownTag
# ---------------------------------------------------------------------------

class TestUnknownTag:
    def test_empty_fallback_no_result(self):
        tbl = _make_table('t', [_col('val')])
        conn, schema = _setup_db(tbl, [])
        results = validate_database(conn, schema)
        conn.close()
        assert [r for r in results if r.check == 'unknown_tag'] == []

    def test_one_fallback_tag_gives_warning(self):
        tbl = _make_table('t', [_col('val')])
        conn, schema = _setup_db(
            tbl, [],
            fallback_rows=[{'_cifflow_block_id': 'b', '_cifflow_row_id': 1, 'tag': '_cell.unknown', 'value': 'x'}],
        )
        results = validate_database(conn, schema)
        conn.close()
        ut = [r for r in results if r.check == 'unknown_tag']
        assert len(ut) == 1
        assert ut[0].severity == 'Warning'
        assert ut[0].tag == '_cell.unknown'
        assert ut[0].value == '_cell.unknown'
        assert ut[0].table == '_cif_fallback'
        assert ut[0].column == 'tag'

    def test_same_tag_two_blocks_gives_two_warnings(self):
        tbl = _make_table('t', [_col('val')])
        conn, schema = _setup_db(
            tbl, [],
            fallback_rows=[
                {'_cifflow_block_id': 'b1', '_cifflow_row_id': 1, 'tag': '_x.y'},
                {'_cifflow_block_id': 'b2', '_cifflow_row_id': 2, 'tag': '_x.y'},
            ],
        )
        results = validate_database(conn, schema)
        conn.close()
        ut = [r for r in results if r.check == 'unknown_tag']
        assert len(ut) == 2

    def test_cifflow_block_id_filter_limits_fallback(self):
        tbl = _make_table('t', [_col('val')])
        conn, schema = _setup_db(
            tbl, [],
            fallback_rows=[
                {'_cifflow_block_id': 'b1', '_cifflow_row_id': 1, 'tag': '_x.y'},
                {'_cifflow_block_id': 'b2', '_cifflow_row_id': 2, 'tag': '_x.y'},
            ],
        )
        results = validate_database(conn, schema, block_id='b1')
        conn.close()
        ut = [r for r in results if r.check == 'unknown_tag']
        assert len(ut) == 1
        assert ut[0].block_id == 'b1'


# ---------------------------------------------------------------------------
# TestKeylessSetCardinality
# ---------------------------------------------------------------------------

class TestKeylessSetCardinality:
    def _make_keyless_table(self):
        col = _col('val', type_contents='Text')
        return _make_table('t', [col], keyless_set=True, category_class='Set')

    def _create_keyless_db(self, tbl_def, rows):
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute(
            'CREATE TABLE "t" ("_cifflow_block_id" TEXT NOT NULL, "_cifflow_id" TEXT NOT NULL, '
            '"_cifflow_row_id" INTEGER NOT NULL, "val" TEXT, PRIMARY KEY ("_cifflow_id"))'
        )
        for row in rows:
            conn.execute('INSERT INTO "t" VALUES (?, ?, ?, ?)',
                         (row['_cifflow_block_id'], row['id'], row['_cifflow_row_id'], row.get('val')))
        conn.commit()
        schema = _make_schema(tbl_def)
        return conn, schema

    def test_one_row_per_block_no_result(self):
        tbl = self._make_keyless_table()
        conn, schema = self._create_keyless_db(tbl, [
            {'_cifflow_block_id': 'b', 'id': '1', '_cifflow_row_id': 1},
        ])
        results = validate_database(conn, schema)
        conn.close()
        assert [r for r in results if r.check == 'keyless_set_cardinality'] == []

    def test_two_rows_same_block_gives_error(self):
        tbl = self._make_keyless_table()
        conn, schema = self._create_keyless_db(tbl, [
            {'_cifflow_block_id': 'b', 'id': '1', '_cifflow_row_id': 1},
            {'_cifflow_block_id': 'b', 'id': '2', '_cifflow_row_id': 2},
        ])
        results = validate_database(conn, schema)
        conn.close()
        ksc = [r for r in results if r.check == 'keyless_set_cardinality']
        assert len(ksc) == 1
        assert ksc[0].severity == 'Error'
        assert ksc[0].value == '2'

    def test_violation_in_one_block_only(self):
        tbl = self._make_keyless_table()
        conn, schema = self._create_keyless_db(tbl, [
            {'_cifflow_block_id': 'b1', 'id': '1', '_cifflow_row_id': 1},
            {'_cifflow_block_id': 'b1', 'id': '2', '_cifflow_row_id': 2},
            {'_cifflow_block_id': 'b1', 'id': '3', '_cifflow_row_id': 3},
            {'_cifflow_block_id': 'b2', 'id': '4', '_cifflow_row_id': 4},
        ])
        results = validate_database(conn, schema)
        conn.close()
        ksc = [r for r in results if r.check == 'keyless_set_cardinality']
        assert len(ksc) == 1
        assert ksc[0].block_id == 'b1'
        assert ksc[0].value == '3'

    def test_cifflow_block_id_filter_to_clean_block_no_result(self):
        tbl = self._make_keyless_table()
        conn, schema = self._create_keyless_db(tbl, [
            {'_cifflow_block_id': 'b1', 'id': '1', '_cifflow_row_id': 1},
            {'_cifflow_block_id': 'b1', 'id': '2', '_cifflow_row_id': 2},
            {'_cifflow_block_id': 'b2', 'id': '3', '_cifflow_row_id': 3},
        ])
        results = validate_database(conn, schema, block_id='b2')
        conn.close()
        assert [r for r in results if r.check == 'keyless_set_cardinality'] == []

    def test_cifflow_block_id_filter_to_violating_block_gives_error(self):
        tbl = self._make_keyless_table()
        conn, schema = self._create_keyless_db(tbl, [
            {'_cifflow_block_id': 'b1', 'id': '1', '_cifflow_row_id': 1},
            {'_cifflow_block_id': 'b1', 'id': '2', '_cifflow_row_id': 2},
            {'_cifflow_block_id': 'b2', 'id': '3', '_cifflow_row_id': 3},
        ])
        results = validate_database(conn, schema, block_id='b1')
        conn.close()
        ksc = [r for r in results if r.check == 'keyless_set_cardinality']
        assert len(ksc) == 1

    def test_keyed_set_not_subject_to_cardinality_check(self):
        # A table with a natural PK is not a keyless Set; no cardinality check.
        pk_col = _col('id', type_contents='Text', is_primary_key=True, nullable=False)
        val_col = _col('val', type_contents='Text')
        tbl = TableDef(
            name='t',
            definition_id='_t',
            category_class='Set',
            columns=[_synthetic('_cifflow_block_id'), _synthetic('_cifflow_row_id'), pk_col, val_col],
            primary_keys=['id'],
        )
        schema = _make_schema(tbl)
        conn = sqlite3.connect(':memory:')
        apply_fallback_schema(conn)
        conn.execute('CREATE TABLE "t" ("_cifflow_block_id" TEXT NOT NULL, "_cifflow_row_id" INTEGER NOT NULL, "id" TEXT, "val" TEXT, PRIMARY KEY ("id"))')
        conn.execute('INSERT INTO "t" VALUES (?, ?, ?, ?)', ('b', 1, 'id1', 'v1'))
        conn.execute('INSERT INTO "t" VALUES (?, ?, ?, ?)', ('b', 2, 'id2', 'v2'))
        conn.commit()
        results = validate_database(conn, schema)
        conn.close()
        assert [r for r in results if r.check == 'keyless_set_cardinality'] == []


# ---------------------------------------------------------------------------
# TestInternalError
# ---------------------------------------------------------------------------

class TestInternalError:
    def test_exception_inside_run_gives_internal_error(self):
        from unittest.mock import patch
        from cifflow.validation import _db_validate

        tbl = _make_table('t', [_col('val')])
        conn, schema = _setup_db(tbl, [])

        with patch.object(_db_validate, '_run_validation', side_effect=RuntimeError('boom')):
            results = validate_database(conn, schema)
        conn.close()

        ie = [r for r in results if r.check == 'internal_error']
        assert len(ie) == 1
        assert ie[0].severity == 'Error'
        assert 'boom' in ie[0].message


# ---------------------------------------------------------------------------
# Helpers unit tests
# ---------------------------------------------------------------------------

class TestParseTypeDimension:
    def test_valid_1d(self):
        assert parse_type_dimension('[3]') == (3,)

    def test_valid_2d(self):
        assert parse_type_dimension('[2,3]') == (2, 3)

    def test_empty_returns_none(self):
        assert parse_type_dimension('[]') is None

    def test_zero_value_returns_none(self):
        assert parse_type_dimension('[0,3]') is None

    def test_malformed_returns_none(self):
        assert parse_type_dimension('3,3') is None
        assert parse_type_dimension('[abc]') is None


class TestExtractLeaves:
    def test_flat_list(self):
        assert extract_leaves([1, 2, 3]) == ['1', '2', '3']

    def test_nested_list(self):
        assert extract_leaves([[1, 2], [3, 4]]) == ['1', '2', '3', '4']

    def test_dict_values_only(self):
        leaves = extract_leaves({'a': '1', 'b': '2'})
        assert set(leaves) == {'1', '2'}

    def test_null_gives_null_leaf(self):
        leaves = extract_leaves([None])
        assert leaves == [_NULL_LEAF]

    def test_string_leaf(self):
        assert extract_leaves('hello') == ['hello']
