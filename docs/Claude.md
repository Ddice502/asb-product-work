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
5. Run the required local CodeRabbit CLI review against the completed local
   implementation.
6. Create one clean local-only candidate commit after the final required checks
   and local CodeRabbit CLI review are complete.
7. Record and return the exact local candidate commit SHA in the concise
   merge-gate report.
8. Do not push/sync the candidate commit, open a pull request, merge, deploy,
   restart services, promote to home, access home, access real data, access
   credentials, access `.env` files, or alter system configuration.
9. Return only a concise merge-gate report or a genuine hard-stop report.

The CodeRabbit workflow, review requirements, finding disposition, evidence
requirements, remediation limits, and owner merge gate are defined exclusively
in `docs/SECOND_BRAIN_EXECUTION_HANDOFF.md`. Follow that contract. Do not
duplicate, weaken, reorder, bypass, or invent CodeRabbit workflow steps here.

If a boundary, required source, or safety fact cannot be proven, stop and
report that one fact. Do not improvise a topology or workaround.
