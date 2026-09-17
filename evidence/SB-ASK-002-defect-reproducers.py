"""SB-ASK-002 defect reproducers. Read-only; run from the repository root.

These reproduce defects the end-to-end suite EXPOSED but deliberately does NOT assert, because
fixing them means changing retrieval/chunking behaviour, which SB-ASK-002 is not authorised to do.
"""
import importlib.util, tempfile, io, contextlib, sqlite3
from pathlib import Path

s = importlib.util.spec_from_file_location('amb', 'src/ask-my-brain/ask_my_brain.py')
amb = importlib.util.module_from_spec(s); s.loader.exec_module(amb)
tmp = Path(tempfile.mkdtemp())
cfg = {"vault_root": str(Path("fixtures/vault").resolve()), "database_path": str(tmp / "i.db"),
       "ollama_url": "http://127.0.0.1:1/none", "model": "m",
       "exclude_prefixes": ["90 Archive"], "exclude_path_parts": [".obsidian"]}
with contextlib.redirect_stdout(io.StringIO()):
    amb.build_index(cfg)

print("D1  HTML comments are not stripped from retrieval evidence")
body = sqlite3.connect(tmp / "i.db").execute(
    "select body from chunks where path like '%Comment Marker%'").fetchone()[0]
print("    indexed body contains '<!--':", "<!--" in body)
print("    the stripper's pattern in chunk_note is r\"<!--[\\\\s\\\\S]*?-->\" - the doubled")
print("    backslashes make [\\\\s\\\\S] a class of {backslash, s, S}, not 'any character'.")
print("    Same doubling in the fence, separator and blank-line collapse patterns.")

print()
print("D2  accented content is indexed but unreachable")
print("    query_terms('cafe resume pinata') ->", amb.query_terms("cafe resume pinata"))
print("    query_terms('cafe resume pinata' accented) ->", amb.query_terms("café résumé piñata"))
print("    FTS5 itself matches: 'café' ->",
      sqlite3.connect(tmp / "i.db").execute(
          "select count(*) from chunks_fts where chunks_fts match 'café'").fetchone()[0], "chunk(s)")
print("    but search() returns:", len(amb.search(cfg, "café résumé piñata", 5)), "rows")
print("    and unaccented search() returns:", len(amb.search(cfg, "cafe resume pinata", 5)), "rows")
print("    cause: both query_terms and the coverage reranker strip non-ASCII")
print("    (r'[A-Za-z0-9]+' and r'[^a-z0-9]+'), so 'cafe' never equals the body token 'caf'.")

print()
print("D3  a question made only of stopwords is answered at HIGH confidence")
rows = amb.search(cfg, "the", 5)
print("    query_terms('the') ->", amb.query_terms("the"), "(fallback: 'the' IS in STOPWORDS)")
print("    search('the') returns", len(rows), "rows, strength:", amb.deterministic_evidence_strength(rows))
print("    the stopword fallback is deliberate; its interaction with the strength")
print("    contract is what produces a confident answer to a contentless question.")
