# Orbit Air Baggage Desk

A Python CLI for baggage claims backed by SQLite. Feature implementation has
not started yet.

## Development setup

Requires Python 3.12 or newer and uv.

```sh
uv sync --locked
source .venv/bin/activate
```

```sh
# Full test suite (once feature tests have been added)
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
