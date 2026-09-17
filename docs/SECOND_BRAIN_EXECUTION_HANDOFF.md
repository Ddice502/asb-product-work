## Code review workflow

CodeRabbit is the required independent code reviewer only when the package
meets the explicit Codex-required/high-risk criteria in this operating
contract. It is not a default review gate for ordinary UI composition,
templates, pure functions, or deterministic test-covered low-risk workflows.

CodeRabbit is advisory. It has no authority to edit, merge, publish, promote,
restart services, access home, access real data, create package identities, or
change package scope.

CodeRabbit must never run concurrently with Agent C.

### When CodeRabbit is required

Run CodeRabbit review only when a frozen package:

- handles real data, import/export, vault mutation, credentials, permissions,
  networking, external APIs, a schema/database/index migration,
  promotion-to-home behavior, or a security-sensitive capture/retrieval path;
  or
- is a milestone bundle integrating several packages.

A package brief must state whether CodeRabbit is required and why.

Do not add CodeRabbit review merely because a package has user-visible behavior.
For a low-risk package that skips CodeRabbit, the package brief must state why
the package is outside the high-risk criteria.

### Review order and candidate scope

CodeRabbit review occurs only after:

1. Primary Claude Code has completed all package implementation and local
   validation;
2. the candidate has been frozen at one full candidate SHA;
3. the candidate is clean, descends from the recorded package base, and
   contains only declared allowed paths;
4. Agent C has verified that exact frozen candidate SHA in an isolated or
   detached checkout and has passed the package tests plus applicable
   regressions.

CodeRabbit must review the same frozen candidate SHA that Agent C verified.
It must not review a moving branch, an uncommitted implementation state, home,
production, a remote branch, a pull request, or any path outside the isolated
sanitized product target.

Do not push a branch, open a pull request, synchronize to a remote, or use the
CodeRabbit GitHub App as part of this product-delivery workflow.

### Frozen-candidate review

Run CodeRabbit from an isolated checkout of the exact frozen candidate SHA.
Set the review base to the recorded package base or to the isolated product
integration base specified in the package brief.

Use the installed CLI's documented equivalent of the following command:

```bash
cr review --agent --base <recorded-package-base>
```

The `--agent` mode provides structured output intended for coding-agent
workflows. The CLI supports explicit base-branch comparison, and `cr` is the
short alias for `coderabbit`.

Do not use an uncommitted-change review as the final frozen-candidate gate.
`--uncommitted` is appropriate only for advisory feedback during local
implementation, because it reviews staged and tracked local edits rather than
proving the committed frozen candidate.

If advisory pre-freeze review is used during implementation, it must remain
within the package time and repair budget and must not replace the final
post-Agent-C frozen-candidate review. The maximum remains two total
evidence-based repair cycles for the package.

### Finding disposition

Evaluate every CodeRabbit finding against the exact frozen candidate and the
approved package scope.

Fix a confirmed finding when it concerns:

- correctness;
- security or containment;
- data integrity;
- compatibility;
- concurrency;
- authorization or permissions;
- destructive operation;
- real-data, import/export, vault, credential, networking, API, migration,
  promotion, or security-sensitive retrieval/capture behavior;
- a regression of an approved product behavior; or
- a missing integration safeguard required by the package.

Do not make implementation changes solely for style, preference, formatting,
or non-executable wording findings unless they conflict with an existing
project convention or make a machine-readable contract, emitted product
string, test assertion, prompt/trailer contract, receipt fact, SHA, changed
file list, test command, or exit result incorrect.

A finding limited to non-executable wording does not block a behaviorally
verified package. Record it in `docs/WORDING_CLEANUP_LOG.md`, label it
`WORDING`, spend no repair cycle on it, and report the candidate at the merge
gate.

A CodeRabbit finding about a product-emitted string, asserted test string,
prompt/trailer contract, receipt machine fact, SHA, changed-file list, test
command, or exit code is not a wording-only finding. Treat it as behavior or
evidence and resolve it before the merge gate.

### Remediation and re-verification

If CodeRabbit identifies a confirmed blocking issue:

1. Primary Claude Code may perform one bounded remediation within the same
   approved package scope and remaining two-cycle repair budget.
2. The repaired result becomes a new frozen candidate SHA.
3. Re-run all affected local package tests, parsing/import checks, and
   applicable regressions.
4. Agent C must re-verify the new exact frozen candidate SHA before any final
   CodeRabbit review.
5. CodeRabbit then reviews that exact new frozen SHA.
6. Do not create a new package identity, silently extend the time budget, or
   change tests merely to make the candidate pass.

If the same root cause or a repeated architectural finding persists after the
allowed repair budget, preserve the branch, review output, test results, diff,
and candidate SHA. Return one terminal hard-stop report rather than creating a
new remediation job or expanding scope.

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

### Evidence and merge gate

For a package requiring CodeRabbit, retain in the package evidence folder:

- the full frozen candidate SHA reviewed;
- the recorded base SHA used for comparison;
- the exact CodeRabbit command;
- the true command exit result;
- the complete review artifact or a durable reference to it;
- each finding's disposition: fixed, rejected with evidence, or `WORDING`;
- any remediation candidate SHA and its corresponding local-test, Agent C, and
  final CodeRabbit results;
- CodeRabbit limitations, including whether the review was static-only; and
- confirmation that no live system changed.

Include the CodeRabbit result and limitations in
`evidence/package-result.json` and the concise terminal merge-gate report.

A package is ready for owner merge only when all required local tests pass,
Agent C has passed for the exact frozen candidate, required CodeRabbit review
has completed for that same candidate, all confirmed blocking findings have
been resolved, and the candidate remains clean and within its declared allowed
paths.

CodeRabbit completion does not authorize a merge, promotion, deployment,
remote synchronization, service restart, home access, or production change.
Jamale alone decides whether to merge the candidate into the isolated product
integration line. Promotion to home remains a separate owner-approved release
event.
