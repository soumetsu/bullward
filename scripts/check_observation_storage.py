"""Run from the project root: python -m scripts.check_observation_storage."""

import argparse
import asyncio
from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory

from backend.observations import initialize_database, record_observation
from backend.yahoo_stream import check_subscription


MODULE = "scripts.check_observation_storage"
ROOT = Path(__file__).resolve().parents[1]
TICKER = "AAPL"
FIRST = {"id": TICKER, "time": "1794234660000", "price": 101, "day_volume": "850", "test_data": True}
REPEAT = dict(reversed(tuple(FIRST.items())))
CHANGED = {**FIRST, "price": 102}
LATE = {**FIRST, "time": "1794234600000", "price": 100}
REVISION_ORIGINAL = {**FIRST, "time": "1794234720000", "price": 103}
REVISION_CHANGED = {**REVISION_ORIGINAL, "price": 104}
EVENT_ID = "test-event-1"


class TestClient:
    """Supply one constructed Yahoo-style frame to the backend receiver."""

    def __init__(self, message):
        self.message = message
        self._ws = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def subscribe(self, ticker):
        assert ticker == self.message["id"]

    async def recv(self):
        return json.dumps({"message": json.dumps(self.message)})

    def _decode_message(self, encoded):
        return json.loads(encoded)


async def send(database, message):
    result = await check_subscription(TestClient(message), TICKER, 5, database)
    assert result == 0, f"The backend failed to process test data (exit {result})"


def read_rows(database):
    uri = f"{database.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM observations ORDER BY id")]


async def populate(database):
    await send(database, FIRST)
    original = read_rows(database)
    assert len(original) == 1, "The receiver should store one row for the first frame"
    await send(database, REPEAT)
    assert read_rows(database) == original, "The exact repeat changed the stored rows"
    await send(database, CHANGED)
    await send(database, LATE)


def record(database):
    initialize_database(database)
    asyncio.run(populate(database))
    received_at = datetime.now(UTC)
    original_id = record_observation(database, REVISION_ORIGINAL, received_at, source_event_id=EVENT_ID)
    revision_id = record_observation(database, REVISION_CHANGED, received_at, source_event_id=EVENT_ID)

    rows = read_rows(database)
    expected = [FIRST, CHANGED, LATE, REVISION_ORIGINAL, REVISION_CHANGED]
    assert len(rows) == len(expected), f"Expected five test observations, found {len(rows)}"
    for row, message in zip(rows, expected, strict=True):
        assert row["ticker"] == message["id"]
        assert row["source_time_ms"] == int(message["time"])
        assert datetime.fromisoformat(row["received_at_utc"]).utcoffset() == timedelta(0)
        assert json.loads(row["source_message_json"]) == message
    assert len({row["source"] for row in rows}) == 1 and rows[0]["source"]
    assert rows[2]["source_time_ms"] < rows[0]["source_time_ms"] and rows[2]["id"] > rows[0]["id"]
    assert [row["source_event_id"] for row in rows] == [None, None, None, EVENT_ID, EVENT_ID]
    assert [row["revision_of_id"] for row in rows] == [None, None, None, None, original_id]
    assert rows[3]["id"] == original_id and rows[4]["id"] == revision_id

    database.with_suffix(".json").write_text(json.dumps(rows), encoding="utf-8")
    print("Stored rows (constructed test data, in receipt order):")
    for row in rows:
        print(json.dumps({**row, "source_message_json": json.loads(row["source_message_json"])}))
    print("Reading one row per observation ID counts the repeated frame once and preserves both distinct messages at the same source time.")


def reopen(database):
    expected = json.loads(database.with_suffix(".json").read_text(encoding="utf-8"))
    assert read_rows(database) == expected, "Rows differ after restarting the recorder"
    asyncio.run(send(database, REPEAT))
    assert read_rows(database) == expected, "The recorder stored the exact repeat after restarting"
    print(f"PASS: a new backend recorder process reopened the same {len(expected)} rows and suppressed the exact repeat again.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--record", type=Path, help=argparse.SUPPRESS)
    mode.add_argument("--reopen", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.record:
        record(args.record)
    elif args.reopen:
        reopen(args.reopen)
    else:
        with TemporaryDirectory(prefix="invest-observation-test-") as directory:
            database = Path(directory) / "observations.sqlite3"
            print(f"TEST DATA ONLY. Disposable SQLite database: {database}", flush=True)
            for step in ("--record", "--reopen"):
                subprocess.run([sys.executable, "-m", MODULE, step, str(database)], cwd=ROOT, check=True, timeout=45)


if __name__ == "__main__":
    main()
