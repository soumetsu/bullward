"""Check session-aligned aggregation using the supplied synthetic minutes."""

from datetime import datetime

from backend.main import history
from backend.timeframes import aggregate_candles, session_bounds


def utc(stamp):
    return datetime.fromisoformat(stamp + "Z")


def minute(stamp, volume):
    return {"candle": {
        "timestamp": stamp + "Z", "open": 104, "high": 106,
        "low": 103, "close": 105, "volume": volume,
    }, "ended": True}


def main():
    rows = [{"candle": candle, "ended": True} for candle in history()]
    cutoff = utc("2026-11-09T14:33:00")
    five, = aggregate_candles(rows, "5m", cutoff)
    assert five == {
        "candle": {"timestamp": "2026-11-09T14:30:00Z", "open": 100,
                   "high": 105, "low": 99, "close": 104, "volume": 3550},
        "ended": False, "observed_minutes": 3, "expected_minutes": 5,
    }, five
    daily, = aggregate_candles(rows, "1d", cutoff)
    assert daily["candle"] == five["candle"]
    assert not daily["ended"] and daily["observed_minutes"] == 3 and daily["expected_minutes"] == 390
    print("PASS supplied sample: 5m and daily 14:30 UTC, O100 H105 L99 C104 V3550, forming, partial coverage")

    rows.append(minute("2026-11-09T14:35:00", 0))
    assert aggregate_candles(rows, "5m", cutoff) == [five]
    cutoff = utc("2026-11-10T14:35:00")
    first, second = aggregate_candles(rows, "5m", cutoff)
    assert first["ended"] and second["candle"]["timestamp"] == "2026-11-09T14:35:00Z"
    assert second["candle"]["volume"] == 0
    assert len(aggregate_candles(rows, "1d", cutoff)) == 1
    rows.append(minute("2026-11-10T14:30:00", None))
    first, second = aggregate_candles(rows, "1d", cutoff)
    assert first["observed_minutes"] == 4 and first["candle"]["volume"] == 3550
    assert second["candle"]["timestamp"] == "2026-11-10T14:30:00Z"
    assert second["candle"]["volume"] is None
    print("PASS next bucket and session, receipt cutoff, known zero and unknown volume")

    assert session_bounds(utc("2026-11-26T14:30:00").date()) is None
    summer = session_bounds(utc("2026-07-06T13:30:00").date())
    assert summer[0] == utc("2026-07-06T13:30:00")
    rows = [minute("2026-11-27T17:30:00", 12), minute("2026-11-27T18:00:00", 34)]
    hour, = aggregate_candles(rows, "1h", utc("2026-11-27T18:00:00"))
    assert hour["ended"] and hour["expected_minutes"] == 30 and hour["candle"]["volume"] == 12
    print("PASS Nasdaq holiday, daylight saving time, and shortened final hour")


if __name__ == "__main__":
    main()
