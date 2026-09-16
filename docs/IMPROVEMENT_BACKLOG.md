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