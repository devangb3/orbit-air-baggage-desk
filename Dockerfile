# STRETCH GOAL 4: one image for running the CLI, pytest, and Ruff.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked
COPY claims.py db.py main.py seed_claims.csv ./
COPY tests/ ./tests/
COPY .claude/hooks/protect-tests.py ./.claude/hooks/protect-tests.py
CMD ["python", "main.py", "--help"]
