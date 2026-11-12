"""SQLite storage for decoded Yahoo Finance observations."""

import argparse
from contextlib import closing
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "market-observations.sqlite3"
SOURCE = "yahoo_finance"


def initialize_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                ticker TEXT NOT NULL,
                source_time_ms INTEGER NOT NULL CHECK (source_time_ms > 0),
                received_at_utc TEXT NOT NULL,
                source_message_json TEXT NOT NULL CHECK (json_valid(source_message_json)),
                source_event_id TEXT,
                revision_of_id INTEGER REFERENCES observations(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS observations_exact_message
                ON observations(source, coalesce(source_event_id, ''), source_message_json);
            CREATE INDEX IF NOT EXISTS observations_source_order
                ON observations(ticker, source_time_ms, id);
            CREATE INDEX IF NOT EXISTS observations_event
                ON observations(source, ticker, source_event_id, id)
                WHERE source_event_id IS NOT NULL;
        """)


def record_observation(
    db_path: Path,
    message: dict,
    received_at: datetime,
    *,
    source_event_id: str | None = None,
) -> int | None:
    """Insert a decoded message, returning its row ID or None for an exact repeat.

    Only supply source_event_id if the feed establishes the identity of a
    revisable event. Yahoo price frames used here have no such identifier.
    """
    if received_at.utcoffset() is None:
        raise ValueError("received_at must include a timezone")
    if source_event_id == "":
        raise ValueError("source_event_id must be nonempty when supplied")

    ticker = message["id"]
    source_time_ms = int(message["time"])
    received_at_utc = received_at.astimezone(UTC).isoformat(timespec="microseconds")
    source_json = json.dumps(message, sort_keys=True, separators=(",", ":"), allow_nan=False)

    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        # Serialize the duplicate and revision lookups with the insert.
        connection.execute("BEGIN IMMEDIATE")
        duplicate = connection.execute(
            """SELECT id FROM observations
               WHERE source = ? AND coalesce(source_event_id, '') = ? AND source_message_json = ?""",
            (SOURCE, source_event_id or "", source_json),
        ).fetchone()
        if duplicate is not None:
            return None

        revision_of_id = None
        if source_event_id is not None:
            previous = connection.execute(
                """SELECT id FROM observations WHERE source = ? AND ticker = ? AND source_event_id = ?
                   ORDER BY id DESC LIMIT 1""",
                (SOURCE, ticker, source_event_id),
            ).fetchone()
            if previous is not None:
                revision_of_id = previous[0]

        inserted = connection.execute(
            """INSERT INTO observations
                   (source, ticker, source_time_ms, received_at_utc, source_message_json, source_event_id, revision_of_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (SOURCE, ticker, source_time_ms, received_at_utc, source_json, source_event_id, revision_of_id),
        )
        return inserted.lastrowid


def main():
    parser = argparse.ArgumentParser(description="Initialize the SQLite observation store.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite file path")
    args = parser.parse_args()
    initialize_database(args.db)
    print(f"Initialized {args.db.resolve()}")


if __name__ == "__main__":
    main()
