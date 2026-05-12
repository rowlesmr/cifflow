import pathlib

import duckdb
import pytest
from pycifparse_core import CifFile

from cifflow import (
    build,
    ingest,
    emit,
    EmitMode,
    generate_schema,
    directory_resolver,
)
from cifflow.dictionary import DictionaryLoader
from cifflow.dictionary.schema import SchemaSpec
from cifflow.output import BlockSpec, OutputPlan, only, any_of, all_of, has
from cifflow.types import CifVersion, ParseError
from cifflow.inspect import inspect_schema

_DATA_DIR = pathlib.Path(r"C:\Users\User\Documents\github\pycifparse\data\dictionaries")

_DIC = _DATA_DIR / 'cif_pow.dic'

_SSD_IN = pathlib.Path(r"C:\Users\User\Documents\github\pycifparse\tests\cif_files\ssd_grouped.cif")


def load_schema(dic_file: pathlib.Path) -> SchemaSpec:
    resolver = directory_resolver(_DATA_DIR)
    source = dic_file.read_text(encoding='utf-8')
    d = DictionaryLoader(resolver=resolver).load(source, base_uri=dic_file.name)
    return generate_schema(d)

def pow_schema():
    return load_schema(_DIC)


schema = pow_schema()

#inspect_schema(schema)

ssd_in_txt = _SSD_IN.read_text(encoding='utf-8')

ssd_in_cif, ssd_in_parse_errors = build(ssd_in_txt, mode='strict')
assert not len(ssd_in_parse_errors)

ssd_in_conn, ssd_in_ingest_warnings = ingest(ssd_in_cif, db="testing.duckdb", schema=schema)
assert not ssd_in_ingest_warnings

output_plan = OutputPlan(
                            specs=[
                                BlockSpec(
                                    matches=has(*schema.descendants('publication')),
                                    category_order=[],  # other categories follow alphabetically
                                ),
                                BlockSpec(
                                    matches=only("diffrn_radiation"),
                                    category_order=[],  # other categories follow alphabetically
                                ),
                                BlockSpec(
                                    matches=only("pd_instr"),
                                    category_order=[],  # other categories follow alphabetically
                                ),
                                BlockSpec(
                                    matches=has("pd_instr_detector"),
                                    category_order=[],  # other categories follow alphabetically
                                ),
                                BlockSpec(
                                    matches=only("diffrn"),
                                    category_order=[],  # other categories follow alphabetically
                                ),
                                BlockSpec(
                                    matches=all_of('pd_diffractogram', "pd_phase"),
                                    category_order=[],
                                ),
                                BlockSpec(
                                    matches=only("structure"),
                                    category_order=["structure",
                                                    "cell",
                                                    "atom_site",
                                                    ],  # other categories follow alphabetically
                                    column_order={
                                        'cell': ['length_a', "length_b", "length_c", 'angle_alpha', 'angle_beta', 'angle_gamma', 'volume'],
                                        'atom_site': ['label', 'type_symbol', 'fract_x', 'fract_y', 'fract_z', 'occupancy', 'site_symmetry_multiplicity'],
                                    },
                                ),
                                BlockSpec(
                                    matches=only("pd_phase"),
                                    category_order=[],
                                ),
                                BlockSpec(
                                    matches=only("model"),
                                    category_order=[],
                                ),
                                BlockSpec(
                                    matches=only("space_group"),
                                    category_order=[],
                                ),
                                BlockSpec(
                                    matches=only("pd_diffractogram"),
                                    category_order=[
                                        "pd_diffractogram",
                                        "pd_calc_overall",
                                        "pd_meas_overall",
                                        "pd_proc_ls",
                                        ['pd_data', 'pd_meas', 'pd_proc', 'pd_calc'],  # merge group
                                    ],
                                ),
                                BlockSpec(
                                    matches=None,  # catch-all: anything not matched above, alphabetical order
                                ),
                            ],
                        )



ssd_out_txt = emit(
            ssd_in_conn,
            schema,
            mode=EmitMode.GROUPED,
            plan=output_plan,
            reconstruct_su=True,)

print(ssd_out_txt)


ssd_out_cif, ssd_out_parse_errors = build(
    ssd_out_txt,
    mode='strict',
)

print(f"{len(ssd_in_cif.blocks)=}, {len(ssd_out_cif.blocks)=}")

for bn_in, bn_out in zip(ssd_in_cif.blocks, ssd_out_cif.blocks):
    blk_in, blk_out = ssd_in_cif[bn_in], ssd_out_cif[bn_out]
    print(f"{blk_in.name} --> {blk_out.name}")

print(output_plan)