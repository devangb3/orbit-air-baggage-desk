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
