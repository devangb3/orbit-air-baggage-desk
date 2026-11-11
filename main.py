"""Command-line interface for the baggage desk. Parsing and output only."""

import argparse
import io
import sqlite3
import sys
from datetime import UTC, datetime

import claims
from db import connect_db, init_db

DEFAULT_DB = "baggage.db"
CLAIM_COLUMNS = (
    "id", "passenger", "flight", "type", "tier", "status",
    "assigned_agent", "filed_at", "deadline", "note",
)
HISTORY_COLUMNS = ("changed_at", "old_status", "new_status", "actor")
# Escape the backslash first so escaped output stays unambiguous.
_CELL_ESCAPES = str.maketrans({"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"})


class CliError(Exception):
    """An expected user-facing failure."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        raise CliError(message)


def build_parser():
    parser = _Parser(prog="main.py", description="Orbit Air Baggage Desk")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite database path")
    parser.add_argument("--now", help="ISO-8601 date and time; missing timezone defaults to UTC")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    file_cmd = commands.add_parser("file", help="file a new claim")
    file_cmd.add_argument("--passenger", required=True)
    file_cmd.add_argument("--flight", required=True)
    file_cmd.add_argument("--type", required=True, dest="claim_type")
    file_cmd.add_argument("--tier", required=True)
    file_cmd.add_argument("--actor", required=True)
    file_cmd.add_argument("--note")

    list_cmd = commands.add_parser("list", help="list claims")
    list_cmd.add_argument("--status")
    list_cmd.add_argument("--tier")

    assign_cmd = commands.add_parser("assign", help="assign an agent")
    assign_cmd.add_argument("id", type=int)
    assign_cmd.add_argument("--agent", required=True)
    assign_cmd.add_argument("--actor", required=True)

    for name in ("resolve", "close"):
        cmd = commands.add_parser(name, help=f"{name} a claim")
        cmd.add_argument("id", type=int)
        cmd.add_argument("--actor", required=True)

    commands.add_parser("escalate", help="escalate overdue claims")
    commands.add_parser("report", help="count open and overdue open/assigned claims by tier")

    history_cmd = commands.add_parser("history", help="show a claim's history")
    history_cmd.add_argument("id", type=int)

    seed_cmd = commands.add_parser("seed", help="import claims from a CSV file")
    seed_cmd.add_argument("csv_path")
    return parser


def _cell(value):
    """Render one value so it cannot break the one-row-per-line, tab-separated output."""
    return "" if value is None else str(value).translate(_CELL_ESCAPES)


def _print_rows(rows, columns):
    print("\t".join(columns))
    for row in rows:
        print("\t".join(_cell(row[c]) for c in columns))


def _read_csv_text(path):
    """Read a seed file as UTF-8, naming the line of any undecodable byte."""
    try:
        with open(path, "rb") as source:
            raw = source.read()
    except OSError as error:
        raise CliError(f"cannot read {path}: {error.strerror or error}") from None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        line = raw.count(b"\n", 0, error.start) + 1
        raise CliError(f"line {line}: {path} is not valid UTF-8 ({error.reason})") from None


def run(args, connection, now):
    """Dispatch one command. Returns the process exit code."""
    if args.command == "file":
        claim_id = claims.file_claim(
            connection, passenger=args.passenger, flight=args.flight,
            claim_type=args.claim_type, tier=args.tier, actor=args.actor,
            now=now, note=args.note,
        )
        print(f"Filed claim {claim_id}")
    elif args.command == "list":
        _print_rows(claims.list_claims(connection, status=args.status, tier=args.tier),
                    CLAIM_COLUMNS)
    elif args.command == "assign":
        agent = claims.assign_claim(connection, args.id, agent=args.agent, actor=args.actor,
                                    now=now)
        print(f"Assigned claim {args.id} to {agent}")
    elif args.command == "resolve":
        claims.resolve_claim(connection, args.id, actor=args.actor, now=now)
        print(f"Resolved claim {args.id}")
    elif args.command == "close":
        claims.close_claim(connection, args.id, actor=args.actor, now=now)
        print(f"Closed claim {args.id}")
    elif args.command == "escalate":
        escalated, errors = claims.escalate_claims(connection, now=now)
        if escalated:
            print("Escalated claims: " + ", ".join(map(str, escalated)))
        else:
            print("No claims escalated")
        for claim_id, message in sorted(errors.items()):
            print(f"error: failed to escalate claim {claim_id}: {message}", file=sys.stderr)
        return 1 if errors else 0
    elif args.command == "history":
        claims.get_claim(connection, args.id)
        _print_rows(claims.get_history(connection, args.id), HISTORY_COLUMNS)
    elif args.command == "report":
        _print_rows(claims.report_claims(connection, now=now), ("tier", "open", "overdue"))
    elif args.command == "seed":
        source = io.StringIO(_read_csv_text(args.csv_path), newline="")
        claim_ids = claims.seed_claims(connection, source, now=now)
        print(f"Seeded {len(claim_ids)} claims: " + ", ".join(map(str, claim_ids)))
    return 0


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        now = claims.parse_timestamp(args.now) if args.now is not None else datetime.now(UTC)
        connection = connect_db(args.db)
        try:
            init_db(connection)
            return run(args, connection, now)
        finally:
            connection.close()
    except (CliError, ValueError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
