"""Branch 2 contract; escalation itself belongs to branch 3.

Expected claims API:
    assign_claim(conn, claim_id, *, agent, actor, now)
    resolve_claim(conn, claim_id, *, actor, now)
    close_claim(conn, claim_id, *, actor, now)
    get_history(conn, claim_id) -> mapping rows ordered by changed_at, then id

Invalid transitions, blank actors/agents and missing claims raise ValueError.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import claims
from db import connect_db, init_db

FILED_AT = datetime(2026, 11, 10, 9, tzinfo=UTC)
CHANGED_AT = FILED_AT + timedelta(hours=1)


@pytest.fixture
def conn(tmp_path):
    connection = connect_db(tmp_path / "status.db")
    connection.row_factory = sqlite3.Row
    init_db(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def claim_in_status(conn):
    def create(status="open"):
        claim_id = claims.file_claim(
            conn, passenger="Morgan Reed", flight="OA202", claim_type="lost",
            tier="silver", actor="filing-agent", now=FILED_AT, note="Green suitcase",
        )
        # Prepare states directly so tests do not depend on other new operations.
        if status != "open":
            conn.execute("BEGIN")
            try:
                conn.execute(
                    "UPDATE claims SET status = ?, assigned_agent = ? WHERE id = ?",
                    (status, "Previous Agent", claim_id),
                )
                conn.execute(
                    "INSERT INTO history (claim_id, old_status, new_status, actor, changed_at) "
                    "VALUES (?, 'open', ?, 'fixture', ?)",
                    (claim_id, status, (FILED_AT + timedelta(minutes=1)).isoformat()),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        return claim_id

    return create


def snapshot(conn, claim_id):
    claim = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    history = conn.execute(
        "SELECT * FROM history WHERE claim_id = ? ORDER BY id", (claim_id,)
    ).fetchall()
    return dict(claim), [dict(row) for row in history]


def invoke(operation, conn, claim_id, **overrides):
    arguments = {"actor": "  supervisor  ", "now": CHANGED_AT}
    if operation == "assign":
        arguments["agent"] = "  Casey Jordan  "
    arguments.update(overrides)
    return getattr(claims, f"{operation}_claim")(conn, claim_id, **arguments)


@pytest.mark.parametrize(
    "before,operation,after",
    [
        ("open", "assign", "assigned"),
        ("escalated", "assign", "assigned"),
        ("assigned", "resolve", "resolved"),
        ("escalated", "resolve", "resolved"),
        ("resolved", "close", "closed"),
    ],
)
def test_allowed_transition_updates_only_status_agent_and_history(
    conn, claim_in_status, before, operation, after
):
    claim_id = claim_in_status(before)
    original, old_history = snapshot(conn, claim_id)
    invoke(operation, conn, claim_id)
    stored, history = snapshot(conn, claim_id)
    expected = dict(original, status=after)
    if operation == "assign":
        expected["assigned_agent"] = "Casey Jordan"
    assert stored == expected
    assert len(history) == len(old_history) + 1
    assert history[:-1] == old_history
    entry = history[-1]
    assert entry["claim_id"] == claim_id
    assert entry["old_status"] == before
    assert entry["new_status"] == after
    assert entry["actor"] == "supervisor"
    assert entry["changed_at"] == CHANGED_AT.isoformat()
    assert not conn.in_transaction


@pytest.mark.parametrize(
    "before,operation",
    [
        ("open", "resolve"), ("open", "close"),
        ("assigned", "assign"), ("assigned", "close"),
        ("escalated", "close"),
        ("resolved", "assign"), ("resolved", "resolve"),
        ("closed", "assign"), ("closed", "resolve"), ("closed", "close"),
    ],
)
def test_forbidden_transition_leaves_claim_and_history_unchanged(
    conn, claim_in_status, before, operation
):
    claim_id = claim_in_status(before)
    original = snapshot(conn, claim_id)
    with pytest.raises(ValueError) as error:
        invoke(operation, conn, claim_id)
    assert before in str(error.value).lower()
    assert snapshot(conn, claim_id) == original
    assert not conn.in_transaction


@pytest.mark.parametrize("status", ["open", "escalated"])
@pytest.mark.parametrize("agent", ["", " \t\n", None])
def test_assignment_requires_nonblank_agent(conn, claim_in_status, status, agent):
    claim_id = claim_in_status(status)
    original = snapshot(conn, claim_id)
    with pytest.raises(ValueError, match="agent"):
        invoke("assign", conn, claim_id, agent=agent)
    assert snapshot(conn, claim_id) == original


@pytest.mark.parametrize(
    "status,operation", [("open", "assign"), ("assigned", "resolve"), ("resolved", "close")]
)
@pytest.mark.parametrize("actor", ["", " \t\n"])
def test_status_change_requires_nonblank_actor(conn, claim_in_status, status, operation, actor):
    claim_id = claim_in_status(status)
    original = snapshot(conn, claim_id)
    with pytest.raises(ValueError, match="actor"):
        invoke(operation, conn, claim_id, actor=actor)
    assert snapshot(conn, claim_id) == original


@pytest.mark.parametrize("operation", ["assign", "resolve", "close"])
def test_missing_claim_is_rejected_without_writes(conn, operation):
    with pytest.raises(ValueError) as error:
        invoke(operation, conn, 999)
    assert "999" in str(error.value)
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0


@pytest.mark.parametrize(
    "status,operation", [("open", "assign"), ("assigned", "resolve"), ("resolved", "close")]
)
def test_status_change_rejects_naive_time(conn, claim_in_status, status, operation):
    claim_id = claim_in_status(status)
    original = snapshot(conn, claim_id)
    with pytest.raises(ValueError, match="(?i)timezone|aware|offset|utc"):
        invoke(operation, conn, claim_id, now=CHANGED_AT.replace(tzinfo=None))
    assert snapshot(conn, claim_id) == original


def test_history_returns_complete_lifecycle_for_only_requested_claim(conn, claim_in_status):
    claim_id = claim_in_status()
    claim_in_status()  # Another passenger's history must not leak into this result.
    invoke("assign", conn, claim_id)
    invoke("resolve", conn, claim_id)  # Same time: history ID breaks the tie.
    invoke("close", conn, claim_id, now=CHANGED_AT + timedelta(minutes=1))
    history = list(claims.get_history(conn, claim_id))
    assert [(row["old_status"], row["new_status"]) for row in history] == [
        (None, "open"), ("open", "assigned"),
        ("assigned", "resolved"), ("resolved", "closed"),
    ]
    assert [row["actor"] for row in history] == ["filing-agent"] + ["supervisor"] * 3
    assert [row["changed_at"] for row in history] == [
        FILED_AT.isoformat(), CHANGED_AT.isoformat(), CHANGED_AT.isoformat(),
        (CHANGED_AT + timedelta(minutes=1)).isoformat(),
    ]
    assert all(row["claim_id"] == claim_id for row in history)
    assert history[1]["id"] < history[2]["id"]


def test_history_orders_by_time_before_id(conn, claim_in_status):
    claim_id = claim_in_status("escalated")
    # Insert an earlier audit entry last to distinguish time ordering from ID ordering.
    conn.execute(
        "INSERT INTO history (claim_id, old_status, new_status, actor, changed_at) "
        "VALUES (?, 'open', 'assigned', 'fixture', ?)",
        (claim_id, (FILED_AT + timedelta(seconds=30)).isoformat()),
    )
    rows = list(claims.get_history(conn, claim_id))
    assert [row["new_status"] for row in rows] == ["open", "assigned", "escalated"]
