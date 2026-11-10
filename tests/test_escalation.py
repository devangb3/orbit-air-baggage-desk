"""Branch 3 contract: overdue boundaries, escalation and atomic history.

Expected claims API:
    is_overdue(deadline: datetime, *, now: datetime) -> bool
    escalate_claims(connection, *, now: datetime) -> (escalated_ids, errors)

escalated_ids is a list of successful IDs in ascending order. errors maps failed
claim IDs to nonempty error strings, allowing the CLI to report partial failure.
Each claim has its own transaction, as settled in PLAN.md. Both time arguments
to is_overdue must be aware; escalate_claims must reject naive now before writes.
"""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

import claims
from db import connect_db, init_db

FILED_AT = datetime(2026, 11, 10, 9, tzinfo=UTC)
OVERDUE_AT = FILED_AT + timedelta(hours=73)


@pytest.fixture
def conn(tmp_path):
    connection = connect_db(tmp_path / "escalation.db")
    connection.row_factory = sqlite3.Row
    init_db(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def make_claim(conn):
    def create(*, tier="gold", status="open", now=FILED_AT):
        claim_id = claims.file_claim(
            conn, passenger="Taylor Quinn", flight="OA303", claim_type="delayed",
            tier=tier, actor="desk", now=now, note="Red suitcase",
        )
        if status in ("assigned", "resolved", "closed"):
            claims.assign_claim(conn, claim_id, agent="Casey", actor="desk", now=now)
        if status in ("resolved", "closed"):
            claims.resolve_claim(conn, claim_id, actor="desk", now=now)
        if status == "closed":
            claims.close_claim(conn, claim_id, actor="desk", now=now)
        if status == "escalated":
            # Already-escalated fixture independent of the new batch operation.
            conn.execute("BEGIN")
            try:
                conn.execute("UPDATE claims SET status = 'escalated' WHERE id = ?", (claim_id,))
                conn.execute(
                    "INSERT INTO history (claim_id, old_status, new_status, actor, changed_at) "
                    "VALUES (?, 'open', 'escalated', 'system', ?)",
                    (claim_id, (now + timedelta(hours=73)).isoformat()),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        return claim_id

    return create


def snapshot(conn, claim_id):
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    history = conn.execute(
        "SELECT * FROM history WHERE claim_id = ? ORDER BY id", (claim_id,)
    ).fetchall()
    return dict(row), [dict(entry) for entry in history]


@pytest.mark.parametrize("tier,hours", [("gold", 4), ("silver", 24), ("standard", 72)])
@pytest.mark.parametrize("seconds,expected", [(-60, False), (0, False), (60, True), (1, True)])
def test_overdue_boundary_and_escalation(conn, make_claim, tier, hours, seconds, expected):
    claim_id = make_claim(tier=tier)
    before = snapshot(conn, claim_id)
    deadline = FILED_AT + timedelta(hours=hours)
    now = deadline + timedelta(seconds=seconds)
    assert claims.is_overdue(deadline, now=now) is expected
    ids, errors = claims.escalate_claims(conn, now=now)
    assert errors == {}
    assert ids == ([claim_id] if expected else [])
    if expected:
        assert snapshot(conn, claim_id)[0]["status"] == "escalated"
    else:
        assert snapshot(conn, claim_id) == before


@pytest.mark.parametrize("status", ["open", "assigned"])
def test_escalation_preserves_claim_fields_and_records_complete_history(conn, make_claim, status):
    claim_id = make_claim(status=status)
    before, old_history = snapshot(conn, claim_id)
    ids, errors = claims.escalate_claims(conn, now=OVERDUE_AT)
    stored, history = snapshot(conn, claim_id)
    assert ids == [claim_id]
    assert errors == {}
    assert stored == dict(before, status="escalated")
    assert history[:-1] == old_history
    assert len(history) == len(old_history) + 1
    assert {key: history[-1][key] for key in (
        "claim_id", "old_status", "new_status", "actor", "changed_at"
    )} == {
        "claim_id": claim_id, "old_status": status, "new_status": "escalated",
        "actor": "system", "changed_at": OVERDUE_AT.isoformat(),
    }
    assert not conn.in_transaction


def test_mixed_batch_escalates_only_overdue_open_and_assigned_claims(conn, make_claim):
    first = make_claim(status="assigned")
    skipped = [make_claim(status=s) for s in ("escalated", "resolved", "closed")]
    skipped += [make_claim(status=s, now=OVERDUE_AT) for s in ("open", "assigned")]
    # At exactly the deadline, an assigned claim must also remain unchanged.
    skipped.append(make_claim(status="assigned", now=OVERDUE_AT - timedelta(hours=4)))
    last = make_claim()
    originals = {claim_id: snapshot(conn, claim_id) for claim_id in skipped}
    ids, errors = claims.escalate_claims(conn, now=OVERDUE_AT)
    assert ids == [first, last]
    assert errors == {}
    for claim_id, original in originals.items():
        assert snapshot(conn, claim_id) == original
    for claim_id in ids:
        assert snapshot(conn, claim_id)[0]["status"] == "escalated"


def test_repeated_escalation_is_a_noop(conn, make_claim):
    claim_id = make_claim()
    assert claims.escalate_claims(conn, now=OVERDUE_AT) == ([claim_id], {})
    before = snapshot(conn, claim_id)
    assert claims.escalate_claims(conn, now=OVERDUE_AT + timedelta(hours=1)) == ([], {})
    assert snapshot(conn, claim_id) == before


def test_empty_database_has_no_escalations(conn):
    assert claims.escalate_claims(conn, now=OVERDUE_AT) == ([], {})


def test_offset_and_fractional_times_are_compared_as_instants(conn, make_claim):
    claim_id = make_claim()
    deadline = FILED_AT + timedelta(hours=4)
    # Lexical ISO comparisons can misorder a fractional second against '+00:00'.
    now = (deadline + timedelta(microseconds=1)).astimezone(
        timezone(timedelta(hours=-5))
    )
    assert claims.is_overdue(deadline, now=now) is True
    assert claims.escalate_claims(conn, now=now) == ([claim_id], {})
    history = snapshot(conn, claim_id)[1]
    assert history[-1]["changed_at"] == now.astimezone(UTC).isoformat()


@pytest.mark.parametrize("argument", ["deadline", "now"])
def test_overdue_rejects_naive_times(argument):
    values = {"deadline": FILED_AT, "now": OVERDUE_AT}
    values[argument] = values[argument].replace(tzinfo=None)
    with pytest.raises(ValueError, match="(?i)timezone|aware|offset|utc"):
        claims.is_overdue(values["deadline"], now=values["now"])


def test_escalation_rejects_naive_now_without_writes(conn, make_claim):
    claim_id = make_claim()
    before = snapshot(conn, claim_id)
    with pytest.raises(ValueError, match="(?i)timezone|aware|offset|utc"):
        claims.escalate_claims(conn, now=OVERDUE_AT.replace(tzinfo=None))
    assert snapshot(conn, claim_id) == before
    assert not conn.in_transaction


@pytest.mark.parametrize("status", ["open", "assigned"])
def test_history_failure_rolls_back_claim_and_continues_batch(conn, make_claim, status):
    earlier = make_claim()
    failing = make_claim(status=status)
    later = make_claim(status="assigned")
    original = snapshot(conn, failing)
    # IDs are database-generated integers, not external SQL input.
    conn.execute(f"""
        CREATE TRIGGER reject_one_history BEFORE INSERT ON history
        WHEN NEW.claim_id = {failing} AND NEW.new_status = 'escalated'
        BEGIN SELECT RAISE(ABORT, 'forced history failure'); END
    """)
    ids, errors = claims.escalate_claims(conn, now=OVERDUE_AT)
    assert not conn.in_transaction
    # Reopen to check persisted state, not just this connection's view.
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    reopened = connect_db(path)
    reopened.row_factory = sqlite3.Row
    try:
        assert snapshot(reopened, failing) == original
        for claim_id in (earlier, later):
            row, history = snapshot(reopened, claim_id)
            assert row["status"] == "escalated"
            assert history[-1]["new_status"] == "escalated"
            assert history[-1]["actor"] == "system"
    finally:
        reopened.close()
    assert ids == [earlier, later]
    assert set(errors) == {failing}
    assert "forced history failure" in errors[failing]
