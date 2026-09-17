# Synthetic fixtures only

Every file here is invented. No fixture may be copied, derived, summarised or reconstructed from the
owner's real captures, notes, vault, inbox, mail, calendar or voice data. A fixture that needs to look
like a real note is written from scratch with invented content.

## `vault/` — synthetic vault for retrieval-core tests

`fixtures/vault/` is a small invented Obsidian-shaped vault used by
`tests/test_retrieval_core_e2e.py` to exercise the real index build and the real search path with no
database, no model, and no vault of the owner's anywhere in the picture. Every note, person, project,
place and date in it was made up for this fixture.

Each note exists to exercise one named retrieval behaviour, and the tests name the note they depend
on. Notes are deliberately small so a failure points at one behaviour. The vault holds 26 notes:

| note | exercises |
| --- | --- |
| `20 Projects/Greenhouse Rebuild.md` | frontmatter, `# Title` extraction, heading-split chunking, line spans, provenance frontmatter |
| `20 Projects/Kiln Shed.md` | a second note sharing vocabulary with the first, so ranking has to choose |
| `10 Areas/Workshop Inventory.md` | many short headings, so one note can offer more chunks than the per-file cap allows |
| `10 Areas/Saw Care.md` | a `# Tool Maintenance` heading that deliberately differs from the filename stem, and an `authority` value (`canonical_reference`) the scorer does **not** bonus |
| `10 Areas/Second Brain Current Status.md` | `authority: current_project_status` + `status: current`, the one combination that earns the ±0.35 authority bonus |
| `10 Areas/Second Brain Older Status.md` | the same subject without authority frontmatter, so the bonus is what decides the ranking |
| `10 Areas/Long Line Note.md` | a single line longer than `max_chunk_chars`, for the long-line split |
| `10 Areas/Comment Marker Note.md` | comment shapes outside a fence: one between sentences, two on one line with text between and either side, one between two words with no spaces (the replacement is a space, not nothing), one spanning several lines and closing mid-line with text after it; plus a two-dash line that is NOT a rule, and a run of blank lines. All markers must go and every sentence kept (was defect D1, repaired by SB-ASK-006) |
| `10 Areas/Comment Fence Note.md` | a terminated comment containing a fence delimiter, which must still be removed in full, and an unterminated marker in prose, which must remove nothing at all. Resolving the fence before the comment state makes the first leak and the second swallow the note |
| `10 Areas/Commented Title Note.md` | a heading inside an HTML comment and a heading inside a fence, both before the note's real heading. Neither may become the title: the title is indexed at the highest bm25 column weight and is printed as `Title:` in the evidence block |
| `10 Areas/Title Precedence Note.md` | a level-two heading, then two level-one headings. The title must be the FIRST level-one heading: neither the level-two heading above it nor the second level-one heading below it |
| `10 Areas/Hidden Title Only.md` | a note whose ONLY heading is inside a comment. The title falls back to the filename stem, and the no-signal fallback chunk therefore carries the filename rather than the comment |
| `10 Areas/Rule Opener Note.md` | an unterminated comment opener on a line that is otherwise a horizontal rule, with nothing else open. Neither branch that could destroy the line fires, and each for a different reason: last_closing_line returns 0, so the opener is literal from the outset and the truncating branch is never entered, while the rule pattern is applied to the line, as it is to every non-fenced line, and simply does not match it |
| `10 Areas/Fence Shapes Note.md` | three ways a fence delimiter is not simply "three backticks": a three-backtick line carrying an info string is content, not a closer; a tilde run is a fence too; four spaces of indent makes an indented code block, not a fence. Fences of four or more backticks are covered by `Fence Closer Note.md`, not here |
| `10 Areas/Break Markers Note.md` | mixtures of break characters, which are not thematic breaks and must survive, beside runs of a single marker, which are stripped |
| `10 Areas/Bound Exactness Note.md` | every line an unterminated opener. Pins that each is treated as literal text and keeps its marker. Since SB-ASK-009 decides openers up front this settles in one pass, so it no longer exercises the restart bound |
| `10 Areas/Multi Opener Note.md` | three unterminated openers followed by a fence. Each must be literal text and the fence must still be recognised afterwards. Since SB-ASK-009 decides openers up front this settles in one pass and no longer exercises a restart |
| `10 Areas/Give Back Fence Note.md` | an unterminated comment opener followed by a fence containing a heading, and deliberately no real heading of its own. The fence must not have been swallowed: the fenced heading must not become the title, and the delimiters must still be dropped |
| `10 Areas/Fence Closer Note.md` | a four-backtick fence that a three-backtick line must not close, and a tilde fence that a backtick line must not close. Relaxing either rule ends the fence early and strips what was inside it |
| `10 Areas/Backtick Info Note.md` | a backtick line whose info string contains a backtick, which opens no fence |
| `10 Areas/Fenced Markup Note.md` | a fenced block containing a heading-like line, an HTML comment and a rule, all of which must survive verbatim, beside a comment and a rule OUTSIDE the fence which must be stripped. The heading-like line must not split the chunk |
| `10 Areas/Diacritics Note.md` | accented characters. FTS5 indexes them correctly but `search()` cannot reach them (finding D2); this note pins that defect |
| `10 Areas/Bare Note.md` | no headings and no frontmatter: title falls back to the filename stem |
| `10 Areas/Empty Signal Note.md` | frontmatter only, below the readable-signal threshold |
| `90 Archive/Old Greenhouse Notes.md` | lives under an excluded prefix and must never be indexed |
| `.obsidian/workspace.md` | lives under an excluded path part and must never be indexed |

Indexing this vault yields 24 notes and 37 chunks, with 2 files skipped.
`tests/test_retrieval_core_e2e.py` asserts those numbers, so adding or editing a note here means
updating them: a fixture change cannot pass unnoticed.

## `outside-the-vault.md` — deliberately not in the vault

`fixtures/outside-the-vault.md` sits one level ABOVE `fixtures/vault/`, so the indexer's `rglob`
cannot reach it. It carries exactly the `authority` and provenance frontmatter the retrieval core
knows how to read. It exists so the vault-containment checks can only pass because containment
works — before it, those checks passed merely because the escape target had nothing to read. It is
covered by the same synthetic-content scan as the vault notes, and the suite asserts it never
reaches the index.

## What the tests do and do not touch

Building an index from this vault writes only to a temporary directory, and the suite asserts that
every database handle the code opened was inside it. The suite also snapshots the size and mtime of
every file under `fixtures/vault/` and asserts they are unchanged at the end; that snapshot covers
the vault only, not the whole of `fixtures/`.
