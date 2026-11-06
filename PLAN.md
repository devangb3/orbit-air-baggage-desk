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
Run as `python main.py [--now ISO_TIMESTAMP] <command>`. `--now` goes before the command and defaults to the real clock in `main.py` only. Rule functions always receive `now` as a parameter.

| Command | Behaviour |
|---|---|
| `file` | Files a claim from passenger, flight, type, tier, optional note and actor. |
| `list` | Lists claims, with optional `--status` and `--tier` filters. |
| `assign` | Assigns an agent and moves the claim to `assigned`. |
| `resolve` | Moves the claim to `resolved`. |
| `close` | Moves the claim to `closed`. |
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
| **Atomicity.** Python's `sqlite3` opens transactions implicitly, and a partial write (claim without history) would corrupt the audit trail. | Wrap each claim-plus-history write in one explicit `with conn:` block. Test rollback by forcing the history insert to fail. |
| **Rules drift between code and database.** The CHECK constraints and the Python validation could disagree. | Define the type, tier and status lists once in `claims.py` and build the CHECK clauses from them in `db.py`. Test that bad values fail both layers. |
| **Seed leaves a half-loaded database.** One bad CSV row could leave earlier rows saved. | Validate and load the whole file in one transaction (all or nothing), with errors naming the row. Pending decision 5. |
| **Foreign keys off by default in SQLite.** History could reference missing claims. | Run `PRAGMA foreign_keys = ON` on every connection in `db.py`. |

## Decisions to settle
1. **Actor source.** Filing and status commands need an actor. Options are a required `--actor` flag or a default. Escalation uses the fixed value `system`. Proposed: `--actor` required on `file`, `assign`, `resolve` and `close`.
2. **Assigned agent.** Is it free-text or a row in an agents table? You listed only claims and history, so proposed: free-text.
3. **Agent required on every move into `assigned`**, including `escalated → assigned`. Can an already assigned claim be reassigned? The table says no (`assigned → assigned` is not allowed). Proposed: no reassignment.
4. **Note.** Is it set at filing only, or can later commands update it? History has no note column. Proposed: filing only.
5. **Seed CSV format.** The columns, whether each row's filed time comes from the CSV or from `--now`, and whether a bad row aborts the whole file or skips that row. Proposed columns: `passenger,flight,type,tier,note`. Filed time is `--now`, actor is `seed`, and a bad row aborts the whole file.
6. **Escalation atomicity.** Is it one transaction for the whole run or one per claim? Proposed: one per claim, so one failure doesn't block the rest, and failures are reported.
7. **ID format.** Integer autoincrement. Say so if you want prefixed IDs like `BC-0001`.
8. **CLI failures.** Proposed: a clear message on stderr and a non-zero exit code.

---

## Verification (once implemented)
- Run `pytest tests/test_claims.py` on each branch. Expect at least 5, 6, 6 and 3 passing tests respectively, 20 in total at the end.
- Smoke test with a temp database:
  `python main.py --now 2026-11-06T09:00:00Z file ...`, then `list`, `assign`, `python main.py --now 2026-11-07T09:00:00Z escalate`, then `history <id>`.
- Check by hand that a gold claim filed at 09:00 is escalated at 13:00:01 but not at exactly 13:00:00.
