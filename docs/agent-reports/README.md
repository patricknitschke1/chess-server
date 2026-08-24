# Agent Reports

Output directory for subagent reports — design reviews, audits, benchmark sweeps, and any other delegated work whose full output is too long to belong in a conversation.

## Conventions

- Filename: `YYYY-MM-DD-<topic>-<kind>.md` (e.g. `2026-08-23-spec-review-systems-design.md`)
- Every report opens with: what was reviewed, by whom, against what version (commit SHA), and a verdict.
- Reports are committed. They are a record of what was checked and when, and they are workshop material.
- Reports are **advisory**. Acting on one means a separate change, with the reasoning captured there.
