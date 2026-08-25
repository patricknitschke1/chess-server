---
name: dispatching-build-subagents
description: Use when dispatching a subagent to build, fix, review or plan anything in this repository — covers scope fencing, the mutation-proof test rule, the environment facts every run needs, and the report format. Also use when writing or reviewing an implementation plan, since plans must specify the mutation for every test.
---

# Dispatching build subagents

Every dispatch in this project needs the same preamble. Re-typing it by hand is
how it drifts. Take what applies and drop the rest.

## The rule that pays for itself

**A test you have not watched fail is not evidence.**

Eleven times in this project a mutation specified in a plan turned out to be
unable to fail the test it was attached to. The two worst:

- A test hard-coded the step order it existed to verify, so it asserted its own
  mutation and could never have passed against any implementation.
- A planned mutation killed nothing because the code was wrong in a way that
  masked it — `take_payload` consumed the mailbox on serve, so the stale-payload
  guard was silently doing two jobs. The honest fix was to the implementation,
  and it brought delivery back in line with §6.2's idempotency.

Eight tests shipped in phases 1–2 that could not fail: two with no assertion at
all, one tautological, and one asserting an upper bound that a completely dead
clock satisfied *more easily* than a working one.

So: for every test, name the mutation that makes it fail, run it, confirm red,
restore, confirm green. If the named mutation cannot fail the test, **say so and
find one that does** — and if the honest fix is to the code rather than the test,
make it. That finding is the most valuable thing a report produces.

Where a step's expected outcome is genuinely "no change" — removing dead code,
for instance — say so explicitly, so a green run is not mistaken for proof.

## Environment facts to state in every dispatch

- Python 3.14.3 in `.venv`. **Always `.venv/bin/pytest` and `.venv/bin/python`.**
  Never bare `pytest`/`python`.
- The current test count and commit SHA, with "confirm before you start".
- Test config lives in `pyproject.toml` only. Server tests in `tests/chess_server/`.
- Server tests use a file-backed SQLite database under `tmp_path`, **never
  `:memory:`** — two connections to `:memory:` are two separate databases, so the
  reader/writer split, WAL and `BEGIN IMMEDIATE` contention are all unobservable.
- **Never pipe a long test run through `tail`.** It buffers all output, the run
  looks idle, VS Code backgrounds it as a hang, and killing the terminal dumps the
  entire scrollback into context. Three of those dumps cost 152KB this project.
- **Clear `__pycache__` between mutation steps.** A `cp` restore inside the same
  second leaves a stale `.pyc` that Python reuses, so the "restored" run silently
  executes the mutant:
  `find . -path ./.venv -prune -o -name __pycache__ -type d -exec rm -rf {} +`

## Scope fencing

Name the files the task may touch, and name the ones it may not. The standard
fence:

> Do NOT touch `chess_core/`, `starter-kit/`, `docs/`, `AGENTS.md`. If something
> there looks wrong, **report it — do not fix it.**

Repo layout and cross-document coherence are the orchestrator's call. A subagent
that quietly fixes a spec has made a decision nobody reviewed.

Sanctioned exceptions exist and should be stated when they apply — a `store/`
schema change from an `api/` task was accepted once because the alternative
duplicated §6.4 clock arithmetic outside `chess_core`. Require a loud
justification rather than forbidding it outright.

## Report format

**Ask for a report, not a transcript.** State it explicitly:

> Keep your reply short. Do not paste file contents back. If you run low on room,
> commit what is finished and say where you stopped rather than returning nothing.

This matters: several runs in this project returned *no output at all* after
exhausting their budget on a long reply, and two left work uncommitted. Once
report length was capped, spilled output dropped from 52KB to 12KB per run.

Always ask for these four:

1. Per task: what was built, test names, pass counts, and the mutation output
   proving each test can fail.
2. **Every disagreement between the plan, the specs and reality — including ones
   they resolved.** Every task so far has surfaced a real bug this way. A report
   with no disagreements is one to distrust.
3. Anything they were tempted to fix but left alone.
4. Final `.venv/bin/pytest -q` result and `git status --short`.

## Commits

Commit per task with the plan's message. No `--no-verify`, no amend, no rebase.
Every task ends with the full suite green and the tree committable.

## After the run

- `git status --short` and `git log --oneline`. Subagents run out of budget
  mid-task: check for uncommitted work before assuming a silent run produced
  nothing, and finish or commit it yourself.
- **Do not assume unexpected changes are the subagent's.** They may be the user's.
  Ask before reverting anything that looks deliberate; `git log` does not
  distinguish who typed what.
- Verify the headline claims rather than accepting them. Reports have been wrong
  about a spec contradiction that did not exist, and right about ones I would not
  have found.

## Writing plans for these dispatches

Same rule, one level up: a plan must name the mutation for every test it
specifies. **Reference the spec, do not restate it** — the phase 1 and 2 plans ran
to 90KB and 70KB because they inlined implementations, and duplicated normative
text is exactly what the pre-build review punished throughout the server spec.
Fixes landed in prose while stale code blocks stayed, and the code block is what
gets copied.

Target 300–500 lines. Build the file incrementally on disk, appending task by
task; a plan held in one response hits the length limit and writes nothing.
