"""Receive one Yahoo Finance quote from the backend environment."""

import argparse
import asyncio
from datetime import UTC, datetime
import json
from math import isfinite
from pathlib import Path
import sqlite3
import sys

from .observations import DEFAULT_DB_PATH, initialize_database, record_observation


def parse_quote(message, ticker, received_at):
    if message.get("id") != ticker:
        return None

    try:
        price = float(message["price"])
        source_time_ms = int(message["time"])
        source_time = datetime.fromtimestamp(source_time_ms / 1000, UTC)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    if not isfinite(price) or source_time_ms <= 0:
        return None

    quote = {
        "ticker": ticker,
        "price": price,
        "source_time_ms": source_time_ms,
        "source_time_utc": source_time.isoformat(timespec="milliseconds"),
        "received_at_utc": received_at.isoformat(timespec="milliseconds"),
    }
    if currency := message.get("currency"):
        quote["currency"] = currency
    for field in ("day_volume", "last_size"):
        try:
            quote[field] = int(message[field])
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
    return quote


async def check_subscription(client, ticker, timeout, db_path=DEFAULT_DB_PATH):
    """Return 0 after recording a quote, 1 on failure, or 2 on subscription timeout."""
    opened = False

    try:
        async with asyncio.timeout(timeout):
            async with client:
                opened = True
                print("WebSocket opened.", flush=True)
                await client.subscribe(ticker)
                print(f"Subscription sent for {ticker}.", flush=True)

                # yfinance 1.8.0's listen() loops even after a clean remote close.
                # Read its subscribed socket directly so closure ends this check.
                while True:
                    frame = await client._ws.recv()
                    received_at = datetime.now(UTC)
                    encoded = json.loads(frame).get("message")
                    if encoded:
                        message = client._decode_message(encoded)
                        if quote := parse_quote(message, ticker, received_at):
                            break
    except TimeoutError:
        print(f"Timed out after {timeout:g}s (connection opened: {opened}). Receipt unverified.", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"{type(error).__name__}: {error} (connection opened: {opened}). Receipt unverified.", file=sys.stderr)
        return 1

    try:
        initialize_database(db_path)
        observation_id = record_observation(db_path, message, received_at)
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"Quote received but recording failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps(quote, indent=2))
    if observation_id is None:
        print("Exact repeat. The original observation remains stored.")
    else:
        print(f"Stored observation {observation_id} in {db_path.resolve()}.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker", nargs="?", default="AAPL", help="Yahoo Finance ticker (default: AAPL)")
    parser.add_argument("--timeout", type=float, default=30, help="Subscription deadline in seconds (default: 30)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite observation file path")
    args = parser.parse_args()
    ticker = args.ticker.strip().upper()
    if not ticker or any(character.isspace() for character in ticker):
        parser.error("provide one ticker symbol")
    if not isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a positive, finite number")

    try:
        from yfinance import AsyncWebSocket
    except ModuleNotFoundError as error:
        print(f"Yahoo Finance client unavailable: {error}", file=sys.stderr)
        return 1

    return asyncio.run(check_subscription(AsyncWebSocket(verbose=False), ticker, args.timeout, args.db))


if __name__ == "__main__":
    raise SystemExit(main())
