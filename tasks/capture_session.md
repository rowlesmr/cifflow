# cifflow — Session Capture Prompt

Use this prompt at the end of a session (or mid-session before a context reset) to extract updates for `tasks/todo.md` and `tasks/lessons.md`.

---

## The Prompt

```
Review everything we have done in this session and produce two clearly separated outputs.

---

### OUTPUT 1: todo.md changes

Produce a complete replacement "## What was done" section and an updated "## What's Next" section, and updated "## Open Decisions" section if any decisions were made or new ones emerged.

For "What was done", include:
- A 2–4 sentence summary of the overall goal of this session
- The final test count and pass/fail state
- Each meaningful change made, as a bullet: what was changed, in which file, and why (one sentence each)
- Any bugs fixed, named by their symptom and root cause (one sentence each)

For "What's Next", re-order and update the existing priority list based on what was completed and what became clear during this session. Add any new items that emerged. Remove items that are done.

For "Open Decisions", add any new decisions that were deferred, and mark any that were resolved (with a one-sentence resolution note, then remove them on the next cleanup pass).

Format exactly as the existing todo.md uses, so it can be pasted in directly.

---

### OUTPUT 2: lessons.md additions

For each non-obvious thing learned during this session — a bug whose root cause was surprising, a design decision that required real reasoning, a gotcha in a library or tool, a pattern that should be reused — produce one lesson entry in this exact format:

## Lesson N — <short title> (<date>)

**Context:** <file or component where this arose>

**Mistake/Problem:** <what went wrong or what the situation was> (omit if it was a pure design decision with no mistake)

**Fix:** <what was changed> (omit if no code change)

**Rule:** <the distilled, reusable takeaway — one to three sentences, written as a directive>

Rules for lesson entries:
- Number from the current highest lesson + 1 (current highest: {CURRENT_LESSON_NUMBER})
- Only include lessons for things that were genuinely non-obvious or would be easy to repeat
- Do not include lessons for: spec compliance that was clearly specified, simple typos, straightforward implementation steps
- Keep each entry under 12 lines total
- The Rule must be reusable — written so that someone (or Claude) reading it cold would know what to do differently
- If a lesson involves a diagnostic approach that was effective, add a one-sentence "Diagnostic note:" at the end

Do not include lessons for things already captured in the existing lessons.md.
```

---

## Usage notes

**When to run it:**
- At natural session end before closing
- Before a `/clear` or context reset mid-session
- When the context window is getting long and you want to checkpoint

**Before running:**
- Fill in `{CURRENT_LESSON_NUMBER}` with the current highest lesson number from `lessons.md`
- Optionally paste in the current `## What's Next` section so Claude can update it accurately rather than reconstructing it from memory

**After running:**
- Paste OUTPUT 1 into `tasks/todo.md`, replacing the relevant sections
- Append OUTPUT 2 entries to `tasks/lessons.md`, above the index or at the top
- Update the lesson index in `lessons.md` with any new topic tags

**What this prompt does NOT capture:**
- Large design documents or spec content — those belong in `prompts/`
- Performance profiling data — goes in `tasks/lessons.md` as a lesson only if the finding is reusable
- Completed stage checklists — leave those in git history, not in todo.md
```
