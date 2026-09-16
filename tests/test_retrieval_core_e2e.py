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
    [defect-pins]          today's DEFECTIVE behaviour, pinned so a fix cannot pass unnoticed
    [containment]          no network, no writes to fixtures, database confined to the temp dir

READ THIS BEFORE TRUSTING [defect-pins]. Every assertion in that one checkpoint states what the
retrieval core does TODAY and is WRONG. None of them is a requirement, and none may be cited as
one. They exist because SB-ASK-002 found four real defects it was not authorised to fix, and an
unpinned defect can be fixed or worsened silently. When a defect is repaired, its pin FAILS: that
failure is the signal to delete the pin, not to revert the fix. Every other checkpoint in this file
asserts behaviour that is correct and must stay correct.

Two of those four are now repaired and their pins replaced by assertions of the CORRECT behaviour:

    D4  a chunk span covers its text, not the blank lines around it   -> [chunking]  (SB-ASK-005)
    D1  markup is stripped outside fenced blocks and only there       -> [chunking]  (SB-ASK-006)

D2 (accented content is indexed but unreachable through search) and D3 (a question made only of
stopwords is answered at HIGH) remain OPEN and remain pinned. D2 needs the reranker to stop
re-deriving matched terms by string comparison; D3 cannot be fixed by editing query_terms at all,
because deterministic_evidence_strength returns HIGH for any all_terms match however few or generic
the terms are, so every change to the term set moves strength as a side effect. Do not read those
two pins as requirements.
"""
from __future__ import annotations

import atexit
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
# Deliberately OUTSIDE the vault: the escape target the containment checks need.
OUTSIDE_NOTE = ROOT / "fixtures" / "outside-the-vault.md"

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


CONNECTED: list = []

# Captured before any spy is installed. Comparing amb.sqlite3.connect against sqlite3.connect
# would be a tautology: amb.sqlite3 IS the sqlite3 module, so both sides move together.
REAL_SQLITE_CONNECT = sqlite3.connect


def build(config: dict) -> str:
    """Run the real build, returning its stdout receipt."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        amb.build_index(config)
    return buffer.getvalue()


def watch_sqlite():
    """Record every database path the code under test opens, so containment is observed rather
    than assumed. Asserting on a path this test itself constructed would prove nothing.

    Returns a restore callable."""
    real_connect = REAL_SQLITE_CONNECT

    def spy(target, *args, **kwargs):
        CONNECTED.append(str(target))
        return real_connect(target, *args, **kwargs)

    amb.sqlite3.connect = spy

    def restore():
        amb.sqlite3.connect = real_connect

    return restore


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
    check(len(notes) == 16, f"[fixture-vault] the vault holds its 16 notes (got {len(notes)})")
    check(OUTSIDE_NOTE.is_file(),
          "[fixture-vault] the deliberate out-of-vault note is present and scanned below too")
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in
                       list(VAULT.rglob("*.md")) + [OUTSIDE_NOTE])
    for forbidden in ("/AI/", "192.168.", "theadmin@", "/home/jro", "Obsidian/"):
        check(forbidden not in corpus,
              f"[fixture-vault] no fixture names {forbidden!r}: the vault is synthetic")
    import re as _re
    urls = _re.findall(r"https?://[^\s)'\"]+", corpus)
    check(urls, "[fixture-vault] the vault does contain the URL this check is about")
    check(all("example.invalid" in u for u in urls),
          f"[fixture-vault] every URL in the vault is on the reserved-invalid test domain (got {urls})")

    with tempfile.TemporaryDirectory(prefix="sb-ask-002-") as work:
        database = Path(work) / "index" / "fixture.db"
        config = config_for(database)
        restore_sqlite = watch_sqlite()
        atexit.register(restore_sqlite)  # check() exits on first failure; never leak the spy

        # -------------------------------------------------------------- [index-build]
        receipt = build(config)
        check("BUILD_STATUS=PASS" in receipt, f"[index-build] the build reports PASS\n{receipt}")
        check("NOTES_INDEXED=14" in receipt, f"[index-build] 14 notes indexed\n{receipt}")
        check("CHUNKS_INDEXED=24" in receipt, f"[index-build] 24 chunks indexed\n{receipt}")
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
        check(metadata["notes_indexed"] == "14" and metadata["chunks_indexed"] == "24",
              "[index-build] the receipt and the metadata table agree")
        integrity = rows_of(database, "PRAGMA integrity_check")
        check(list(integrity[0].values())[0] == "ok", "[index-build] SQLite reports the index sound")
        fts = rows_of(database, "SELECT count(*) AS n FROM chunks_fts WHERE chunks_fts MATCH 'glazing'")
        check(fts[0]["n"] >= 1, "[index-build] the FTS5 table answers a term query")
        accented = rows_of(database,
                           "SELECT count(*) AS n FROM chunks_fts WHERE chunks_fts MATCH 'café'")
        check(accented[0]["n"] >= 1,
              "[index-build] the index matches an accented term directly (the retrieval half that "
              "works; reaching it through search() is defect D2)")
        folded = rows_of(database, "SELECT count(*) AS n FROM chunks_fts WHERE chunks_fts MATCH 'cafe'")
        check(folded[0]["n"] >= 1,
              "[index-build] the tokenizer folds diacritics: the unaccented 'cafe' finds the "
              "accented text. Note this is what unicode61 does by default (remove_diacritics 1); "
              "the configured '2' is not what earns it for these characters")

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
        check(len(paths) == 14, f"[exclusion] exactly the 14 permitted notes are indexed (got {len(paths)})")

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
        saw_care = rows_of(database, "SELECT * FROM chunks WHERE path = ?", "10 Areas/Saw Care.md")
        check(len(saw_care) == 1, "[chunking] the renamed note yields one chunk")
        check(saw_care[0]["title"] == "Tool Maintenance",
              f"[chunking] the title comes from the '# ' heading (got {saw_care[0]['title']!r}), "
              "and this fixture's heading deliberately differs from its filename stem 'Saw Care'")
        check(all(c["title"] == "Greenhouse Rebuild" for c in greenhouse),
              "[chunking] every chunk of a note carries that note's title")
        check(all("source_sha256" not in c["body"] for c in greenhouse),
              "[chunking] frontmatter is not indexed as retrievable body text")
        check(greenhouse[0]["start_line"] > 5,
              "[chunking] chunk line numbers start after the frontmatter block")
        check(all(1 <= c["start_line"] <= c["end_line"] for c in greenhouse),
              "[chunking] every chunk has a sane 1-based line span")
        source_lines = (VAULT / "20 Projects/Greenhouse Rebuild.md").read_text(encoding="utf-8").splitlines()
        check(any(c["end_line"] > c["start_line"] for c in greenhouse),
              "[chunking] a multi-line chunk records a span, not a collapsed single line")

        # D4, repaired by SB-ASK-005: a span covers its text and not the blank lines either side.
        # Pinning the exact spans also catches an off-by-one in start_line or end_line.
        spans = [(c["start_line"], c["end_line"]) for c in greenhouse]
        check(spans == [(10, 10), (14, 15), (19, 20), (24, 24)],
              f"[chunking] chunk spans are exact and exclude the blank lines around the text "
              f"(got {spans})")
        for start, end in spans:
            check(source_lines[start - 1].strip() != "" and source_lines[end - 1].strip() != "",
                  f"[chunking] span {start}-{end} both opens and closes on a line carrying text")
        for chunk in greenhouse:
            span = "\n".join(source_lines[chunk["start_line"] - 1:chunk["end_line"]])
            body_first = chunk["body"].splitlines()[0].strip()
            check(body_first and body_first in span,
                  f"[chunking] the span {chunk['start_line']}-{chunk['end_line']} actually contains "
                  f"the chunk's own first line of text")
            check(chunk["end_line"] <= len(source_lines),
                  "[chunking] end_line stays inside the file")

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
        check({c["end_line"] for c in long_line} == {3},
              f"[chunking] and ends on it too, so a split line reports a single-line span "
              f"(got {[(c['start_line'], c['end_line']) for c in long_line]})")
        maximum = int(config.get("max_chunk_chars", 3500))
        check(all(len(c["body"]) <= maximum for c in long_line),
              f"[chunking] no chunk exceeds the configured maximum of {maximum}")

        # D1, repaired by SB-ASK-006. Outside a fenced block, comments and rules are stripped.
        comment_body = rows_of(database, "SELECT body FROM chunks WHERE path = ?",
                               "10 Areas/Comment Marker Note.md")[0]["body"]
        check("<!--" not in comment_body and "pipeline marker" not in comment_body,
              f"[chunking] an HTML comment is stripped from indexed evidence (got {comment_body!r})")
        check("The first real sentence" in comment_body and "The second real sentence" in comment_body,
              "[chunking] while the sentences either side of it are kept")
        # Two comments on ONE line: a greedy stripper matches from the first '<!--' to the last
        # '-->' and eats the text between them.
        check("BRIDGE_TEXT" in comment_body,
              f"[chunking] text between two comments on one line is kept: the stripper is not "
              f"greedy (got {comment_body!r})")
        check("Alpha BRIDGE_TEXT omega" in comment_body,
              "[chunking] and the text either side of them on that line is kept too")
        # A comment that opens on one line and closes several lines later.
        check("runs across several lines" not in comment_body,
              "[chunking] a comment spanning several lines is removed in full")
        check("The third real sentence" in comment_body,
              "[chunking] and the sentence after it survives")
        check("\n\n\n" not in comment_body,
              f"[chunking] a run of blank lines is collapsed to one (got {comment_body!r})")
        check("The fourth real sentence" in comment_body,
              "[chunking] and the sentence after that run survives")
        # A comment is replaced by a SPACE, not by nothing, or the words either side fuse.
        check("FUSEDLEFT FUSEDRIGHT" in comment_body,
              f"[chunking] a comment between two words leaves them separated (got {comment_body!r})")
        # Text after a closing marker on the SAME line is evidence and must be kept.
        check("TAILAFTERCLOSE" in comment_body,
              "[chunking] text following a closing marker on its own line is kept")
        # The rule pattern needs three or more marks; two dashes are ordinary text.
        check("\n--\n" in comment_body,
              f"[chunking] a two-character dash line is NOT a rule and survives (got {comment_body!r})")

        # A comment must never consume text it does not enclose. The fence test has to be
        # resolved AFTER an open comment, or a delimiter inside the comment steals its closing
        # marker: either the commented-out text leaks into the evidence, or the rest of the note
        # is swallowed. Both happened in the first candidate of this package.
        fence_comment = rows_of(database, "SELECT * FROM chunks WHERE path = ? ORDER BY start_line",
                                "10 Areas/Comment Fence Note.md")
        check(len(fence_comment) == 1,
              f"[chunking] the comment-fence note is one chunk (got {len(fence_comment)})")
        fc_body = fence_comment[0]["body"]
        check("SECRETMARKER" not in fc_body,
              f"[chunking] a comment containing a fence delimiter is still removed in full, so its "
              f"text never reaches the model (got {fc_body!r})")
        check("Prose after the comment" in fc_body,
              "[chunking] and the prose after that comment survives")
        # An UNTERMINATED marker encloses nothing, so it may remove nothing.
        check("ORPHAN_TAIL" in fc_body,
              f"[chunking] an unterminated marker does not swallow the rest of the note "
              f"(got {fc_body!r})")
        check("an opener <!-- that never closes" in fc_body,
              "[chunking] and its line is kept, the marker treated as the literal text it is")
        # The give-back must restore the line as it was AFTER complete comments were removed.
        # Restoring the raw line resurrects a comment that genuinely closed, so one line reading
        # '<!--metadata--> <!--' would put the metadata back into the evidence.
        check("ENCLOSEDSECRET" not in fc_body,
              f"[chunking] a COMPLETE comment sharing a line with a surviving opener stays removed "
              f"(got {fc_body!r})")
        check("ENCLOSEDLINE" in fc_body,
              "[chunking] while that line's own prose is kept")
        # An opener whose line begins with a rule must still be given back, not destroyed. It
        # needs its own note: the rule branch returns early, so this path is only reached when no
        # other comment is already open.
        rule_opener = rows_of(database, "SELECT body FROM chunks WHERE path = ?",
                              "10 Areas/Rule Opener Note.md")[0]["body"]
        check("RULEOPENER" in rule_opener,
              f"[chunking] an unterminated opener on a rule line is given back too "
              f"(got {rule_opener!r})")
        check("Closing prose" in rule_opener,
              "[chunking] and the prose after it survives")
        # An odd number of fence delimiters inside a comment must not leave the fence latched on,
        # or everything after it stops being stripped.
        check("LATCHSECRET" not in fc_body,
              f"[chunking] an odd fence delimiter inside a comment does not latch the fence on, so "
              f"later comments are still stripped (got {fc_body!r})")
        check("LATCHPROSE" in fc_body,
              "[chunking] and the prose around that later comment is kept")
        # Two openers on one line: the leftmost opens, so everything from it is enclosed.
        check("FIRSTSECRET" not in fc_body and "SECONDSECRET" not in fc_body,
              f"[chunking] with two openers on one line the leftmost opens the comment, so neither "
              f"is left behind (got {fc_body!r})")
        check("KEEPTWO" in fc_body,
              "[chunking] while the text before them is kept")
        check("TAILTEXT" in fc_body,
              "[chunking] and the text after the closing marker is kept")
        check("-->" not in fc_body,
              f"[chunking] no closing marker is left anywhere in the body (got {fc_body!r})")

        # Inside a fenced block nothing is stripped: a note documenting markup legitimately
        # contains a comment or a rule there. Three earlier attempts at D1 deleted exactly this.
        fenced = rows_of(database, "SELECT * FROM chunks WHERE path = ? ORDER BY start_line",
                         "10 Areas/Fenced Markup Note.md")
        check(len(fenced) == 1,
              f"[chunking] the fenced-markup note is ONE chunk: a '#' line inside a fence is a shell "
              f"comment, not a heading, and must not split it (got {len(fenced)})")
        check(fenced[0]["heading"] == "Fenced Markup Note",
              f"[chunking] and the chunk keeps the note's real heading (got {fenced[0]['heading']!r})")
        fenced_body = fenced[0]["body"]
        check("# rotate the credential before the deploy" in fenced_body,
              f"[chunking] a heading-like line inside a fence survives verbatim (got {fenced_body!r})")
        check("<!-- the rule above is part of the printed banner -->" in fenced_body,
              "[chunking] an HTML comment inside a fence survives verbatim")
        check("\n---\n" in fenced_body,
              "[chunking] a rule inside a fence survives verbatim")
        check(fenced_body.count("---") == 1,
              f"[chunking] while the rule OUTSIDE the fence is stripped (got {fenced_body!r})")
        check("a pipeline marker outside the fence" not in fenced_body,
              "[chunking] and so is the comment outside the fence")
        body_lines = fenced_body.split("\n")
        check("```" not in body_lines and "```bash" not in body_lines,
              f"[chunking] no line of the body is a fence delimiter: they are dropped "
              f"(got {body_lines!r})")
        for phrase in ("Prose before the fence", "Prose between the fence", "Prose after the rule"):
            check(phrase in fenced_body, f"[chunking] {phrase!r} survives")
        # Four or more backticks are NOT recognised as a fence. That is a declared limitation, so
        # it is pinned in both directions: the line is plain text, and a comment on it is stripped
        # exactly as it would be anywhere else outside a fence.
        check("QUADSECRET" in fenced_body,
              f"[chunking] a four-backtick line is not a fence, so its content is ordinary text "
              f"(got {fenced_body!r})")
        check("QUADMARKER" not in fenced_body,
              "[chunking] and a comment on that line is stripped, not protected")

        # D1 and D4 are coupled: once markup is really stripped, a span must not end on a line
        # that is no longer in the body. The rule and the comment are the last two markup lines
        # before the closing prose, so a span that ignored them would end on one of them.
        source = (VAULT / "10 Areas/Fenced Markup Note.md").read_text(encoding="utf-8").splitlines()
        span_end = source[fenced[0]["end_line"] - 1]
        check(span_end.strip() and span_end.strip() in fenced_body,
              f"[chunking] the span ends on a line that is still IN the body after stripping "
              f"(line {fenced[0]['end_line']}: {span_end!r})")
        span_start = source[fenced[0]["start_line"] - 1]
        check(span_start.strip() and span_start.strip() in fenced_body,
              f"[chunking] and starts on one too (line {fenced[0]['start_line']}: {span_start!r})")

        empty = rows_of(database, "SELECT * FROM chunks WHERE path = ?", "10 Areas/Empty Signal Note.md")
        check(len(empty) == 1 and empty[0]["body"] == "Empty Signal Note",
              "[chunking] a note with no readable signal falls back to a single title chunk")
        check((empty[0]["start_line"], empty[0]["end_line"]) == (1, 1),
              f"[chunking] and that fallback chunk carries a valid 1-based placeholder span "
              f"(got {(empty[0]['start_line'], empty[0]['end_line'])})")

        # The span guard is a predicate on the line's content, not merely on the line existing.
        # Called directly so the case does not need a fixture note of its own.
        _, spaced = amb.chunk_note(
            "# T\n\n   \nreal sentence one that is long enough here\n   \n"
            "real sentence two that is long enough here\n   \n",
            "spaced.md",
            3500,
        )
        check(len(spaced) == 1, f"[chunking] the whitespace-padded note yields one chunk (got {len(spaced)})")
        check((spaced[0]["start_line"], spaced[0]["end_line"]) == (4, 6),
              f"[chunking] a whitespace-only line does not extend a span at either end "
              f"(got {(spaced[0]['start_line'], spaced[0]['end_line'])})")

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
        escape = "../" * 12 + "etc/hostname"
        check(amb.provenance_for_path(config, escape) == {},
              "[provenance-authority] escaping upward to a real system file yields nothing")
        # The escape target below EXISTS and carries exactly the frontmatter the core reads, so
        # these two checks can only pass because containment works, not because there was nothing
        # to find. Without it they would pass against a core with no containment at all.
        check(OUTSIDE_NOTE.is_file(), "[provenance-authority] the escape target exists")
        outside_text = OUTSIDE_NOTE.read_text(encoding="utf-8")
        check("current_project_status" in outside_text and "source_url" in outside_text,
              "[provenance-authority] and it carries readable authority and provenance frontmatter")
        check(amb.provenance_for_path(config, "../outside-the-vault.md") == {},
              "[provenance-authority] provenance is never read from outside the vault, even when "
              "the escape target really does carry provenance frontmatter")
        check(amb.retrieval_authority_for_path(config, "../outside-the-vault.md") == {},
              "[provenance-authority] authority is never read from outside the vault, even when "
              "the escape target really does carry authority frontmatter")
        indexed_paths = {r["path"] for r in rows_of(database, "SELECT DISTINCT path FROM chunks")}
        check(not any("outside-the-vault" in path for path in indexed_paths),
              "[provenance-authority] and it never reaches the index")
        check(amb.retrieval_authority_for_path(config, "10 Areas/Bare Note.md") == {},
              "[provenance-authority] a note with no frontmatter yields no authority")
        other_authority = amb.retrieval_authority_for_path(config, "10 Areas/Saw Care.md")
        check(other_authority == {"authority": "canonical_reference", "status": "current"},
              f"[provenance-authority] an authority value the scorer does not bonus is still read "
              f"verbatim (got {other_authority})")
        check(other_authority["authority"] != "current_project_status",
              "[provenance-authority] and it is not the one value that earns the bonus")
        authority = amb.retrieval_authority_for_path(config, "10 Areas/Second Brain Current Status.md")
        check(authority == {"authority": "current_project_status", "status": "current"},
              f"[provenance-authority] authority frontmatter is read (got {authority})")

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
        for row in hits:
            expected = amb.provenance_for_path(config, row["path"])
            check(row["provenance"] == expected,
                  f"[search] every selected row carries its own note's provenance, not only the first "
                  f"({row['path']})")
        check(amb.deterministic_evidence_strength(hits) == "HIGH",
              "[search] real rows drive the existing strength contract")
        check(all(p != "90 Archive/Old Greenhouse Notes.md" for p in (r["path"] for r in hits)),
              "[search] the excluded archive note is unreachable through search")

        partial = amb.search(config, "which saw needs sharpening?", 8)
        check(partial, "[search] a partially-matched question still retrieves evidence")
        check(any(r["match_mode"] == "partial_terms" for r in partial),
              "[search] a chunk matching only some terms is reported as partial_terms")
        coverages = [r["term_coverage"] for r in partial]
        check(all(0.0 < c < 1.0 for c in coverages),
              f"[search] partial matches carry a real fractional coverage (got {coverages})")
        check(coverages == sorted(coverages, reverse=True),
              f"[search] rows come back ranked, best coverage first (got {coverages})")
        check(amb.deterministic_evidence_strength(partial) == "LOW",
              "[search] dispersed partial matches drive the LOW branch of the strength contract")

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
        # Dilution: a real term surrounded by irrelevant ones is refused BEFORE retrieval.
        check(len(amb.search(config, "chisels", 5)) >= 1,
              "[search] the term 'chisels' on its own does retrieve")
        check(amb.search(config, "chisels dirigible parliament sonata quantum", 5) == [],
              "[search] that same term diluted by four irrelevant ones retrieves nothing")

        # The coverage gate: this question DOES reach reranking - candidates come back from the
        # index - and is then rejected for insufficient coverage. Counting the candidates is what
        # makes this different from the check above: remove either coverage gate and rows appear.
        gated = "frame bench dirigible"
        candidates = []
        real_match = amb.execute_match

        def counting_match(*args, **kwargs):
            found = real_match(*args, **kwargs)
            candidates.append(len(found))
            return found

        amb.execute_match = counting_match
        try:
            gated_rows = amb.search(config, gated, 5)
        finally:
            amb.execute_match = real_match
        check(max(candidates) >= 1,
              f"[search] the gated question really does retrieve candidates (got {candidates})")
        check(gated_rows == [],
              f"[search] and every one of them is then rejected for insufficient term coverage, "
              f"so the gate - not an empty index - is what returns nothing")

        # The 0.35 top-candidate gate, isolated. This question's best candidate scores ~0.33 -
        # above the 0.30 row floor, below the 0.35 top gate - so it is the top gate alone that
        # returns nothing. Both its terms are in the index and retrieve on their own.
        check(len(amb.search(config, "inventory", 5)) >= 1,
              "[search] 'inventory' on its own retrieves")
        check(amb.search(config, "inventory dirigible", 20) == [],
              "[search] a question whose best candidate scores between the 0.30 floor and the "
              "0.35 top gate is refused by the top gate alone")
        check(amb.search(config, "workshop dirigible", 20) == [],
              "[search] and so is a second question in that same band")

        # Exact counts at the coverage boundary. Loosen the coverage arithmetic or the row filter
        # and these counts change, which is the only way this vault can observe those gates: no
        # question over these fixtures produces a top coverage inside [0.30, 0.35), so the row
        # filter and the top-candidate gate cannot be told apart here. Recorded as a limitation.
        for question, expected in (
            ("bench dirigible", 3),
            ("saw bench clamps dirigible parliament", 1),
            ("frame bench dirigible", 0),
        ):
            got = amb.search(config, question, 20)
            check(len(got) == expected,
                  f"[search] {question!r} yields exactly {expected} row(s) at the coverage "
                  f"boundary (got {len(got)}: {[round(r['term_coverage'], 3) for r in got]})")
            check(all(r["term_coverage"] >= 0.30 for r in got),
                  f"[search] and no row below the 0.30 coverage floor survives for {question!r}")
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
        historical = amb.search(config, "what was the second brain status previously and historically?", 5)
        penalised = [r for r in historical if r["path"] == "10 Areas/Second Brain Current Status.md"]
        check(penalised and penalised[0]["authority_bonus"] == -0.35,
              f"[search] the same note is penalised on a historical question "
              f"(got {[r['authority_bonus'] for r in penalised]})")

        # -------------------------------------------------------------- [scope]
        spanning = "the panel saw and the greenhouse frame"
        unscoped = amb.search(config, spanning, 8)
        folders = {r["path"].split("/")[0] for r in unscoped}
        check(folders == {"10 Areas", "20 Projects"},
              f"[scope] unscoped, this question genuinely spans both folders (got {folders})")
        inside = amb.search(config, spanning, 8, {"include_paths": ["20 Projects/**"]})
        check(inside and all(r["path"].startswith("20 Projects/") for r in inside),
              "[scope] include_paths narrows a genuinely-spanning result to the named subtree")
        check(len(inside) < len(unscoped),
              f"[scope] narrowing actually removed rows ({len(unscoped)} -> {len(inside)})")
        outside_scope = amb.search(config, spanning, 8, {"exclude_paths": ["20 Projects/**"]})
        check(outside_scope and all(not r["path"].startswith("20 Projects/") for r in outside_scope),
              "[scope] exclude_paths subtracts that subtree and leaves the rest")
        check({r["path"] for r in inside}.isdisjoint({r["path"] for r in outside_scope}),
              "[scope] the two halves of the same question are disjoint")
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

        # -------------------------------------------------------------- [defect-pins]
        # Everything in this block asserts DEFECTIVE behaviour. See the module docstring.
        # Repairing any of these defects will fail its pin; delete the pin, keep the repair.

        # D2 (HIGH): FTS5 indexes accented content correctly, but query_terms (r"[A-Za-z0-9]+")
        # and the coverage reranker (r"[^a-z0-9]+") both strip non-ASCII, so the term never
        # matches the body token and the chunk is discarded after retrieval.
        check(amb.query_terms("café résumé piñata") == ["caf", "sum", "pi", "ata"],
              "[defect-pins] DEFECT D2 PINNED (not a requirement): an accented question is shredded "
              "into fragments by query_terms")
        check(amb.search(config, "café résumé piñata", 5) == [],
              "[defect-pins] DEFECT D2 PINNED (not a requirement): accented content is indexed but "
              "unreachable through search")
        check(amb.search(config, "cafe resume pinata", 5) == [],
              "[defect-pins] DEFECT D2 PINNED (not a requirement): the unaccented spelling cannot "
              "reach it either")

        # D3 (MEDIUM): a question of pure stopwords falls through query_terms' deliberate
        # no-useful-terms fallback and is answered at full confidence.
        check("the" in amb.STOPWORDS and amb.query_terms("the") == ["the"],
              "[defect-pins] DEFECT D3 PINNED (not a requirement): a stopword survives as a search "
              "term through the no-useful-terms fallback")
        stopword_rows = amb.search(config, "the", 5)
        check(stopword_rows and amb.deterministic_evidence_strength(stopword_rows) == "HIGH",
              "[defect-pins] DEFECT D3 PINNED (not a requirement): a contentless question yields "
              "HIGH-strength evidence and would be answered by the model")

        # -------------------------------------------------------------- [containment]
        check(len(CONNECTED) >= 2,
              f"[containment] the code under test opened databases and we observed them (got {CONNECTED})")
        def bare(target: str) -> str:
            """open_database connects through a file: URI, so compare the plain path."""
            return target[5:].split("?", 1)[0] if target.startswith("file:") else target

        outside = [c for c in CONNECTED if not bare(c).startswith(work)]
        check(outside == [],
              f"[containment] every database the code opened was inside the temp dir (outside: {outside})")
        check(any(bare(c) == str(database) for c in CONNECTED),
              "[containment] the configured database is among the paths actually opened")
        read_handles = [c for c in CONNECTED if c.startswith("file:")]
        check(read_handles and all("mode=ro" in c for c in read_handles),
              f"[containment] every read handle the code opened is read-only (got {set(read_handles)})")
        strays = [str(p) for p in ROOT.rglob("*.db")] + [str(p) for p in ROOT.rglob("*.sqlite*")]
        check(strays == [], f"[containment] no database file was left in the repository (got {strays})")
        restore_sqlite()
        check(amb.sqlite3.connect is REAL_SQLITE_CONNECT,
              "[containment] the suite leaves sqlite3.connect as it found it, compared against a "
              "reference captured before the spy existed")

    check(snapshot_fixtures() == before,
          "[containment] every fixture file is byte-for-byte and mtime unchanged")
    check(not database.exists(),
          "[containment] the temporary index is gone once the suite finishes")

    print(f"PASS retrieval_core_e2e: {CHECKS} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
