"""
CifScalar — a CIF scalar value that behaves as a str but carries ValueType.
"""

from cifflow.types import ValueType


class CifScalar(str):
    """
    A CIF scalar value.

    Subclasses ``str`` so all string operations, comparisons, and
    ``isinstance(v, str)`` checks work without modification.  The
    ``value_type`` attribute records the original lexical form of the value
    as assigned by the lexer.

    Attributes
    ----------
    value_type:
        The ``ValueType`` of this value as emitted by the lexer.  Never
        modified after construction.

    Examples
    --------
    >>> s = CifScalar('1.234', ValueType.STRING)
    >>> s == '1.234'
    True
    >>> isinstance(s, str)
    True
    >>> s.value_type
    <ValueType.STRING: 'string'>
    """

    value_type: ValueType

    def __new__(cls, value: str, value_type: ValueType = ValueType.STRING) -> 'CifScalar':
        instance = super().__new__(cls, value)
        instance.value_type = value_type
        return instance

    def __repr__(self) -> str:
        return f'CifScalar({str.__repr__(self)}, {self.value_type!r})'
