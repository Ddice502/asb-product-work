Local AI Second Brain Product Execution Handoff

Purpose

This document is the standing operating contract for finishing the Local AI
Second Brain without turning Jamale into the message bus between agents. Its
only objective is to deliver tested Second Brain product capabilities. It does
not authorize further work on custom coordinators, runners, dashboards,
watchdogs, task queues, review frameworks, or tmux automation unless an owner
explicitly reopens that work after a product release.

The prior UEP, Control Room, and M0-A experiments are evidence of what was
tried. They are not the delivery mechanism for the next product work. Preserve
their branches, tags, receipts, and known-issue records. Do not delete,
"clean up," retry, merge, activate, or extend them.

Success Definition

The project is moving forward only when a tested user-facing Second Brain
capability is closer to running on home. New audit tools, pipeline features,
test harnesses, dashboards, status pages, worker managers, or process-control
code do not count as product progress by themselves.

A package is complete only when it has:

a frozen candidate in the isolated product target;

deterministic tests that prove the intended behavior and relevant
regressions;

Agent C verification of that frozen candidate;

Codex review only when the package handles real data, import/export, vault
mutation, credentials, permissions, networking/external APIs, a
schema/database/index migration, promotion-to-home behavior, or a
security-sensitive capture/retrieval path; or when it is a milestone bundle
integrating several packages;

an owner decision to merge the candidate into the isolated product
integration line; and

later, an owner-approved and reversible promotion to home.

"Built in the product target" is not the same as "running on home." Never
claim the latter before the promotion and smoke-test gate passes.

Roles and Authority

Role

May do

Must not do

Owner, Jamale

Set release priorities; approve a package; accept/reject a completed candidate; authorize promotion to home; decide real policy/UX choices and any scope expansion.

Relay routine implementation messages, approve ordinary repairs, monitor panes, or restart workers for normal product work.

Primary Claude Code

Own direct implementation of one approved product package in the isolated product worktree; inspect, edit, test, repair routine failures, create candidate/evidence, and request the final gates.

Build more delivery infrastructure; touch home, vault, real capture data, databases, production services, or unapproved paths; merge/promote without owner decision.

Agent C

Dynamically verify the exact frozen candidate after local tests pass.

Repair the candidate, change scope, run concurrently with Codex on the same candidate, or approve a release.

Codex

Independently review a frozen candidate after Agent C passes when the package meets the explicit high-risk criteria in this document.

Review ordinary UI composition, templates, pure functions, or test-covered low-risk workflows by default; participate in iterative implementation/debugging; review a moving branch; approve/publish/promote; or run concurrently with Agent C.

Local models

Advisory analysis only, such as a local review or summarization.

Be the primary implementer for product packages unless the owner explicitly changes this policy after a demonstrated capability test.

Cloud Claude development is permitted only in the isolated, sanitized product
target and only for owner-approved product packages. It must never receive real
notes, inbox content, vault data, credentials, .env files, databases, state,
or any data copied directly from home.

Current Development Boundary

The following is the intended, read-only-reverify-before-use boundary. If any
fact differs, stop and report that single fact; do not improvise a new topology.

Development host: jro.desktop.

Isolated product target: /home/jro/asb-product-work.

Product integration branch: uep/work.

Product target has no Git remote and contains only the approved source-only
export and invented/sanitized fixtures.

Product source integration must not contact home during implementation or
tests.

home remains production and is outside all development execution paths.

Pinned development runtime and existing coordinator live checkout remain
untouched by product work.

Existing A/B/C tmux sessions and the legacy coordinator are not part of the
direct product workflow.

UEP artifacts may be read for evidence but must not receive new product work.

Before every package, run read-only checks of the target branch, clean tracked
tree, no remote, permitted source roots, and the source-only import manifest.
If the package requires a file absent from the target, create a clearly scoped
owner decision; never reach into home during a worker run.

The Operating Model

There is one direct product executor, not a multi-agent conveyor belt:

Jamale approves one product package, not individual implementation
tasks.

Primary Claude Code works directly in one isolated branch/worktree from the
product integration line using normal repository tools.

Claude may make ordinary implementation and test repairs within the package
scope. It must remain within the declared allowed paths and package time
budget.

Claude creates one frozen candidate only after all package tests pass.

Agent C verifies the exact candidate.

Codex reviews only if the package meets the high-risk criteria above.

Jamale receives one concise terminal report: merge gate, a true hard stop,
or the later promotion gate.

Do not decompose a package into a series of owner choices merely because it
contains several files or tests. Internal subtasks are Claude's responsibility.
Do not auto-requeue, auto-reassign, create remediation jobs, or invent a new
state machine.

Package size and time budget

A normal package should deliver one user-visible workflow or one coherent
slice of a workflow. Target 4 to 8 hours of implementation effort, not a
single 15-minute file and not a week-long program. Examples are a grounded
answer response path, a capture-to-inbox flow, proposal review/promotion, a
daily briefing flow, or a voice capture slice.

For every package, Claude declares before implementation:

purpose and user-visible outcome;

allowed and protected paths;

base commit and isolated branch;

deterministic tests and manual synthetic scenario;

maximum elapsed implementation time;

maximum two evidence-based repair cycles;

whether Codex is required; and

exact rollback, which is normally abandoning the isolated candidate branch.

If the time or repair budget is reached, Claude freezes the branch, records
tests/diff/evidence, and gives one terminal escalation. It must not silently
continue, start a new job identity, or spend an unbounded number of model
calls. An ordinary failing test does not need owner approval; it consumes the
package's repair budget.

Progress and notification rule

Normal progress is quiet. Jamale is not expected to inspect a terminal, a
dashboard, or a phone every few minutes.

Before the first unattended product package, configure one private ntfy topic
on Jamale's Pixel and prove both a high-priority terminal alert and a
low-priority digest from jro.desktop. Store the topic/token only in an
owner-readable state/configuration location outside the repository and never
place it in a work order, source file, evidence receipt, or Git history. The
package contract records the notification mechanism and the date/time of its
successful delivery test, but never the secret or topic value.

The product executor must write a plain terminal receipt at package end. Until
the phone delivery test passes, do not claim unattended notification is
complete and do not start an unattended package. A scheduled check at the
package's stated end time is only a temporary fallback, not the final workflow.

Only these events warrant an immediate owner message:

a confirmed containment/security boundary issue;

package time or repair budget exhausted;

an unresolvable missing source/dependency;

a real user-policy decision that changes behavior or scope;

candidate ready for owner merge; or

promotion/release decision.

All messages must say: package, current state, exact reason, what evidence is
preserved, whether any live system changed, recommended choice, and the one
action required. They must not ask for a progress check, a copy/paste relay,
or a routine retry approval.

Product Package Lifecycle

1. Package approval

The owner approves a concise package brief. It must identify the product
outcome, user impact, allowed paths, test plan, risk, estimated time, whether
it can ever touch real data, and the eventual owner decision. Low-level
implementation plans remain internal.

2. Direct implementation

Primary Claude Code starts from the recorded integration base in an isolated
product branch. It uses repository editing tools to inspect the existing
source and make narrow edits. It must not regenerate a large existing file as
model text, require a model to calculate hashes, or require the model to
produce opaque identifiers. Deterministic tools own hashes, file validation,
and test execution.

Any edit to an existing large file must be a normal local edit/diff performed
through repository tools. Before a candidate is frozen, run parsing/import
checks, the package gate, and applicable regression tests. Tests must remain
outside the implementation's allowed paths once the package is approved.

3. Repair

Claude may repair ordinary defects without asking Jamale. A repair is allowed
only when it is relevant to the failing package requirement and leaves the
candidate cleaner than before. Two evidence-based cycles are the maximum. If
the same root cause persists after that, preserve the branch and return an
escalation rather than creating a new job or changing the test to make code
pass.

When a test is used to guide multi-step work, it must expose individually
named requirements/checkpoints. A single command exit code is not a progress
measure. Tests may be reorganized for observability only when an equivalence
proof shows their requirements were not weakened, removed, or made editable
by the package.

4. Freeze and verification

When tests pass, freeze the candidate SHA. The candidate must be clean,
descend from the package base, and include only declared allowed paths.

Agent C runs after the freeze and before Codex. Agent C must verify the exact
SHA in a detached/isolated checkout, run the package tests plus relevant
regressions, and perform a base-control check where practical. Agent C cannot
edit the candidate.

5. Independent review

Use Codex only for the explicit high-risk criteria in the Success Definition.
Ordinary UI composition, rendering, templates, pure functions, and
test-covered low-risk workflows normally require local tests plus Agent C only.
When required, Codex runs only after Agent C passes and against the exact
frozen SHA. A static-only review must be labelled honestly. Codex findings may
trigger one bounded Claude remediation inside the same package; the repaired
candidate repeats local validation and Agent C before a final Codex review. A
repeated architectural finding is a hard stop, not an invitation to create a
new remediation identity.

Pure low-risk helper modules with deterministic, pre-committed tests may skip
Codex when the package brief states why. User-visible behavior by itself does
not require Codex; the specific high-risk criteria do.

6. Merge to product integration

Jamale decides whether to merge the completed candidate into the isolated
product integration branch. This changes the product target only. It does not
alter home, activate a service, publish anything, or synchronize to a
remote.

At this gate, report:

candidate SHA and integration merge SHA;

exact files changed and diff summary;

tests, Agent C result, and Codex result/limitations;

user-visible behavior gained;

remaining known limitations;

rollback path; and

whether the next package can begin independently.

7. Promotion to home

Promotion is a separate release event after one or more integrated product
packages form a coherent release bundle. It requires explicit owner approval,
a read-only diff against home, a verified backup/rollback point, a bounded
copy/apply procedure, service smoke tests, and a clear recovery command.

No development agent has authority to promote, restart home services,
modify databases, ingest real data, alter vault content, or deploy without
this decision.

Current Product Work

The current intended user-visible capability is grounded Ask My Brain answers:

render a compact SOURCES: block after a grounded answer;

preserve source provenance and deduplicate notes;

do not invoke the answer model for UNKNOWN evidence, NO_EVIDENCE, or
single-source/un-corroborated LOW evidence;

corroborated LOW evidence from at least two distinct notes or paths is
answerable, but the response must visibly retain its LOW evidence status and
include its deduplicated source references;

return a fixed insufficient-evidence response in the suppressed cases;

label any relevant available references as Related notes:;

do not change ranking, scoring, chunking, index behavior, database behavior,
the accepted prompt/trailer contract, or real data handling.

Known product modules already merged in the isolated product target include:

src/ask-my-brain/source_references.py;

src/ask-my-brain/weak_evidence.py; and

the KI-154 row-shape adapter required to map real sanitized search rows to
source-reference inputs.

The current integration target's ask_my_brain.py must be re-verified
read-only before work begins. Direct product implementation owns the remaining
composition/wiring change. Do not run it through the old UEP text-only engine.

First Direct Product Package

Primary Claude Code receives this standing work order after read-only baseline
verification:

You are the sole implementer for the Grounded Ask My Brain Response package.

Host: jro.desktop.
Work only in /home/jro/asb-product-work from the recorded uep/work base, on a
new isolated candidate branch. Use normal repository editing and test tools.

Deliver the complete user-visible response integration:
- call the already-merged source-reference renderer from ask_my_brain.py;
- print SOURCES after grounded answers;
- preserve note provenance and deduplicate notes;
- do not invoke the answer model for UNKNOWN evidence, NO_EVIDENCE, or
  single-source/un-corroborated LOW evidence; return the fixed
  insufficient-evidence response and show any available references as
  Related notes;
- corroborated LOW evidence from at least two distinct notes or paths IS
  answerable, but the response must visibly retain its LOW evidence status
  and include its deduplicated source references.

Do not alter ranking, scoring, chunking, indexing, database behavior, capture,
home, production, real data, vault data, the accepted prompt/trailer contract,
or any file outside the package's approved source and pre-committed test
paths. Do not build or modify UEP, Control Room, M0-A, a runner, a ledger, a
watchdog, tmux code, or deployment tooling.

Read the existing source and tests first. Make normal narrow repository edits;
never regenerate the complete existing module as model text and never require
yourself to compute hashes or opaque tokens. Run parse/import checks and the
unchanged package tests after every meaningful edit.

Continue through ordinary coding and test failures. You may make up to two
evidence-based repair cycles. Stop only for a real scope/security boundary,
missing required source, or exhausted package budget. Preserve the branch and
evidence on stop.

When local tests pass, freeze one candidate. Request Agent C verification of
that exact SHA. After Agent C passes, request one Codex review of that exact
SHA. Agent C and Codex must never run concurrently. Do not merge or touch
home.

Return only with a concise terminal merge-gate report or a true hard-stop
report. Do not ask the owner for routine clarification, retry approval,
progress confirmation, or a message relay.

Choosing What Comes After Grounded Answers

After the current package is merged to the isolated product integration line,
perform one read-only product baseline reconciliation. Its output is a
human-readable ordered list of actual product workflows in the source export,
with each classified as:

already working and tested;

existing but missing tests/fixtures;

incomplete implementation;

missing from the imported source; or

blocked because it requires a separately approved home/data decision.

Use the accepted release roadmap to select the next package. The expected
product areas include capture/inbox, proposal and Obsidian promotion,
retrieval/answer UX, daily briefing, approved automation, and voice. Do not
assume each is missing; use the baseline reconciliation to determine the
actual sequence. Choose complete workflows, not convenience maintenance work.

Non Negotiable Exclusions

Until the owner explicitly reopens them, do not do any of the following:

extend or retry M0-A, Control Room, UEP, campaign worktrees, worker pools,
tmux recovery, custom dispatchers, watchdogs, dashboards, or task queues;

write new audit/detector/receipt tools unless a named product package cannot
be safely tested without one;

run the old UEP workers for product implementation;

use the local 14B model as primary product implementer;

reach from jro.desktop into home during product implementation/testing;

copy databases, state, knowledge, logs, credentials, .env files, vault
paths, real inbox/capture data, or user content into the product target;

alter home services, containers, databases, or files;

merge to main/pinned runtime, synchronize to a remote, activate a runtime,
or deploy automatically;

use Agent C and Codex concurrently;

create new task IDs to bypass a repair/review budget; or

ask Jamale to repeatedly copy prompts, inspect panes, or decide routine
implementation details.

Evidence and Documentation

For each product package, retain a small evidence folder in the product target
or its designated audit location with:

package brief and base SHA;

candidate SHA and diff summary;

test commands and true exit results;

Agent C result;

Codex result, including any dynamic/static limitation;

merge decision/merge SHA when accepted;

rollback instructions; and

concise terminal status.

The executor must also generate evidence/package-result.json at every
terminal state. It is the machine-readable handoff for Agent C, Codex, the
owner status view, and later promotion work. It is generated from Git/test
facts, not manually written prose. Minimum fields are:

{
  "package_id": "SB-ASK-001",
  "base_sha": "full SHA",
  "candidate_sha": "full SHA or null",
  "branch": "candidate branch",
  "status": "READY_FOR_REVIEW | READY_FOR_MERGE | ESCALATED",
  "changed_files": ["repository-relative paths"],
  "tests": [{"command": "exact command", "exit_code": 0}],
  "repair_cycles_used": 0,
  "agent_c_required": true,
  "codex_required": false,
  "known_limitations": [],
  "rollback": "abandon candidate branch or revert isolated merge SHA"
}

The receipt contains no credentials, ntfy topic, real note content, vault
paths, database rows, or secret-shaped test output.

Write product documentation only when it describes a delivered behavior,
decision, limitation, or release state. Do not create ceremonial status
documents to simulate progress.

Owner Interaction Contract

Jamale should normally receive no more than:

one concise package approval request;

one terminal merge-gate or genuine hard-stop report; and

one later promotion decision for a release bundle.

If a proposed action would create more interactions than this, the executor
must absorb the routine work internally or stop with a single clear statement
of the genuine boundary. It must not turn Jamale into a relay between models.

Final Rule

If a proposed next action does not directly make the Local AI Second Brain
more usable, safer to promote, or verifiably closer to a product release, do
not do it now.
