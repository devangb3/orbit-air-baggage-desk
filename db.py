"""SQLite connection and schema setup."""

import sqlite3

from claims import CLAIM_TYPES, STATUSES, TIERS


def _in(values):
    return ", ".join(f"'{value}'" for value in values)


def connect_db(path):
    """Open a connection with foreign keys on and explicit transaction control."""
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(connection):
    """Create the schema if missing; existing data is preserved."""
    connection.executescript(f"""
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            passenger TEXT NOT NULL,
            flight TEXT NOT NULL,
            type TEXT NOT NULL CHECK (type IN ({_in(CLAIM_TYPES)})),
            tier TEXT NOT NULL CHECK (tier IN ({_in(TIERS)})),
            status TEXT NOT NULL CHECK (status IN ({_in(STATUSES)})),
            assigned_agent TEXT NULL,
            note TEXT NULL,
            filed_at TEXT NOT NULL,
            deadline TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER NOT NULL REFERENCES claims(id),
            old_status TEXT NULL CHECK (old_status IN ({_in(STATUSES)})),
            new_status TEXT NOT NULL CHECK (new_status IN ({_in(STATUSES)})),
            actor TEXT NOT NULL,
            changed_at TEXT NOT NULL
        );
    """)
