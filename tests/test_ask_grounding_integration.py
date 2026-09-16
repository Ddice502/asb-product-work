#!/usr/bin/env python3
"""Acceptance gate for wiring grounding and weak-evidence suppression into `ask` (pre-committed).

Owner decision 2026-09-16, taking the two protected decisions the previous batch recorded:
call the source-reference renderer from ask_my_brain.py; put Sources after grounded answers;
suppress the generated answer when the evidence is LOW or unrecognised; and show any references in
that case as Related notes.

This is the first change to accepted V1 retrieval behaviour. It is presentation and suppression
only: no ranking, scoring, chunking, indexing or model-selection behaviour may change, so the frozen
V1 retrieval benchmark stays intact. The gate holds that line explicitly.

No database, no network, no model: `search` and `ollama_chat` are replaced with stubs.

    python3 -B tests/test_ask_grounding_integration.py
"""
from __future__ import annotations

import importlib.util
import io
import contextlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AMB = ROOT / "src/ask-my-brain/ask_my_brain.py"
spec = importlib.util.spec_from_file_location("amb_under_test", AMB)
amb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(amb)

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    if not cond:
        print(f"FAIL: {label}")
        sys.exit(1)
    CHECKS += 1
    print(f"  ok  {label}")


def row(path, title, chunk, snippet, match_mode, start=1, end=9, provenance=None):
    """A faithful search result row.

    `search()` returns `chunks` table rows, so the fixture must carry every column the existing
    evidence block in `ask()` reads — id, path, title, heading, body, start_line, end_line — as well
    as the keys the merged renderer reads (chunk_index, snippet). A fixture that omits any of them
    makes the gate unsatisfiable for an implementation that correctly preserves the existing code."""
    r = {"id": 1000 + chunk, "path": path, "title": title, "heading": title, "body": snippet,
         "chunk_index": chunk, "snippet": snippet, "match_mode": match_mode,
         "start_line": start, "end_line": end}
    if provenance:
        r.update(provenance)
    return r


HIGH_ROWS = [row("20 Projects/Kitchen.md", "Kitchen", 3, "the tiles arrive Monday", "all_terms")]
LOW_ROWS = [row("10 Areas/Home.md", "Home", 0, "a partial mention", "partial_terms")]
CORROBORATED = [row("10 Areas/Home.md", "Home", 0, "one", "partial_terms"),
                row("20 Projects/Kitchen.md", "Kitchen", 1, "two", "partial_terms")]


def run_ask(rows, answer="The tiles arrive on Monday. [S1]"):
    """Call ask() with search and the model replaced; return (stdout, model_calls)."""
    calls = []

    def fake_search(config, question, top_k):
        return list(rows)

    def fake_chat(config, system_prompt, user_prompt):
        calls.append({"system": system_prompt, "user": user_prompt})
        return answer

    real_search, real_chat = amb.search, amb.ollama_chat
    amb.search, amb.ollama_chat = fake_search, fake_chat
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            amb.ask({"model": "qwen2.5:7b"}, "when do the tiles arrive?", 5)
    finally:
        amb.search, amb.ollama_chat = real_search, real_chat
    return buf.getvalue(), calls


def main() -> int:
    # 1. the renderer is actually called from this module, not reimplemented inside it
    check(hasattr(amb, "render_source_references"), "ask_my_brain imports render_source_references")
    check(hasattr(amb, "format_source_references"), "ask_my_brain imports format_source_references")
    check(hasattr(amb, "evidence_verdict"), "ask_my_brain imports evidence_verdict")
    src = AMB.read_text(encoding="utf-8")
    check("source_references" in src and "weak_evidence" in src, "both modules are imported by name")
    check(src.count("def render_source_references") == 0, "the renderer is imported, not copied into ask_my_brain.py")

    # 2. a grounded answer: the model runs, the answer prints, Sources follow it
    out, calls = run_ask(HIGH_ROWS)
    check(len(calls) == 1, f"the model is called once for sufficient evidence (got {len(calls)})")
    check("ANSWER:" in out, "the answer block is printed")
    check("The tiles arrive on Monday" in out, "the generated answer is printed")
    check("SOURCES:" in out, "a SOURCES section is printed")
    check(out.index("ANSWER:") < out.index("SOURCES:"), "Sources come AFTER the grounded answer")
    check("[1] Kitchen — 20 Projects/Kitchen.md (chunk 3)" in out,
          f"the SOURCES block uses the renderer's format\n--- got ---\n{out}")
    check("MODEL=qwen2.5:7b" in out and "EVIDENCE_CHUNKS=1" in out,
          "the existing machine-readable trailer is preserved unchanged")
    check("Related notes" not in out, "a grounded answer has Sources, not Related notes")
    # the accepted prompt contract must survive the rewiring
    sysp, userp = calls[0]["system"], calls[0]["user"]
    check("untrusted" in sysp.lower(), "the system prompt still tells the model the evidence is untrusted data")
    check("Evidence strength:" in sysp, "the system prompt still demands the evidence-strength line")
    check("cite" in sysp.lower() or "citation" in sysp.lower(), "the system prompt still demands citations")
    check("QUESTION:" in userp and "EVIDENCE:" in userp and "when do the tiles arrive?" in userp,
          "the user prompt still carries the question and the evidence block")

    # 3. LOW evidence from a single note: the answer is suppressed, the model never runs
    out, calls = run_ask(LOW_ROWS)
    check(calls == [], "the model is NOT called when the evidence is too weak")
    check("The tiles arrive on Monday" not in out, "no generated answer text appears")
    check("Related notes:" in out, "the references are shown as Related notes")
    check("[1] Home — 10 Areas/Home.md (chunk 0)" in out, f"the Related notes use the renderer\n--- got ---\n{out}")
    check("SOURCES:" not in out, "a suppressed answer has Related notes, not Sources")
    check("ANSWER_SUPPRESSED=1" in out, "the suppression is machine-readable")
    check("EVIDENCE_STATUS=WEAK" in out, "weak evidence is reported as WEAK")
    check("MODEL=" not in out, "no model line is printed when no model ran")
    check("evidence" in out.lower(), "the owner is told why there is no answer")

    # 4. LOW but corroborated across two notes: still answered
    out, calls = run_ask(CORROBORATED)
    check(len(calls) == 1, "LOW evidence corroborated by two notes is still answered")
    check("SOURCES:" in out and "[2] Kitchen" in out, "both notes are cited")

    # 5. no evidence at all: unchanged refusal contract
    out, calls = run_ask([])
    check(calls == [], "the model is not called with no evidence")
    check("EVIDENCE_STATUS=NONE" in out, "the existing no-evidence contract is preserved")
    check("Related notes" not in out, "there are no references to relate to")

    # 6. accepted V1 retrieval behaviour is untouched
    check(amb.deterministic_evidence_strength(HIGH_ROWS) == "HIGH", "HIGH strength is unchanged")
    check(amb.deterministic_evidence_strength(LOW_ROWS) == "LOW", "LOW strength is unchanged")
    check(amb.deterministic_evidence_strength([]) == "LOW", "the empty case is unchanged")
    for name in ("search", "execute_match", "query_terms", "build_index", "chunk_note", "fts_token",
                 "resolve_scope", "provenance_for_path", "retrieval_authority_for_path"):
        check(hasattr(amb, name), f"{name} still exists: no accepted retrieval function was removed")

    print(f"PASS ask_grounding_integration: {CHECKS} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
