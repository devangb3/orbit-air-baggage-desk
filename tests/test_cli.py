"""CLI contract, exercised through real processes and isolated databases.

python main.py [--db PATH] [--now ISO_TIMESTAMP] <command>
file --passenger NAME --flight FLIGHT --type TYPE --tier TIER --actor ACTOR [--note NOTE]
list [--status STATUS] [--tier TIER]
assign ID --agent AGENT --actor ACTOR
resolve ID --actor ACTOR
close ID --actor ACTOR
escalate
history ID
seed CSV_PATH

Output must show the relevant claim data/IDs; no exact table or JSON format is
required. Expected errors use stderr and nonzero exit codes without tracebacks.
"""

import csv
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from db import connect_db, init_db

MAIN = Path(__file__).resolve().parents[1] / "main.py"
NOW = "2026-11-10T09:00:00Z"
STORED_NOW = "2026-11-10T09:00:00+00:00"
HEADER = ["passenger", "flight", "type", "tier", "note"]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "selected.db"
    connection = connect_db(path)
    init_db(connection)
    connection.close()
    return path


@pytest.fixture
def cli(tmp_path, db_path):
    def run(*arguments, now=NOW, explicit_db=True):
        assert MAIN.is_file(), "CLI entry point main.py has not been implemented"
        command = [sys.executable, str(MAIN)]
        if explicit_db:
            command += ["--db", str(db_path)]
        if now is not None:
            command += ["--now", now]
        return subprocess.run(
            [*command, *map(str, arguments)], cwd=tmp_path,
            capture_output=True, text=True, timeout=10, check=False,
        )

    return run


def success(result):
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    return result.stdout


def failure(result, *fragments):
    assert result.returncode != 0
    assert result.stderr.strip()
    assert "Traceback" not in result.stderr
    for fragment in fragments:
        assert fragment.lower() in result.stderr.lower()


def rows(path, table):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")]


def file_args(passenger="Avery Unique", tier="gold", actor="desk"):
    return [
        "file", "--passenger", passenger, "--flight", "OA404",
        "--type", "delayed", "--tier", tier, "--actor", actor,
    ]


def write_csv(tmp_path, data, header=HEADER):
    path = tmp_path / "seed_claims.csv"
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(header)
        writer.writerows(data)
    return path


def test_file_and_list_show_claim_and_persist_fields(cli, db_path):
    output = success(cli(*file_args(), "--note", "Blue suitcase"))
    claim = rows(db_path, "claims")[0]
    assert re.search(rf"\b{claim['id']}\b", output)
    assert claim["passenger"] == "Avery Unique"
    assert claim["flight"] == "OA404"
    assert claim["note"] == "Blue suitcase"
    assert claim["status"] == "open"
    assert claim["filed_at"] == STORED_NOW
    assert claim["deadline"] == "2026-11-10T13:00:00+00:00"
    listing = success(cli("list"))
    for value in ("Avery Unique", "OA404", "delayed", "gold", "open"):
        assert value in listing


@pytest.mark.parametrize(
    "filters,expected",
    [
        ([], {"Avery Unique", "Blair Unique", "Casey Unique"}),
        (["--status", "open"], {"Avery Unique", "Blair Unique"}),
        (["--tier", "gold"], {"Avery Unique", "Casey Unique"}),
        (["--status", "assigned", "--tier", "gold"], {"Casey Unique"}),
        (["--status", "assigned", "--tier", "silver"], set()),
    ],
)
def test_list_filters(cli, db_path, filters, expected):
    names = ("Avery Unique", "Blair Unique", "Casey Unique")
    for name, tier in zip(names, ("gold", "silver", "gold"), strict=True):
        success(cli(*file_args(name, tier)))
    claim_id = rows(db_path, "claims")[-1]["id"]
    success(cli("assign", claim_id, "--agent", "Jordan", "--actor", "desk"))
    output = success(cli("list", *filters))
    for name in names:
        assert (name in output) == (name in expected)


def test_assign_resolve_close_and_history(cli, db_path):
    success(cli(*file_args()))
    claim_id = rows(db_path, "claims")[0]["id"]
    success(cli("assign", claim_id, "--agent", "Jordan", "--actor", "assigner"))
    success(cli("resolve", claim_id, "--actor", "resolver"))
    success(cli("close", claim_id, "--actor", "closer"))
    stored = rows(db_path, "claims")[0]
    assert stored["status"] == "closed"
    assert stored["assigned_agent"] == "Jordan"
    history = rows(db_path, "history")
    assert [row["new_status"] for row in history] == ["open", "assigned", "resolved", "closed"]
    assert [row["actor"] for row in history] == ["desk", "assigner", "resolver", "closer"]
    output = success(cli("history", claim_id))
    for value in ("open", "assigned", "resolved", "closed", "assigner", "resolver", "closer"):
        assert value in output
    assert "2026-11-10" in output


def test_now_controls_exact_deadline_then_escalation(cli, db_path):
    success(cli(*file_args()))
    claim_id = rows(db_path, "claims")[0]["id"]
    success(cli("escalate", now="2026-11-10T13:00:00Z"))
    assert rows(db_path, "claims")[0]["status"] == "open"
    assert len(rows(db_path, "history")) == 1
    output = success(cli("escalate", now="2026-11-10T13:01:00Z"))
    assert re.search(rf"\b{claim_id}\b", output)
    assert rows(db_path, "claims")[0]["status"] == "escalated"
    assert rows(db_path, "history")[-1]["actor"] == "system"
    assert rows(db_path, "history")[-1]["changed_at"] == "2026-11-10T13:01:00+00:00"
    assert "Avery Unique" in success(cli("list", "--status", "escalated"))


@pytest.mark.parametrize("now", ["not-a-date", "2026-11-10T09:00:00"])
def test_invalid_now_is_clear_and_does_not_write(cli, db_path, now):
    failure(cli(*file_args(), now=now))
    assert rows(db_path, "claims") == []
    assert rows(db_path, "history") == []


def test_now_accepts_explicit_offset(cli, db_path):
    success(cli(*file_args(), now="2026-11-10T14:30:00+05:30"))
    assert rows(db_path, "claims")[0]["filed_at"] == STORED_NOW


@pytest.mark.parametrize(
    "flag,value,field",
    [("--passenger", " ", "passenger"), ("--flight", "", "flight"),
     ("--type", "missing", "type"), ("--tier", "platinum", "tier")],
)
def test_invalid_filing_values_are_errors(cli, db_path, flag, value, field):
    args = file_args()
    args[args.index(flag) + 1] = value
    failure(cli(*args), field)
    assert rows(db_path, "claims") == []
    assert rows(db_path, "history") == []


def test_actor_is_required(cli, db_path):
    failure(cli(*file_args()[:-2]), "actor")
    assert rows(db_path, "claims") == []


def test_invalid_transition_and_missing_claim_are_errors(cli, db_path):
    success(cli(*file_args()))
    claim_id = rows(db_path, "claims")[0]["id"]
    before = (rows(db_path, "claims"), rows(db_path, "history"))
    failure(cli("close", claim_id, "--actor", "desk"), "open")
    failure(cli("assign", 999, "--agent", "Jordan", "--actor", "desk"), "999")
    assert (rows(db_path, "claims"), rows(db_path, "history")) == before


def test_partial_escalation_reports_success_and_failure(cli, db_path):
    success(cli(*file_args("Failed Passenger")))
    success(cli(*file_args("Successful Passenger")))
    failed, succeeded = [row["id"] for row in rows(db_path, "claims")]
    with sqlite3.connect(db_path) as connection:
        connection.execute(f"""
            CREATE TRIGGER reject_history BEFORE INSERT ON history
            WHEN NEW.claim_id = {failed} AND NEW.new_status = 'escalated'
            BEGIN SELECT RAISE(ABORT, 'forced history failure'); END
        """)
    result = cli("escalate", now="2026-11-10T14:00:00Z")
    failure(result, str(failed), "forced history failure")
    assert re.search(rf"\b{succeeded}\b", result.stdout)
    assert [row["status"] for row in rows(db_path, "claims")] == ["open", "escalated"]
    assert len(rows(db_path, "history")) == 3


def test_seed_imports_csv_with_fixed_time_and_literal_notes(cli, db_path, tmp_path):
    note = 'Handle broken, label says "fragile".\nKeep upright.'
    write_csv(tmp_path, [
        ["Avery Seed", "OA501", "damaged", "gold", note],
        ["Blair Seed", "OA502", "lost", "silver", ""],
        ["Casey Seed", "OA503", "delayed", "standard", "Black bag"],
    ])
    success(cli("seed", "seed_claims.csv"))
    stored = rows(db_path, "claims")
    assert [row["passenger"] for row in stored] == ["Avery Seed", "Blair Seed", "Casey Seed"]
    assert stored[0]["note"] == note
    assert {row["filed_at"] for row in stored} == {STORED_NOW}
    assert {row["status"] for row in stored} == {"open"}
    assert [row["deadline"] for row in stored] == [
        "2026-11-10T13:00:00+00:00", "2026-11-11T09:00:00+00:00",
        "2026-11-13T09:00:00+00:00",
    ]
    history = rows(db_path, "history")
    assert len(history) == 3
    assert {row["actor"] for row in history} == {"seed"}
    assert {row["changed_at"] for row in history} == {STORED_NOW}


def test_seed_without_global_options_uses_default_database_and_clock(cli, tmp_path, db_path):
    write_csv(tmp_path, [["Default Seed", "OA505", "lost", "gold", ""]])
    before = datetime.now(UTC)
    success(cli("seed", "seed_claims.csv", now=None, explicit_db=False))
    after = datetime.now(UTC)
    default_db = tmp_path / "baggage.db"
    assert default_db.exists()
    stored = rows(default_db, "claims")
    assert len(stored) == 1
    assert before <= datetime.fromisoformat(stored[0]["filed_at"]) <= after
    assert rows(db_path, "claims") == []


@pytest.mark.parametrize("bad_row", [
    ["Bad Row", "OA506", "lost", "platinum", ""],
    ["Bad Row", "OA506", "lost", "gold"],
    ["Bad Row", "OA506", "lost", "gold", "", "extra"],
])
def test_bad_seed_row_reports_line_and_rolls_back_import(cli, tmp_path, db_path, bad_row):
    success(cli(*file_args("Existing Passenger")))
    before = (rows(db_path, "claims"), rows(db_path, "history"))
    write_csv(tmp_path, [["Good Row", "OA505", "lost", "gold", ""], bad_row])
    result = cli("seed", "seed_claims.csv")
    failure(result)
    assert re.search(r"(?i)(row|line)\s*[:#]?\s*3\b", result.stderr)
    assert (rows(db_path, "claims"), rows(db_path, "history")) == before


@pytest.mark.parametrize("header", [
    ["passenger", "flight", "type", "tier"],
    ["passenger", "flight", "type", "tier", "note", "extra"],
    ["passenger", "flight", "type", "tier", "note", "note"],
])
def test_bad_seed_header_is_rejected(cli, tmp_path, db_path, header):
    write_csv(tmp_path, [], header=header)
    result = cli("seed", "seed_claims.csv")
    failure(result)
    assert re.search(r"(?i)header|column", result.stderr)
    assert rows(db_path, "claims") == []


def test_seed_history_failure_rolls_back_entire_import(cli, tmp_path, db_path):
    success(cli(*file_args("Existing Passenger")))
    before = (rows(db_path, "claims"), rows(db_path, "history"))
    with sqlite3.connect(db_path) as connection:
        connection.execute("""
            CREATE TRIGGER reject_seed_history BEFORE INSERT ON history
            WHEN (SELECT passenger FROM claims WHERE id = NEW.claim_id) = 'Failing Seed'
            BEGIN SELECT RAISE(ABORT, 'forced seed history failure'); END
        """)
    write_csv(tmp_path, [
        ["Good Seed", "OA505", "lost", "gold", ""],
        ["Failing Seed", "OA506", "lost", "silver", ""],
    ])
    failure(cli("seed", "seed_claims.csv"), "forced seed history failure")
    assert (rows(db_path, "claims"), rows(db_path, "history")) == before


def test_missing_seed_file_is_a_clear_error(cli):
    failure(cli("seed", "absent.csv"), "absent.csv")


def test_database_open_failure_is_a_clear_error(cli, tmp_path):
    # An existing directory cannot be opened as a SQLite database file.
    failure(cli("--db", tmp_path, "list", explicit_db=False))
