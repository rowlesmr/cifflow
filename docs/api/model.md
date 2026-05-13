# CIF Model

## Core types (Rust extension)

::: cifflow_core
    options:
      members: [CifSaveFrame, CifBlock, CifFile, parse_cif, parse_arrow, parse_arrow_file]

## Builder

::: cifflow.cifmodel.builder
    options:
      members: [CifBuilder, build, build_arrow, build_arrow_file]

## Writer

::: cifflow.cifmodel.writer
    options:
      members: [SaveFrameWriter, BlockWriter, CifWriter]

## Clean

::: cifflow.cifmodel.clean
    options:
      members: [CleanWarning, clean]
