---
authority: current_project_status
status: current
source_type: manual_note
source_url: https://example.invalid/must-never-be-read
provenance_status: verified
---

# Outside The Vault

This invented note sits deliberately OUTSIDE `fixtures/vault/`, one level up, and carries exactly
the frontmatter the retrieval core knows how to read. It exists so the vault-containment checks in
`tests/test_retrieval_core_e2e.py` can only pass because containment works — not because the escape
target happened to have no frontmatter. Nothing may ever index or retrieve it.
