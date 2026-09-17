# Product decision items

Deferred, non-blocking product decisions. Nothing here blocks a package or a
merge. An item is worked only when the owner selects it as a package.

---

## PD-001 — Review LOW-evidence presentation

**Status:** open, deferred, non-blocking.
**Raised:** 2026-09-16, out of the SB-ASK-001 Codex review.
**Blocks:** nothing. SB-ASK-001 proceeds on the decision recorded below.

Questions to settle:

- Should LOW answers use a less confident answer style?
- Should the UI state "Based on limited corroborating evidence"?
- Should citation labels map directly to model evidence chunk IDs, or should
  the model use note-level citation identifiers too?

### Why it was raised

SB-ASK-001 made two related things visible.

1. Corroborated LOW evidence is answered. The owner decided on 2026-09-16 that
   this is correct: two independent notes agreeing is real corroboration, and
   the answer already carries `Evidence strength: LOW`. What is not settled is
   whether that status is *presented* strongly enough for a reader who is
   skimming. The requirement wording in
   `docs/SECOND_BRAIN_EXECUTION_HANDOFF.md` has been corrected to match this
   decision.

2. Citation granularity changed. SB-ASK-001's first candidate showed the model
   per-chunk `[Sn]` labels while printing a `SOURCES:` block deduplicated per
   note, so an emitted citation could resolve to the wrong note. The accepted
   fix numbers the evidence per note, so a citation now resolves at note
   granularity: `[S1]` covers every chunk drawn from that note, and that note's
   entry lists all its chunk numbers. The alternative — per-chunk citation
   identifiers on both sides — was not taken, and is the third question above.

### Constraint on whoever takes this

Answering question 3 in the per-chunk direction means changing the merged
`source_references.py` renderer and/or the KI-154 `search_row_adapter.py`, plus
their accepted tests and the pre-committed gate
`tests/test_ask_grounding_integration.py`. That is a separate approved package,
not an edit inside another one.

### Related recorded limitations

`evidence/package-result.json` for SB-ASK-001 carries the accepted limitations
this item sits next to, in particular that `(chunk N)` is the row's
`start_line` rather than a chunk ordinal, and that the start-end line range is
no longer printed.

---

## PD-002 — Indented marker runs are deleted as thematic breaks (D6)

**Status:** open, deferred, non-blocking.
**Raised:** 2026-09-17, by Codex reviewing SB-ASK-009; provenance corrected by
Agent C reviewing SB-ASK-012.
**Blocks:** nothing. Pinned as defect D6 in `tests/test_retrieval_core_e2e.py`.

`_RULE_LINE_RE` accepts any amount of leading whitespace, so a run of markers
indented four or more spaces, which Markdown treats as indented code and not a
thematic break, is deleted from the indexed body. It also cannot match a break
written with spaces between its markers, such as `* * *`, which is kept.

The deletion is not inherited. At `4573b55` the rule pattern was written with
doubled backslashes and deleted no rule line; the deletion became live at
`44e7588` (SB-ASK-006), and arrived on `uep/work` with the SB-ASK-012 merge.

The repair for the deletion is one token, `^[ \t]*` to `^ {0,3}`. Either repair
fails its D6 pin, which is the signal to replace the pin with an assertion of
the correct behaviour.

## PD-003 — Heading patterns are quadratic in an interior whitespace run

**Status:** open, deferred, non-blocking.
**Raised:** 2026-09-17, by Agent C reviewing SB-ASK-012.
**Blocks:** nothing. Not pinned.

`^(#{1,6})\s+(.+?)\s*$` in `chunk_note` and `^#\s+(.+?)\s*$` in `note_title`
backtrack quadratically through a run of spaces or tabs inside a heading line.
`# x`, 32,000 spaces, `y` takes 4.9 seconds. The patterns are identical at
`4573b55` and in every commit back to the import, so this was not introduced
by the SB-ASK-006 to SB-ASK-012 line.

`build_index` has no file-size cap, so one such line in one note is enough to
slow an index build. SB-ASK-012 closed the same exposure for unclosed comment
openers; this one remains.

