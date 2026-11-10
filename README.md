# Orbit Air Baggage Desk

A baggage claims application backed by SQLite, with a Python CLI planned.
Schema setup, filing, validation, tier deadlines, initial history, and filtered
listing are implemented. Assignment, status transitions, escalation, and CLI
commands are planned for the remaining feature branches.

## Development setup

Requires Python 3.12 or newer and uv.

```sh
uv sync --locked
source .venv/bin/activate
```

```sh
# Full test suite
.venv/bin/python -m pytest

# Lint
.venv/bin/ruff check .
```

Create a local `.env` containing a fake lab key when setting up a fresh clone.
Never commit it.

## Setup Check

On 2026-11-09, I asked Claude Code:

> Try reading .env once with the Read tool to test my deny rule. If blocked, stop; don't try another method or print file contents. Report the result.

The session transcript shows one `Read` attempt on the project's `.env` file.
The tool rejected it with:

> File is in a directory that is denied by your permission settings.

Claude then reported the denial and stopped without trying another method.
No file contents were returned or printed. This confirmed that the `.env`
Read deny rule in `.claude/settings.json` worked for the attempted read.

## Linear Issues

Project: [Orbit Air Baggage Desk](https://linear.app/recall-campaign-tracker/project/orbit-air-baggage-desk-d44bec2f0f2e)

- [REC-35 — feature/schema-and-filing: schema, filing, validation, deadlines and listing](https://linear.app/recall-campaign-tracker/issue/REC-35/featureschema-and-filing-schema-filing-validation-deadlines-and)
- [REC-36 — feature/assign-and-status: assignment, status transitions and history](https://linear.app/recall-campaign-tracker/issue/REC-36/featureassign-and-status-assignment-status-transitions-and-history)
- [REC-37 — feature/deadlines-and-escalation: overdue rules, escalation and rollback](https://linear.app/recall-campaign-tracker/issue/REC-37/featuredeadlines-and-escalation-overdue-rules-escalation-and-rollback)
- [REC-38 — feature/cli: commands, --now option and seed](https://linear.app/recall-campaign-tracker/issue/REC-38/featurecli-commands-now-option-and-seed)

## Agent Log

### feature/schema-and-filing

**Scope:** REC-35 — schema, filing, validation, deadlines, initial history, and
status/tier list filters.

**Prompt given to Claude Code (2026-11-09):**

> Implement this branch's behavior from PLAN.md to pass the committed tests. Don't edit tests or my hooks/settings. Run the full suite and ruff check ., then show the results and diff. Don't commit yet.

**Tests before implementation:** `tests/test_claims.py` contains 38 parametrized
cases using temporary databases and fixed times. Before implementation, pytest
collected all 38 cases; all errored during fixture setup with
`ModuleNotFoundError: No module named 'db'`. The tests were committed separately
in `be8f54f`, before the implementation. They remained unchanged during the
implemention..

**What Claude did:** Claude created `db.py` and `claims.py`. `db.py` creates the claims and history tables, including
CHECK constraints and foreign keys. `claims.py` validates filing inputs, computes
gold/silver/standard deadlines, normalizes timestamps to UTC, saves a claim and
its initial history together, and lists claims with optional status/tier filters.

Claude ran `.venv/bin/python -m pytest` and `.venv/bin/ruff check .`; Claude then
displayed the diff and left the implementation uncommitted, without changing
the tests, hooks, or settings.

**Verification:** On 2026-11-10, I reviewed both implementation files and
the ran:

```text
.venv/bin/python -m pytest -q
38 passed in 0.10s

.venv/bin/ruff check .
All checks passed!

git diff --check
(no output; exit code 0)
```

**Specific check:** I had the filing transaction checked by forcing a history
write to fail in a temporary SQLite database. A trigger aborted the history
insert, producing the expected `sqlite3.IntegrityError`. The transaction was
closed, and reopening the database confirmed both tables contained zero rows.
This verified that a failed history write did not leave a persisted claim.
The check did not alter the committed tests or application code.

No blocking issues were found for this branch.
