#!/usr/bin/env python3
"""Acceptance gate for the Ask My Brain source-reference renderer (Tech-Lead authored, pre-committed).

v1.0 release scope (PM Handoff 4.1) includes "indexing and grounded retrieval with source references",
and the accepted V1 retrieval baseline requires grounded answers with provenance. `ask_my_brain.py`
selects and ranks the evidence, but nothing renders the citation block an answer must carry. This gate
pins that renderer. It is additive: no ranking, scoring or index behaviour is touched, so the frozen
V1 retrieval benchmark is unaffected.

    python3 -B tests/test_source_references.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/ask-my-brain/source_references.py"
if not MODULE.is_file():
    print(f"FAIL: {MODULE.relative_to(ROOT)} does not exist")
    sys.exit(1)
spec = importlib.util.spec_from_file_location("source_references", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
render_source_references = mod.render_source_references
format_source_references = mod.format_source_references

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    if not cond:
        print(f"FAIL: {label}")
        sys.exit(1)
    CHECKS += 1
    print(f"  ok  {label}")


def row(path, title, chunk, snippet, match_mode="all_terms"):
    return {"path": path, "title": title, "chunk_index": chunk, "snippet": snippet, "match_mode": match_mode}


def main() -> int:
    # 1. one reference per distinct note, in first-appearance order
    rows = [
        row("20 Projects/Kitchen.md", "Kitchen", 3, "  the   tiles   arrive   Monday "),
        row("10 Areas/Home.md", "Home", 0, "maintenance list"),
        row("20 Projects/Kitchen.md", "Kitchen", 1, "second chunk of the same note"),
    ]
    refs = render_source_references(rows)
    check(isinstance(refs, list), "render_source_references returns a list")
    check(len(refs) == 2, f"one reference per distinct note (got {len(refs)})")
    check([r["path"] for r in refs] == ["20 Projects/Kitchen.md", "10 Areas/Home.md"],
          f"references keep first-appearance order (got {[r['path'] for r in refs]})")
    check([r["n"] for r in refs] == [1, 2], f"references are numbered from 1 (got {[r['n'] for r in refs]})")

    # 2. every chunk of a note is collected, sorted and deduplicated
    check(refs[0]["chunks"] == [1, 3], f"all chunks of a note are collected and sorted (got {refs[0]['chunks']})")
    check(refs[1]["chunks"] == [0], f"a single-chunk note carries its one chunk (got {refs[1]['chunks']})")
    again = render_source_references(rows + [row("20 Projects/Kitchen.md", "Kitchen", 3, "dupe")])
    check(again[0]["chunks"] == [1, 3], f"a repeated chunk index appears once (got {again[0]['chunks']})")

    # 3. the title comes from the note, and the snippet from its first selected chunk
    check(refs[0]["title"] == "Kitchen", "the reference carries the note title")
    check(refs[0]["snippet"] == "the tiles arrive Monday",
          f"the snippet is the first chunk's text with whitespace collapsed (got {refs[0]['snippet']!r})")

    # 4. a long snippet is bounded
    long_ref = render_source_references([row("a.md", "A", 0, "x " * 400)])[0]
    check(len(long_ref["snippet"]) <= 202, f"a snippet is bounded at 200 characters plus an ellipsis (got {len(long_ref['snippet'])})")
    check(long_ref["snippet"].endswith("…"), "a truncated snippet ends with an ellipsis")

    # 5. rows that cannot be cited are skipped, never guessed at
    check(render_source_references([]) == [], "no evidence yields no references")
    check(render_source_references([{"title": "no path"}]) == [], "a row without a path is skipped")
    untitled = render_source_references([row("10 Areas/x.md", "", 0, "s")])
    check(len(untitled) == 1 and untitled[0]["title"] == "10 Areas/x.md",
          f"a row without a title falls back to its path (got {untitled[0]['title']!r})")

    # 6. the rendered block
    text = format_source_references(refs)
    lines = text.splitlines()
    check(lines[0] == "[1] Kitchen — 20 Projects/Kitchen.md (chunks 1, 3)", f"first line (got {lines[0]!r})")
    check(lines[1] == "[2] Home — 10 Areas/Home.md (chunk 0)", f"a single chunk reads 'chunk', not 'chunks' (got {lines[1]!r})")
    check(len(lines) == 2, f"one line per reference (got {len(lines)})")
    check(format_source_references([]) == "", "no references render as an empty string, never as a heading with nothing under it")

    # 7. the renderer is pure
    snapshot = [dict(r) for r in rows]
    render_source_references(rows)
    check(rows == snapshot, "the input rows are not mutated")

    print(f"PASS source_references: {CHECKS} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
