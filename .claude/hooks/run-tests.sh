#!/bin/sh
# Send pytest failures back to Claude Code as a blocking Stop-hook result.
project_dir=${CLAUDE_PROJECT_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)}
if ! cd "$project_dir"; then
    echo "Stop hook could not enter the project directory." >&2
    exit 2
fi

if [ ! -x .venv/bin/python ]; then
    echo "Stop hook requires the project environment. Run uv sync --locked." >&2
    exit 2
fi

.venv/bin/python -m pytest >&2
result=$?
case "$result" in
    0) exit 0 ;;
    5)
        # Before the first feature branch, there are no tests to run yet.
        if [ ! -d tests ]; then
            echo "No tests collected yet; the setup-only project may stop." >&2
            exit 0
        fi
        ;;
esac
echo "Pytest did not pass. Fix the implementation; do not change approved tests without asking. During intentional red-test work, stop here for the learner's review." >&2
exit 2
