# Improvement Backlog

This file records possible improvements. Nothing in this file authorizes work.
Only implement an item during an idle window between product packages.

## Template

### Improvement name
- Pain observed:
- How often it happens:
- Approximate time lost:
- Smallest safe improvement:
- What it must not touch:
- Can it be tested outside the active product worktree?
- Rollback:
- Success measure:
- Design sketch (prep only, not built):
- Status: proposed / testing / adopted / rejected

## Candidates

### VS Code product workspace
- Pain observed: Need a clearer place to inspect product diffs, Git status, and package evidence.
- How often it happens: Every package.
- Approximate time lost: To be measured.
- Smallest safe improvement: Use a dedicated VS Code profile that opens only the isolated product target.
- What it must not touch: Home, vault, runtime, real data, active Claude branch.
- Can it be tested outside the active product worktree? Yes.
- Rollback: Switch back to the normal VS Code profile or delete this profile.
- Success measure: Inspect package evidence and Git diff faster without manual terminal navigation.
- Status: adopted

### Package template
- Pain observed: Package briefs repeated from scratch each time.
- How often it happens: Consider after 1-2 more packages, if briefs prove repetitive.
- Approximate time lost: To be measured.
- Smallest safe improvement: Extract a reusable brief template from repeated sections.
- What it must not touch: Active package content.
- Can it be tested outside the active product worktree? Yes.
- Rollback: Delete the template, keep writing briefs by hand.
- Success measure: Less time spent re-typing boilerplate per package.
- Status: proposed

### One-command package launcher
- Pain observed: Setup steps repeat consistently at the start of each package.
- How often it happens: Consider after 2-3 packages, if setup steps repeat consistently.
- Approximate time lost: To be measured.
- Smallest safe improvement: Single script wrapping the known-good setup sequence.
- What it must not touch: Environment config, credentials.
- Can it be tested outside the active product worktree? Yes.
- Rollback: Delete the script, run steps manually.
- Success measure: Package setup takes one command instead of several manual steps.
- Status: proposed

### Test/evidence collector
- Pain observed: Collecting receipts (test output, logs) for a package is manual.
- How often it happens: Observed 3 times already (evidence/package-result.json, package-result-sb-ask-002.json, package-result-sb-ask-003.json all follow the same hand-assembled shape: package_id, base/candidate sha, tests[], defects_repaired[], known_limitations[], rollback). Trigger condition (2-3 packages) is effectively met — worth considering during the next idle window.
- Approximate time lost: To be measured next package (watch for how long assembling package-result-sb-ask-004.json-equivalent takes by hand).
- Smallest safe improvement: A script that captures the command, candidate SHA, and actual process exit status from an externally executed verification process, then emits an updated package-result-<id>.json schema skeleton pre-filled with verified execution data, leaving narrative fields for manual authorship.
- What it must not touch: Never runs tests itself or decides pass/fail — it must only record actual exit codes fetched from external execution processes rather than log parsing or unverified operator input; narrative/judgment fields stay human-written.
- Can it be tested outside the active product worktree? Yes — can be validated by pointing it at the three existing package-result-*.json files and confirming it reproduces their tests[]/sha fields from git + shell history.
- Rollback: Delete the script, assemble the JSON by hand as today.
- Success measure: The mechanical fields (shas, branch, exit codes) of a package-result file are generated, not hand-typed, with no change to the narrative content the reviewer expects.
- Design sketch (prep only, not built): Reads `git rev-parse HEAD` / merge-base for base_sha+candidate_sha+branch. Intercepts or securely queries the active process runner for commands + exit codes to ensure verified merge-gate evidence. Writes a versioned, backward-compatible JSON skeleton extending the core shape with `coderabbit_review_result` and `coderabbit_limitations` fields alongside the original keys so downstream readers see no schema breaks.
- Status: proposed

### Local requirement-review concept
- Pain observed: Agent C/Codex once caught a basic defect that required a later repair cycle.
- How often it happens: One confirmed instance so far (SB-ASK-001, evidence/CODEX_RESULT.md): Codex FAILed a candidate that both prior reviewers (tests + Agent C) had passed, because green tests encoded a contractual violation (LOW/unknown evidence still reached the model). Continue tracking; consider only if this repeats.
- Approximate time lost: One full repair cycle in that instance.
- Smallest safe improvement: During a future, separately authorized idle-window experiment, evaluate whether a local requirement-versus-diff analysis could flag contradictions between what tests assert and what the frozen specification requires before formal review handoff.
- What it must not touch: Must not replace, skip, alter, or reinterpret Agent C/Codex review requirements, owner merge authority, or the candidate branch's test suite. It must not edit tests to satisfy analysis findings.
- Can it be tested outside the active product worktree? Yes — use archived package-result-*.json files, diffs, and task artifacts; no live worktree access is needed.
- Rollback: Do not adopt the experiment or stop using it; packages continue through the established review process.
- Success measure: If separately adopted, fewer formal-review failures caused by a documented mismatch between passing tests and the frozen requirement.
- Design sketch (prep only, not built): A future experiment could provide archived package evidence, the candidate diff, and frozen requirement text to a local analysis tool, then produce an advisory findings note. Any output would be non-authoritative and would not constitute review completion, approval, merge authorization, or a substitute for required verification.
- Status: proposed

### ntfy digest/merge summary
- Pain observed: No single digest of phone notifications after a package.
- How often it happens: Consider after phone notifications work reliably.
- Approximate time lost: To be measured.
- Smallest safe improvement: Combine existing ntfy notifications into one summary message.
- What it must not touch: Existing per-event notification behavior.
- Can it be tested outside the active product worktree? Yes.
- Rollback: Revert to individual notifications only.
- Success measure: One readable digest instead of scattered notifications.
- Status: proposed

### Sequential package queue
- Pain observed: Starting packages manually is a repeated bottleneck.
- How often it happens: Consider only when starting packages manually is a repeated bottleneck.
- Approximate time lost: To be measured.
- Smallest safe improvement: Queue that starts the next package automatically once the prior one is resolved.
- What it must not touch: Package review/merge gates, owner decisions, or any review requirement.
- Can it be tested outside the active product worktree? Yes.
- Rollback: Disable the queue, start packages manually.
- Success measure: Less manual intervention between packages.
- Status: proposed

### Merge helper
- Pain observed: Merging clean, low-risk packages is repetitive manual work.
- How often it happens: Consider only after several clean low-risk integration merges.
- Approximate time lost: To be measured.
- Smallest safe improvement: Script that assists with known-safe merge preparation steps for low-risk packages after the owner has made a merge decision.
- What it must not touch: Packages flagged as risky or requiring review; review results must not be treated as merge approval; owner merge authority must remain unchanged.
- Can it be tested outside the active product worktree? Yes.
- Rollback: Merge manually.
- Success measure: Faster, consistent owner-authorized merges for low-risk packages.
- Status: proposed
