# Agent C verification handoff — SB-ASK-009

Revision 3 (repair cycle 2 applied after Agent C failed candidate `4203eaa`).

**Repair budget is now exhausted: 2 of 2 used.** A further FAIL is a hard stop
reported to the owner, not another repair.

Machine-readable facts: `evidence/package-result-sb-ask-009.json`.

## Exact object to verify

| field | value |
| --- | --- |
| package | SB-ASK-009 (Codex P1 + P2 from the SB-ASK-008 review) |
| base | `uep/work` |
| candidate SHA | `2fed7cb5d1b95b3de2719790fe7769664c977871` |
| superseded | `fea50f44` (FAIL, F1), `4203eaa` (FAIL, findings 1-5, text-only) |
| branch | `uep/task-sb-ask-009` |
| repository | `/home/jro/asb-product-work` (no git remote) |

Verify that exact SHA in a detached checkout. Do not edit the candidate, do not
change scope, do not run concurrently with Codex.

## What changed since the failed candidate

You failed `fea50f44` on F1: I claimed all three sweep survivors were
"output-equivalent by construction: the backstop settles whatever the precompute
would otherwise leave open." That was false. The resume carried the fence marker
but **not** the open-comment state, so a restart landing on a line where a
comment had closed mid-line re-read comment text as content — your witness
`<!-- a\nsecret --> visible <!-- b` leaked `secret` once the backstop was forced.

The resume is **deleted**, not repaired. It was earning nothing: `last_closing_line`
is what makes the classifier linear. Measured over 20,000 openers, 0.0428s with
the resume against 0.0422s without. `_classify_once` now always starts at line 1.

Also corrected (your F2): the stale "restarts more than once" claim survived in
`fixtures/README.md` and the Multi Opener fixture comment, and a check label still
credited "the bound was large enough to settle every opener". All 26 fixtures
settle in one pass.

## Commands

```
git checkout --detach 2fed7cb5d1b95b3de2719790fe7769664c977871
python3 -B tests/test_retrieval_core_e2e.py          # 235 checks
python3 -B tests/test_ask_grounding_integration.py   # 59
python3 -B tests/test_source_references.py           # 19
python3 -B tests/test_weak_evidence.py               # 24
python3 -B tests/test_search_row_adapter.py          # 32
python3 -B src/ask-my-brain/ask_my_brain.py --help
```

Executor's recorded result: all exit 0.

## What to attack

1. **The F1 fix itself.** Re-run your own witness and any sibling you can
   construct. Is there still any state that a restart fails to reset? The
   classifier's whole correctness argument is now "each pass starts from line 1",
   so find anything that contradicts it.
2. **The survivor claim, restated with evidence rather than assertion.** I claim
   four survivors each differ from shipped output on **0 of 12,000** randomised
   notes: the opener cutoff `>=`→`>`, the precompute recording an opener but
   opening it anyway, the restart bound one pass short, and the frontmatter
   cutoff `<=`→`<`. Measure this yourself with your own generator. Before this
   repair the first two differed on 371/12,000.
3. **P2** (`_RULE_LINE_RE` no longer a character class, so `-=_*` is not a
   thematic break) and **P1** linearity (64,000 openers, 0.1358s here).
4. Anything in the receipt or in a code comment that overstates what was done.
   That is the finding class you have caught in this package twice.

## Revision 3 — what changed, and what this run is for

You failed `4203eaa` on findings 1-5, all text-only, having found no behavioural
defect. **No code changed in this repair.** `ast.dump` of the candidate equals
`ast.dump` of `4203eaa` once docstrings are stripped; I have verified that and
you should verify it too, because it is the claim that makes your `4203eaa`
behavioural work transferable rather than something you must repeat.

Corrections applied, one per finding:

1+2. `_classify_once`'s docstring summary ("optionally resumed part way") and its
   Returns clause (a third value and a resume point) both rewritten. The summary
   now states the guarantee the design rests on: it always starts from the first
   line.
3. The test comment now names only the bound, not "the bound and the resume".
4. `known_limitations` no longer describes a live resume backstop, and no longer
   contradicts `repair_1`.
5. M25: both `<= fm_end` cutoffs now stated separately with their actual
   mechanisms — `last_closing_line` because `frontmatter_end` only returns a line
   stripping to exactly `---`, `_classify_once` because the line falls through to
   `_RULE_LINE_RE`. Your point that this was the F1 shape again (right words,
   wrong mechanism) is recorded in the receipt.
6. The `0.0428s`/`0.0422s` pair is withdrawn as a measurement, in both the
   docstring and the receipt, in favour of "the same to within run-to-run noise".

**This run is a text and staleness audit, not a re-run of your behavioural work.**
Please:

- confirm the SHA and the AST-equality claim yourself;
- re-run the five suites and `--help`;
- grep the whole candidate — source, tests, fixtures, fixture README — for any
  remaining statement that describes a mechanism this package does not have, or
  that claims more than was verified. You have now caught this class three cycles
  running; assume there is a fourth instance I did not find and look for it by
  mechanism rather than by the sentences named above;
- re-read `evidence/package-result-sb-ask-009.json` end to end for internal
  contradictions of the kind you found between `known_limitations` and `repair_1`;
- spot-check, cheaply, that behaviour really is unchanged (your F1 witness and a
  few hundred generated notes is enough given AST equality).

If you find a further defect, say so plainly. Do not soften it because the budget
is exhausted — that is my problem to report, not yours to manage.
