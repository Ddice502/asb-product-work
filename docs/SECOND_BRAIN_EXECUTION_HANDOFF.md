## Code review workflow

CodeRabbit is the required independent code-review system for packages that
reach the product-delivery merge workflow.

The required sequence is:

**Implement → run existing project checks → run CodeRabbit CLI before
commit/push → commit and push → open PR → GitHub Actions checks run →
CodeRabbit GitHub App final review runs → resolve confirmed high-impact
findings → merge only after CI and CodeRabbit are complete.**

CodeRabbit is advisory. It has no authority to independently edit, merge,
publish, promote, restart services, access home, access real data, create
package identities, expand package scope, or authorize deployment.

CodeRabbit CLI and the CodeRabbit GitHub App serve different purposes:

* CodeRabbit CLI is the required local pre-commit/pre-push review.
* The CodeRabbit GitHub App is the required final pull-request review.
* A successful CLI review does not replace the GitHub App review.
* A successful GitHub App review does not replace existing project checks or
  the required pre-commit CLI review.

Any other project-specific verification or acceptance criteria remain in
effect unless separately changed by the owner. They must not replace,
reorder, or bypass this CodeRabbit workflow.

### Required review sequence

For every package entering this workflow:

1. Primary Claude Code completes the approved implementation within the
   declared package scope.
2. Run all existing project checks applicable to the changed code, including
   existing format, lint, type, test, build, security, deployment/smoke, and
   project-specific acceptance checks.
3. Run CodeRabbit CLI against the completed local implementation before the
   implementation is committed or pushed.
4. Evaluate the CLI findings and resolve any confirmed high-impact issue before
   proceeding. If code changes as a result, rerun the affected project checks
   and CodeRabbit CLI before commit/push.
5. Commit the reviewed implementation.
6. Push the commit or branch to the approved development remote.
7. Open or update the pull request for that package.
8. Allow the required GitHub Actions checks to run against the pull-request
   head.
9. Allow the CodeRabbit GitHub App to perform the final review of the
   pull-request head.
10. Evaluate the final GitHub App findings and resolve every confirmed
    high-impact finding.
11. If remediation changes code, rerun the applicable project checks and
    CodeRabbit CLI before committing and pushing the repair. GitHub Actions and
    the CodeRabbit GitHub App must then run again against the updated
    pull-request head.
12. Merge only when the final pull-request head has completed the required CI
    checks and final CodeRabbit GitHub App review and no confirmed high-impact
    finding remains unresolved.

Do not treat an earlier commit, earlier CI run, earlier CodeRabbit review, or
earlier pull-request head as evidence for code that changed afterward.

### CodeRabbit CLI pre-commit review

Run CodeRabbit CLI after implementation and existing local project checks, but
before commit and push.

The CLI review is intended to catch issues while the implementation is still
local and inexpensive to repair.

Use the installed CodeRabbit CLI's documented review command appropriate to
the repository and current working changes. Record the exact command that was
actually run rather than documenting a hypothetical command.

The CLI review may inspect the completed uncommitted implementation because
this stage intentionally occurs before commit/push.

Do not describe the CLI result as the final CodeRabbit review. The final
CodeRabbit gate is the GitHub App review of the pull request.

If the CLI identifies a confirmed high-impact problem:

1. Repair it within the approved package scope.
2. Run the affected existing project checks again.
3. Run CodeRabbit CLI again.
4. Proceed to commit/push only after the reviewed local implementation is
   suitable for the pull-request stage.

Substantive CodeRabbit-driven repairs count toward the package's existing
two-cycle evidence-based repair budget.

### Pull-request and final CodeRabbit review

After the required local checks and CodeRabbit CLI review are complete:

1. commit the implementation;
2. push it to the approved development remote;
3. open or update the pull request;
4. run the required GitHub Actions checks; and
5. obtain the CodeRabbit GitHub App final review.

The CodeRabbit GitHub App must review the current pull-request head that is
being considered for merge.

Do not merge based solely on:

* a local CodeRabbit CLI result;
* an earlier CodeRabbit GitHub App review;
* CI results from an earlier pull-request head;
* tests from code that changed afterward; or
* a review artifact that does not correspond to the current merge candidate.

If the pull-request head changes after CI or CodeRabbit review, rerun the
checks and reviews required for the changed code before merge.

### Finding disposition

Evaluate every CodeRabbit finding against the actual implementation, approved
package scope, and current pull-request head.

Fix a confirmed finding when it concerns:

* correctness;
* security or containment;
* data integrity;
* compatibility;
* concurrency;
* authorization or permissions;
* destructive operation;
* real-data, import/export, vault, credential, networking, API, migration,
  promotion, or security-sensitive retrieval/capture behavior;
* a regression of an approved product behavior;
* a missing integration safeguard required by the package; or
* another issue that could materially affect correct, safe, or reliable
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

A finding may be rejected when the evidence demonstrates that it does not
apply, is incorrect, is outside the approved package scope, or does not
represent a real defect. Record the reason and supporting evidence.

### Remediation and re-verification

If either CodeRabbit stage identifies a confirmed high-impact issue, Primary
Claude Code may perform bounded remediation within the same approved package
scope and remaining two-cycle repair budget.

For a local CLI finding:

1. repair the implementation;
2. rerun affected existing project checks;
3. rerun CodeRabbit CLI; and
4. only then continue to commit and push.

For a finding from the final CodeRabbit GitHub App review:

1. repair the implementation within the same package and pull request;
2. rerun all affected local package tests, parsing/import checks, and
   applicable regressions;
3. rerun CodeRabbit CLI before committing or pushing the repair;
4. commit and push the repaired code;
5. allow GitHub Actions to run against the new pull-request head;
6. allow the CodeRabbit GitHub App to review the new pull-request head; and
7. evaluate the new results before considering merge.

Do not create a new package identity, silently extend the time or repair
budget, expand scope, or change tests merely to make the candidate pass.

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
3. Add or update the appropriate deterministic unit, integration, contract,
   or end-to-end tests.
4. Verify error handling, retries, idempotency, restart behavior, and data
   integrity where applicable.
5. Treat missing integration evidence as incomplete work, not as a passing
   review.
6. Do not inspect or contact home, real vault data, real databases, production
   services, credentials, or external production APIs to obtain that evidence.

These requirements are part of the existing project checks and acceptance
criteria and are not replaced by CodeRabbit.

### Evidence and merge gate

Retain sufficient evidence to prove the complete review sequence.

For each package, retain as applicable:

* the package base SHA or recorded comparison base;
* the final commit SHA and pull-request head SHA;
* the exact local CodeRabbit CLI command used;
* the true CodeRabbit CLI command exit result;
* the CLI review artifact or a durable reference to it;
* the existing local project checks that were run and their actual results;
* the pull-request identifier;
* the GitHub Actions checks and final results;
* the CodeRabbit GitHub App final-review result;
* each material finding's disposition: fixed, rejected with evidence, or
  `WORDING`;
* any remediation commit SHA and the checks and reviews corresponding to it;
* CodeRabbit limitations relevant to interpreting the review;
* confirmation that the final evidence corresponds to the current
  pull-request head; and
* confirmation that no unauthorized live or production system changed.

Include the CodeRabbit results and relevant limitations in
`evidence/package-result.json` and in the concise terminal merge-gate report
where those artifacts are required by the package.

A package is ready for owner merge only when:

* implementation is complete within declared scope;
* all required existing project checks pass;
* CodeRabbit CLI reviewed the final code before its commit/push;
* the final code is committed and pushed to the approved development remote;
* the pull request represents the intended package;
* required GitHub Actions checks have completed successfully against the
  current pull-request head;
* the CodeRabbit GitHub App final review has completed against the current
  pull-request head;
* all confirmed high-impact findings have been resolved;
* any rejected finding has supporting evidence;
* the final pull-request head remains within the package's declared scope; and
* the required evidence corresponds to that final pull-request head.

CodeRabbit completion does not by itself authorize merge, promotion,
deployment, service restart, home access, or production change.

Jamale retains the final authority to approve or perform the merge after the
required CI and CodeRabbit gates are complete.

Promotion to home remains a separate owner-approved release event.
