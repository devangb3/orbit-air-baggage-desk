"""Checks for the optional report and Claude test-edit guard."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import claims
from db import connect_db, init_db

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude/hooks/protect-tests.py"
NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    connection = connect_db(tmp_path / "bonus.db")
    init_db(connection)
    try:
        yield connection
    finally:
        connection.close()


def test_report_counts_each_tier_and_only_actionable_overdue_claims(conn):
    for tier, hours in (("gold", 4), ("silver", 24), ("standard", 72)):
        for status, seconds in (("open", -1), ("open", 0), ("open", 1),
                                ("assigned", -1), ("escalated", -1),
                                ("resolved", -1), ("closed", -1)):
            claim_id = claims.file_claim(
                conn, passenger="Bonus Passenger", flight="OA1", claim_type="lost",
                tier=tier, actor="desk", now=NOW - timedelta(hours=hours) + timedelta(seconds=seconds),
            )
            conn.execute("UPDATE claims SET status = ? WHERE id = ?", (status, claim_id))
    before = conn.total_changes
    result = claims.report_claims(conn, now=NOW)
    assert result == [
        {"tier": tier, "open": 3, "overdue": 2}
        for tier in ("standard", "silver", "gold")
    ]
    assert conn.total_changes == before


def test_empty_report_includes_all_tiers(conn):
    assert claims.report_claims(conn, now=NOW) == [
        {"tier": tier, "open": 0, "overdue": 0}
        for tier in ("standard", "silver", "gold")
    ]


def test_report_rejects_naive_time(conn):
    with pytest.raises(ValueError, match="timezone"):
        claims.report_claims(conn, now=NOW.replace(tzinfo=None))


def test_report_command_uses_supplied_now(tmp_path):
    path = tmp_path / "report.db"
    conn = connect_db(path)
    init_db(conn)
    claims.file_claim(conn, passenger="Report Passenger", flight="OA1", claim_type="lost",
                      tier="gold", actor="desk", now=NOW - timedelta(hours=4))
    conn.close()
    for time, count in ((NOW, 0), (NOW + timedelta(minutes=1), 1)):
        result = subprocess.run(
            [sys.executable, str(ROOT / "main.py"), "--db", str(path),
             "--now", time.isoformat(), "report"], capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            "tier\topen\toverdue", "standard\t0\t0", "silver\t0\t0", f"gold\t1\t{count}",
        ]


def run_hook(root, payload):
    assert HOOK.is_file(), "test guard has not been implemented"
    return subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)},
        capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
def test_hook_blocks_test_edits_before_any_write(tmp_path, tool):
    tests = tmp_path / "tests"
    tests.mkdir()
    target = tests / "approved.py"
    target.write_text("approved content\n")
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    result = run_hook(tmp_path, {
        "tool_name": tool, "cwd": str(tmp_path),
        "tool_input": {key: str(target), "content": "replacement"},
    })
    # This is the dispatch rule: the edit may execute only if the pre-hook allows it.
    if result.returncode == 0:
        target.write_text("replacement")
    assert result.returncode == 2
    assert "Blocked" in result.stderr
    assert target.read_text() == "approved content\n"


@pytest.mark.parametrize("path", ["tests/new.py", "./tests/new.py", "src/../tests/new.py"])
def test_hook_blocks_relative_paths(tmp_path, path):
    result = run_hook(tmp_path, {
        "tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": path},
    })
    assert result.returncode == 2


def test_hook_resolves_symlink_into_tests(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "tests", target_is_directory=True)
    result = run_hook(tmp_path, {
        "tool_name": "Edit", "cwd": str(tmp_path),
        "tool_input": {"file_path": "alias/approved.py"},
    })
    assert result.returncode == 2


@pytest.mark.parametrize("tool,path", [("Edit", "claims.py"), ("Read", "tests/test_claims.py")])
def test_hook_allows_implementation_edits_and_test_reads(tmp_path, tool, path):
    result = run_hook(tmp_path, {
        "tool_name": tool, "cwd": str(tmp_path), "tool_input": {"file_path": path},
    })
    assert result.returncode == 0, result.stderr


def test_hook_fails_closed_for_missing_path(tmp_path):
    assert run_hook(tmp_path, {"tool_name": "Edit", "tool_input": {}}).returncode == 2
