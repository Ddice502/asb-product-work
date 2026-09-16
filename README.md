# Second Brain product-development target (isolated)

This repository is the Unattended Execution Pipeline's **product** target on `jro.desktop`. It is
separate from the control-plane target `~/asb-uep-work` and is authorized separately.

It is isolated by construction:

- no git remote, and none may be added;
- no credential, token or connection string of any kind;
- no connection to `home` / `192.168.1.100`, and no synchronization back to it;
- no vault path, no real inbox, no capture stream, no personal data;
- fixtures under `fixtures/` are **synthetic only** — invented content, invented names, invented dates.

Product source arrives here only through an owner-approved export (see `EXPORT_CONTRACT.md`).
Nothing in this repository is deployed anywhere; a candidate leaves it only through an explicit
owner merge decision, exactly as the control-plane target works.

## Layout

| path | holds |
| --- | --- |
| `src/` | product modules under development |
| `tests/` | Tech-Lead-authored acceptance gates, pre-committed before the card that implements them |
| `fixtures/` | synthetic fixtures only |

## Integration line

`uep/work` is the recorded integration base. Every task branch starts from its tip (KI-147).
