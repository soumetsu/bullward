"""Construct and verify an offline receipt-ordered replay."""

from argparse import ArgumentParser
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from backend.observations import initialize_database, record_observation
from backend.replay import recorded_symbols, replay_step


def at(time):
    return datetime.fromisoformat(f"2026-11-09T{time}Z")


def seed_replay(database):
    initialize_database(database)
    for source, receipt, price, volume, event_id in [
        ("14:30:55", "14:30:56", 100, 1000, None),
        ("14:31:10", "14:31:11", 102, 1010, None),
        ("14:31:45", "14:31:46", 104, 1018, "revisable"),
        ("14:32:05", "14:32:06", 105, 1028, None),
        ("14:31:25", "14:32:10", 108, 1014, None),
        ("14:31:45", "14:32:10", 103, 1020, "revisable"),
    ]:
        record_observation(database, {
            "id": "AAPL", "time": int(at(source).timestamp() * 1000),
            "price": price, "day_volume": volume, "test_data": True,
        }, at(receipt), source_event_id=event_id)


def read_rows(database):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return connection.execute("SELECT * FROM observations ORDER BY received_at_utc, id").fetchall()


def frames(database):
    seen = []
    after_id = through_id = None
    while True:
        frame = replay_step(database, "AAPL", at("14:30:56"), at("14:32:11"), "1m", after_id, through_id)
        seen.append(frame)
        if frame["done"]:
            return seen
        after_id = frame["observation"]["id"]
        through_id = frame["through_id"]


def minute(frame):
    return next(row["candle"] for row in frame["candles"] if row["candle"]["timestamp"].endswith("14:31:00Z"))


def main():
    with TemporaryDirectory(prefix="invest-replay-", dir=Path(__file__).resolve().parents[1] / "backend/data") as directory:
        database = Path(directory) / "observations.sqlite3"
        seed_replay(database)
        stored = read_rows(database)
        first = frames(database)
        assert len(first) == len(stored) == 6
        assert [item["observation"]["id"] for item in first] == list(range(1, 7))
        assert minute(first[3]) == {
            "timestamp": "2026-11-09T14:31:00Z", "open": 102, "high": 104,
            "low": 102, "close": 104, "volume": 18,
        }
        assert minute(first[4])["high"] == 108 and minute(first[4])["close"] == 104
        assert minute(first[5])["close"] == 103 and minute(first[5])["volume"] == 20
        assert first[4]["observation"]["received_at"] == first[5]["observation"]["received_at"]
        assert first[4]["observation"]["source_time_ms"] < first[3]["observation"]["source_time_ms"]
        assert frames(database) == first
        assert read_rows(database) == stored
        assert recorded_symbols(database)[0]["symbol"] == "AAPL"
        empty = replay_step(database, "AAPL", at("15:00:00"), at("15:01:00"), "1m", None, None)
        assert empty["observation"] is None and empty["done"]
        print("PASS: repeatable six-frame replay, late arrival, tied-receipt correction, empty range, and unchanged SQLite rows")


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, help="Create a new constructed replay database.")
    args = parser.parse_args()
    if args.seed:
        if args.seed.exists():
            parser.error("The fixture database already exists.")
        seed_replay(args.seed)
    else:
        main()
