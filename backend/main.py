import asyncio
from contextlib import asynccontextmanager, suppress
import csv
from datetime import UTC, datetime
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .candles import load_observed_minutes
from .live_feed import LiveFeed, SYMBOL
from .observations import DEFAULT_DB_PATH, initialize_database
from .timeframes import aggregate_candles

Interval = Literal["1m", "5m", "15m", "1h", "1d"]
Symbol = Literal["AAPL"]
SAMPLE_CANDLES = Path(__file__).parent / "data" / "sample-candles.csv"
CANDLE_FIELDS = ["timestamp", "open", "high", "low", "close", "volume"]


@asynccontextmanager
async def lifespan(app):
    database = Path(os.environ.get("INVEST_OBSERVATIONS_DB", DEFAULT_DB_PATH))
    await asyncio.to_thread(initialize_database, database)
    app.state.feed = LiveFeed(database)
    task = None
    if os.environ.get("INVEST_LIVE_FEED", "1") != "0":
        task = asyncio.create_task(app.state.feed.run())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Project 4 Invest API", lifespan=lifespan)
app.state.source_label = "Yahoo Finance"


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def history() -> list[dict[str, str | float | int]]:
    with SAMPLE_CANDLES.open(newline="", encoding="utf-8") as candle_file:
        reader = csv.DictReader(candle_file)
        if reader.fieldnames != CANDLE_FIELDS:
            raise ValueError(f"Expected CSV columns: {', '.join(CANDLE_FIELDS)}")

        candles = [
            {
                "timestamp": row["timestamp"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
            }
            for row in reader
        ]

    return sorted(candles, key=lambda candle: candle["timestamp"])


def market_history(db_path, symbol, interval):
    as_of = datetime.now(UTC)
    minutes, source_time = load_observed_minutes(db_path, symbol, as_of=as_of)
    return aggregate_candles(minutes, interval, as_of), source_time


@app.get("/api/history")
def chart_history(symbol: Symbol | None = None, interval: Interval = "1m"):
    if symbol is None:
        return history()
    rows, source_time = market_history(app.state.feed.db_path, symbol, interval)
    return {
        "symbol": symbol, "interval": interval, "source": app.state.source_label,
        "latest_source_ms": source_time, "candles": rows,
    }


@app.websocket("/api/candles/live")
async def live_candles(socket: WebSocket, symbol: Symbol = SYMBOL, interval: Interval = "1m"):
    await socket.accept()
    feed = socket.app.state.feed
    previous = None
    last_status = None
    try:
        while True:
            rows, source_time = await asyncio.to_thread(market_history, feed.db_path, symbol, interval)
            current = {row["candle"]["timestamp"]: row for row in rows}
            metadata = (feed.status, source_time)
            initial = previous is None
            updated = rows if initial else [row for key, row in current.items() if previous.get(key) != row]
            removed = [] if initial else sorted(previous.keys() - current.keys())
            if initial or updated or removed or metadata != last_status:
                await socket.send_json({
                    "type": "snapshot" if initial else "update",
                    "symbol": symbol, "interval": interval, "source": socket.app.state.source_label,
                    "feed_status": metadata[0], "latest_source_ms": metadata[1],
                    "candles": updated, "removed": removed,
                })
            previous, last_status = current, metadata
            try:
                incoming = await asyncio.wait_for(socket.receive(), timeout=1)
            except TimeoutError:
                continue
            if incoming["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
