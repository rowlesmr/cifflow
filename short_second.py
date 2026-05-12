import sys
import pathlib
from cifflow import (
    DictionaryLoader, directory_resolver,
    save_dictionary, load_dictionary,
    generate_schema,
    build, ingest, emit, EmitMode,
    CifWriter,
)
from cifflow.types import CifVersion

# 1. Load dictionary (with JSON cache to avoid re-parsing on every run)
cache = pathlib.Path('cif_pow_cache.json')
resolver = directory_resolver('data/dictionaries')
if cache.exists():
    dictionary = load_dictionary(cache)
else:
    dictionary = DictionaryLoader(resolver=resolver).load(
        open('data/dictionaries/cif_pow.dic', encoding='utf-8').read())
    save_dictionary(dictionary, cache)

# 2. Derive schema
schema = generate_schema(dictionary)

# 3. Parse CIF
cif, errors = build(open(r'C:\Users\User\Documents\github\pycifparse\tests\cif_files\second_short.cif', encoding='utf-8').read())

writer = CifWriter(cif.version, cif=cif)  # wrap the parsed CifFile

for block in writer.blocks:
    blk = writer.get_block(block)
    for loop in blk.loops:
        if len(blk[loop[0]]) > 10:
            for tag in loop:
                blk.reassign_tag(tag, blk[tag][:5] + blk[tag][-5:])

# 4. Ingest into an in-memory DuckDB database
#    Pass a file path string to persist: ingest(cif, 'output.db', schema=schema)
conn, warnings = ingest(writer.build(), schema=schema)

# 5. Emit CIF
output = emit(conn, schema, mode=EmitMode.ORIGINAL, version=CifVersion.CIF_2_0)
open('second_short_decimated.cif', 'w', encoding='utf-8').write(output)