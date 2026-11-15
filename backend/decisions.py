"""Freeze completed-candle evidence and record model proposals for paper accounts."""

from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
import os
import sqlite3
from typing import Annotated, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, Field

from .candles import load_observed_minutes
from .indicators import IndicatorRequest, calculate_indicators
from .live_feed import SYMBOL
from .observations import SOURCE
from .paper import DEFAULT_ORDER_SIZE, LIVE_BOOK, STARTING_CASH, _has_accounts, stamp, transaction
from .timeframes import EASTERN, INTERVAL_MINUTES, aggregate_candles, session_bounds

MAX_ROW = 2**63 - 1
RowId = Annotated[int, Field(strict=True, ge=1, le=MAX_ROW)]
Window = Annotated[int, Field(strict=True, gt=0)]
Interval = Literal["1m", "5m", "15m", "1h", "1d"]
MODEL = "gpt-6-luna"
QUESTION_NAME = "paper_action"
CHOICES = {
    "buy": "Add the configured order size in shares if simulated cash covers it.",
    "sell": "Reduce the existing position by the configured order size.",
    "hold": "Keep the existing open position.",
    "wait": "Stay flat in the paper account.",
    "close": "Exit the entire existing position.",
}


class ReplayContext(BaseModel):
    start: AwareDatetime
    cursor: RowId
    through_id: RowId
    book: UUID | None = None


class SnapshotRequest(BaseModel):
    symbol: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^\S+$")] = SYMBOL
    interval: Interval = "1m"
    timestamp: AwareDatetime
    short: Window = 2
    long: Window = 3
    replay: ReplayContext | None = None


class DecisionRequest(BaseModel):
    snapshot_id: UUID | None = None


class ProviderFailure(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


def _candle_end(start, interval):
    session = session_bounds(start.astimezone(EASTERN).date())
    duration = timedelta(minutes=INTERVAL_MINUTES[interval])
    if session and session[0] <= start < session[1] and (start - session[0]) % duration == timedelta():
        return min(start + duration, session[1])
    raise ValueError("Select a regular-session candle start aligned to the requested interval.")


def _order_size():
    try:
        size = int(os.environ.get("INVEST_PAPER_ORDER_SIZE", str(DEFAULT_ORDER_SIZE)))
    except ValueError:
        size = 0
    if not 1 <= size <= 2**53 - 1:
        raise ValueError("INVEST_PAPER_ORDER_SIZE must be a positive whole number of shares.")
    return size


def _historical_account(connection, symbol, book, cutoff, through_id, at_id):
    if book == LIVE_BOOK and symbol != SYMBOL:
        return None
    if _has_accounts(connection) and book:
        account = connection.execute("SELECT symbol FROM paper_accounts WHERE id = ?", (book,)).fetchone()
        if account and account[0] != symbol:
            return None
        fill = connection.execute(
            """SELECT p.cash, p.shares
               FROM paper_fills f JOIN paper_orders o ON o.id = f.order_id
               LEFT JOIN paper_pnl p ON p.account_id = o.account_id
                  AND p.event = 'Fill' AND p.event_key = CAST(o.id AS TEXT)
               WHERE o.account_id = ? AND o.symbol = ? AND f.executed_at <= ?
                 AND (? = 'live' OR (f.observation_id <= ? AND
                      (f.executed_at < ? OR f.observation_id <= ?)))
               ORDER BY f.executed_at DESC, f.order_id DESC LIMIT 1""",
            (book, symbol, cutoff, book, through_id, cutoff, at_id),
        ).fetchone()
        if fill:
            return {"book": book, "cash": fill[0], "shares": fill[1]} if fill[0] is not None else None
        if not account and book != LIVE_BOOK:
            return None
    elif book and book != LIVE_BOOK:
        return None
    return {"book": book, "cash": STARTING_CASH, "shares": 0}


def _replay_cutoff(connection, replay, symbol, cutoff):
    if replay is None:
        return None, MAX_ROW, MAX_ROW, LIVE_BOOK
    start = replay.start.astimezone(UTC)
    row = connection.execute(
        """SELECT received_at_utc FROM observations
           WHERE id = ? AND id <= ? AND ticker = ? AND source = ? AND received_at_utc >= ?""",
        (replay.cursor, replay.through_id, symbol, SOURCE, stamp(start)),
    ).fetchone()
    if not row or start >= cutoff or row[0] < stamp(cutoff):
        raise ValueError("Replay must reach the candle close before its decision can be frozen.")
    at_id = replay.cursor if row[0] == stamp(cutoff) else MAX_ROW
    return start, replay.through_id, at_id, str(replay.book) if replay.book else None


def _read_evidence(db_path, request, cutoff, symbol):
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection, connection:
        connection.execute("BEGIN")
        received_from, through_id, at_id, book = _replay_cutoff(connection, request.replay, symbol, cutoff)
        minutes, _ = load_observed_minutes(
            db_path, symbol, as_of=cutoff, received_from=received_from,
            through_id=through_id, at_id=at_id, connection=connection,
        )
        account = _historical_account(connection, symbol, book, stamp(cutoff), through_id, at_id)
        test_data = connection.execute(
            """SELECT 1 FROM observations WHERE source = ? AND ticker = ?
                 AND received_at_utc >= ? AND (received_at_utc, id) <= (?, ?)
                 AND id <= ? AND source_time_ms <= ?
                 AND source_message_json ->> '$.test_data' = 1 LIMIT 1""",
            (SOURCE, symbol, stamp(received_from) if received_from else "", stamp(cutoff),
             at_id, through_id, int(cutoff.timestamp() * 1000)),
        ).fetchone()
    return minutes, account, test_data is not None


def create_snapshot(db_path, request, source):
    start = request.timestamp.astimezone(UTC)
    cutoff = _candle_end(start, request.interval)
    if cutoff > datetime.now(UTC):
        raise ValueError("The selected candle is still forming.")
    if request.short >= request.long:
        raise ValueError("The short SMA window must be smaller than the long window.")
    symbol = request.symbol.upper()
    minutes, account, test_data = _read_evidence(db_path, request, cutoff, symbol)
    candles = [
        row["candle"] for row in aggregate_candles(minutes, request.interval, cutoff)
        if row["ended"]
    ]
    indicator_result = calculate_indicators(IndicatorRequest(
        short=request.short, long=request.long, candles=[{**candle, "ended": True} for candle in candles],
    ))
    timestamp = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    selected = next((candle for candle in candles if candle["timestamp"] == timestamp), None)
    indicator_values = [
        next((point["value"] for point in item["points"] if point["timestamp"] == timestamp), None)
        for item in indicator_result["series"]
    ]
    crossover = next((item["direction"] for item in indicator_result["crossovers"] if
                      item["timestamp"] == timestamp), None)
    missing = []
    if selected is None or selected["close"] <= 0:
        missing.append("completed candle closing price")
    if None in indicator_values:
        missing.append("both completed-candle SMAs")
    if account is None:
        missing.append("paper account at candle close")
    facts = {
        "symbol": symbol, "interval": request.interval, "candle_timestamp": timestamp,
        "decision_time": stamp(cutoff), "close": selected["close"] if selected else None,
        "sma": {"short": indicator_values[0], "long": indicator_values[1],
                "short_window": request.short, "long_window": request.long, "crossover": crossover},
        "account": account, "order_size": _order_size(),
        "market_source": source, "market_data": "constructed" if test_data else "recorded",
    }
    snapshot = {
        "id": str(uuid4()), "facts": facts, "candles": candles, "indicators": indicator_result,
        "missing": missing, "replay": request.replay.model_dump(mode="json") if request.replay else None,
    }
    with transaction(db_path) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS decision_snapshots (
                id TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json))
            )
        """)
        connection.execute("INSERT INTO decision_snapshots VALUES (?, ?)", (snapshot["id"], json.dumps(snapshot)))
    return snapshot


def _load(db_path, table, column, record_id):
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name = ? AND type = 'table'", (table,)).fetchone():
            row = connection.execute(f"SELECT {column} FROM {table} WHERE id = ?", (record_id,)).fetchone()
            return json.loads(row[0]) if row else None
    return None


def get_snapshot(db_path, record_id):
    return _load(db_path, "decision_snapshots", "snapshot_json", record_id)


def get_decision(db_path, record_id):
    return _load(db_path, "decisions", "decision_json", record_id)


def ask_openai(snapshot):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        raise ProviderFailure("provider_unconfigured", "Set OPENAI_API_KEY in the backend environment.")
    facts = snapshot["facts"]
    payload = {
        "model": MODEL,
        "input": [{"role": "user", "content": [{"type": "input_json", "data": {
            "snapshot_id": snapshot["id"], **facts,
        }}]}],
        "questions": [{
            "type": "choice", "name": QUESTION_NAME,
            "instructions": "Select one proposed action for this long-only paper trading simulation using the provided facts.",
            "choices": [{"value": key, "description": meaning} for key, meaning in CHOICES.items()],
        }],
    }
    request = Request("https://api.openai.com/v1/decisions", data=json.dumps(payload).encode(),
                      headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        status = "timeout" if error.code in (408, 504) else "provider_error"
        error.close()
        raise ProviderFailure(status, f"OpenAI returned HTTP {error.code}.") from None
    except (TimeoutError, URLError, OSError) as error:
        timeout = isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError)
        raise ProviderFailure("timeout" if timeout else "provider_error",
                              "The OpenAI request timed out." if timeout else "Could not reach OpenAI.") from None
    except (ValueError, UnicodeError):
        raise ProviderFailure("malformed_answer", "OpenAI returned an unreadable response.") from None


def _proposal(response):
    if isinstance(response, dict) and isinstance(response.get("answers"), list):
        answers = [answer for answer in response["answers"] if
                   isinstance(answer, dict) and answer.get("name") == QUESTION_NAME]
        if len(answers) == 1 and answers[0].get("type") == "refusal":
            raise ProviderFailure("provider_refusal", "The provider declined to answer the named question.")
        if len(answers) == 1 and answers[0].get("type") == "choice":
            choice = answers[0].get("choice")
            if isinstance(choice, str) and choice in CHOICES:
                return choice
    raise ProviderFailure("malformed_answer", "The provider did not return one allowed named choice.")


def _validate(action, facts):
    account = facts["account"]
    shares, size = account["shares"], facts["order_size"]
    if action == "buy" and Decimal(account["cash"]) < size * Decimal(str(facts["close"])):
        return None, "Cash at the snapshot close does not cover the configured share quantity."
    if action == "sell" and shares < size:
        return None, "The position is smaller than the configured sale size."
    if action in ("hold", "close") and shares == 0:
        return None, "This action requires an open position."
    if action == "wait" and shares != 0:
        return None, "Wait requires a flat position."
    return {"buy": size, "sell": size, "close": shares}.get(action), None


def request_decision(db_path, snapshot_id, ask=ask_openai, *, provider="OpenAI", model=MODEL):
    snapshot = get_snapshot(db_path, snapshot_id)
    result = {
        "id": str(uuid4()), "snapshot_id": snapshot_id, "facts": snapshot["facts"] if snapshot else None,
        "provider": provider, "model": model, "provider_decision_id": None, "model_call": "not_sent",
        "proposal": None, "quantity": None, "eligible_for_paper_order": False,
    }
    if not snapshot or snapshot["missing"]:
        result["status"] = "missing_input"
        result["explanation"] = ("Missing evidence: " + ", ".join(snapshot["missing"]) + "."
                                 if snapshot else "An existing snapshot ID is required.")
    else:
        facts = snapshot["facts"]
        sma = facts["sma"]
        evidence = (f"{facts['symbol']} {facts['interval']} closed at {facts['close']} with "
                    f"short/long SMA {sma['short']}/{sma['long']} and {sma['crossover'] or 'no'} crossover. "
                    f"Paper cash was {facts['account']['cash']} with {facts['account']['shares']} shares.")
        result["model_call"] = "attempted"
        try:
            response = ask(snapshot)
            result["model_call"] = "returned"
            action = _proposal(response)
            quantity, reason = _validate(action, facts)
            result.update(
                proposal=action, quantity=quantity, status="invalid_proposal" if reason else "valid",
                explanation=evidence + (f" {reason}" if reason else ""),
                eligible_for_paper_order=(reason is None and quantity is not None and
                                          facts["market_data"] == "recorded" and
                                          facts["account"]["book"] is not None),
            )
            if isinstance(response.get("id"), str):
                result["provider_decision_id"] = response["id"]
        except ProviderFailure as error:
            if error.status == "provider_unconfigured":
                result["model_call"] = "not_sent"
            result.update(status=error.status, explanation=f"{evidence} {error}")
    with transaction(db_path) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY, decision_json TEXT NOT NULL CHECK (json_valid(decision_json))
            )
        """)
        connection.execute("INSERT INTO decisions VALUES (?, ?)", (result["id"], json.dumps(result)))
    return result
