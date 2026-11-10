"""Business rules for baggage claims. Never reads the clock."""

import csv
import sqlite3
from datetime import UTC, datetime, timedelta

CLAIM_TYPES = ("delayed", "lost", "damaged")
TIERS = ("standard", "silver", "gold")
STATUSES = ("open", "assigned", "escalated", "resolved", "closed")
DEADLINE_HOURS = {"gold": 4, "silver": 24, "standard": 72}
TRANSITIONS = {
    "open": ("assigned", "escalated"),
    "assigned": ("resolved", "escalated"),
    "escalated": ("assigned", "resolved"),
    "resolved": ("closed",),
    "closed": (),
}


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
    # An omitted or blank note is stored as NULL; any other note is kept verbatim.
    if note is not None and not note.strip():
        note = None
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


def _change_status(connection, claim_id, new_status, *, actor, now, agent=None):
    """Move a claim to new_status with its history row in one transaction."""
    actor = _required_text("actor", actor)
    if new_status == "assigned":
        agent = _required_text("agent", agent)
    changed_at = to_utc(now)
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT status FROM claims WHERE id = ?", (claim_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"claim {claim_id} not found")
        old_status = row[0]
        if new_status not in TRANSITIONS[old_status]:
            raise ValueError(f"cannot move claim {claim_id} from {old_status} to {new_status}")
        if new_status == "assigned":
            connection.execute(
                "UPDATE claims SET status = ?, assigned_agent = ? WHERE id = ?",
                (new_status, agent, claim_id),
            )
        else:
            connection.execute(
                "UPDATE claims SET status = ? WHERE id = ?", (new_status, claim_id)
            )
        connection.execute(
            "INSERT INTO history (claim_id, old_status, new_status, actor, changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (claim_id, old_status, new_status, actor, changed_at.isoformat()),
        )
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
    return agent


def assign_claim(connection, claim_id, *, agent, actor, now):
    """Assign an agent and move the claim to assigned. Returns the stored agent."""
    return _change_status(connection, claim_id, "assigned", agent=agent, actor=actor, now=now)


def resolve_claim(connection, claim_id, *, actor, now):
    """Move the claim to resolved."""
    _change_status(connection, claim_id, "resolved", actor=actor, now=now)


def close_claim(connection, claim_id, *, actor, now):
    """Move the claim to closed."""
    _change_status(connection, claim_id, "closed", actor=actor, now=now)


def get_history(connection, claim_id):
    """Return a claim's history ordered by change time, then history ID."""
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute(
        "SELECT * FROM history WHERE claim_id = ? ORDER BY id", (claim_id,)
    ).fetchall()
    # Compare parsed datetimes (not strings); sort is stable, so ID breaks ties.
    return sorted(rows, key=lambda row: datetime.fromisoformat(row["changed_at"]))


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


def is_overdue(deadline, *, now):
    """Return True only strictly after the deadline; both times must be aware."""
    return to_utc(now) > to_utc(deadline)


ESCALATABLE = ("open", "assigned")
ESCALATION_ACTOR = "system"


def _escalate_one(connection, claim_id, changed_at):
    """Escalate one claim with its history row in its own transaction.

    Returns True if escalated, False if the claim is no longer eligible.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT status, deadline FROM claims WHERE id = ?", (claim_id,)
        ).fetchone()
        # Re-check inside the transaction in case the claim changed meanwhile.
        if (
            row is None
            or row[0] not in ESCALATABLE
            or not is_overdue(datetime.fromisoformat(row[1]), now=changed_at)
        ):
            connection.execute("ROLLBACK")
            return False
        connection.execute(
            "UPDATE claims SET status = 'escalated' WHERE id = ?", (claim_id,)
        )
        connection.execute(
            "INSERT INTO history (claim_id, old_status, new_status, actor, changed_at) "
            "VALUES (?, ?, 'escalated', ?, ?)",
            (claim_id, row[0], ESCALATION_ACTOR, changed_at.isoformat()),
        )
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
    return True


def escalate_claims(connection, *, now):
    """Escalate every overdue open or assigned claim, one transaction per claim.

    Returns (escalated_ids, errors): successful IDs in ascending order, and a
    mapping of failed claim IDs to error messages. A failure rolls back that
    claim's update and history together; remaining claims are still processed.
    """
    changed_at = to_utc(now)
    placeholders = ", ".join("?" for _ in ESCALATABLE)
    candidates = connection.execute(
        f"SELECT id, deadline FROM claims WHERE status IN ({placeholders}) ORDER BY id",
        ESCALATABLE,
    ).fetchall()
    eligible = [
        row[0] for row in candidates
        if is_overdue(datetime.fromisoformat(row[1]), now=changed_at)
    ]
    escalated, errors = [], {}
    for claim_id in eligible:
        try:
            if _escalate_one(connection, claim_id, changed_at):
                escalated.append(claim_id)
        except (sqlite3.Error, ValueError) as error:
            errors[claim_id] = str(error) or type(error).__name__
    return escalated, errors


def parse_timestamp(text):
    """Parse an ISO-8601 timestamp with an explicit Z or UTC offset, as UTC."""
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        raise ValueError(
            f"invalid timestamp {text!r}: use ISO-8601 with a date, time and Z or UTC offset"
        ) from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(
            f"invalid timestamp {text!r}: an explicit Z or UTC offset is required"
        )
    return moment.astimezone(UTC)


def get_claim(connection, claim_id):
    """Return the claim row, or raise ValueError if it does not exist."""
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    row = cursor.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if row is None:
        raise ValueError(f"claim {claim_id} not found")
    return row


SEED_HEADER = ("passenger", "flight", "type", "tier", "note")
SEED_ACTOR = "seed"


def _next_record(reader, line):
    """Return the next parsed CSV record, naming the line if the CSV is malformed."""
    try:
        return next(reader, None)
    except csv.Error as error:
        raise ValueError(f"line {line}: malformed CSV: {error}") from error


def _seed_records(source):
    """Yield (line_number, row) pairs, validating the header and row widths.

    Parsing is strict, so malformed CSV such as an unterminated quoted field is
    rejected instead of silently absorbing the rest of the file. Line numbers
    count the header as line 1 and give the physical line where each record
    starts, so quoted multi-line notes do not misreport later rows.
    """
    reader = csv.reader(source, strict=True)
    header = _next_record(reader, 1)
    if header is None:
        raise ValueError("CSV file is empty; expected header: " + ",".join(SEED_HEADER))
    duplicates = sorted({name for name in header if header.count(name) > 1})
    missing = [name for name in SEED_HEADER if name not in header]
    unexpected = [name for name in header if name not in SEED_HEADER]
    if duplicates or missing or unexpected:
        problems = []
        if missing:
            problems.append(f"missing columns: {', '.join(missing)}")
        if duplicates:
            problems.append(f"duplicate columns: {', '.join(duplicates)}")
        if unexpected:
            problems.append(f"unexpected columns: {', '.join(unexpected)}")
        raise ValueError(
            f"invalid CSV header ({'; '.join(problems)}); expected: {','.join(SEED_HEADER)}"
        )
    while True:
        start = reader.line_num + 1
        record = _next_record(reader, start)
        if record is None:
            return
        if not record:
            continue
        if len(record) != len(header):
            raise ValueError(
                f"line {start}: expected {len(header)} columns, got {len(record)}"
            )
        yield start, dict(zip(header, record, strict=True))


def seed_claims(connection, source, *, now):
    """File every CSV record in one transaction; any failure rolls back all.

    `source` is an open text file (opened with newline=""). All claims share
    `now` and the actor "seed". Returns the new claim IDs in file order.
    """
    filed_at = to_utc(now)
    claim_ids = []
    connection.execute("BEGIN IMMEDIATE")
    try:
        for line, record in _seed_records(source):
            try:
                claim_ids.append(insert_claim(
                    connection, passenger=record["passenger"], flight=record["flight"],
                    claim_type=record["type"], tier=record["tier"], actor=SEED_ACTOR,
                    now=filed_at, note=record["note"],
                ))
            except (ValueError, sqlite3.Error) as error:
                raise ValueError(f"line {line}: {error}") from error
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
    return claim_ids
