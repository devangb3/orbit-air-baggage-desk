"""Business rules for baggage claims. Never reads the clock."""

import sqlite3
from datetime import UTC, datetime, timedelta

CLAIM_TYPES = ("delayed", "lost", "damaged")
TIERS = ("standard", "silver", "gold")
STATUSES = ("open", "assigned", "escalated", "resolved", "closed")
DEADLINE_HOURS = {"gold": 4, "silver": 24, "standard": 72}


def to_utc(moment):
    """Return a timezone-aware datetime normalized to UTC; reject naive ones."""
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("time must be timezone-aware (explicit UTC offset or Z)")
    return moment.astimezone(UTC)


def _required_text(field, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be blank")
    return value.strip()


def _choice(field, value, allowed):
    if value not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(allowed)}")
    return value


def insert_claim(connection, *, passenger, flight, claim_type, tier, actor, now, note=None):
    """Validate and insert a claim plus its history row. Never commits."""
    passenger = _required_text("passenger", passenger)
    flight = _required_text("flight", flight)
    claim_type = _choice("type", claim_type, CLAIM_TYPES)
    tier = _choice("tier", tier, TIERS)
    actor = _required_text("actor", actor)
    filed_at = to_utc(now)
    deadline = filed_at + timedelta(hours=DEADLINE_HOURS[tier])
    cursor = connection.execute(
        "INSERT INTO claims (passenger, flight, type, tier, status, note, filed_at, deadline) "
        "VALUES (?, ?, ?, ?, 'open', ?, ?, ?)",
        (passenger, flight, claim_type, tier, note, filed_at.isoformat(), deadline.isoformat()),
    )
    claim_id = cursor.lastrowid
    connection.execute(
        "INSERT INTO history (claim_id, old_status, new_status, actor, changed_at) "
        "VALUES (?, NULL, 'open', ?, ?)",
        (claim_id, actor, filed_at.isoformat()),
    )
    return claim_id


def file_claim(connection, *, passenger, flight, claim_type, tier, actor, now, note=None):
    """File a claim and its initial history row in one transaction."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        claim_id = insert_claim(
            connection, passenger=passenger, flight=flight, claim_type=claim_type,
            tier=tier, actor=actor, now=now, note=note,
        )
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
    return claim_id


def list_claims(connection, *, status=None, tier=None):
    """List claims ordered by ID, optionally filtered by status and tier."""
    clauses, params = [], []
    if status is not None:
        clauses.append("status = ?")
        params.append(_choice("status", status, STATUSES))
    if tier is not None:
        clauses.append("tier = ?")
        params.append(_choice("tier", tier, TIERS))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    return cursor.execute(f"SELECT * FROM claims{where} ORDER BY id", params).fetchall()
