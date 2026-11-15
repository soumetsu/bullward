"""Aggregate regular-session Nasdaq stock candles from UTC minute candles."""

from datetime import UTC, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from .candles import CandleMinute

INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 390}
EASTERN = ZoneInfo("America/New_York")

# https://nasdaqtrader.com/Trader.aspx?id=Calendar
HOLIDAYS_2026 = {"01-01", "01-19", "02-16", "04-03", "05-25", "06-19",
                 "07-03", "09-07", "11-26", "12-25"}
EARLY_CLOSES_2026 = {"11-27", "12-24"}


@lru_cache(maxsize=512)
def session_bounds(day):
    if day.year != 2026 or day.weekday() >= 5:
        return None
    month_day = day.strftime("%m-%d")
    if month_day in HOLIDAYS_2026:
        return None
    close_hour = 13 if month_day in EARLY_CLOSES_2026 else 16
    start = datetime.combine(day, time(9, 30), EASTERN).astimezone(UTC)
    end = datetime.combine(day, time(close_hour), EASTERN).astimezone(UTC)
    return start, end


def aggregate_candles(minutes: list[CandleMinute], interval: str, as_of: datetime) -> list[dict]:
    duration = timedelta(minutes=INTERVAL_MINUTES[interval])
    one_minute = timedelta(minutes=1)
    buckets = {}
    for row in minutes:
        candle = row["candle"]
        minute = datetime.fromisoformat(candle["timestamp"])
        session = session_bounds(minute.astimezone(EASTERN).date())
        if session is None or not session[0] <= minute < session[1] or minute > as_of:
            continue
        opened, closed = session
        start = opened + ((minute - opened) // duration) * duration
        end = min(start + duration, closed)
        buckets.setdefault((start, end), {})[minute] = candle

    result = []
    for (start, end), minute_candles in sorted(buckets.items()):
        candles = [minute_candles[minute] for minute in sorted(minute_candles)]
        volumes = [candle["volume"] for candle in candles]
        result.append({
            "candle": {
                "timestamp": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "open": candles[0]["open"],
                "high": max(candle["high"] for candle in candles),
                "low": min(candle["low"] for candle in candles),
                "close": candles[-1]["close"],
                "volume": None if None in volumes else sum(volumes),
            },
            "ended": as_of >= end,
            "observed_minutes": len(candles),
            "expected_minutes": (end - start) // one_minute,
        })
    return result
