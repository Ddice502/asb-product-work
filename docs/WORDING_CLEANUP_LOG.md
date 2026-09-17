# Wording cleanup log

Non-executable wording that a reviewer found false or loose and that was not
corrected, under "Wording findings do not block" in
`docs/SECOND_BRAIN_EXECUTION_HANDOFF.md`. Nothing here blocks a package or a
merge. An item is worked only when the owner selects a cleanup package.

Locations are anchor strings, not line numbers: numbers in this package line
went stale three times, anchors did not. Each anchor occurred exactly once in
its file at `48752ee`.

---

## In the tree

| id | file | anchor | what is wrong | found by |
| --- | --- | --- | --- | --- |
| WL-01 | `src/ask-my-brain/ask_my_brain.py` | `Both patterns were previously written with` | False twice. "never fire": at `4573b55` the newline pattern `r"\\n{3,}"` fires on a literal backslash-n run and rewrites it (`C:\nnnnotes` is indexed as `C:`, two newlines, `otes`). "Both patterns ... doubled backslashes": `r"[ \t]+"` has a single backslash at `4573b55` and works there; only the newline pattern was doubled. In the line since `44e7588`. | Agent C, on `48752ee` |
| WL-02 | `src/ask-my-brain/ask_my_brain.py` | `behaviour this line has always been declared to have` | Loose. "still removed" holds from `44e7588` on; at `4573b55` no rule line is removed, so a setext underline is kept there. | Agent C, on `48752ee` |
| WL-03 | `tests/test_retrieval_core_e2e.py` | `Neither is a fault uep/work has` | The lead clause reads strong on its own; the two sentences after it make the passage accurate. "only because" is overdetermined: a working pattern could not match `* * *` either. | Agent C, on `0089904` |
| WL-04 | `tests/test_retrieval_core_e2e.py` | `[0.30, 0.35)` | The comment says no question over the fixtures produces a top coverage in that band; assertions a few lines above it contradict that. Declared in the SB-ASK-002 receipt, never corrected. | Agent C, on `8bd3781` |
| WL-05 | `tests/test_retrieval_core_e2e.py` | `What it does NOT do, so that passing is not mistaken` | Not false: a coverage gap. The `[doc-hygiene]` guard reads that file and `fixtures/README.md` for seven substrings. It does not read `src/`, where WL-01 sat through four packages, and its list has no entry for the inert-pattern claim. | Agent C, on `a837dcb` and `48752ee` |

## In commit messages, which cannot be amended without rewriting history

| id | commit | statement | what is true |
| --- | --- | --- | --- |
| WL-06 | `4203eaa` | "0.0428s with it against 0.0422s without" offered as showing the resume earned nothing | The conclusion holds; the two figures are inside run-to-run noise and the sign flips between best and median. |
| WL-07 | `83ad280` | "Both behaviours are identical at uep/work" | False for the second D6 witness: `    ---` is kept at `4573b55` and deleted from `44e7588` on. Corrected in `0089904`. |
| WL-08 | `83ad280` | "each is within about 2x of uep/work" | Per-line shapes are 0.9x to 2.2x; single-line shapes are up to 34x, all under 2.2ms. Corrected in `0089904`. |
| WL-09 | `0089904` | "13 million exhaustive strings" | 12,093,235. |
| WL-10 | `0089904` | "which matches nothing" | The `4573b55` rule pattern matches a marker run wrapped in literal backslashes. It deletes no rule line. Corrected in `48752ee`. |
| WL-11 | `48752ee` | "The sentence existed in three places" | Four. WL-01 is the fourth. |

## In receipts under `evidence/`, which is untracked

| id | file | field | what is wrong |
| --- | --- | --- | --- |
| WL-12 | `package-result-sb-ask-010.json` | `repair_2.o_e` | Says the line citations were rebased onto the candidate. They were not; the field that carried them is now marked withdrawn, and this one still claims the rebase. |
| WL-13 | `package-result-sb-ask-010.json` | `closes[AC3-1].guard_proved_non_vacuous` | Cites `tests:359, 533, 543, 554` as the candidate's. At `16bbf63` those sites are 361, 536, 544, 557; two of the four cited numbers match no commit. |
| WL-14 | `package-result-sb-ask-011.json` | `closes[O1].what` | Undercounts the unmarked lines carrying "Give Back": says three in the test file, there were five. |
| WL-15 | `package-result-sb-ask-011.json` | `changed_files` / `product_code_unchanged` | Adjacent fields measured against different bases (`16bbf63` and `2fed7cb`) without saying so. |
