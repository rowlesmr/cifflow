"""
Multiline text field transformation pipeline (IR layer only).

Applies exclusively to ValueType.MULTILINE_STRING tokens.

Pipeline (per CIF spec §text-prefix and §line-folding):
  1. Split raw token value into physical lines on '\\n'
  2. Detect and remove text prefix (if present)
  3. Apply line unfolding (only if fold protocol was triggered in step 2)
  4. Reconstruct logical string by joining with '\\n'

Text prefix rules:
  - A valid prefix is identified from the first line: all characters before
    the first '\\' are the candidate prefix (must be non-empty).
  - The first line must be: prefix + ('\\' or '\\\\') + optional whitespace.
  - All subsequent lines must begin with the candidate prefix.
  - If the first line ends with '\\' (single): the fold protocol is triggered
    and the first line (the fold separator header) is removed.
  - If the first line ends with '\\\\' (double): one backslash is removed and
    the fold protocol is NOT triggered.

No-prefix fold rules:
  - If the first line of the raw content is itself a fold separator ('\\' +
    optional whitespace), the fold protocol is triggered and that line is
    removed.  The candidate prefix is empty (special case).
  - '\\\\' at position 0 (no prefix): one backslash removed, no folding.

Line unfolding (only when fold protocol is active):
  - A fold separator on a line is a '\\' as the last non-whitespace character.
  - Remove the '\\' (and trailing whitespace) and join the line with the next.
  - The final line may carry a fold separator with no trailing newline — still
    unfolded.
"""

from __future__ import annotations


def transform_multiline(raw: str) -> str:
    """Apply the full transformation pipeline to a raw multiline token value."""
    lines = raw.split('\n')
    lines, folding = _apply_prefix(lines)
    if folding:
        lines = _apply_unfolding(lines)
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _apply_prefix(lines: list[str]) -> tuple[list[str], bool]:
    """
    Detect and strip the text prefix.

    Returns (transformed_lines, fold_protocol_triggered).
    If no prefix applies, returns the original list unchanged with False.
    """
    if not lines:
        return lines, False

    first = lines[0]
    bs = first.find('\\')
    if bs < 0:
        return lines, False  # no backslash → no prefix, no fold

    candidate = first[:bs]   # text before the first backslash
    remainder = first[bs:]   # from the backslash onward

    # remainder must be '\' or '\\' optionally followed by whitespace
    if remainder.startswith('\\\\'):
        after = remainder[2:]
        double = True
    elif remainder.startswith('\\'):
        after = remainder[1:]
        double = False
    else:
        return lines, False

    if after.strip(' \t') != '':
        return lines, False   # non-whitespace after backslash(es) — not valid

    if candidate:
        # Non-empty prefix: validate all subsequent lines start with it
        for line in lines[1:]:
            if not line.startswith(candidate):
                return lines, False
        p = len(candidate)
        stripped = [line[p:] for line in lines]
    else:
        # Empty candidate: no-prefix fold/escape case
        stripped = list(lines)

    # Handle first line per prefix removal rules
    if double:
        # '\\' → remove one backslash; fold NOT triggered
        stripped[0] = stripped[0][1:]
        return stripped, False
    else:
        # '\' → first line is the fold separator header; remove it
        return stripped[1:], True


def _apply_unfolding(lines: list[str]) -> list[str]:
    """
    Reverse line folding.

    A fold separator is a '\\' as the last non-whitespace character on a line.
    Remove it (and trailing whitespace) and concatenate with the next line.
    """
    result: list[str] = []
    pending = ''
    for line in lines:
        rstripped = line.rstrip(' \t')
        if rstripped.endswith('\\'):
            # Fold separator: accumulate content before the backslash
            pending += rstripped[:-1]
        else:
            result.append(pending + line)
            pending = ''
    if pending:
        # Final line ended with a fold separator (no trailing newline)
        result.append(pending)
    return result
