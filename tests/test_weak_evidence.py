#!/usr/bin/env python3
"""Acceptance gate for Ask My Brain weak-evidence handling (Tech-Lead authored, pre-committed).

The accepted V1 retrieval baseline requires weak-evidence handling, and the roadmap names
insufficient-evidence behaviour as a retrieval property. `ask_my_brain.py` already computes a
deterministic HIGH/MEDIUM/LOW strength, but nothing decides what the system should *say* when the
evidence does not support an answer. This gate pins that decision as one pure function.

It is additive: no ranking, scoring or index behaviour is touched, and the existing strength
computation is an input, not a thing this card changes. The frozen V1 retrieval benchmark is
unaffected.

    python3 -B tests/test_weak_evidence.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/ask-my-brain/weak_evidence.py"
if not MODULE.is_file():
    print(f"FAIL: {MODULE.relative_to(ROOT)} does not exist")
    sys.exit(1)
spec = importlib.util.spec_from_file_location("weak_evidence", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
evidence_verdict = mod.evidence_verdict

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    if not cond:
        print(f"FAIL: {label}")
        sys.exit(1)
    CHECKS += 1
    print(f"  ok  {label}")


def rows(*paths):
    return [{"path": p, "chunk_index": 0, "match_mode": "partial_terms"} for p in paths]


def main() -> int:
    # 1. shape
    v = evidence_verdict([], "LOW")
    check(isinstance(v, dict), "evidence_verdict returns a dict")
    for key in ("sufficient", "reason", "message"):
        check(key in v, f"the verdict carries {key!r}")

    # 2. no evidence: never answer
    check(v["sufficient"] is False, "no evidence is never sufficient")
    check(v["reason"] == "no_evidence", f"the reason is machine-readable (got {v['reason']!r})")
    check(v["message"].strip() != "", "an insufficient verdict carries a message for the owner")
    check("evidence" in v["message"].lower(), f"the message says the problem is evidence (got {v['message']!r})")

    # 3. LOW strength from a single note is not enough to answer
    one = evidence_verdict(rows("10 Areas/a.md"), "LOW")
    check(one["sufficient"] is False, "LOW strength from one note is not sufficient")
    check(one["reason"] == "weak_evidence", f"the reason distinguishes weak from absent (got {one['reason']!r})")
    check(one["message"].strip() != "", "a weak verdict carries a message")

    # 4. LOW strength corroborated across notes is enough
    two = evidence_verdict(rows("10 Areas/a.md", "20 Projects/b.md"), "LOW")
    check(two["sufficient"] is True, "LOW strength corroborated by two distinct notes is sufficient")
    check(two["reason"] == "low_but_corroborated", f"the reason records why (got {two['reason']!r})")
    check(two["message"] == "", "a sufficient verdict carries no refusal message")
    same = evidence_verdict(rows("10 Areas/a.md", "10 Areas/a.md"), "LOW")
    check(same["sufficient"] is False, "two chunks of the SAME note are not two notes")

    # 5. MEDIUM and HIGH are sufficient
    for strength in ("MEDIUM", "HIGH"):
        got = evidence_verdict(rows("10 Areas/a.md"), strength)
        check(got["sufficient"] is True and got["reason"] == "sufficient" and got["message"] == "",
              f"{strength} strength is sufficient (got {got})")

    # 6. an unknown or missing strength fails closed, as LOW
    for strength in ("", None, "probably fine", "low"):
        got = evidence_verdict(rows("10 Areas/a.md"), strength)
        check(got["sufficient"] is False, f"an unrecognised strength {strength!r} fails closed")
    check(evidence_verdict(rows("a.md", "b.md"), None)["sufficient"] is True,
          "failing closed still honours corroboration: unknown strength behaves exactly as LOW")

    # 7. rows without a path cannot corroborate anything
    check(evidence_verdict([{"chunk_index": 0}, {"chunk_index": 1}], "LOW")["sufficient"] is False,
          "rows with no path do not count as notes")

    # 8. the function is pure
    data = rows("a.md", "b.md")
    snapshot = [dict(r) for r in data]
    evidence_verdict(data, "LOW")
    check(data == snapshot, "the input rows are not mutated")

    print(f"PASS weak_evidence: {CHECKS} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
