#!/usr/bin/env python3
"""Acceptance gate for the KI-154 search-row adapter (Tech-Lead authored, pre-committed).

`search()` in ask_my_brain.py returns `chunks` table rows plus retrieval metadata:
id, path, title, heading, body, start_line, end_line, file_sha256, file_mtime_ns, file_size,
match_mode, matched_terms, term_coverage, retrieval_quality, authority_bonus, score, provenance.
The merged renderer reads `chunk_index` and `snippet`, which those rows do not carry. This gate pins a
pure adapter that gives the renderer the fields it needs without changing anything about retrieval.

    python3 -B tests/test_search_row_adapter.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/ask-my-brain/search_row_adapter.py"
if not MODULE.is_file():
    print(f"FAIL: {MODULE.relative_to(ROOT)} does not exist")
    sys.exit(1)
spec = importlib.util.spec_from_file_location("search_row_adapter", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
adapt_search_rows = mod.adapt_search_rows

REND = ROOT / "src/ask-my-brain/source_references.py"
rspec = importlib.util.spec_from_file_location("source_references", REND)
rend = importlib.util.module_from_spec(rspec)
rspec.loader.exec_module(rend)

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    if not cond:
        print(f"FAIL: {label}")
        sys.exit(1)
    CHECKS += 1
    print(f"  ok  {label}")


def real_row(rid, path, title, heading, body, start, end, match_mode="all_terms"):
    """Exactly the shape search() produces: every chunks column plus the retrieval metadata."""
    return {"id": rid, "path": path, "title": title, "heading": heading, "body": body,
            "start_line": start, "end_line": end, "file_sha256": "0" * 64, "file_mtime_ns": 1, "file_size": 10,
            "match_mode": match_mode, "matched_terms": ["tiles"], "term_coverage": 1.0,
            "retrieval_quality": 0.9, "authority_bonus": 0.0, "score": 1.5, "provenance": {"kind": "note"}}


def main() -> int:
    rows = [
        real_row(7, "20 Projects/Kitchen.md", "Kitchen", "Delivery", "  the  tiles arrive Monday ", 40, 52),
        real_row(3, "20 Projects/Kitchen.md", "Kitchen", "Budget", "budget is fixed", 12, 20),
        real_row(9, "10 Areas/Home.md", "Home", "", "maintenance list", 1, 8, "partial_terms"),
    ]
    out = adapt_search_rows(rows)

    # 1. shape and order
    check(isinstance(out, list) and len(out) == 3, f"one adapted row per search row, in order (got {len(out) if isinstance(out, list) else type(out)})")
    check([r["path"] for r in out] == [r["path"] for r in rows], "order is preserved")

    # 2. the renderer's keys are supplied from the real columns
    check(out[0]["snippet"] == "  the  tiles arrive Monday ", f"snippet is the chunk body, untouched (got {out[0]['snippet']!r})")
    check(out[0]["chunk_index"] == 40, f"chunk_index is the chunk's start_line (got {out[0]['chunk_index']})")
    check(out[1]["chunk_index"] == 12 and out[2]["chunk_index"] == 1, "every row gets its own start_line as chunk_index")

    # 3. nothing the existing code needs is lost: the adapted row is a superset of the real row
    for key in ("id", "path", "title", "heading", "body", "start_line", "end_line", "match_mode",
                "matched_terms", "term_coverage", "retrieval_quality", "authority_bonus", "score", "provenance"):
        check(out[0][key] == rows[0][key], f"the real column {key!r} is carried through unchanged")

    # 4. the adapter never mutates its input
    check("snippet" not in rows[0] and "chunk_index" not in rows[0], "the input rows are not mutated")

    # 5. degenerate inputs
    check(adapt_search_rows([]) == [], "no rows adapt to no rows")
    partial = adapt_search_rows([{"path": "x.md", "body": "b"}])
    check(partial[0]["snippet"] == "b" and partial[0]["chunk_index"] == 0, "a row missing start_line gets chunk_index 0, not an exception")
    nobody = adapt_search_rows([{"path": "x.md", "start_line": 5}])
    check(nobody[0]["snippet"] == "" and nobody[0]["chunk_index"] == 5, "a row missing body gets an empty snippet")

    # 6. the adapted rows drive the merged renderer correctly: this is the point of the card
    refs = rend.render_source_references(out)
    check(len(refs) == 2, f"the renderer sees two distinct notes (got {len(refs)})")
    check(refs[0]["chunks"] == [12, 40], f"a note's chunks are its start_lines, sorted (got {refs[0]['chunks']})")
    check(refs[0]["snippet"] == "the tiles arrive Monday", f"the first selected chunk's body becomes the snippet (got {refs[0]['snippet']!r})")
    text = rend.format_source_references(refs)
    check(text.splitlines()[0] == "[1] Kitchen — 20 Projects/Kitchen.md (chunks 12, 40)", f"the rendered line is complete (got {text.splitlines()[0]!r})")

    # 7. the adapter is pure and standalone
    src = MODULE.read_text(encoding="utf-8")
    for forbidden in ("import sqlite3", "import urllib", "import requests", "import ask_my_brain", "/AI/"):
        check(forbidden not in src, f"the adapter does not contain {forbidden!r}")

    print(f"PASS search_row_adapter: {CHECKS} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
