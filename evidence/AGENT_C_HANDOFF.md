# Agent C verification handoff — SB-ASK-001 (Grounded Ask My Brain Response)

Revision 2 (repair cycle 1 applied after Agent C failed candidate 700862f).

Machine-readable facts: `evidence/package-result.json`.

## Exact object to verify

| field | value |
| --- | --- |
| package | SB-ASK-001 |
| base (recorded integration base) | `f3ca146f2cba204b3d5667b6a3903d34d9f6c4a4` (`uep/work`) |
| candidate SHA | `947f7b191f51ec5113f1b4f790b73bf3522deebe` |
| branch | `uep/task-sb-ask-001` |
| repository | `/home/jro/asb-product-work` (no git remote) |
| changed files | `src/ask-my-brain/ask_my_brain.py` (only) |

Verify that exact SHA in a detached / isolated checkout. Do not edit the
candidate, do not change scope, and do not run concurrently with Codex.

## Commands

```
git checkout --detach 947f7b191f51ec5113f1b4f790b73bf3522deebe
python3 -B tests/test_ask_grounding_integration.py   # package gate
python3 -B tests/test_source_references.py           # regression
python3 -B tests/test_weak_evidence.py               # regression
python3 -B tests/test_search_row_adapter.py          # regression
```

Executor's recorded result: all four exit 0
(59 / 19 / 24 / 32 checks), plus an import check of `ask_my_brain.py` exit 0.
No database, network, model, or vault is touched: the gate stubs `search` and
`ollama_chat`.

## Base control check

On the base `f3ca146` the same four commands give:
`test_ask_grounding_integration.py` exit 1 (the package gate fails, as it must
before the package), and the other three exit 0. The candidate must turn only
the first from red to green.

## What the candidate claims

1. `ask_my_brain.py` imports — does not copy — `render_source_references`,
   `format_source_references` (source_references.py), `evidence_verdict`
   (weak_evidence.py) and `adapt_search_rows` (search_row_adapter.py, KI-154).
   They are loaded by path because the directory name contains hyphens.
2. A new pure seam `render_ask_output(source_rows, answer_text, model)`
   composes the entire response. It adapts real `search()` rows, renders the
   citation block through the merged renderer, and decides suppression through
   the merged verdict.
3. Grounded answers print a compact `SOURCES:` block after the answer;
   notes are deduplicated per path and provenance is preserved.
4. On LOW / unknown evidence `ask()` takes the verdict **before** the model is
   reachable, prints the fixed insufficient-evidence response with
   `Related notes:`, `ANSWER_SUPPRESSED=1` and `EVIDENCE_STATUS=WEAK`, and
   never calls `ollama_chat`.
5. Unchanged: ranking, scoring, chunking, indexing, database behaviour,
   capture, the system/user prompt contract, and the `MODEL=` /
   `EVIDENCE_CHUNKS=` trailer. The pre-existing `EVIDENCE_STATUS=NONE`
   no-evidence response is preserved verbatim.

## What repair cycle 1 changed (verify this specifically)

Agent C failed `700862f` because the model was shown per-chunk `[S1]/[S2]/[S3]`
labels while `SOURCES:` had become one entry per deduplicated note, so an
emitted `[S2]` resolved to a different note and the last label dangled. This
revision adds `evidence_labels()`, which numbers each evidence chunk with the
number its NOTE carries in the reference block, and `_evidence_part()`, which
factors the `[Sn]` block out byte-unchanged. `_reference_lines()` now keeps
every distinct provenance for a note, not just the first chunk's.

Please re-check, by your own probe: an emitted citation resolves to the note it
came from; no label dangles; the `[Sn]` block text and the prompt are unchanged;
the context budget still holds; provenance is complete.

## Known limitations to weigh

See `known_limitations` in `evidence/package-result.json`. Findings 3, 4 and 5
of your previous report are recorded there as accepted limitations (the
`(chunk N)` = `start_line` labelling belongs to the merged KI-154 adapter and is
out of this package's scope). Findings 6 and 7 are recorded as pre-existing and
identical on the base.

## Order of gates

Agent C first, on this SHA. Codex only after Agent C passes, on this same SHA,
never concurrently. No merge to `uep/work`, and nothing touches home.
