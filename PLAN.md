# Orbit Air Baggage Desk: Plan

## Problem
Baggage claims get lost in inbox threads. Agents don't know what's theirs, nobody can show who changed a claim and when, and high-tier passengers' claims go overdue unnoticed. Orbit Air Baggage Desk gives the desk one place to file claims, assign them, move them through a fixed lifecycle, and escalate the ones past their deadline.

Stack: Python standard library only (`argparse`, `sqlite3`, `datetime`, `csv`), with `pytest` for tests.

## Files
| File | Role |
|---|---|
| `db.py` | Opens SQLite connections and creates the schema. |
| `claims.py` | All business rules: filing, validation, deadlines, transitions, escalation, history. Takes a connection and a `now` argument; never reads the clock. |
| `main.py` | The CLI: argument parsing, calls into `claims.py`, prints output. No rules live here. |
| `tests/test_claims.py` | All tests. Each test uses a fresh temp SQLite database. |

## Rules
**Fields:** ID, passenger, flight, type, tier, status, assigned agent, note, filed time, deadline.

**Validation**
- Passenger and flight must be non-blank. Whitespace-only counts as blank.
- Type is one of `delayed`, `lost`, `damaged`. Tier is one of `standard`, `silver`, `gold`.
- Errors name the field and list the allowed values.

**Deadlines** are `filed_at` plus the tier's window. Gold is 4h, silver is 24h and standard is 72h. Overdue means `now > deadline` strictly, so a claim is not overdue at exactly the deadline.

**Status lifecycle:** a claim starts `open`.

| From | Allowed to |
|---|---|
| open | assigned, escalated |
| assigned | resolved, escalated |
| escalated | assigned, resolved |
| resolved | closed |
| closed | nothing |

**Escalation** applies only to overdue claims that are `open` or `assigned`.

**History:** filing and every status change record old status, new status, actor and time. Filing records old status as `NULL`. The claim write and its history row happen in one transaction. If the history insert fails, both roll back.

## Tables
**claims**
| Column | Type / constraint |
|---|---|
| id | INTEGER PRIMARY KEY AUTOINCREMENT |
| passenger | TEXT NOT NULL |
| flight | TEXT NOT NULL |
| type | TEXT NOT NULL, CHECK in (delayed, lost, damaged) |
| tier | TEXT NOT NULL, CHECK in (standard, silver, gold) |
| status | TEXT NOT NULL, CHECK in (open, assigned, escalated, resolved, closed) |
| assigned_agent | TEXT NULL |
| note | TEXT NULL |
| filed_at | TEXT NOT NULL (UTC ISO-8601) |
| deadline | TEXT NOT NULL (UTC ISO-8601) |

**history**
| Column | Type / constraint |
|---|---|
| id | INTEGER PRIMARY KEY AUTOINCREMENT |
| claim_id | INTEGER NOT NULL, FOREIGN KEY to claims(id) |
| old_status | TEXT NULL, CHECK (same status values) |
| new_status | TEXT NOT NULL, CHECK (same status values) |
| actor | TEXT NOT NULL |
| changed_at | TEXT NOT NULL (UTC ISO-8601) |

## Commands
Run as `python main.py [--db PATH] [--now ISO_TIMESTAMP] <command>`. Both global options go before the command. The database defaults to `baggage.db` in the current working directory; `--db` selects an isolated database for tests and demonstrations. `--now` defaults to the real UTC clock in `main.py` only. Rule functions always receive a timezone-aware `now` as a parameter.

| Command | Behaviour |
|---|---|
| `file` | Files a claim from passenger, flight, type, tier, an optional note and required `--actor`. |
| `list` | Lists claims, with optional `--status` and `--tier` filters. |
| `assign` | Requires `--agent` and `--actor`; assigns the agent and moves the claim to `assigned`. |
| `resolve` | Requires `--actor`; moves the claim to `resolved`. |
| `close` | Requires `--actor`; moves the claim to `closed`. |
| `escalate` | Escalates every overdue open or assigned claim and reports the escalated IDs. |
| `history` | Shows a claim's history in time order. |
| `seed <csv_path>` | Files claims from a CSV file. |

## Branches and test minimums
Each branch is merged only when its tests pass. Tests are written first, so each branch starts red.

### 1. `feature/schema-and-filing` (min 5 tests)
Covers schema, filing, validation, deadlines and listing.
1. Filing stores a claim as `open` with the given fields.
2. Filing writes one history row (old `NULL`, new `open`, actor, time).
3. Blank or whitespace passenger or flight is rejected with a clear message.
4. An invalid type or tier is rejected with a message listing the allowed values.
5. Deadlines are `filed_at` plus 4h, 24h or 72h for gold, silver or standard, and listing filters by status and tier.

### 2. `feature/assign-and-status` (min 6 tests)
Covers assignment, transitions and history.
1. Assigning an open claim sets the agent and status `assigned`.
2. Every allowed transition in the table succeeds.
3. Disallowed transitions are rejected (for example `open → resolved` and `closed → anything`), and the claim and history are unchanged.
4. Each transition appends a history row with old status, new status, actor and time.
5. `escalated → assigned` requires an agent.
6. History returns rows in order for a claim with several changes.

### 3. `feature/deadlines-and-escalation` (min 6 tests)
Covers overdue rules, escalation and rollback.
1. A claim exactly at its deadline is not overdue.
2. A claim one second past its deadline is overdue.
3. Escalation moves overdue open and assigned claims to `escalated` and returns their IDs.
4. Escalation skips escalated, resolved and closed claims, and claims that are not yet overdue.
5. Each escalation writes a history row with actor `system`.
6. If the history insert fails, the claim change rolls back too (inject a failure and check both tables).

### 4. `feature/cli` (min 3 tests)
Covers commands, `--now` and seed.
1. `file` then `list` through `main.py` shows the claim, and `--status` and `--tier` filters work.
2. `--now` placed before the command controls overdue checks, so `escalate` with a future `--now` reports the expected IDs.
3. `seed` loads a valid CSV, and a bad row gives a clear error naming the row.

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| **Time handling.** Mixed timezones or naive timestamps make overdue checks wrong or flaky. | Store UTC ISO-8601 only. Reject `--now` values without a clear format. Rule functions take `now`, so tests never touch the clock. Test the exact-deadline boundary. |
| **Atomicity.** Python's `sqlite3` opens transactions implicitly, and a partial write (claim without history) would corrupt the audit trail. | Give each operation an explicit transaction boundary covering claim and history. Seed owns one outer transaction for all rows; its filing helper must not commit independently or use nested connection context managers that commit early. Test rollback by forcing the history insert to fail. |
| **Rules drift between code and database.** The CHECK constraints and the Python validation could disagree. | Define the type, tier and status lists once in `claims.py` and build the CHECK clauses from them in `db.py`. Test that bad values fail both layers. |
| **Seed leaves a half-loaded database.** One bad CSV row could leave earlier rows saved. | Validate and load the whole file in one transaction (all or nothing), with errors naming the row. Verify that a later invalid row or history failure leaves neither imported claims nor their history. |
| **Foreign keys off by default in SQLite.** History could reference missing claims. | Run `PRAGMA foreign_keys = ON` on every connection in `db.py`. |

## Settled decisions
1. **Actor source.** Require a non-blank `--actor` on `file`, `assign`, `resolve` and `close`. Trim surrounding whitespace. Escalation always records actor `system`; CSV import always records actor `seed`. These are audit labels, not authenticated identities.
2. **Assigned agent.** Store the agent as free text in `claims.assigned_agent`; no agents table. Require a non-blank `--agent` on `assign` and trim surrounding whitespace. New claims have no assigned agent.
3. **Assignment and reassignment.** Require an agent on every move into `assigned`, including `escalated → assigned`. That move replaces any previous assigned agent. Reject `assigned → assigned`, so direct reassignment is not supported. Escalation, resolution and closure preserve the stored agent for reference.
4. **Note.** Set the optional note at filing only; later commands cannot edit it. Store notes as data and never interpret their contents as instructions.
5. **Seed CSV.** Use the header `passenger,flight,type,tier,note` and invoke as `python main.py seed seed_claims.csv`. All imported claims use the same invocation time (the supplied `--now` or the UTC time captured once by the CLI) and actor `seed`. Reject missing/duplicate/unexpected columns and malformed or invalid rows. Report the CSV line number for row errors, counting the header as line 1. Load all claims and history in one transaction; any validation or write failure rolls back the whole import.
6. **Escalation atomicity.** Use one transaction per eligible claim. If a claim or history write fails, roll back both for that claim, report its ID and error, and continue with the remaining eligible claims. Return/report only successfully escalated IDs, in ascending order. The CLI exits non-zero if any claim failed, even when others succeeded.
7. **ID format.** Use SQLite integer autoincrement IDs, without prefixes.
8. **CLI failures.** Print clear validation, missing-claim, transition and database errors to stderr and exit non-zero. Expected user errors do not print tracebacks. Successful commands exit zero; escalation with no eligible claims is a successful no-op and reports no affected IDs.
9. **Timezones.** Accept ISO-8601 timestamps with a date, time and explicit `Z` or numeric UTC offset. Reject naive timestamps in both the CLI and rule functions. Normalize to UTC before calculating deadlines or comparing times, and store timestamps consistently with the `+00:00` suffix while preserving fractional seconds. Compare parsed datetimes, not raw timestamp strings. History is ordered by change time, then history ID to break ties.
10. **Database selection.** Use SQLite at `baggage.db` in the current working directory unless global `--db PATH` is supplied. Open/create the selected database and initialize the schema through `db.py`, enabling foreign keys on every connection. Tests and demonstrations always select temporary databases; database files remain ignored by Git.

---

## Verification (once implemented)
- Run `pytest tests/test_claims.py` on each branch. Expect at least 5, 6, 6 and 3 passing tests respectively, 20 in total at the end.
- Smoke test with a temp database:
  `python main.py --now 2026-11-06T09:00:00Z file ...`, then `list`, `assign`, `python main.py --now 2026-11-07T09:00:00Z escalate`, then `history <id>`.
- Check by hand that a gold claim filed at 09:00 is escalated at 13:00:01 but not at exactly 13:00:00.
