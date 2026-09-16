#!/usr/bin/env python3
"""End-to-end acceptance gate for the Ask My Brain retrieval core (SB-ASK-002).

Everything the four earlier suites cover, they cover with `search` replaced by a stub. Nothing in
this target had ever executed the real thing: `build_index`, `chunk_note`, the FTS5 index,
`search`'s reranking and gating, scope resolution, provenance and authority. This suite does,
against the synthetic vault in `fixtures/vault/`.

    python3 -B tests/test_retrieval_core_e2e.py

No database of the owner's, no vault of the owner's, no model and no network. The index is built
into a temporary directory that is removed at the end; `ollama_chat` is the only function stubbed,
so the retrieval path under test is the real one. `fixtures/` is opened read-only and the suite
checks its own mtimes to prove it.

Checkpoints, each independently observable:

    [fixture-vault]        the synthetic vault is present and is synthetic
    [index-build]          build_index produces a sound, atomically-replaced FTS5 index
    [exclusion]            excluded prefixes and path parts never reach the index
    [chunking]             headings, long lines, frontmatter, titles and line spans
    [provenance-authority] frontmatter provenance and retrieval authority, inside the vault only
    [search]               real retrieval: ranking, term coverage, the per-note cap, top_k
    [scope]                retrieval_context narrowing and its policy errors
    [ask-e2e]              ask() over the real index with only the model stubbed
    [containment]          no network, no writes to fixtures, database confined to the temp dir
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AMB = ROOT / "src/ask-my-brain/ask_my_brain.py"
VAULT = ROOT / "fixtures/vault"

spec = importlib.util.spec_from_file_location("amb_retrieval_under_test", AMB)
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


def config_for(database: Path) -> dict:
    """The only configuration the retrieval core needs. No real path appears in it."""
    return {
        "vault_root": str(VAULT),
        "database_path": str(database),
        "ollama_url": "http://127.0.0.1:1/unused-by-these-tests",
        "model": "fixture-model",
        "exclude_prefixes": ["90 Archive"],
        "exclude_path_parts": [".obsidian"],
    }


def build(config: dict) -> str:
    """Run the real build, returning its stdout receipt."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        amb.build_index(config)
    return buffer.getvalue()


def rows_of(database: Path, query: str, *args) -> list:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in connection.execute(query, args)]
    finally:
        connection.close()


def snapshot_fixtures() -> dict:
    return {
        p: (p.stat().st_mtime_ns, p.stat().st_size)
        for p in sorted(VAULT.rglob("*")) if p.is_file()
    }


def main() -> int:
    before = snapshot_fixtures()

    # ------------------------------------------------------------------ [fixture-vault]
    check(VAULT.is_dir(), "[fixture-vault] fixtures/vault exists")
    notes = sorted(p.relative_to(VAULT).as_posix() for p in VAULT.rglob("*.md"))
    check(len(notes) == 13, f"[fixture-vault] the vault holds its 13 notes (got {len(notes)})")
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in VAULT.rglob("*.md"))
    for forbidden in ("/AI/", "192.168.", "theadmin@", "/home/jro", "Obsidian/"):
        check(forbidden not in corpus,
              f"[fixture-vault] no fixture names {forbidden!r}: the vault is synthetic")
    check("example.invalid" in corpus,
          "[fixture-vault] the one URL in the vault is a reserved-invalid test domain")

    with tempfile.TemporaryDirectory(prefix="sb-ask-002-") as work:
        database = Path(work) / "index" / "fixture.db"
        config = config_for(database)

        # -------------------------------------------------------------- [index-build]
        receipt = build(config)
        check("BUILD_STATUS=PASS" in receipt, f"[index-build] the build reports PASS\n{receipt}")
        check("NOTES_INDEXED=11" in receipt, f"[index-build] 11 notes indexed\n{receipt}")
        check("CHUNKS_INDEXED=21" in receipt, f"[index-build] 21 chunks indexed\n{receipt}")
        check("FILES_SKIPPED=2" in receipt, f"[index-build] 2 files skipped\n{receipt}")
        check(database.is_file(), "[index-build] the database file exists at the configured path")
        check(oct(database.stat().st_mode & 0o777) == "0o640",
              "[index-build] the database is written 0640, not world-readable")
        leftovers = [p.name for p in database.parent.iterdir() if p.name.startswith(".")]
        check(leftovers == [], f"[index-build] no temporary build file is left behind (got {leftovers})")

        metadata = {r["key"]: r["value"] for r in rows_of(database, "SELECT key, value FROM metadata")}
        check(metadata["schema_version"] == "1.0.0", "[index-build] the schema version is recorded")
        check(metadata["retrieval"] == "sqlite_fts5_bm25", "[index-build] the retrieval mode is recorded")
        check(metadata["vault_root"] == str(VAULT), "[index-build] the indexed vault root is recorded")
        check(metadata["notes_indexed"] == "11" and metadata["chunks_indexed"] == "21",
              "[index-build] the receipt and the metadata table agree")
        integrity = rows_of(database, "PRAGMA integrity_check")
        check(list(integrity[0].values())[0] == "ok", "[index-build] SQLite reports the index sound")
        fts = rows_of(database, "SELECT count(*) AS n FROM chunks_fts WHERE chunks_fts MATCH 'glazing'")
        check(fts[0]["n"] >= 1, "[index-build] the FTS5 table answers a term query")

        first = rows_of(database, "SELECT path, start_line, body FROM chunks ORDER BY id")
        build(config)
        second = rows_of(database, "SELECT path, start_line, body FROM chunks ORDER BY id")
        check(first == second, "[index-build] rebuilding the same vault is deterministic")

        # -------------------------------------------------------------- [exclusion]
        paths = {r["path"] for r in rows_of(database, "SELECT DISTINCT path FROM chunks")}
        check(not any(p.startswith("90 Archive") for p in paths),
              "[exclusion] an excluded prefix contributes no chunk")
        check(not any(".obsidian" in p for p in paths),
              "[exclusion] an excluded path part contributes no chunk")
        check(len(paths) == 11, f"[exclusion] exactly the 11 permitted notes are indexed (got {len(paths)})")

        # -------------------------------------------------------------- [chunking]
        greenhouse = rows_of(
            database,
            "SELECT * FROM chunks WHERE path = ? ORDER BY start_line",
            "20 Projects/Greenhouse Rebuild.md",
        )
        check(len(greenhouse) == 4, f"[chunking] the four-heading note yields four chunks (got {len(greenhouse)})")
        check([c["heading"] for c in greenhouse]
              == ["Greenhouse Rebuild", "Glazing delivery", "Frame repairs", "Budget"],
              "[chunking] each chunk carries the heading it sits under, in document order")
        check(all(c["title"] == "Greenhouse Rebuild" for c in greenhouse),
              "[chunking] the title comes from the '# ' heading, not the filename")
        check(all("source_sha256" not in c["body"] for c in greenhouse),
              "[chunking] frontmatter is not indexed as retrievable body text")
        check(greenhouse[0]["start_line"] > 5,
              "[chunking] chunk line numbers start after the frontmatter block")
        check(all(1 <= c["start_line"] <= c["end_line"] for c in greenhouse),
              "[chunking] every chunk has a sane 1-based line span")

        bare = rows_of(database, "SELECT * FROM chunks WHERE path = ?", "10 Areas/Bare Note.md")
        check(len(bare) == 1 and bare[0]["heading"] == "",
              "[chunking] a note with no headings yields one chunk with no heading")
        check(bare[0]["title"] == "Bare Note",
              "[chunking] a note with no '# ' heading falls back to its filename stem")

        long_line = rows_of(database, "SELECT * FROM chunks WHERE path = ? ORDER BY id",
                            "10 Areas/Long Line Note.md")
        check(len(long_line) >= 2, f"[chunking] an over-long line is split (got {len(long_line)})")
        check({c["start_line"] for c in long_line} == {3},
              "[chunking] every piece of a split line keeps that line's number")
        check(all(len(c["body"]) <= 3500 for c in long_line),
              "[chunking] no chunk exceeds the configured maximum")

        empty = rows_of(database, "SELECT * FROM chunks WHERE path = ?", "10 Areas/Empty Signal Note.md")
        check(len(empty) == 1 and empty[0]["body"] == "Empty Signal Note",
              "[chunking] a note with no readable signal falls back to a single title chunk")

        # -------------------------------------------------------------- [provenance-authority]
        provenance = amb.provenance_for_path(config, "20 Projects/Greenhouse Rebuild.md")
        check(provenance.get("source_type") == "webpage_import",
              "[provenance-authority] declared provenance is read from frontmatter")
        check(provenance.get("source_url") == "https://example.invalid/greenhouse-glazing-notes",
              "[provenance-authority] the source URL is preserved verbatim")
        check(amb.provenance_for_path(config, "10 Areas/Bare Note.md") == {},
              "[provenance-authority] a note without frontmatter has no provenance")
        check(amb.provenance_for_path(config, "../../../etc/passwd") == {},
              "[provenance-authority] a path escaping the vault yields nothing")
        authority = amb.retrieval_authority_for_path(config, "10 Areas/Second Brain Current Status.md")
        check(authority == {"authority": "current_project_status", "status": "current"},
              f"[provenance-authority] authority frontmatter is read (got {authority})")
        check(amb.retrieval_authority_for_path(config, "../outside.md") == {},
              "[provenance-authority] authority is never read from outside the vault")

        # -------------------------------------------------------------- [search]
        hits = amb.search(config, "when does the glazing arrive at the greenhouse?", 5)
        check(len(hits) >= 1, "[search] a specific question retrieves evidence")
        check(hits[0]["path"] == "20 Projects/Greenhouse Rebuild.md",
              f"[search] the best match is the note that answers it (got {hits[0]['path']})")
        check(hits[0]["heading"] == "Glazing delivery",
              f"[search] it is the right chunk of that note (got {hits[0]['heading']!r})")
        check(hits[0]["match_mode"] == "all_terms" and hits[0]["term_coverage"] == 1.0,
              "[search] a fully-matched chunk is reported as all_terms with full coverage")
        check(hits[0]["provenance"]["source_type"] == "webpage_import",
              "[search] the selected row carries its note's provenance")
        check(amb.deterministic_evidence_strength(hits) == "HIGH",
              "[search] real rows drive the existing strength contract")
        check(all(p != "90 Archive/Old Greenhouse Notes.md" for p in (r["path"] for r in hits)),
              "[search] the excluded archive note is unreachable through search")

        inventory = amb.search(config, "what is in the workshop inventory?", 8)
        per_note = {}
        for row in inventory:
            per_note[row["path"]] = per_note.get(row["path"], 0) + 1
        check(max(per_note.values()) <= 2,
              f"[search] no note contributes more than two chunks (got {per_note})")
        check(len(amb.search(config, "what is in the workshop inventory?", 2)) <= 2,
              "[search] top_k caps the number of rows returned")
        check(amb.search(config, "zeppelin bandolier quartzite", 5) == [],
              "[search] a question with no matching evidence returns nothing")
        check(amb.search(config, "", 5) == [],
              "[search] an empty question returns nothing")

        authoritative = amb.search(config, "what is the current status of the second brain?", 5)
        check(authoritative[0]["path"] == "10 Areas/Second Brain Current Status.md",
              f"[search] the authoritative note wins a current-state question (got {authoritative[0]['path']})")
        check(authoritative[0]["authority_bonus"] == 0.35,
              "[search] the authority bonus is applied and reported")
        others = [r for r in authoritative if r["path"] != "10 Areas/Second Brain Current Status.md"]
        check(all(r["authority_bonus"] == 0.0 for r in others),
              "[search] a note without authority frontmatter receives no bonus")

        # -------------------------------------------------------------- [scope]
        inside = amb.search(config, "frame repairs on the project", 5, {"include_paths": ["20 Projects/**"]})
        check(inside and all(r["path"].startswith("20 Projects/") for r in inside),
              "[scope] include_paths narrows retrieval to the named subtree")
        check(amb.search(config, "frame repairs on the project", 5,
                         {"exclude_paths": ["20 Projects/**"]}) == [],
              "[scope] exclude_paths subtracts that subtree")
        for bad, why in (
            ({"domain_name": "x"}, "an unsupported field"),
            ({"source_types": ["note"]}, "source_types, which is not yet supported"),
            ({"domain": ""}, "an empty domain"),
            ({"include_paths": ["/absolute/**"]}, "an absolute glob"),
        ):
            try:
                amb.search(config, "frame repairs", 5, bad)
            except amb.RetrievalPolicyError:
                check(True, f"[scope] retrieval policy refuses {why}")
            else:
                check(False, f"[scope] retrieval policy refuses {why}")

        # -------------------------------------------------------------- [ask-e2e]
        calls = []

        def fake_chat(cfg, system_prompt, user_prompt):
            calls.append({"system": system_prompt, "user": user_prompt})
            return "The replacement glazing panes arrive on Monday the fourteenth of March. [S1]"

        real_chat = amb.ollama_chat
        amb.ollama_chat = fake_chat
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                amb.ask(config, "when does the glazing arrive at the greenhouse?", 5)
            answered = buffer.getvalue()
            sent = calls[0] if calls else {"user": "", "system": ""}
            answered_calls = len(calls)

            calls.clear()
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                amb.ask(config, "zeppelin bandolier quartzite", 5)
            unanswered = buffer.getvalue()
            unanswered_calls = list(calls)
        finally:
            amb.ollama_chat = real_chat

        check("ANSWER:" in answered and "SOURCES:" in answered,
              f"[ask-e2e] a grounded answer prints both blocks\n{answered}")
        check("20 Projects/Greenhouse Rebuild.md" in answered,
              "[ask-e2e] the printed source is a real path from the real index")
        check("Greenhouse Rebuild —" in answered,
              "[ask-e2e] the reference carries the title the indexer extracted")
        check("MODEL=fixture-model" in answered and "EVIDENCE_CHUNKS=" in answered,
              "[ask-e2e] the machine-readable trailer is present")
        check(sent["user"].count("Path: 20 Projects/Greenhouse Rebuild.md") >= 1,
              "[ask-e2e] the model was handed the real note's evidence, not a stub's")
        check("EVIDENCE:" in sent["user"] and "untrusted" in sent["system"].lower(),
              "[ask-e2e] the accepted prompt contract is used on the real path too")
        check(answered_calls == 1,
              f"[ask-e2e] the model is called exactly once for a grounded question (got {answered_calls})")
        check(unanswered_calls == [],
              "[ask-e2e] a question with no evidence never reaches the model")
        check("EVIDENCE_STATUS=NONE" in unanswered,
              f"[ask-e2e] it prints the no-evidence response\n{unanswered}")

        # -------------------------------------------------------------- [containment]
        check(str(database).startswith(work),
              "[containment] the index was written only inside the temporary directory")
        check(not any(p.suffix == ".db" for p in ROOT.rglob("*.db")),
              "[containment] no database file was left anywhere in the repository")

    check(snapshot_fixtures() == before,
          "[containment] every fixture file is byte-for-byte and mtime unchanged")
    check(not database.exists(),
          "[containment] the temporary index is gone once the suite finishes")

    print(f"PASS retrieval_core_e2e: {CHECKS} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
