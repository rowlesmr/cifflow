"""
CIF version detection.

Scans the beginning of a CIF source string to identify the version magic line,
returning the detected version, the remaining source after the magic line is
consumed, the line offset (how many lines were consumed), and any errors.

Must be called before the lexer is instantiated.
"""

import re
from typing import List, Tuple

from cifflow.types import CifVersion, ParseError

# Magic line pattern: optional BOM, #\#CIF_, version token, optional trailing whitespace
_MAGIC_RE = re.compile(r'^\ufeff?#\\#CIF_(\S+)\s*$')


def detect_version(
    source: str,
) -> Tuple[CifVersion, str, int, List[ParseError]]:
    """Detect the CIF version from *source*.

    Parameters
    ----------
    source
        Full CIF source string to inspect.

    Returns
    -------
    Tuple[CifVersion, str, int, List[ParseError]]
        ``(version, remaining, line_offset, errors)`` — the detected
        :class:`~cifflow.types.CifVersion`, the source with the magic line
        consumed, the number of lines consumed before the lexer starts, and
        any :class:`~cifflow.types.ParseError` objects found during detection.
    """
    errors: List[ParseError] = []
    lines = source.splitlines(keepends=True)

    for i, raw_line in enumerate(lines):
        # Strip BOM only for the purpose of whitespace/content detection
        line = raw_line.lstrip('\ufeff')

        # Skip whitespace-only lines (including bare-BOM lines)
        if not line.strip():
            continue

        # This is the candidate line — try to match the magic code
        # Match against the raw_line (may have leading BOM)
        m = _MAGIC_RE.match(raw_line.rstrip('\r\n'))
        if m is None:
            # Not a magic line; leave it for normal processing
            remaining = ''.join(lines[i:])
            return CifVersion.CIF_1_1, remaining, i, errors

        version_str = m.group(1)
        remaining = ''.join(lines[i + 1:])
        line_offset = i + 1

        if version_str == '2.0':
            return CifVersion.CIF_2_0, remaining, line_offset, errors
        elif version_str == '1.1':
            return CifVersion.CIF_1_1, remaining, line_offset, errors
        else:
            errors.append(ParseError(
                error_type='lexical',
                message=f'unrecognised CIF version: {raw_line.rstrip()}',
                line=i + 1,
                column=1,
                context=raw_line.rstrip(),
                recovery_action='defaulting to CIF 2.0',
            ))
            return CifVersion.CIF_2_0, remaining, line_offset, errors

    # EOF before any non-whitespace content
    return CifVersion.CIF_1_1, source, 0, errors
