"""Branch 1 contract: schema, filing, validation, deadlines and listing.

Expected API:
    db.connect_db(path) -> sqlite3.Connection
    db.init_db(connection) -> None
    claims.file_claim(connection, *, passenger, flight, claim_type, tier,
                      actor, now, note=None) -> integer claim ID
    claims.list_claims(connection, *, status=None, tier=None) -> mapping rows

Imports live in fixtures so pytest can collect every case before implementation
exists. SQL fixtures for list filters do not depend on later transition features.
"""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

FILED_AT = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    from db import connect_db, init_db

    connection = connect_db(tmp_path / "claims.db")
    connection.row_factory = sqlite3.Row
    try:
        init_db(connection)
        yield connection
    finally:
        connection.close()


@pytest.fixture
def file_claim(conn):
    from claims import file_claim as create_claim

    def file_with_defaults(**overrides):
        fields = {
            "passenger": "Avery Lane",
            "flight": "OA101",
            "claim_type": "delayed",
            "tier": "gold",
            "actor": "desk-agent",
            "now": FILED_AT,
        }
        fields.update(overrides)
        return create_claim(conn, **fields)

    return file_with_defaults


def test_schema_has_required_columns_and_enables_foreign_keys(conn):
    expected_columns = {
        "claims": {
            "id", "passenger", "flight", "type", "tier", "status",
            "assigned_agent", "note", "filed_at", "deadline",
        },
        "history": {
            "id", "claim_id", "old_status", "new_status", "actor", "changed_at",
        },
    }
    for table, expected in expected_columns.items():
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert expected <= columns
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    foreign_keys = conn.execute("PRAGMA foreign_key_list(history)").fetchall()
    assert any(
        row["table"] == "claims" and row["from"] == "claim_id" and row["to"] == "id"
        for row in foreign_keys
    )


def test_schema_initialization_preserves_existing_claims(conn, file_claim):
    from db import init_db

    claim_id = file_claim()
    init_db(conn)
    assert conn.execute("SELECT id FROM claims").fetchone()["id"] == claim_id
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 1


@pytest.mark.parametrize("claim_type", ["delayed", "lost", "damaged"])
def test_filing_stores_fields_and_starts_open(conn, file_claim, claim_type):
    note = "Blue suitcase; handle broken."
    claim_id = file_claim(claim_type=claim_type, note=note)
    assert isinstance(claim_id, int) and claim_id > 0
    claim = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    assert claim is not None
    assert claim["passenger"] == "Avery Lane"
    assert claim["flight"] == "OA101"
    assert claim["type"] == claim_type
    assert claim["tier"] == "gold"
    assert claim["status"] == "open"
    assert claim["assigned_agent"] is None
    assert claim["note"] == note
    assert claim["filed_at"] == FILED_AT.isoformat()


def test_filing_accepts_an_omitted_note(conn, file_claim):
    claim_id = file_claim()
    claim = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    assert claim["note"] in (None, "")


def test_filing_creates_one_complete_initial_history_entry(conn, file_claim):
    claim_id = file_claim()
    history = conn.execute("SELECT * FROM history").fetchall()
    assert len(history) == 1
    assert history[0]["claim_id"] == claim_id
    assert history[0]["old_status"] is None
    assert history[0]["new_status"] == "open"
    assert history[0]["actor"] == "desk-agent"
    assert history[0]["changed_at"] == FILED_AT.isoformat()


@pytest.mark.parametrize("field", ["passenger", "flight"])
@pytest.mark.parametrize("blank", ["", " \t\n"])
def test_filing_rejects_blank_names_without_writes(conn, file_claim, field, blank):
    with pytest.raises(ValueError, match=field):
        file_claim(**{field: blank})
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0


@pytest.mark.parametrize(
    "field,bad_value,message_field,allowed",
    [
        ("claim_type", "missing", "type", ("delayed", "lost", "damaged")),
        ("claim_type", "", "type", ("delayed", "lost", "damaged")),
        ("tier", "platinum", "tier", ("standard", "silver", "gold")),
        ("tier", "", "tier", ("standard", "silver", "gold")),
    ],
)
def test_filing_rejects_invalid_choices_clearly(
    conn, file_claim, field, bad_value, message_field, allowed
):
    with pytest.raises(ValueError) as error:
        file_claim(**{field: bad_value})
    message = str(error.value).lower()
    assert message_field in message
    assert all(value in message for value in allowed)
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0


@pytest.mark.parametrize("actor", ["", " \t\n"])
def test_filing_rejects_blank_actor_without_writes(conn, file_claim, actor):
    with pytest.raises(ValueError, match="actor"):
        file_claim(actor=actor)
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0


def test_filing_trims_actor_in_history(conn, file_claim):
    claim_id = file_claim(actor="  desk-agent  ")
    row = conn.execute("SELECT actor FROM history WHERE claim_id = ?", (claim_id,))
    assert row.fetchone()["actor"] == "desk-agent"


@pytest.mark.parametrize("tier,hours", [("gold", 4), ("silver", 24), ("standard", 72)])
def test_deadline_uses_the_tier_window(conn, file_claim, tier, hours):
    claim_id = file_claim(tier=tier)
    row = conn.execute("SELECT deadline FROM claims WHERE id = ?", (claim_id,))
    assert row.fetchone()["deadline"] == (FILED_AT + timedelta(hours=hours)).isoformat()


def test_filing_normalizes_offset_times_to_utc_and_preserves_microseconds(conn, file_claim):
    filed_at = datetime(
        2026, 11, 9, 9, 0, 0, 123456,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    expected_utc = datetime(2026, 11, 9, 3, 30, 0, 123456, tzinfo=UTC)
    claim_id = file_claim(now=filed_at)
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    assert row["filed_at"] == expected_utc.isoformat()
    assert row["deadline"] == (expected_utc + timedelta(hours=4)).isoformat()
    history = conn.execute("SELECT changed_at FROM history WHERE claim_id = ?", (claim_id,))
    assert history.fetchone()["changed_at"] == expected_utc.isoformat()


def test_filing_rejects_naive_time_without_writes(conn, file_claim):
    with pytest.raises(ValueError, match="(?i)timezone|aware|offset|utc"):
        file_claim(now=datetime(2026, 11, 9, 9, 0))
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0


@pytest.mark.parametrize(
    "column,value", [("type", "missing"), ("tier", "platinum"), ("status", "pending")]
)
def test_database_rejects_invalid_claim_values_directly(conn, file_claim, column, value):
    claim_id = file_claim()
    original = dict(conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone())
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"), conn:
        conn.execute(f"UPDATE claims SET {column} = ? WHERE id = ?", (value, claim_id))
    stored = dict(conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone())
    assert stored == original


@pytest.mark.parametrize("column", ["old_status", "new_status"])
def test_database_rejects_invalid_history_statuses(conn, file_claim, column):
    claim_id = file_claim()
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"), conn:
        conn.execute(
            f"UPDATE history SET {column} = ? WHERE claim_id = ?", ("pending", claim_id)
        )
    row = conn.execute("SELECT * FROM history WHERE claim_id = ?", (claim_id,)).fetchone()
    assert row["old_status"] is None
    assert row["new_status"] == "open"


def test_database_rejects_history_for_a_missing_claim(conn):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"), conn:
        conn.execute(
            "INSERT INTO history (claim_id, old_status, new_status, actor, changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (999, None, "open", "desk-agent", FILED_AT.isoformat()),
        )
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0


@pytest.mark.parametrize(
    "filters,expected_positions",
    [
        ({}, {0, 1, 2, 3}),
        ({"status": "open"}, {0, 1}),
        ({"status": "assigned"}, {2}),
        ({"tier": "gold"}, {0, 2}),
        ({"tier": "silver"}, {1}),
        ({"tier": "standard"}, {3}),
        ({"status": "open", "tier": "gold"}, {0}),
        ({"status": "assigned", "tier": "silver"}, set()),
    ],
)
def test_listing_filters_by_status_and_tier(conn, file_claim, filters, expected_positions):
    from claims import list_claims

    ids = [file_claim(tier=tier) for tier in ("gold", "silver", "gold", "standard")]
    # Prepare other statuses directly: transition behavior belongs to branch 2.
    with conn:
        conn.execute(
            "UPDATE claims SET status = 'assigned', assigned_agent = 'Morgan' WHERE id = ?",
            (ids[2],),
        )
        conn.execute("UPDATE claims SET status = 'closed' WHERE id = ?", (ids[3],))
    assert len(set(ids)) == 4
    results = list_claims(conn, **filters)
    returned_ids = [row["id"] for row in results]
    assert len(returned_ids) == len(set(returned_ids))
    assert set(returned_ids) == {ids[position] for position in expected_positions}


def test_listing_an_empty_database_returns_no_claims(conn):
    from claims import list_claims

    assert list(list_claims(conn)) == []
