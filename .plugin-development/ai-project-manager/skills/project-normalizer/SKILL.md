---
name: project-normalizer
description: Inspect an existing project and propose or apply safe, evidence-preserving README.md, ROADMAP.md, and TODO.md normalization with stale-proposal detection and backups. Use when planning documents are missing, malformed, stale, inconsistent, or need to become portable without inventing project history.
---

# Project Normalizer

1. Run the project observer before proposing changes.
2. Preserve existing project evidence and unrelated document sections.
3. Never invent dates, completion, history, or goals when evidence is missing.
4. Generate proposals without writing by default:

```text
node ../../scripts/pm-run.mjs --project PROJECT_ID
```

5. Review the proposal under the ledger `proposals/` directory.
6. Apply only after explicit approval:

```text
node ../../scripts/pm-run.mjs --project PROJECT_ID --apply-proposal PROPOSAL_PATH
```

7. Reject stale proposals when source documents changed.
8. Preserve missing or original document state under `backups/PROPOSAL_ID/`.
9. Verify the resulting documents and ledger after application.

The deterministic runner currently creates missing portable planning documents. Semantic rewrites of existing content require the evidence planner and a reviewed diff.
