"""Verify minute candles from a disposable database of constructed test observations."""

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.candles import load_minute_candles
from backend.observations import DEFAULT_DB_PATH, initialize_database, record_observation


BASELINE = ("14:59:59", 99, 1000)
FIRST = ("15:00:05", 100, 1004)
MIDDLE = ("15:00:20", 103, 1009)
LAST = ("15:00:50", 101, 1011)
EXPECTED = {
    "timestamp": "2026-11-09T15:00:00Z",
    "open": 100,
    "high": 103,
    "low": 100,
    "close": 101,
    "volume": 11,
}


def utc(clock_time):
    return datetime.fromisoformat(f"2026-11-09T{clock_time}+00:00")


def store(db_path, ticker, observation, *, receipt=None, event_id=None):
    source_time, price, volume = observation
    message = {
        "id": ticker,
        "time": str(int(utc(source_time).timestamp() * 1000)),
        "price": price,
        "test_data": True,
    }
    if volume is not None:
        message["day_volume"] = str(volume)
    record_observation(db_path, message, utc(receipt or source_time), source_event_id=event_id)


def check(db_path, ticker, label, cutoff, expected_candle, *, ended):
    minutes = load_minute_candles(db_path, ticker, as_of=utc(cutoff))
    matches = [minute for minute in minutes if minute["candle"]["timestamp"] == expected_candle["timestamp"]]
    expected = {"candle": expected_candle, "ended": ended}
    assert matches == [expected], f"{label}: expected {expected!r}, got {matches!r}"
    print(f"PASS {label}: {matches[0]}")


def main():
    print("Constructed test data only. Source and receipt times are UTC on 2026-11-09.")
    with TemporaryDirectory(prefix="invest-candle-test-", dir=DEFAULT_DB_PATH.parent) as directory:
        db_path = Path(directory) / "observations.sqlite3"
        initialize_database(db_path)

        for observation in (BASELINE, FIRST, MIDDLE, LAST):
            store(db_path, "TEST-COMPLETE", observation)
        check(db_path, "TEST-COMPLETE", "minute still open", "15:00:59", EXPECTED, ended=False)
        check(db_path, "TEST-COMPLETE", "minute ended", "15:01:00", EXPECTED, ended=True)

        for observation in (BASELINE, FIRST, LAST):
            store(db_path, "TEST-LATE", observation)
        store(db_path, "TEST-LATE", MIDDLE, receipt="15:01:10")
        check(db_path, "TEST-LATE", "before late receipt", "15:01:00", {**EXPECTED, "high": 101}, ended=True)
        check(db_path, "TEST-LATE", "after late receipt", "15:01:10", EXPECTED, ended=True)

        volume_cases = (
            ("TEST-NO-BASELINE", "missing baseline", (FIRST, MIDDLE, LAST)),
            ("TEST-RESET", "session counter reset", (BASELINE, FIRST, ("15:00:20", 103, 3), ("15:00:50", 101, 5))),
            ("TEST-MISSING-COUNTER", "missing counter", (BASELINE, FIRST, ("15:00:20", 103, None), LAST)),
        )
        for ticker, label, observations in volume_cases:
            for observation in observations:
                store(db_path, ticker, observation)
            check(db_path, ticker, label, "15:01:00", {**EXPECTED, "volume": None}, ended=True)

        for observation in (("14:58:59", 99, 1000), FIRST, MIDDLE, LAST):
            store(db_path, "TEST-GAP", observation)
        check(db_path, "TEST-GAP", "empty intervening minute", "15:01:00", {**EXPECTED, "volume": None}, ended=True)

        for observation in (BASELINE, FIRST, LAST):
            store(db_path, "TEST-REVISION", observation)
        store(db_path, "TEST-REVISION", MIDDLE, event_id="test-event-1")
        store(db_path, "TEST-REVISION", ("15:00:20", 102, 1008), receipt="15:01:20", event_id="test-event-1")
        check(db_path, "TEST-REVISION", "before correction", "15:01:00", EXPECTED, ended=True)
        check(db_path, "TEST-REVISION", "after correction", "15:01:20", {**EXPECTED, "high": 102}, ended=True)


if __name__ == "__main__":
    main()
