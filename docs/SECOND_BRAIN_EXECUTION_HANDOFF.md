## Code review workflow

CodeRabbit is the required independent code-review system for packages that
reach the product-delivery merge workflow.

The required sequence is:

**Primary Claude Code: implement → run existing project checks → run
CodeRabbit CLI before the candidate commit → create one local-only candidate
commit → return that exact candidate SHA and merge-gate evidence.**

**Owner-controlled pull-request stage: commit/push the reviewed candidate →
open or update PR → GitHub Actions checks run → CodeRabbit GitHub App final
review runs → resolve confirmed high-impact findings through an approved
remediation package if needed → owner merges only after CI and CodeRabbit are
complete.**

CodeRabbit is advisory. It has no authority to independently edit, merge,
publish, promote, restart services, access home, access real data, create
package identities, expand package scope, authorize deployment, access
credentials, access `.env` files, or alter system configuration.

CodeRabbit CLI and the CodeRabbit GitHub App serve different purposes:

- CodeRabbit CLI is the required local pre-commit and pre-push review.
- The CodeRabbit GitHub App is the required final pull-request review.
- A successful CLI review does not replace the GitHub App review.
- A successful GitHub App review does not replace existing project checks or
  the required pre-commit CLI review.
- CodeRabbit completion does not by itself prove end-to-end runtime behavior,
  authorize merge, promotion, deployment, service restart, home access, or
  production change.

Any other project-specific verification or acceptance criteria remain in
effect unless separately changed by the owner. They must not replace, reorder,
or bypass this CodeRabbit workflow.

### Required review sequence

For every package entering this workflow:

1. Primary Claude Code completes the approved implementation within the
   declared package scope and approved isolated worktree.
2. Primary Claude Code runs all existing project checks applicable to the
   changed code, including existing format, lint, type, test, build, security,
   deployment/smoke, and project-specific acceptance checks.
3. Primary Claude Code runs CodeRabbit CLI against the completed local
   implementation before the candidate commit or any push.
4. Primary Claude Code evaluates all CLI findings and resolves every confirmed
   high-impact issue within the approved package scope and remaining repair
   budget.
5. If code changes as a result of a CodeRabbit CLI finding, Primary Claude
   Code reruns the affected project checks and CodeRabbit CLI before
   proceeding.
6. Primary Claude Code creates one clean local-only candidate commit
   after the final required checks and CodeRabbit CLI review are complete.
7. Primary Claude Code records the exact local candidate commit SHA and returns
   the required merge-gate report with final local-check and CodeRabbit
   CLI-review evidence bound to that SHA.
8. Primary Claude Code must not push, sync to a remote, open a pull request,
   merge, deploy, restart services, promote to home, access home, access real
   data, access credentials, access `.env` files, or alter system
   configuration.
9. The owner reviews the returned evidence and, if approved, verifies
   that the local candidate commit SHA matches the reported SHA, pushes that
   exact candidate commit to the approved development remote, and opens or
   updates the pull request.
10. Required GitHub Actions checks run against the current pull-request head.
11. CodeRabbit GitHub App performs the final review of the current
    pull-request head.
12. The owner evaluates final GitHub App findings. A confirmed high-impact
    finding requires an approved bounded remediation package. It must not
    trigger autonomous scope expansion, an unbounded repair loop, or
    unauthorized remote actions.
13. If a remediation commit is pushed, GitHub Actions and CodeRabbit GitHub
    App must run again against the new pull-request head.
14. The owner merges only when the final pull-request head has completed the
    required CI checks and final CodeRabbit GitHub App review, no confirmed
    high-impact finding remains unresolved, and the evidence corresponds to
    that exact pull-request head.

Do not treat an earlier commit, earlier CI run, earlier CodeRabbit review, or
earlier pull-request head as evidence for code that changed afterward.

### CodeRabbit CLI pre-commit review

Primary Claude Code runs CodeRabbit CLI after implementation and existing local
project checks, but before creating the final local-only candidate commit and
before any owner-controlled push.

The CLI review is intended to catch issues while the implementation is still
local and inexpensive to repair.

Use the installed CodeRabbit CLI's documented review command appropriate to
the repository and current working changes. Record the exact command that was
actually run rather than documenting a hypothetical command.

The CLI review may inspect the completed uncommitted implementation because
this stage intentionally occurs before the candidate commit and before push.

The standard local review command is:

```bash
cr review -c docs/Claude.md --agent --uncommitted --base main
```

If the repository default branch changes, replace `main` with the actual
default branch.

Do not describe the CLI result as the final CodeRabbit review. The final
CodeRabbit gate is the GitHub App review of the current pull-request head.

If CodeRabbit CLI identifies a confirmed high-impact problem:

1. Repair it within the approved package scope.
2. Run the affected existing project checks again.
3. Run CodeRabbit CLI again.
4. Create one local-only candidate commit only after the reviewed local
   implementation is suitable for the owner-controlled pull-request stage.
5. Record and return the exact candidate commit SHA with the review evidence.

Substantive CodeRabbit-driven repairs count toward the package's existing
two-cycle evidence-based repair budget.

Stop after a maximum of two CodeRabbit review/repair passes. If a confirmed
root cause or architectural finding persists after the allowed repair budget,
preserve the branch, diff, check results, review output, and relevant commit
SHA information, then return one genuine terminal hard-stop report.

### Pull-request and final CodeRabbit review

After Primary Claude Code returns a clean local-only candidate commit SHA and
merge-gate report, the owner may perform the remote pull-request stage:

1. Verify that the candidate commit SHA matches the reported SHA and that no
   uncommitted changes alter the reviewed candidate.
2. Push that exact candidate commit to the approved development remote.
3. Open or update the pull request targeting `main`.
4. Allow required GitHub Actions checks to run.
5. Allow CodeRabbit GitHub App to perform the final review.

Primary Claude Code must not perform remote Git, pull-request, merge,
deployment, promotion, home-access, credential, or system-configuration
actions.

The CodeRabbit GitHub App must review the current pull-request head that is
being considered for merge.

Do not merge based solely on:

- A local CodeRabbit CLI result.
- An earlier CodeRabbit GitHub App review.
- CI results from an earlier pull-request head.
- Tests from code that changed afterward.
- A review artifact that does not correspond to the current merge candidate.
- A CodeRabbit approval or lack of findings without the required project
  checks and integration evidence.

If the pull-request head changes after CI or CodeRabbit review, rerun the
checks and reviews required for the changed code before merge.

### Finding disposition

Evaluate every CodeRabbit finding against the actual implementation, approved
package scope, and current pull-request head.

Fix a confirmed finding when it concerns:

- Correctness.
- Security or containment.
- Data integrity.
- Compatibility.
- Concurrency.
- Authorization or permissions.
- Destructive operation.
- Real-data, import/export, vault, credential, networking, API, migration,
  promotion, or security-sensitive retrieval/capture behavior.
- A regression of an approved product behavior.
- A missing integration safeguard required by the package.
- Another issue that could materially affect correct, safe, or reliable
  operation.

Do not make implementation changes solely for style, preference, formatting,
or non-executable wording findings unless they conflict with an existing
project convention or make a machine-readable contract, emitted product
string, test assertion, prompt/trailer contract, receipt fact, SHA,
changed-file list, test command, or exit result incorrect.

A finding limited to non-executable wording does not by itself block an
otherwise behaviorally verified package. Record it in
`docs/WORDING_CLEANUP_LOG.md`, label it `WORDING`, spend no repair cycle on
it, and include it in the merge-gate report.

A CodeRabbit finding about a product-emitted string, asserted test string,
prompt/trailer contract, receipt machine fact, SHA, changed-file list, test
command, or exit code is not a wording-only finding. Treat it as behavior or
evidence and resolve it before the merge gate when it is confirmed and
high-impact.

A finding may be rejected when evidence demonstrates that it does not apply,
is incorrect, is outside the approved package scope, or does not represent a
real defect. Record the reason and supporting evidence.

### Remediation and re-verification

If either CodeRabbit stage identifies a confirmed high-impact issue, Primary
Claude Code may perform bounded remediation only within the same approved
package scope and remaining two-cycle repair budget.

For a local CodeRabbit CLI finding:

1. Repair the implementation.
2. Rerun affected existing project checks.
3. Rerun CodeRabbit CLI.
4. Create a new clean local-only candidate commit if remediation changed the
   candidate.
5. Record and return the new exact candidate commit SHA with updated evidence
   before any owner-controlled push.

For a confirmed finding from the final CodeRabbit GitHub App review:

1. The owner records the finding and determines whether it is confirmed,
   in-scope, and eligible for remediation under the existing package rules.
2. If remediation is approved, Primary Claude Code performs only the bounded
   local repair within the same approved package scope and remaining repair
   budget.
3. Primary Claude Code reruns all affected local package checks, parsing/import
   checks, applicable regressions, and CodeRabbit CLI before creating and
   returning a new clean local-only candidate commit SHA and evidence.
4. The owner verifies the reviewed repair candidate SHA and pushes it to the
   approved development remote.
5. GitHub Actions runs against the new pull-request head.
6. CodeRabbit GitHub App reviews the new pull-request head.
7. The owner evaluates the resulting evidence before considering merge.

Do not create a new package identity, silently extend the time or repair
budget, expand scope, change tests merely to make the candidate pass, or use
an unresolved finding as a reason to bypass the required review gate.

If the same root cause or a repeated architectural finding persists after the
allowed repair budget, preserve the branch, pull request, review output, test
results, diff, and relevant commit SHAs. Return one terminal hard-stop report
rather than creating another remediation loop or expanding scope.

### Integration-impact review

For every changed API, database schema, migration, job payload, event,
configuration value, environment variable, authentication or authorization
rule, public function, or externally consumed output:

1. Identify affected callers and consumers within the sanitized product source
   export.
2. Preserve backward compatibility or implement and test the approved
   migration within package scope.
3. Add or update appropriate deterministic unit, integration, contract, or
   end-to-end tests.
4. Verify validation, error handling, retries, idempotency, restart behavior,
   concurrency behavior, and data integrity where applicable.
5. Treat missing integration evidence as incomplete work, not as a passing
   review.
6. Do not inspect or contact home, real vault data, real databases, production
   services, credentials, `.env` files, or external production APIs to obtain
   that evidence.

These requirements are part of the existing project checks and acceptance
criteria. They are not replaced by CodeRabbit.

### Evidence and merge gate

Retain sufficient evidence to prove the complete review sequence.

For each package, retain as applicable:

- The package base SHA or recorded comparison base.
- The final local-only candidate commit SHA.
- The final commit SHA and pull-request head SHA.
- The exact local CodeRabbit CLI command used.
- The true CodeRabbit CLI command exit result.
- The CLI review artifact or a durable reference to it.
- The existing local project checks that were run and their actual results.
- The pull-request identifier.
- The GitHub Actions checks and final results.
- The CodeRabbit GitHub App final-review result.
- Each material finding's disposition: fixed, rejected with evidence, or
  `WORDING`.
- Any remediation candidate or commit SHA and the checks and reviews
  corresponding to it.
- CodeRabbit limitations relevant to interpreting the review.
- Confirmation that final evidence corresponds to the current pull-request
  head.
- Confirmation that no unauthorized live or production system changed.

Include CodeRabbit results and relevant limitations in
`evidence/package-result.json` and in the concise terminal merge-gate report
where those artifacts are required by the package.

A package is ready for owner merge only when:

- Implementation is complete within declared scope.
- All required existing project checks pass.
- CodeRabbit CLI reviewed the final code before its local-only candidate
  commit and before any push.
- The exact local-only candidate commit SHA was recorded with the final local
  check and CodeRabbit CLI evidence.
- The owner verified and pushed that exact candidate commit to the approved
  development remote.
- The pull request represents the intended package.
- Required GitHub Actions checks have completed successfully against the
  current pull-request head.
- The CodeRabbit GitHub App final review has completed against the current
  pull-request head.
- All confirmed high-impact findings have been resolved.
- Any rejected finding has supporting evidence.
- The final pull-request head remains within the package's declared scope.
- The required evidence corresponds to that final pull-request head.
- No unauthorized live, home, or production system changed.

CodeRabbit completion does not by itself authorize merge, promotion,
deployment, service restart, home access, production change, or system
configuration change.

Jamale retains final authority to approve or perform the merge after required
CI and CodeRabbit gates are complete.

Promotion to home remains a separate owner-approved release event.
