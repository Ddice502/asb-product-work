# Local AI Second Brain — Product Delivery Rules

Read `docs/SECOND_BRAIN_EXECUTION_HANDOFF.md` before beginning work.
Treat it as the governing product-execution contract, subject to higher-priority
safety instructions and actual repository constraints.

Current mode is direct product delivery only.

Do not:
- Extend, retry, activate, merge, install, or use UEP, Control Room, M0-A,
  campaign worktrees, worker pools, tmux recovery, custom runners, watchdogs,
  task queues, dashboards, or deployment tooling.
- Access home, vault, runtime, live services, real capture/inbox data,
  databases, credentials, `.env` files, or paths outside this product target.
- Merge to main, push/sync to a remote, promote to home, deploy, restart
  services, or alter system configuration.
- Ask the owner to monitor panes, copy prompts/results between tools, approve
  ordinary repairs, or decide routine implementation details.

For each approved package:
1. Perform the handoff's read-only baseline checks.
2. Work only in the approved isolated branch/worktree and declared paths.
3. Complete normal implementation and test repairs independently, within the
   package time and repair budget.
4. Run the declared checks and preserve real evidence.
5. Freeze one clean candidate SHA before verification/review.
6. Return only a concise merge-gate report or a genuine hard-stop report.

If a boundary, required source, or safety fact cannot be proven, stop and
report that one fact. Do not improvise a topology or workaround.
