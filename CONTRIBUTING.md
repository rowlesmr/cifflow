# Contributing to cifflow

## Docstring convention

All public Python symbols use **NumPy-style docstrings**.

Key rules:

- Parameter separator is `name : type` (space before colon). A missing space causes
  mkdocstrings to silently drop that parameter.
- Type hints live in the function signature; do **not** repeat them in the `Parameters`
  section (`arg-type-hints-in-docstring = false` in `pyproject.toml`).
- Internal symbols (`_`-prefixed functions, methods, classes): one short sentence only —
  no sections, no NumPy structure.
- Use `r"""` raw docstrings whenever the body contains backslash characters.

Sections in use:

```
Parameters
----------
Returns
-------
Raises
------
Notes
-----
Examples
--------
```

Reference example: `namer()` in `src/cifflow/output/plan.py`.

## Linting

Install dev dependencies:

```
pip install -e ".[dev]"
```

Run against a single module while converting it:

```
ruff check src/cifflow/types.py
python -m pydoclint src/cifflow/types.py
```

Run across the full source tree:

```
ruff check src/
python -m pydoclint src/
```

Both must pass clean before opening a pull request.

## Building docs locally

Install docs dependencies:

```
pip install -e ".[docs]"
```

The Rust extension must be compiled before building docs (mkdocstrings imports the package):

```
maturin develop
```

Build and validate:

```
mkdocs build --strict
```

`--strict` turns mkdocstrings warnings (missing members, broken references) into errors.

Serve locally with live reload:

```
mkdocs serve
```
