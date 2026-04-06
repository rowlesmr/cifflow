# pycifparse — Lessons Learned

## Lesson 1 — Multiline text field closing delimiter (2026-04-04)

**Context:** Lexer `_read_multiline` implementation.

**Mistake:** After consuming the closing `\n;`, added `_skip_to_eol()` to discard remaining content on the closing line. This silently dropped valid tokens (e.g. `1.0` in `simple_loops.cif`'s `; 1.0`).

**Correct rule:** Per CIF 2.0 EBNF, `text-delim = line-term, ';'`. The closing delimiter is exactly two characters (`\n` + `;`). After consuming them, the lexer returns to NORMAL state immediately. Content after the closing `;` is tokenised normally — if it's a comment it's skipped by comment handling, if it's a value it becomes the next token.

**How to apply:** Never skip anything after the closing `;`. The line boundary is not special; only the two-character delimiter matters.

---

## Lesson 2 — Sequential loops are not nested loops (2026-04-04)

**Context:** Parser `_handle_keyword` for `loop_`.

**Mistake:** Added a `if self._in_loop: halt` guard for `loop_` keywords, treating any `loop_` encountered while a loop was active as a fatal "nested loop" error. This caused `simple_loops.cif` (with three sequential loops) to halt after the first loop.

**Correct rule:** Per CLAUDE.md: "on_loop_end emitted on: EOF, new tag, new loop, new save frame, new data block, STOP_". A `loop_` keyword always terminates the current loop via `_prepare_for_keyword` and starts a fresh one. A "nested loop" in the CIF sense is not representable in the flat token stream — there is no construct that creates a structurally nested loop.

**How to apply:** `loop_` should always call `_prepare_for_keyword` (which closes any active loop/containers/tag) then `_start_loop`. Never halt on `loop_` seen while `_in_loop` is True.

---

## Lesson 4 — `@property` preferred over `cached_property` during incremental construction (2026-04-05)

**Context:** `CifSaveFrame.loops` and `CifSaveFrame.tags` in `cifmodel/model.py`.

**Decision:** Used plain `@property` (recomputed on every access) rather than `cached_property`.

**Reason:** `_loops` and `_tag_order` are mutated during construction by `CifBuilder` (via `_add_loop`, `_append_value`). `cached_property` stores the result on first access and never recomputes — so every mutation would require explicit cache invalidation (`del self.loops`), adding noise to every internal mutation method.

**How to apply:** Use `cached_property` only on data that is immutable after construction, or where the cache lifetime can be clearly defined. For properties backed by lists that grow during construction, plain `@property` is simpler and correct. Switch to `cached_property` only if profiling identifies it as a hot path.

---

## Lesson 3 — `:` is not a bare-word terminator in CIF 2.0 (2026-04-04)

**Context:** Lexer `_read_bare_word` and `tokens()` for CIF 2.0.

**Mistake:** Added `:` as a terminator inside `_read_bare_word` for CIF 2.0. This split valid unquoted values like `2007-12-18T12:16:55+02:00` into multiple tokens, generating spurious "value has no preceding tag" errors.

**Correct rule:** Per CIF 2.0 EBNF, `restrict-char = non-blank-char - ('[' | ']' | '{' | '}')`. The colon is NOT excluded from `restrict-char`, so it is a legal character inside a `wsdelim-string`. The `:` table separator only appears at the start of a new token position (directly after a quoted key, with no preceding whitespace). It is emitted as a standalone token only by the outer `tokens()` loop (when `:` is the first character seen in NORMAL state), never by `_read_bare_word`.

**How to apply:** Do not break on `:` inside `_read_bare_word`. Only the `tokens()` loop emits `:` as a standalone VALUE token.

---

## Lesson 5 — `build()` convenience function is unspecified (2026-04-05)

**Context:** Stage 2 (`cifmodel/builder.py`).

**Decision:** Added `build(source, *, mode='pad') -> tuple[CifFile, list[ParseError]]` as a convenience wrapper around `CifBuilder` + `CifParser`.

**Status:** Not mentioned in CLAUDE.md or the parser prompt. Added as a practical utility. If the spec is later updated to prescribe a different top-level API shape, this function may need to change.

**How to apply:** Treat `build()` as a convenience shortcut, not a canonical API. Do not design downstream layers to depend on it exclusively.

---

## Lesson 6 — Empty loop handling is an extension of the spec (2026-04-05)

**Context:** `CifBuilder.on_loop_end()` in Stage 2.

**Decision:** A loop with zero values (tags declared, no values before loop end) is treated as a distinct semantic error with message "no values", separate from the row-count mismatch error.

**Status:** The prompt specifies row-count validation ("validate that the number of values received is divisible by the number of loop tags") but does not explicitly address the zero-values case. Our handling is a reasonable extension — zero is not divisible by any positive tag count — but it goes beyond what is written.

**How to apply:** If the spec is later clarified to prescribe different behaviour for empty loops (e.g. silent discard, or merging with row-count mismatch), revisit `on_loop_end()`.

---

## Lesson 7 — Strict mode extended beyond its specified scope (2026-04-05)

**Context:** `CifBuilder` mode parameter in Stage 2.

**Decision:** The prompt defines strict/pad mode only for loop row-count mismatch recovery. We applied the same strict mode behaviour (stop accumulating after first semantic error) to two additional cases: empty loops and duplicate block/save frame names.

**Status:** This extension is internally consistent and conservative (strict means strict), but it is not spec-backed for these cases. The duplicate name spec says only "emit `on_error`" with no mention of strict/pad distinction.

**How to apply:** If the spec is later updated to define strict/pad behaviour for these cases differently, the `_semantic_error` helper in `CifBuilder` will need case-specific handling rather than a single `_stopped` flag.

---

## Lesson 8 — Empty save frame names are not recoverable (2026-04-05)

**Context:** Parser `_handle_keyword` for `save_`.

**Decision:** Empty save frame names are not supported, unlike empty data block names (which are handled — error emitted, name stored as `""`).

**Reason:** `save_` is syntactically unambiguous as a frame-close token. There is no token form that could mean "open a save frame with an empty name" without conflicting with the close semantics. The only available heuristic — treating `save_` outside a frame as an opener — would silently misinterpret a common error (accidental `save_` outside a frame) as an empty-named frame open.

**Practical justification:** Save frames appear almost exclusively in DDLm dictionaries, which are well-formed. An empty save frame name in a real file would indicate severe malformation; treating it as a recoverable condition adds complexity for no practical benefit.

**How to apply:** Do not attempt to recover empty save frame names. `save_` outside a save frame remains a syntactic error and is ignored. This is a deliberate deviation from the general principle of allowing empty names with an error.

---

## Lesson 9 — Use a consistent docstring style to support autogeneration (2026-04-05)

**Context:** Project-wide docstrings reviewed ahead of potential documentation autogeneration.

**Problem:** Docstrings are currently inconsistent — a mix of one-liners, Sphinx-style `*name*`
emphasis, and NumPy-style `Parameters` blocks. Public API methods (`__getitem__`, `__contains__`,
`get_all`) have no parameter or return documentation. Private helpers sometimes have more
documentation than public methods.

**How to apply:** When writing or updating docstrings, follow a single style throughout.
NumPy style is preferred (used in `debug.py`):

```python
def method(self, name: str) -> list[CifBlock]:
    """Short one-line summary.

    Longer description if needed.

    Parameters
    ----------
    name:
        Description of the parameter.

    Returns
    -------
    list[CifBlock]
        Description of what is returned.

    Raises
    ------
    KeyError
        If the name is not found.
    """
```

Public methods must always document parameters, return values, and exceptions.
Private methods (`_name`) need only a one-liner. This keeps autogeneration viable
without adding noise to internal code.

---

## Lesson 10 — `:` at the start of a bare-word value (2026-04-06)

**Context:** Lexer `tokens()` — CIF 2.0 table key/value separator handling.

**Mistake:** The `:` standalone-token path fired unconditionally whenever `:` appeared
as the first character in NORMAL state.  This split valid values like `:100.0`
(CIF enumeration range lower-bound) into a standalone `:` token followed by `100.0`,
causing the `:` to be assigned as the tag value and `100.0` to become an orphan.

**Correct rule:** `:` is only a table separator when it is directly adjacent to the
preceding token (no whitespace between them).  When preceded by whitespace it is
the start of a bare-word value and must be read by `_read_bare_word`, which does
not break on `:` — so `:100.0` becomes a single token.

**Fix:** Added `_last_was_ws: bool = True` to the lexer.  Set `True` after consuming
whitespace/newlines/comments; `False` after emitting any token.  Standalone `:` is
only emitted when `not self._last_was_ws`.

**Side effect:** `{ "key" :value }` (whitespace before `:`, no space after) now
produces value `":value"` rather than `"value"`, with a "not followed by : separator"
error instead of "whitespace between key and `:` separator".  The key is still
recovered correctly.  This is an acceptable trade-off — the ambiguity is
unresolvable once `:value` is a single token.

**How to apply:** Never break on `:` inside `_read_bare_word`.  Standalone `:` tokens
are only valid when the lexer is in a non-whitespace context (adjacent to a prior token).

---

## Lesson 11 — SU validation does not belong in the lexer (2026-04-06)

**Context:** `_check_su` function in `lexer/lexer.py`.

**Mistake:** Added a heuristic to flag bare words that look like `number(su)` but fail
the `\(\d+\)$` pattern as lexical errors.  This caused false positives on fax numbers
with area codes in parentheses (e.g. `12(34)9477334` in `cif_core.dic`) and any other
string that happens to start with a numeric pattern followed by `(`.

**Correct rule:** The CIF lexer has no concept of "numeric value with SU" distinct
from any other bare word.  Both are `ValueType.STRING` tokens.  Whether the SU
sub-expression is well-formed is a semantic question, not a lexical one.

**Fix:** Removed `_check_su`, `_NUMERIC_PREFIX_RE`, and `_VALID_SU_RE` entirely.

**How to apply:** Do not validate numeric sub-structure in the lexer.  SU format
validation belongs in the dictionary/ingestion layer where the expected type is known.
