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
on. Notes are deliberately small so a failure points at one behaviour:

| note | exercises |
| --- | --- |
| `20 Projects/Greenhouse Rebuild.md` | frontmatter, `# Title` extraction, heading-split chunking, provenance frontmatter |
| `20 Projects/Kiln Shed.md` | a second note sharing vocabulary with the first, so ranking has to choose |
| `10 Areas/Workshop Inventory.md` | many short headings, so one note can offer more chunks than the per-file cap allows |
| `10 Areas/Tool Maintenance.md` | `authority` / `status` frontmatter, for the authority bonus |
| `10 Areas/Long Line Note.md` | a single line longer than `max_chunk_chars`, for the long-line split |
| `10 Areas/Comment Marker Note.md` | an HTML comment between real sentences, for pipeline-marker stripping |
| `10 Areas/Diacritics Note.md` | accented characters, for `unicode61 remove_diacritics 2` |
| `10 Areas/Bare Note.md` | no headings and no frontmatter |
| `10 Areas/Empty Signal Note.md` | frontmatter only, below the readable-signal threshold |
| `90 Archive/Old Greenhouse Notes.md` | lives under an excluded prefix and must never be indexed |
| `.obsidian/workspace.md` | lives under an excluded path part and must never be indexed |

Building an index from this vault writes only to a temporary directory. Nothing under `fixtures/`
is ever written to by a test.
