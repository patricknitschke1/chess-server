---
name: dispatching-build-subagents
description: Use when dispatching a subagent to build or fix anything in this repository — the environment facts every run needs, scope fencing, and the report format.
---

# Dispatching build subagents

Keep dispatches short. State the scope, the environment, and what "done" means.

## Environment facts to include

- Python 3.14.3 in `.venv`. **Always `.venv/bin/pytest` and `.venv/bin/python`.**
- The current test count and commit SHA, with "confirm before you start".
- Server tests live in `tests/chess_server/` and use a file-backed SQLite database
  under `tmp_path`, **never `:memory:`** — two connections to `:memory:` are two
  separate databases, so the reader/writer split and WAL are unobservable.
- **Never pipe a long test run through `tail`.** It buffers all output, the run
  looks idle, and killing the backgrounded terminal dumps the whole scrollback
  into context.

## Scope fencing

Name the files the task may touch. The standard fence:

> Do NOT touch `chess_core/`, `starter-kit/`, `docs/`, `AGENTS.md`. If something
> there looks wrong, **report it — do not fix it.**

Cross-document coherence is decided outside the subagent. A subagent that
quietly edits a spec has made a decision nobody reviewed.

## Testing

Normal TDD: write the test, watch it fail for the right reason, implement. That
costs nothing extra and stays the default.

**Reserve mutation testing for failures that would be silent** — the clock, CAS
transitions, lock discipline, SSE ordering, anything where wrong code still
returns a plausible answer. Elsewhere a passing suite is good enough.

This is a deliberate reduction for cost. The full discipline found eleven real
defects, including tests that could not fail at all, so expect a few to slip
through now.

## Report format

Ask for a report, not a transcript:

> Keep your reply short. Do not paste file contents. If you run low on room,
> commit what is finished and say where you stopped rather than returning nothing.

Runs have returned *nothing* after spending their whole budget on a long reply.
Ask for: what was built, the final `.venv/bin/pytest -q` result,
`git status --short`, and anything that blocked them or contradicted the spec.

## Commits

Commit per task. No `--no-verify`, no amend, no rebase. Every task ends green.

## After the run

`git status --short` and `git log --oneline`. Subagents run out of budget
mid-task — finish or commit any stray work yourself. Do not assume unexpected
changes are the subagent's; they may be the user's.
