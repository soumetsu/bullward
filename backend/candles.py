"""Build one-minute candles from saved Yahoo Finance observations."""

import argparse
from contextlib import closing
from datetime import UTC, datetime, timedelta
from itertools import groupby
import json
from pathlib import Path
import sqlite3
from typing import TypedDict

from .observations import DEFAULT_DB_PATH, SOURCE

MINUTE_MS = 60_000


class Candle(TypedDict):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int | None


class CandleMinute(TypedDict):
    candle: Candle
    ended: bool


def _session_volume(value: object) -> int | None:
    # Protobuf JSON sends int64 values as strings. SQLite test data may use ints.
    if type(value) is int or isinstance(value, str) and value.isascii() and value.isdecimal():
        total = int(value)
        if total >= 0:
            return total
    return None


def load_minute_candles(
    db_path: Path, ticker: str, *, as_of: datetime | None = None,
) -> list[CandleMinute]:
    """Recompute candles from the versions received by the requested time."""
    cutoff = as_of if as_of is not None else datetime.now(UTC)
    if cutoff.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    cutoff = cutoff.astimezone(UTC)
    ticker = ticker.strip().upper()
    if not ticker or any(character.isspace() for character in ticker):
        raise ValueError("provide one ticker symbol")

    minutes: list[CandleMinute] = []
    previous_minute: int | None = None
    previous_total: int | None = None
    db_uri = db_path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(db_uri, uri=True)) as connection:
        rows = connection.execute(
            """WITH known AS (
                   SELECT id, source_time_ms, received_at_utc, source_message_json, revision_of_id
                   FROM observations WHERE source = ? AND ticker = ? AND received_at_utc <= ?
               )
               SELECT source_time_ms, source_message_json FROM known
               WHERE id NOT IN (SELECT revision_of_id FROM known WHERE revision_of_id IS NOT NULL)
               ORDER BY source_time_ms, received_at_utc, id""",
            (SOURCE, ticker, cutoff.isoformat(timespec="microseconds")),
        )
        for minute, observations in groupby(rows, key=lambda row: row[0] // MINUTE_MS):
            prices = []
            volume: int | None = 0
            for _, payload in observations:
                message = json.loads(payload)
                prices.append(float(message["price"]))
                total = _session_volume(message.get("day_volume"))
                gap = previous_minute is not None and minute - previous_minute > 1
                if total is None or previous_total is None or total < previous_total or gap:
                    volume = None
                elif volume is not None:
                    volume += total - previous_total
                previous_total = total
                previous_minute = minute

            start = datetime.fromtimestamp(minute * 60, UTC)
            candle: Candle = {
                "timestamp": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
                "volume": volume,
            }
            minutes.append({"candle": candle, "ended": cutoff >= start + timedelta(minutes=1)})
    return minutes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker", help="Yahoo Finance ticker")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite observation file path")
    parser.add_argument("--as-of", type=datetime.fromisoformat, help="Receipt cutoff in ISO 8601 with timezone")
    args = parser.parse_args()
    try:
        minutes = load_minute_candles(args.db, args.ticker, as_of=args.as_of)
    except (sqlite3.Error, ValueError) as error:
        parser.exit(1, f"Unable to build candles: {error}\n")
    print(json.dumps(minutes, indent=2))


if __name__ == "__main__":
    main()
