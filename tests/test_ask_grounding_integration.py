#!/usr/bin/env python3
"""Acceptance gate for wiring grounding and weak-evidence suppression into `ask` — revision 2.

Owner decision 2026-09-17: the requirements are unchanged from revision 1 (every one of its 43 checks
is retained, label for label). What changed is OBSERVABILITY. Revision 1 ran 38 of its checks behind a
single `ask()` call, so any error anywhere reported "stopped at check 5" and the pipeline's progress
measure had nothing to read (KI-155). Revision 2 exposes the same requirements as a staircase of named
checkpoints, each independently observable, so a candidate that fixes one defect visibly advances:

    [retrieval-unchanged]       accepted V1 retrieval is untouched (needs nothing new to run)
    [imports]                   the two merged modules and the KI-154 adapter are imported
    [row-adaptation]            real search() rows are adapted before rendering
    [reference-rendering]       the renderer's block is produced from real rows
    [sufficient-composition]    the composed output for sufficient evidence
    [insufficient-composition]  the composed output for weak / absent evidence
    [model-suppression]         ask() never calls the model when the verdict is insufficient
    [ask-end-to-end]            the full ask() path, prompt contract included

Fixture rows are the REAL shape `search()` returns — chunks columns plus retrieval metadata, and
neither `chunk_index` nor `snippet` — so the adaptation must actually happen.

No database, no network, no model: `search` and `ollama_chat` are replaced with stubs.

    python3 -B tests/test_ask_grounding_integration.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
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


def real_row(rid, path, title, heading, body, start, end, match_mode):
    """Exactly what search() returns: every chunks column plus retrieval metadata. No renderer keys."""
    return {"id": rid, "path": path, "title": title, "heading": heading, "body": body,
            "start_line": start, "end_line": end, "file_sha256": "0" * 64, "file_mtime_ns": 1, "file_size": 10,
            "match_mode": match_mode, "matched_terms": ["tiles"], "term_coverage": 1.0,
            "retrieval_quality": 0.9, "authority_bonus": 0.0, "score": 1.5}


HIGH_ROWS = [real_row(7, "20 Projects/Kitchen.md", "Kitchen", "Delivery", "the tiles arrive Monday", 40, 52, "all_terms")]
LOW_ROWS = [real_row(9, "10 Areas/Home.md", "Home", "", "a partial mention", 3, 9, "partial_terms")]
CORROBORATED = [real_row(9, "10 Areas/Home.md", "Home", "", "one", 3, 9, "partial_terms"),
                real_row(7, "20 Projects/Kitchen.md", "Kitchen", "Delivery", "two", 40, 52, "partial_terms")]
ANSWER = "The tiles arrive on Monday. [S1]"


def run_ask(rows, answer=ANSWER):
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


def compose(rows, answer_text, model="qwen2.5:7b"):
    """The pure composition seam, called directly so its behaviour is observable before ask() is."""
    out = amb.render_ask_output(rows, answer_text, model)
    check(isinstance(out, str), "[sufficient-composition] render_ask_output returns a string")
    return out


def main() -> int:
    # ------------------------------------------------------------------ [retrieval-unchanged]
    # Needs nothing new: observable on the base, on every intermediate, and on the final candidate.
    check(amb.deterministic_evidence_strength(HIGH_ROWS) == "HIGH", "HIGH strength is unchanged")
    check(amb.deterministic_evidence_strength(LOW_ROWS) == "LOW", "LOW strength is unchanged")
    check(amb.deterministic_evidence_strength([]) == "LOW", "the empty case is unchanged")
    for name in ("search", "execute_match", "query_terms", "build_index", "chunk_note", "fts_token",
                 "resolve_scope", "provenance_for_path", "retrieval_authority_for_path"):
        check(hasattr(amb, name), f"{name} still exists: no accepted retrieval function was removed")

    # ------------------------------------------------------------------ [imports]
    check(hasattr(amb, "render_source_references"), "ask_my_brain imports render_source_references")
    check(hasattr(amb, "format_source_references"), "ask_my_brain imports format_source_references")
    check(hasattr(amb, "evidence_verdict"), "ask_my_brain imports evidence_verdict")
    check(hasattr(amb, "adapt_search_rows"), "[imports] ask_my_brain imports adapt_search_rows (KI-154)")
    src = AMB.read_text(encoding="utf-8")
    check("source_references" in src and "weak_evidence" in src, "both modules are imported by name")
    check("search_row_adapter" in src, "[imports] the adapter module is imported by name")
    check(src.count("def render_source_references") == 0, "the renderer is imported, not copied into ask_my_brain.py")
    check(src.count("def adapt_search_rows") == 0, "[imports] the adapter is imported, not copied")
    check(callable(getattr(amb, "render_ask_output", None)), "[imports] render_ask_output(source_rows, answer_text, model) exists")

    # ------------------------------------------------------------------ [row-adaptation]
    # Real rows have no chunk_index / snippet. The composed output can only show start_line-based
    # chunks and body snippets if the adapter was applied.
    out = compose(HIGH_ROWS, ANSWER)
    check("(chunk 40)" in out, f"[row-adaptation] the chunk shown is the row's start_line, so adapt_search_rows was applied\n--- got ---\n{out}")
    check("KeyError" not in out and "chunk_index" not in out, "[row-adaptation] no raw key or error leaks into the output")
    check("snippet" not in out or "the tiles arrive Monday" in out, "[row-adaptation] the snippet comes from body")

    # ------------------------------------------------------------------ [reference-rendering]
    check("[1] Kitchen — 20 Projects/Kitchen.md (chunk 40)" in out,
          f"the SOURCES block uses the renderer's format\n--- got ---\n{out}")
    check("{" not in out.split("SOURCES:", 1)[-1].split("MODEL=", 1)[0],
          "[reference-rendering] the block is formatted lines, never a printed dict")

    # ------------------------------------------------------------------ [sufficient-composition]
    check("ANSWER:" in out, "the answer block is printed")
    check("The tiles arrive on Monday" in out, "the generated answer is printed")
    check("SOURCES:" in out, "a SOURCES section is printed")
    check(out.index("ANSWER:") < out.index("SOURCES:"), "Sources come AFTER the grounded answer")
    check("MODEL=qwen2.5:7b" in out and "EVIDENCE_CHUNKS=1" in out,
          "the existing machine-readable trailer is preserved unchanged")
    check("Related notes" not in out, "a grounded answer has Sources, not Related notes")
    check(out.index("SOURCES:") < out.index("MODEL="), "[sufficient-composition] the trailer follows the Sources block")
    two = compose(CORROBORATED, ANSWER)
    check("SOURCES:" in two and "[2] Kitchen" in two, "both notes are cited")

    # ------------------------------------------------------------------ [insufficient-composition]
    low = compose(LOW_ROWS, "")
    check("Related notes:" in low, "the references are shown as Related notes")
    check("[1] Home — 10 Areas/Home.md (chunk 3)" in low, f"the Related notes use the renderer\n--- got ---\n{low}")
    check("SOURCES:" not in low, "a suppressed answer has Related notes, not Sources")
    check("ANSWER_SUPPRESSED=1" in low, "the suppression is machine-readable")
    check("EVIDENCE_STATUS=WEAK" in low, "weak evidence is reported as WEAK")
    check("MODEL=" not in low, "no model line is printed when no model ran")
    check("evidence" in low.lower(), "the owner is told why there is no answer")
    check("The tiles arrive on Monday" not in low, "no generated answer text appears")
    none = compose([], "")
    check("EVIDENCE_STATUS=NONE" in none, "the existing no-evidence contract is preserved")
    check("Related notes" not in none, "there are no references to relate to")

    # ------------------------------------------------------------------ [model-suppression]
    out, calls = run_ask(LOW_ROWS)
    check(calls == [], "the model is NOT called when the evidence is too weak")
    out, calls = run_ask([])
    check(calls == [], "the model is not called with no evidence")
    check("EVIDENCE_STATUS=NONE" in out, "[model-suppression] ask() prints the composed no-evidence output")

    # ------------------------------------------------------------------ [ask-end-to-end]
    out, calls = run_ask(HIGH_ROWS)
    check(len(calls) == 1, f"the model is called once for sufficient evidence (got {len(calls)})")
    check("[1] Kitchen — 20 Projects/Kitchen.md (chunk 40)" in out and "The tiles arrive on Monday" in out,
          "[ask-end-to-end] ask() prints the composed sufficient output")
    sysp, userp = calls[0]["system"], calls[0]["user"]
    check("untrusted" in sysp.lower(), "the system prompt still tells the model the evidence is untrusted data")
    check("Evidence strength:" in sysp, "the system prompt still demands the evidence-strength line")
    check("cite" in sysp.lower() or "citation" in sysp.lower(), "the system prompt still demands citations")
    check("QUESTION:" in userp and "EVIDENCE:" in userp and "when do the tiles arrive?" in userp,
          "the user prompt still carries the question and the evidence block")
    out, calls = run_ask(CORROBORATED)
    check(len(calls) == 1, "LOW evidence corroborated by two notes is still answered")
    out, calls = run_ask(LOW_ROWS)
    check("Related notes:" in out and "ANSWER_SUPPRESSED=1" in out, "[ask-end-to-end] ask() prints the composed suppressed output")

    print(f"PASS ask_grounding_integration: {CHECKS} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
