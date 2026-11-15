import asyncio
from contextlib import asynccontextmanager, suppress
import csv
from datetime import UTC, datetime
import logging
import os
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .candles import load_observed_minutes
from .decisions import DecisionRequest, SnapshotRequest, create_snapshot, get_decision, get_snapshot, request_decision
from .indicators import (
    IndicatorConfig, IndicatorRequest, available_types, calculate_indicators,
    configurations, save_configuration,
)
from .live_feed import LiveFeed, SYMBOL
from .observations import DEFAULT_DB_PATH, initialize_database
from .paper import LIVE_BOOK, advance_replay_account, execute_action, paper_state, process_live_account
from .replay import recorded_symbols, replay_step
from .timeframes import aggregate_candles

Interval = Literal["1m", "5m", "15m", "1h", "1d"]
Symbol = Literal["AAPL"]
SAMPLE_CANDLES = Path(__file__).parent / "data" / "sample-candles.csv"
CANDLE_FIELDS = ["timestamp", "open", "high", "low", "close", "volume"]


class PaperOrderRequest(BaseModel):
    request_id: UUID
    action: Literal["buy", "sell", "close", "hold", "wait"]
    quantity: object = None
    book: UUID | Literal["live"] = LIVE_BOOK
    symbol: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^\S+$")] = SYMBOL
    cursor: Annotated[int | None, Field(ge=1, le=2**63 - 1)] = None


async def watch_paper_account(database):
    while True:
        try:
            await asyncio.to_thread(process_live_account, database)
        except Exception:
            logging.getLogger(__name__).exception("Paper account processing failed")
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app):
    database = Path(os.environ.get("INVEST_OBSERVATIONS_DB", DEFAULT_DB_PATH))
    app.state.feed = LiveFeed(database)
    tasks = []
    enabled = os.environ.get("INVEST_LIVE_FEED", "1") != "0"
    if enabled or not database.exists():
        await asyncio.to_thread(initialize_database, database)
    if enabled:
        tasks = [
            asyncio.create_task(app.state.feed.run()),
            asyncio.create_task(watch_paper_account(database)),
        ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Project 4 Invest API", lifespan=lifespan)
app.state.source_label = os.environ.get("INVEST_SOURCE_LABEL", "Yahoo Finance")


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


@app.get("/api/replay/symbols")
def replay_symbols():
    return {
        "source": app.state.source_label,
        "symbols": recorded_symbols(app.state.feed.db_path),
        "feed_status": app.state.feed.status,
    }


@app.get("/api/replay")
@app.post("/api/replay")
def replay(
    request: Request,
    symbol: Annotated[str, Query(min_length=1, max_length=64, pattern=r"^\S+$")],
    start: datetime, end: datetime, interval: Interval = "1m",
    after_id: Annotated[int | None, Query(ge=1, le=9_223_372_036_854_775_807)] = None,
    through_id: Annotated[int | None, Query(ge=0, le=9_223_372_036_854_775_807)] = None,
    book: UUID | None = None,
):
    database = app.state.feed.db_path
    try:
        frame = replay_step(database, symbol.upper(), start, end, interval, after_id, through_id)
        if request.method == "POST":
            if book is None or after_id is None or through_id is None:
                raise ValueError("A paper replay account and its current observation are required.")
            if frame["observation"]:
                minutes = frame["candles"] if interval == "1m" else replay_step(
                    database, symbol.upper(), start, end, "1m", after_id, through_id,
                )["candles"]
                advance_replay_account(database, str(book), symbol.upper(), after_id,
                                       frame["observation"]["id"], minutes)
        return frame
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/indicators")
def indicator_catalog():
    return {"types": available_types(), "configured": configurations(app.state.feed.db_path)}


@app.post("/api/indicators")
def create_indicator(config: IndicatorConfig):
    try:
        return save_configuration(app.state.feed.db_path, config)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/indicators/calculate")
def indicators(request: IndicatorRequest):
    try:
        return calculate_indicators(request, configurations(app.state.feed.db_path))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/paper")
def paper_account(book: UUID | Literal["live"] = LIVE_BOOK, symbol: str = SYMBOL):
    return paper_state(app.state.feed.db_path, str(book), SYMBOL if book == LIVE_BOOK else symbol.upper())


@app.post("/api/paper/orders")
def submit_paper_order(order: PaperOrderRequest):
    if order.book == LIVE_BOOK and (order.symbol.upper() != SYMBOL or order.cursor is not None):
        raise HTTPException(status_code=422, detail="The live paper account uses AAPL observations.")
    try:
        return execute_action(app.state.feed.db_path, str(order.book), order.symbol.upper(),
                              order.action, str(order.request_id), order.quantity, order.cursor)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/decision-snapshots", status_code=201)
def freeze_decision_snapshot(selection: SnapshotRequest):
    try:
        return create_snapshot(app.state.feed.db_path, selection, app.state.source_label)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/decision-snapshots/{snapshot_id}")
def read_decision_snapshot(snapshot_id: UUID):
    snapshot = get_snapshot(app.state.feed.db_path, str(snapshot_id))
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Decision snapshot not found.")
    return snapshot


@app.post("/api/decisions", status_code=201)
def decide(request: DecisionRequest | None = None):
    snapshot_id = str(request.snapshot_id) if request and request.snapshot_id else None
    return request_decision(app.state.feed.db_path, snapshot_id)


@app.get("/api/decisions/{decision_id}")
def read_decision(decision_id: UUID):
    decision = get_decision(app.state.feed.db_path, str(decision_id))
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found.")
    return decision


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
