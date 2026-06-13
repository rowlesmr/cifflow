"""
Branch-coverage tests for _render_block in cifflow.output.emit.

Branches correspond to the five logical sections identified in Stage 1 of the
complexity-reduction refactor:

  1. Guard              — CIF 1.1 block-name length
  2. organise_fallback  — scalar / pure-loop / mixed-fallback / audit-id stripping
  3. inject_header      — conformance tags and _audit_dataset.id injection
  4. merge_group_item   — ORIGINAL vs GROUPED merge-group dispatch
  5. single_table_item  — column filtering, FK-PK suppression, Set/Loop dispatch
  6. collect_remnant    — unrendered mixed-fallback falls to plain remnant
"""

from __future__ import annotations

import pytest

from cifflow import build, ingest, emit, EmitMode, generate_schema
from cifflow.dictionary import DictionaryLoader
from cifflow.dictionary.schema import ColumnDef, ForeignKeyDef, SchemaSpec, TableDef
from cifflow.output import BlockSpec, OutputPlan
from cifflow.output.emit import _BlockData, _render_block
from cifflow.types import CifVersion


# ---------------------------------------------------------------------------
# Helpers — construct _BlockData and SchemaSpec directly
# ---------------------------------------------------------------------------

def _col(
    name: str,
    *,
    pk: bool = False,
    synthetic: bool = False,
    linked: str | None = None,
) -> ColumnDef:
    return ColumnDef(
        name=name,
        definition_id=f'_test.{name}',
        type_contents='Text',
        nullable=not pk,
        is_primary_key=pk,
        is_synthetic=synthetic,
        linked_item_id=linked,
    )


def _table(
    name: str,
    cols: list[ColumnDef],
    *,
    cls: str = 'Loop',
    fks: list[ForeignKeyDef] | None = None,
) -> TableDef:
    pks = [c.name for c in cols if c.is_primary_key]
    return TableDef(
        name=name,
        definition_id=f'_{name}',
        category_class=cls,
        columns=cols,
        primary_keys=pks,
        foreign_keys=fks or [],
    )


def _schema(*tables: TableDef, extra_c2t: dict | None = None) -> SchemaSpec:
    c2t: dict[tuple[str, str], str] = {
        (t.name, c.name): f'_{t.name}.{c.name}'
        for t in tables
        for c in t.columns
        if not c.is_synthetic
    }
    if extra_c2t:
        c2t.update(extra_c2t)
    return SchemaSpec(tables={t.name: t for t in tables}, column_to_tag=c2t)


def _bd(**kwargs) -> _BlockData:
    defaults: dict = dict(
        name='test',
        table_rows={},
        fallback_rows=[],
        anchor_frozenset=frozenset(),
        anchor_key_dict={},
        suppress_fk_pk=False,
    )
    defaults.update(kwargs)
    return _BlockData(**defaults)


CIF20 = CifVersion.CIF_2_0
CIF11 = CifVersion.CIF_1_1


def _render(block_name: str, data: _BlockData, schema: SchemaSpec,
            version: CifVersion = CIF20) -> str:
    lines = _render_block(block_name, data, schema, version, None, False, False)
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Helpers — emit() round-trip
# ---------------------------------------------------------------------------

def _make_schema(dic_src: str) -> SchemaSpec:
    d = DictionaryLoader().load(dic_src)
    return generate_schema(d)


def _ingest_src(cif_src: str, schema: SchemaSpec | None = None):
    cif, errors = build(cif_src)
    assert not errors, errors
    conn, _ = ingest(cif, None, schema)
    return conn


# ---------------------------------------------------------------------------
# Mini dictionaries
# ---------------------------------------------------------------------------

_EXPT_DIC = """\
#\\#CIF_2.0
data_expt_dic

save_EXPT
  _definition.id        EXPT
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     expt
save_

save_expt.id
  _definition.id        '_expt.id'
  _definition.class     Attribute
  _name.category_id     expt
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Text
save_
"""

_ATOM_DIC = """\
#\\#CIF_2.0
data_atom_dic

save_ATOM
  _definition.id        ATOM
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     atom
  _category_key.name    '_atom.id'
save_

save_atom.id
  _definition.id        '_atom.id'
  _definition.class     Attribute
  _name.category_id     atom
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Integer
save_

save_atom.label
  _definition.id        '_atom.label'
  _definition.class     Attribute
  _name.category_id     atom
  _name.object_id       label
  _type.purpose         Descriptor
  _type.source          Assigned
  _type.container       Single
  _type.contents        Text
save_
"""

_EXPT_ATOM_DIC = """\
#\\#CIF_2.0
data_expt_atom_dic

save_EXPT
  _definition.id        EXPT
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     expt
save_

save_expt.id
  _definition.id        '_expt.id'
  _definition.class     Attribute
  _name.category_id     expt
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Text
save_

save_ATOM
  _definition.id        ATOM
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     atom
  _category_key.name    '_atom.id'
save_

save_atom.id
  _definition.id        '_atom.id'
  _definition.class     Attribute
  _name.category_id     atom
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Integer
save_

save_atom.label
  _definition.id        '_atom.label'
  _definition.class     Attribute
  _name.category_id     atom
  _name.object_id       label
  _type.purpose         Descriptor
  _type.source          Assigned
  _type.container       Single
  _type.contents        Text
save_

save_atom.expt_id
  _definition.id        '_atom.expt_id'
  _definition.class     Attribute
  _name.category_id     atom
  _name.object_id       expt_id
  _type.purpose         Link
  _type.source          Related
  _type.container       Single
  _type.contents        Text
  _name.linked_item_id  '_expt.id'
save_
"""

_MEAS_CALC_DIC = """\
#\\#CIF_2.0
data_meas_calc_dic

save_EXPT
  _definition.id        EXPT
  _definition.scope     Category
  _definition.class     Set
  _name.category_id     expt
save_

save_expt.id
  _definition.id        '_expt.id'
  _definition.class     Attribute
  _name.category_id     expt
  _name.object_id       id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Text
save_

save_MEAS
  _definition.id        MEAS
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     meas
  _category_key.name    '_meas.point_id'
save_

save_meas.point_id
  _definition.id        '_meas.point_id'
  _definition.class     Attribute
  _name.category_id     meas
  _name.object_id       point_id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Integer
save_

save_meas.intensity
  _definition.id        '_meas.intensity'
  _definition.class     Attribute
  _name.category_id     meas
  _name.object_id       intensity
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_

save_CALC
  _definition.id        CALC
  _definition.scope     Category
  _definition.class     Loop
  _name.category_id     calc
  _category_key.name    '_calc.point_id'
save_

save_calc.point_id
  _definition.id        '_calc.point_id'
  _definition.class     Attribute
  _name.category_id     calc
  _name.object_id       point_id
  _type.purpose         Key
  _type.source          Assigned
  _type.container       Single
  _type.contents        Integer
save_

save_calc.intensity
  _definition.id        '_calc.intensity'
  _definition.class     Attribute
  _name.category_id     calc
  _name.object_id       intensity
  _type.purpose         Number
  _type.source          Measured
  _type.container       Single
  _type.contents        Real
save_
"""


# ---------------------------------------------------------------------------
# Branch group 1: Guard
# ---------------------------------------------------------------------------

class TestRenderBlockGuard:
    """CIF 1.1 block-name length check (line 1728)."""

    def test_cif11_long_block_name_raises(self):
        data = _bd()
        schema = SchemaSpec(tables={}, column_to_tag={})
        with pytest.raises(ValueError, match='75-character'):
            _render_block('x' * 76, data, schema, CIF11, None, False, False)

    def test_cif20_long_block_name_allowed(self):
        name = 'x' * 100
        data = _bd()
        schema = SchemaSpec(tables={}, column_to_tag={})
        lines = _render_block(name, data, schema, CIF20, None, False, False)
        assert lines[0] == f'data_{name}'


# ---------------------------------------------------------------------------
# Branch group 2: organise_fallback_rows
# ---------------------------------------------------------------------------

class TestOrganiseFallbackRows:
    """Fallback-row partitioning: scalar, pure-loop, mixed-fallback, audit-id stripping."""

    def test_scalar_fallback_rendered(self):
        # loop_id is None → remnant_rows path → rendered as scalar by _render_fallback
        conn = _ingest_src('#\\#CIF_2.0\ndata_t\n_unknown.tag  hello\n')
        result = emit(conn, SchemaSpec(tables={}, column_to_tag={}))
        assert '_unknown.tag' in result
        assert 'hello' in result

    def test_pure_loop_fallback_rendered_as_loop(self):
        # loop_id set + no ref_table → pure_loop_rows path → loop_ output
        conn = _ingest_src(
            '#\\#CIF_2.0\ndata_t\nloop_\n  _x.a\n  _x.b\n  1 2\n  3 4\n'
        )
        result = emit(conn, SchemaSpec(tables={}, column_to_tag={}))
        assert 'loop_' in result
        assert '_x.a' in result
        assert '_x.b' in result

    def test_mixed_fallback_extra_col_in_known_loop(self):
        # loop_id set + ref_table set → mixed_fallback → extra col appears in structured loop
        schema = _make_schema(_ATOM_DIC)
        cif_src = (
            '#\\#CIF_2.0\ndata_t\n'
            'loop_\n  _atom.id\n  _atom.label\n  _atom.extra\n'
            '  1 H val1\n  2 C val2\n'
        )
        conn = _ingest_src(cif_src, schema)
        result = emit(conn, schema)
        assert '_atom.extra' in result
        assert 'val1' in result
        assert 'val2' in result

    def test_audit_id_stripped_from_pure_loop_when_dataset_id_set(self):
        # _audit_dataset.id in a pure fallback loop is stripped when dataset_id is also set,
        # so the scalar injection controls the output instead of emitting a spurious loop_.
        schema = SchemaSpec(
            tables={},
            column_to_tag={('audit_dataset', 'id'): '_audit_dataset.id'},
        )
        data = _bd(
            fallback_rows=[
                {
                    'loop_id': 1,
                    'ref_table': None,
                    'tag': '_audit_dataset.id',
                    'value': 'DS1',
                    'value_type': 'string',
                },
            ],
            dataset_id='DS1',
        )
        result = _render('test', data, schema)
        assert 'loop_' not in result
        assert '_audit_dataset.id' in result
        assert 'DS1' in result


# ---------------------------------------------------------------------------
# Branch group 3: inject_header_items
# ---------------------------------------------------------------------------

class TestInjectHeaderItems:
    """Conformance tags and _audit_dataset.id injection."""

    def test_no_header_items_produces_only_data_line(self):
        # No conformance_tags, no dataset_id → output is exactly 'data_blk'
        data = _bd()
        schema = SchemaSpec(tables={}, column_to_tag={})
        result = _render('blk', data, schema)
        assert result == 'data_blk'

    def test_conformance_tags_emitted_immediately_after_data_line(self):
        # conformance_tags → lines added before structured content
        tbl = _table('cell', [_col('len', pk=True)], cls='Set')
        schema = _schema(tbl)
        data = _bd(
            table_rows={'cell': [{'len': '5.4', '_cifflow_block_id': 'b', '_cifflow_row_id': 1}]},
            anchor_frozenset=frozenset({'cell'}),
            suppress_fk_pk=True,
            conformance_tags=[('_conform.dict_name', 'mydict')],
        )
        result = _render('blk', data, schema)
        lines = result.splitlines()
        assert lines[0] == 'data_blk'
        conform_idx = next(i for i, ln in enumerate(lines) if '_conform.dict_name' in ln)
        cell_idx = next(i for i, ln in enumerate(lines) if '_cell.len' in ln)
        assert conform_idx < cell_idx

    def test_dataset_id_string_emitted_as_scalar(self):
        # dataset_id is a plain string → scalar tag-value pair, no loop_
        schema = SchemaSpec(
            tables={},
            column_to_tag={('audit_dataset', 'id'): '_audit_dataset.id'},
        )
        data = _bd(dataset_id='MY_DS')
        result = _render('blk', data, schema)
        assert '_audit_dataset.id  MY_DS' in result
        assert 'loop_' not in result

    def test_dataset_id_list_emitted_as_loop(self):
        # dataset_id is a list → loop_ with one value per row
        schema = SchemaSpec(
            tables={},
            column_to_tag={('audit_dataset', 'id'): '_audit_dataset.id'},
        )
        data = _bd(dataset_id=['DS1', 'DS2'])
        result = _render('blk', data, schema)
        assert 'loop_' in result
        assert 'DS1' in result
        assert 'DS2' in result

    def test_dataset_id_skipped_when_audit_dataset_is_set_in_table_rows(self):
        # audit_dataset (Set class) already in table_rows → injection skipped to avoid duplicate
        ad_col = _col('id', pk=True)
        ad_tbl = _table('audit_dataset', [ad_col], cls='Set')
        schema = _schema(ad_tbl, extra_c2t={('audit_dataset', 'id'): '_audit_dataset.id'})
        data = _bd(
            table_rows={
                'audit_dataset': [{'id': 'DS1', '_cifflow_block_id': 'b', '_cifflow_row_id': 1}]
            },
            anchor_frozenset=frozenset({'audit_dataset'}),
            suppress_fk_pk=True,
            dataset_id='DS1',
        )
        result = _render('blk', data, schema)
        # _audit_dataset.id appears exactly once (rendered from table_rows, not injected)
        assert result.count('_audit_dataset.id') == 1

    def test_dataset_id_skipped_when_present_in_fallback_remnant(self):
        # _audit_dataset.id already in scalar fallback → injection skipped
        schema = SchemaSpec(
            tables={},
            column_to_tag={('audit_dataset', 'id'): '_audit_dataset.id'},
        )
        data = _bd(
            fallback_rows=[
                {
                    'loop_id': None,
                    'ref_table': None,
                    'tag': '_audit_dataset.id',
                    'value': 'DS1',
                    'value_type': 'string',
                },
            ],
            dataset_id='DS1',
        )
        result = _render('blk', data, schema)
        assert result.count('_audit_dataset.id') == 1

    def test_audit_dataset_loop_table_popped_and_scalar_injected(self):
        # audit_dataset is Loop class in table_rows → table is popped, scalar is injected instead
        ad_col = _col('id', pk=True)
        ad_tbl = _table('audit_dataset', [ad_col], cls='Loop')  # Loop, not Set
        schema = _schema(ad_tbl, extra_c2t={('audit_dataset', 'id'): '_audit_dataset.id'})
        data = _bd(
            table_rows={
                'audit_dataset': [{'id': 'DS1', '_cifflow_block_id': 'b', '_cifflow_row_id': 1}]
            },
            suppress_fk_pk=False,
            dataset_id='DS1',
        )
        result = _render('blk', data, schema)
        # Table popped → scalar emission, not loop_
        assert '_audit_dataset.id  DS1' in result
        assert 'loop_' not in result


# ---------------------------------------------------------------------------
# Branch group 4: render_merge_group_item
# ---------------------------------------------------------------------------

class TestRenderMergeGroupItem:
    """Merge-group dispatch: ORIGINAL (suppress_loop_fk_pk=True) vs GROUPED path."""

    @pytest.fixture
    def schema(self):
        return _make_schema(_MEAS_CALC_DIC)

    @pytest.fixture
    def conn(self, schema):
        return _ingest_src(
            '#\\#CIF_2.0\ndata_b\n_expt.id E1\n'
            'loop_\n  _meas.point_id\n  _meas.intensity\n  1 10.0\n  2 20.0\n'
            'loop_\n  _calc.point_id\n  _calc.intensity\n  1 11.0\n  2 21.0\n',
            schema,
        )

    def test_original_path_renders_all_columns(self, conn, schema):
        # suppress_loop_fk_pk=True (ORIGINAL mode) → _render_original_loop_group path
        result = emit(conn, schema, mode=EmitMode.ORIGINAL)
        assert '_meas.intensity' in result
        assert '_calc.intensity' in result

    def test_grouped_path_merges_compatible_tables_to_single_loop(self, conn, schema):
        # suppress_loop_fk_pk=False (ONE_BLOCK + merge spec) → _render_merge_group path
        spec = BlockSpec(category_order=[['meas', 'calc']])
        plan = OutputPlan(specs=[spec])
        result = emit(conn, schema, mode=EmitMode.ONE_BLOCK, plan=plan)
        assert result.count('loop_') == 1
        assert '_meas.intensity' in result
        assert '_calc.intensity' in result

    def test_suppress_pkg_computed_for_merge_group_in_grouped_mode(self, conn, schema):
        # GROUPED mode (suppress_fk_pk=True + suppress_all_fk_to_set=True):
        # suppress_pkg is computed for the merge group before calling _render_merge_group.
        # Even when no FK-PK columns exist, the code path is exercised and the merge
        # group still produces a single loop_ with columns from both tables.
        spec = BlockSpec(category_order=[['meas', 'calc']])
        plan = OutputPlan(specs=[spec])
        result = emit(conn, schema, mode=EmitMode.GROUPED, plan=plan)
        assert result.count('loop_') == 1
        assert '_meas.intensity' in result
        assert '_calc.intensity' in result


# ---------------------------------------------------------------------------
# Branch group 5: render_single_table_item
# ---------------------------------------------------------------------------

class TestRenderSingleTableItem:
    """Column filtering, FK-PK suppression, and Set/Loop dispatch."""

    def test_table_with_no_rows_not_rendered(self):
        # data.table_rows.get(table_name) is falsy → table skipped
        schema = _make_schema(_ATOM_DIC)
        conn = _ingest_src('#\\#CIF_2.0\ndata_t\n', schema)
        result = emit(conn, schema)
        assert '_atom.id' not in result
        assert 'loop_' not in result

    def test_set_category_rendered_as_scalar_pairs(self):
        # Set category, single row → tag-value pairs, no loop_
        schema = _make_schema(_EXPT_DIC)
        conn = _ingest_src('#\\#CIF_2.0\ndata_t\n_expt.id E1\n', schema)
        result = emit(conn, schema)
        assert '_expt.id' in result
        assert 'loop_' not in result

    def test_loop_category_rendered_as_loop(self):
        # Loop category, multiple rows → loop_ with column headers and data values
        schema = _make_schema(_ATOM_DIC)
        conn = _ingest_src(
            '#\\#CIF_2.0\ndata_t\nloop_\n  _atom.id\n  _atom.label\n  1 H\n  2 C\n',
            schema,
        )
        result = emit(conn, schema)
        assert 'loop_' in result
        assert '_atom.id' in result
        assert '_atom.label' in result

    def test_fk_pk_col_suppressed_in_grouped_mode(self):
        # suppress_fk_pk=True + suppress_all_fk_to_set=True: a column that is BOTH an FK
        # and a PK (composite key) pointing to a co-emitted Set row is suppressed.
        expt_tbl = _table('expt', [_col('id', pk=True)], cls='Set')
        fk = ForeignKeyDef(
            source_table='atom',
            source_columns=['expt_id'],
            target_table='expt',
            target_columns=['id'],
        )
        atom_tbl = _table(
            'atom',
            [_col('expt_id', pk=True), _col('id', pk=True), _col('label')],
            cls='Loop',
            fks=[fk],
        )
        schema = _schema(expt_tbl, atom_tbl)
        data = _bd(
            table_rows={
                'expt': [{'id': 'E1', '_cifflow_block_id': 'b', '_cifflow_row_id': 1}],
                'atom': [
                    {'expt_id': 'E1', 'id': '1', 'label': 'H',
                     '_cifflow_block_id': 'b', '_cifflow_row_id': 2},
                    {'expt_id': 'E1', 'id': '2', 'label': 'C',
                     '_cifflow_block_id': 'b', '_cifflow_row_id': 3},
                ],
            },
            anchor_frozenset=frozenset({'expt'}),
            suppress_fk_pk=True,
            suppress_all_fk_to_set=True,
        )
        result = _render('blk', data, schema)
        assert '_atom.expt_id' not in result
        assert '_atom.label' in result

    def test_inapplicable_col_filtered_in_grouped_mode(self):
        # suppress_all_fk_to_set=True: column where every value is '.' is removed;
        # other columns still render
        col_id = _col('id', pk=True)
        col_val = _col('val')
        col_inapplicable = _col('dead')
        tbl = _table('t', [col_id, col_val, col_inapplicable], cls='Loop')
        schema = _schema(tbl)
        data = _bd(
            table_rows={'t': [
                {'id': '1', 'val': 'v1', 'dead': '.', '_cifflow_block_id': 'b', '_cifflow_row_id': 1},
                {'id': '2', 'val': 'v2', 'dead': '.', '_cifflow_block_id': 'b', '_cifflow_row_id': 2},
            ]},
            suppress_fk_pk=True,
            suppress_all_fk_to_set=True,
        )
        result = _render('blk', data, schema)
        assert '_t.val' in result
        assert '_t.dead' not in result
        assert 'loop_' in result

    def test_all_inapplicable_cols_causes_table_skip(self):
        # suppress_all_fk_to_set=True + every active column has all-'.' values → table skipped
        col_id = _col('id', pk=True)
        col_val = _col('val')
        tbl = _table('t', [col_id, col_val], cls='Loop')
        schema = _schema(tbl)
        data = _bd(
            table_rows={'t': [
                {'id': '.', 'val': '.', '_cifflow_block_id': 'b', '_cifflow_row_id': 1},
            ]},
            suppress_fk_pk=True,
            suppress_all_fk_to_set=True,
        )
        result = _render('blk', data, schema)
        assert 'loop_' not in result
        assert '_t.' not in result


# ---------------------------------------------------------------------------
# Branch group 6: collect_remnant_rows
# ---------------------------------------------------------------------------

class TestCollectRemnantRows:
    """Remnant row collection: scalar fallback and unrendered mixed-fallback."""

    def test_scalar_fallback_rendered_after_structured_tables(self):
        # Scalar (loop_id=None) fallback rows appear after structured table content
        schema = _make_schema(_EXPT_DIC)
        conn = _ingest_src(
            '#\\#CIF_2.0\ndata_t\n_expt.id E1\n_extra.tag hello\n',
            schema,
        )
        result = emit(conn, schema)
        assert '_expt.id' in result
        assert '_extra.tag' in result
        assert result.index('_expt.id') < result.index('_extra.tag')

    def test_mixed_fallback_with_absent_ref_table_falls_to_remnant(self):
        # A mixed-fallback row whose ref_table is not in table_rows is treated as plain remnant
        tbl = _table('atom', [_col('id', pk=True), _col('label')], cls='Loop')
        schema = _schema(tbl)
        data = _bd(
            table_rows={},  # 'atom' not rendered in this block
            fallback_rows=[
                {
                    'loop_id': 1,
                    'ref_table': 'atom',
                    'col_index': 1,
                    '_cifflow_row_id': 1,
                    'tag': '_atom.extra',
                    'value': 'v1',
                    'value_type': 'string',
                },
            ],
        )
        result = _render('blk', data, schema)
        assert '_atom.extra' in result
        assert 'v1' in result
