#!/usr/bin/env python3
# STRETCH GOAL 1: block Claude file-edit tools from changing approved tests.
"""PreToolUse hook. Exit 2 blocks the requested tool call."""

import json
import os
import sys
from pathlib import Path

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def main():
    try:
        event = json.load(sys.stdin)
        if event.get("tool_name") not in EDIT_TOOLS:
            return 0
        project = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).resolve().parents[2]))
        project = project.resolve()
        inputs = event["tool_input"]
        key = "notebook_path" if event["tool_name"] == "NotebookEdit" else "file_path"
        target = Path(inputs[key])
        if not target.is_absolute():
            target = Path(event.get("cwd", str(project))) / target
        # Check both the spelling and the resolved path (including symlinks).
        lexical = Path(os.path.abspath(target))
        if lexical.is_relative_to(project / "tests") or target.resolve().is_relative_to(
            (project / "tests").resolve()
        ):
            print("Blocked: tests/ is protected. Ask the user to review any test changes.",
                  file=sys.stderr)
            return 2
        return 0
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError):
        print("Blocked: test guard could not validate the edit request.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
