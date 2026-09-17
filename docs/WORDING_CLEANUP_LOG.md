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
| WL-04 | `tests/test_retrieval_core_e2e.py` | `[0.30, 0.35)` | The comment says no question over the fixtures produces a top coverage in that band; assertions a few lines above it contradict that. Declared in the SB-ASK-002 receipt, never corrected. | Agent C, on `8bd3781` |
| WL-05 | `tests/test_retrieval_core_e2e.py` | `What it does NOT do, so that passing is not mistaken` | Not false: a coverage gap. The `[doc-hygiene]` guard reads that file and `fixtures/README.md` for seven substrings. It does not read `src/`, where WL-01 sat through four packages, and its list has no entry for the inert-pattern claim. | Agent C, on `a837dcb` and `48752ee` |
| WL-16 | `tests/test_retrieval_core_e2e.py` | `so without a guard on the line as` | Stale. SB-ASK-013 repair 1 added a guard on the line as written; repair 2 removed it and shows the pattern the visibly-ending line instead. The comment still describes the guard. | Agent C and Codex, on `ebd7667` |
| WL-17 | `src/ask-my-brain/ask_my_brain.py` | `unless the line was cut at an opener` | Exact only for the cut path. On a literal, never-closing opener the raw tail is appended too; harmless, since such a line still contains `<!--` and cannot match. | Agent C and Codex, on `ebd7667` |
| WL-18 | `src/ask-my-brain/ask_my_brain.py` | `is indented code` | Loose. Directly under a paragraph, Markdown treats a four-space-indented run as a lazy continuation of the paragraph, not as code. Keeping the line is right either way. | Agent C, on `a6fcfc0` |
| WL-19 | `tests/test_retrieval_core_e2e.py` | `begins with four spaces and is kept` | True for `<!--a--> <!--b--> ---` only. The line is kept when removed comments plus literal spaces leave four or more leading spaces; `<!--a--><!--b--> ---` leaves two and is dropped. | Agent C, on `a6fcfc0` |
| WL-20 | `docs/PRODUCT_DECISIONS.md` | `Pinned as defect D6` | Stale under a closed status: the D6 pins were replaced by correct-behaviour checks. Covered only by "The text below is the item as it was raised." | Agent C, on `a6fcfc0` |

## In commit messages, which cannot be amended without rewriting history

| id | commit | statement | what is true |
| --- | --- | --- | --- |
| WL-06 | `4203eaa` | "0.0428s with it against 0.0422s without" offered as showing the resume earned nothing | The conclusion holds; the two figures are inside run-to-run noise and the sign flips between best and median. |
| WL-07 | `83ad280` | "Both behaviours are identical at uep/work" | False for the second D6 witness: `    ---` is kept at `4573b55` and deleted from `44e7588` on. Corrected in `0089904`. |
| WL-08 | `83ad280` | "each is within about 2x of uep/work" | Per-line shapes are 0.9x to 2.2x; single-line shapes are up to 34x, all under 2.2ms. Corrected in `0089904`. |
| WL-09 | `0089904` | "13 million exhaustive strings" | 12,093,235. |
| WL-10 | `0089904` | "which matches nothing" | The `4573b55` rule pattern matches a marker run wrapped in literal backslashes. It deletes no rule line. Corrected in `48752ee`. |
| WL-11 | `48752ee` | "The sentence existed in three places" | Four. WL-01 is the fourth. |
| WL-21 | `a6fcfc0` | "172 lines ... 1,224 ..." given as counts "over the same lines", the 960,800 | They are counts over the 686,286 right-stripped lines among those, which is how the pattern sees a line. Over all 960,800 they are 412 and 2,304. Both qualitative claims hold either way. |
| WL-22 | `a3bbcc7` | "The rstrip() that matters is at the top of the loop" | True on a comment-free line only. Beside a comment, the second `rstrip()`, the one Codex pointed at, is the one that matters. Corrected in `ebd7667`. |

## In receipts under `evidence/`, which is untracked

| id | file | field | what is wrong |
| --- | --- | --- | --- |
| WL-12 | `package-result-sb-ask-010.json` | `repair_2.o_e` | Says the line citations were rebased onto the candidate. They were not; the field that carried them is now marked withdrawn, and this one still claims the rebase. |
| WL-13 | `package-result-sb-ask-010.json` | `closes[AC3-1].guard_proved_non_vacuous` | Cites `tests:359, 533, 543, 554` as the candidate's. At `16bbf63` those sites are 361, 536, 544, 557; two of the four cited numbers match no commit. |
| WL-14 | `package-result-sb-ask-011.json` | `closes[O1].what` | Undercounts the unmarked lines carrying "Give Back": says three in the test file, there were five. |
| WL-15 | `package-result-sb-ask-011.json` | `changed_files` / `product_code_unchanged` | Adjacent fields measured against different bases (`16bbf63` and `2fed7cb`) without saying so. |

## Resolved

| id | how |
| --- | --- |
| WL-02 | The comment above `_RULE_LINE_RE` was rewritten by SB-ASK-013 when the pattern changed; the sentence is gone. |
| WL-03 | The D6 pins and their comment were deleted by SB-ASK-013 when D6 was repaired. |

## Not wording, recorded here so it is not lost

| id | what | found by |
| --- | --- | --- |
| TG-01 | Test gap. Every check SB-ASK-013 added for a rule beside a comment reads the first line of its input, so two mutants survive: `truncated` never reset per line, and the raw tail lost on the line that closes a multi-line comment. One check would catch both: `classify_markup(["<!--", "-->---\u00a0"], 0)[1]["kind"] == "keep"`. The code is correct on that input. | Agent C and Codex, on `ebd7667` |
