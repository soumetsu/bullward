"""Freeze completed-candle evidence and record model proposals for paper accounts."""

from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import os
import sqlite3
from typing import Annotated, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, Field

from .candles import load_observed_minutes
from .chart_capture import MAX_DATA_URL, decode_capture
from .indicators import IndicatorRequest, calculate_indicators, configurations
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
CROSSOVER_CHOICES = {
    "up": "The short SMA visibly crosses above the long SMA at the selected candle.",
    "down": "The short SMA visibly crosses below the long SMA at the selected candle.",
    "none": "No crossover is visible at the selected candle.",
    "unreadable": "The crossover cannot be read from the chart.",
}
EVIDENCE_CHOICES = {
    "sufficient": "The chart visibly supports an assessment at the cutoff.",
    "insufficient": "The chart lacks enough readable information at the cutoff.",
}
VERDICTS = {
    "accept": "The proposal is supported by the frozen evidence and all paper rules.",
    "reject_insufficient_cash": "The paper account cannot afford the proposed buy.",
    "reject_unsupported_signal": "The signal, visual evidence, or position does not support the proposal.",
}
STRATEGY = (
    "Buy requires an upward SMA crossover. Sell and close require a downward SMA crossover. "
    "Hold keeps an open position, and wait requires a flat position. "
    "Use the configured size for buy and sell and the whole position for close."
)


class ReplayContext(BaseModel):
    start: AwareDatetime
    cursor: RowId
    through_id: RowId
    book: UUID | None = None


class SnapshotRequest(BaseModel):
    symbol: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^\S+$")] = SYMBOL
    interval: Interval = "1m"
    timestamp: AwareDatetime
    range_start: AwareDatetime | None = None
    short: Window = 2
    long: Window = 3
    replay: ReplayContext | None = None


class DecisionRequest(BaseModel):
    snapshot_id: UUID | None = None
    image_url: Annotated[str | None, Field(max_length=MAX_DATA_URL)] = None


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


def _order_size(variable, fallback):
    try:
        size = int(os.environ.get(variable, str(fallback)))
    except ValueError:
        size = 0
    if not 1 <= size <= 2**53 - 1:
        raise ValueError(f"{variable} must be a positive whole number of shares.")
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
    if request.range_start and request.range_start > start:
        raise ValueError("The chart range must include the selected candle. Reset the view and recapture.")
    symbol = request.symbol.upper()
    minutes, account, test_data = _read_evidence(db_path, request, cutoff, symbol)
    candles = [
        row["candle"] for row in aggregate_candles(minutes, request.interval, cutoff)
        if row["ended"]
    ]
    indicator_result = calculate_indicators(IndicatorRequest(
        short=request.short, long=request.long, candles=[{**candle, "ended": True} for candle in candles],
    ), configurations(db_path))
    timestamp = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    selected = next((candle for candle in candles if candle["timestamp"] == timestamp), None)
    indicator_values = [
        next((point["value"] for point in item["points"] if point["timestamp"] == timestamp), None)
        for item in indicator_result["series"][:2]
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
    order_size = _order_size("INVEST_PAPER_ORDER_SIZE", DEFAULT_ORDER_SIZE)
    facts = {
        "symbol": symbol, "interval": request.interval, "candle_timestamp": timestamp,
        "decision_time": stamp(cutoff), "close": selected["close"] if selected else None,
        "sma": {"short": indicator_values[0], "long": indicator_values[1],
                "short_window": request.short, "long_window": request.long, "crossover": crossover},
        "account": account, "order_size": order_size,
        "sell_size": _order_size("INVEST_PAPER_SELL_SIZE", order_size),
        "market_source": source, "market_data": "constructed" if test_data else "recorded",
    }
    if request.range_start:
        candles = [candle for candle in candles if datetime.fromisoformat(candle["timestamp"]) >= request.range_start]
    chart_times = {candle["timestamp"] for candle in candles}
    for item in indicator_result["series"]:
        item["points"] = [point for point in item["points"] if point["timestamp"] in chart_times]
    indicator_result["crossovers"] = [
        item for item in indicator_result["crossovers"] if item["timestamp"] in chart_times
    ]
    if candles:
        facts["chart_range"] = {"from": candles[0]["timestamp"], "to": candles[-1]["timestamp"]}
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


def _question(name, instructions, choices):
    return {"type": "choice", "name": name, "instructions": instructions,
            "choices": [{"value": key, "description": meaning} for key, meaning in choices.items()]}


def _analyst_questions(chart):
    questions = [_question("action" if chart else QUESTION_NAME,
                           f"Propose one action for this long-only paper account. {STRATEGY}", CHOICES)]
    if chart:
        questions.extend([
            _question("crossover", "What crossover is visible at the selected candle? Use the image for this answer.",
                      CROSSOVER_CHOICES),
            _question("evidence", "Is the chart image sufficient to assess the signal at the cutoff?", EVIDENCE_CHOICES),
        ])
    return questions


def ask_openai(snapshot, questions, image=None, context=None):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        raise ProviderFailure("provider_unconfigured", "Set OPENAI_API_KEY in the backend environment.")
    evidence = {"snapshot_id": snapshot["id"], **snapshot["facts"]}
    if context:
        evidence["review"] = context
    parts = [{"type": "input_json", "data": evidence}]
    if image:
        parts.append({"type": "input_image", "image_url": image})
    payload = {
        "model": MODEL,
        "input": [{"role": "user", "content": parts}],
        "questions": questions,
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


def _call_role(role, ask, snapshot, image, context=None):
    role["call"] = "attempted"
    try:
        response = ask(snapshot, role["questions"], image, context)
        role["call"] = "returned"
        rows = response.get("answers") if isinstance(response, dict) else None
        if not isinstance(rows, list):
            raise ProviderFailure("malformed_answer", "The provider did not return an answer list.")
        if isinstance(response.get("id"), str):
            role["provider_decision_id"] = response["id"]
        errors = []
        refused = []
        for question in role["questions"]:
            name = question["name"]
            matches = [row for row in rows if isinstance(row, dict) and row.get("name") == name]
            answer = matches[0] if len(matches) == 1 else {}
            choice = answer.get("choice")
            if answer.get("type") == "refusal":
                refused.append(name)
            elif answer.get("type") == "choice" and choice in [item["value"] for item in question["choices"]]:
                role["answers"][name] = choice
            else:
                errors.append(name)
        if refused:
            raise ProviderFailure("provider_refusal", "The provider declined: " + ", ".join(refused) + ".")
        if errors:
            raise ProviderFailure("malformed_answer", "Missing or invalid answers: " + ", ".join(errors) + ".")
        role["status"] = "valid"
    except ProviderFailure as error:
        if error.status == "provider_unconfigured":
            role["call"] = "not_sent"
        role.update(status=error.status, explanation=str(error))


def _validate(action, facts):
    account = facts["account"]
    shares, size = account["shares"], facts["order_size"]
    sell_size = facts.get("sell_size", size)
    if action == "buy" and Decimal(account["cash"]) < size * Decimal(str(facts["close"])):
        return None, "Cash at the snapshot close does not cover the configured share quantity."
    if action == "sell" and shares < sell_size:
        return None, "The position is smaller than the configured sale size."
    if action in ("hold", "close") and shares == 0:
        return None, "This action requires an open position."
    if action == "wait" and shares != 0:
        return None, "Wait requires a flat position."
    expected = {"buy": "up", "sell": "down", "close": "down"}.get(action)
    if expected and facts["sma"]["crossover"] != expected:
        return None, f"{action.capitalize()} requires a confirmed {expected} SMA crossover."
    return {"buy": size, "sell": sell_size, "close": shares}.get(action), None


def _role(provider, model, questions):
    return {"provider": provider, "model": model, "call": "not_sent", "status": "not_run",
            "questions": questions, "answers": {}, "provider_decision_id": None, "explanation": None}


def _review_result(result, snapshot, image_url, ask):
    facts = snapshot["facts"]
    sma = facts["sma"]
    evidence = (f"{facts['symbol']} {facts['interval']} closed at {facts['close']} with "
                f"short/long SMA {sma['short']}/{sma['long']} and {sma['crossover'] or 'no'} crossover. "
                f"Paper cash was {facts['account']['cash']} with {facts['account']['shares']} shares.")
    analyst, reviewer = result["analyst"], result["reviewer"]
    _call_role(analyst, ask, snapshot, image_url)
    result.update(model_call=analyst["call"], provider_decision_id=analyst["provider_decision_id"])
    if analyst["status"] != "valid":
        result.update(status=analyst["status"], explanation=f"{evidence} {analyst['explanation']}")
        return
    action = analyst["answers"]["action" if image_url else QUESTION_NAME]
    result["proposal"] = action
    quantity, reason = _validate(action, facts)
    if action == "buy":
        cost = facts["order_size"] * Decimal(str(facts["close"]))
        evidence += f" The proposed {facts['order_size']} share(s) cost {cost} at the snapshot close."
    visual_status = None
    if image_url:
        claim = analyst["answers"]["crossover"]
        actual = sma["crossover"] or "none"
        if claim == "unreadable" or analyst["answers"]["evidence"] == "insufficient":
            visual_status = "insufficient_visual_evidence"
        elif claim != actual:
            visual_status = "conflicting_signal"
        result["visual_comparison"] = {"model": claim, "calculated": actual, "status": visual_status or "matches"}
        evidence += f" The analyst reported {claim}; Python calculated {actual}."
    analyst["explanation"] = evidence
    _call_role(reviewer, ask, snapshot, image_url, {
        "proposal": action, "analyst_answers": analyst["answers"], "recorded_evidence": evidence,
        "backend_rule_result": reason or visual_status or "passed",
    })
    verdict = reviewer["answers"].get("verdict")
    status = "valid"
    detail = ""
    if reviewer["status"] != "valid":
        status, detail = f"review_{reviewer['status']}", reviewer["explanation"]
    else:
        reviewer["explanation"] = evidence + f" Reviewer verdict: {verdict}."
        if reason:
            status, detail = "invalid_proposal", reason
        elif visual_status:
            status, detail = visual_status, "Recapture the chart with a readable view through the selected candle."
        elif verdict != "accept":
            status, detail = "review_rejected", f"Reviewer verdict: {verdict}."
    result.update(
        status=status, quantity=quantity, explanation=f"{evidence} {detail}".strip(),
        eligible_for_paper_order=(status == "valid" and quantity is not None and
                                  facts["market_data"] == "recorded" and facts["account"]["book"] is not None),
    )


def request_decision(db_path, snapshot_id, ask=ask_openai, *, provider="OpenAI", model=MODEL, image_url=None):
    snapshot = get_snapshot(db_path, snapshot_id)
    result = {
        "id": str(uuid4()), "snapshot_id": snapshot_id, "facts": snapshot["facts"] if snapshot else None,
        "provider": provider, "model": model, "provider_decision_id": None, "model_call": "not_sent",
        "proposal": None, "quantity": None, "eligible_for_paper_order": False,
        "analyst": _role(provider, model, _analyst_questions(image_url is not None)),
        "reviewer": _role(provider, model, [_question(
            "verdict", f"Independently review the analyst proposal using the frozen evidence. {STRATEGY} "
            "Reject an unaffordable buy, unsupported or unreadable signals, and position-rule violations.", VERDICTS,
        )]),
        "capture": None, "visual_comparison": None,
    }
    png = None
    if image_url is not None:
        try:
            decoded, tag = decode_capture(image_url)
            if not snapshot or tag != snapshot_id:
                raise ProviderFailure("capture_mismatch", "The PNG belongs to a different snapshot. Recapture the chart.")
            png = decoded
            result["capture"] = {"url": f"/api/decisions/{result['id']}/capture",
                                 "snapshot_id": snapshot_id, "sha256": sha256(png).hexdigest()}
        except ValueError as error:
            result.update(status="invalid_capture", explanation=str(error))
        except ProviderFailure as error:
            result.update(status=error.status, explanation=str(error))
    if "status" not in result:
        if not snapshot or snapshot["missing"]:
            result.update(status="missing_input", explanation=(
                "Missing evidence: " + ", ".join(snapshot["missing"]) + "."
                if snapshot else "An existing snapshot ID is required."
            ))
        else:
            _review_result(result, snapshot, image_url, ask)
    with transaction(db_path) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY, decision_json TEXT NOT NULL CHECK (json_valid(decision_json))
            )
        """)
        connection.execute("INSERT INTO decisions VALUES (?, ?)", (result["id"], json.dumps(result)))
        if png is not None:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS decision_captures (
                    decision_id TEXT PRIMARY KEY REFERENCES decisions(id),
                    snapshot_id TEXT NOT NULL REFERENCES decision_snapshots(id), png BLOB NOT NULL
                )
            """)
            connection.execute("INSERT INTO decision_captures VALUES (?, ?, ?)", (result["id"], snapshot_id, png))
    return result


def get_capture(db_path, decision_id):
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'decision_captures' AND type = 'table'"
        ).fetchone()
        if exists:
            row = connection.execute("SELECT png FROM decision_captures WHERE decision_id = ?", (decision_id,)).fetchone()
            if row:
                return row[0]
    return None
