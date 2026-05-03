"""
cifflow.inspect — inspection and visualisation tools.

Each function pretty-prints the internal state of one pipeline layer to
stdout (or a caller-supplied file).  All are opt-in and zero-overhead when
not called.
"""

from cifflow.inspect._lexer import inspect_lexer
from cifflow.inspect._parser import inspect_parse, ParseHandler
from cifflow.inspect._model import inspect_model
from cifflow.inspect._schema import inspect_schema
from cifflow.inspect._ingest import inspect_ingest, TraceEvent

__all__ = [
    'inspect_lexer',
    'inspect_parse',
    'ParseHandler',
    'inspect_model',
    'inspect_schema',
    'inspect_ingest',
    'TraceEvent',
]
