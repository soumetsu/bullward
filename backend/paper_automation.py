"""Schedule reviewed chart decisions and submit them to the local paper ledger."""

from contextlib import closing
from datetime import UTC, datetime
import json
import os
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, Field
import sqlite3

from .chart_capture import MAX_DATA_URL
from .decisions import Interval, ReplayContext, Window, _candle_end, create_snapshot
from .live_feed import SYMBOL
from .observations import DEFAULT_DB_PATH
from .paper import (
    LIVE_BOOK, MAX_DECISION_AGE, _account, _observation, ensure_account,
    has_automation, initialize_paper, stamp, submit_order, transaction,
)

STRATEGY_ID = "sma-crossover-v1"
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS paper_automation_runs (
           id TEXT PRIMARY KEY, book TEXT NOT NULL REFERENCES paper_accounts(id),
           symbol TEXT NOT NULL, interval TEXT NOT NULL, strategy TEXT NOT NULL,
           short_window INTEGER NOT NULL, long_window INTEGER NOT NULL,
           started_at TEXT NOT NULL, replay_start TEXT, replay_ceiling INTEGER,
           enabled INTEGER NOT NULL DEFAULT 1, test_mode INTEGER NOT NULL
       )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS one_active_paper_run
       ON paper_automation_runs(book) WHERE enabled = 1""",
    """CREATE TABLE IF NOT EXISTS paper_automation_jobs (
           id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES paper_automation_runs(id),
           book TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL, strategy TEXT NOT NULL,
           candle TEXT NOT NULL, cutoff TEXT NOT NULL, cursor INTEGER,
           snapshot_id TEXT, decision_id TEXT UNIQUE, order_id INTEGER UNIQUE REFERENCES paper_orders(id),
           status TEXT NOT NULL, reason TEXT,
           UNIQUE(book, strategy, symbol, interval, candle)
       )""",
)


class AutomationStart(BaseModel):
    symbol: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^\S+$")] = SYMBOL
    interval: Interval = "1m"
    short: Window = 2
    long: Window = 3
    replay: ReplayContext | None = None


class AutomationAnalysis(BaseModel):
    image_url: Annotated[str | None, Field(max_length=MAX_DATA_URL)] = None
    capture_error: Annotated[str | None, Field(max_length=300)] = None


def _reader(db_path):
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return closing(connection)


def _run(connection, run_id):
    if has_automation(connection):
        row = connection.execute("SELECT * FROM paper_automation_runs WHERE id = ?", (run_id,)).fetchone()
        if row:
            return row
    raise ValueError("Paper automation run not found.")


def _job(connection, run_id, job_id):
    row = connection.execute(
        "SELECT * FROM paper_automation_jobs WHERE id = ? AND run_id = ?", (job_id, run_id),
    ).fetchone()
    if not row:
        raise ValueError("Paper automation candle not found.")
    return row


def _replay_clock(connection, symbol, context):
    row = _observation(connection, context.cursor, symbol)
    if not row or not context.book or context.cursor > context.through_id:
        raise ValueError("Select an active replay observation for the paper account.")
    if stamp(context.start.astimezone(UTC)) > row["received_at_utc"]:
        raise ValueError("The replay observation is before the selected receipt range.")
    return row["received_at_utc"]


def _stop(connection, run_id, reason):
    connection.execute("UPDATE paper_automation_runs SET enabled = 0 WHERE id = ?", (run_id,))
    connection.execute(
        """UPDATE paper_orders SET status = 'rejected', reason = ? WHERE status = 'pending' AND id IN
           (SELECT order_id FROM paper_automation_jobs WHERE run_id = ?)""", (reason, run_id),
    )
    connection.execute(
        """UPDATE paper_automation_jobs SET status = 'skipped', reason = ?
           WHERE run_id = ? AND status IN ('preparing', 'awaiting_capture', 'analyzing', 'pending')""",
        (reason, run_id),
    )


def stop_previous_runs(db_path):
    with _reader(db_path) as reader:
        if not has_automation(reader):
            return
    with transaction(db_path) as connection:
        for row in connection.execute("SELECT id FROM paper_automation_runs WHERE enabled = 1").fetchall():
            _stop(connection, row["id"], "Backend restarted. Start paper automation again to continue.")


def start_automation(db_path, settings):
    if settings.short >= settings.long:
        raise ValueError("The short SMA window must be smaller than the long window.")
    symbol = settings.symbol.upper()
    replay = settings.replay
    if replay is None and symbol != SYMBOL:
        raise ValueError("The live paper account uses AAPL observations.")
    test_mode = os.environ.get("INVEST_PAPER_TEST_MODE") == "1" and db_path.resolve() != DEFAULT_DB_PATH.resolve()
    initialize_paper(db_path)
    with transaction(db_path) as connection:
        for statement in SCHEMA:
            connection.execute(statement)
        started = _replay_clock(connection, symbol, replay) if replay else stamp(datetime.now(UTC))
        book = str(replay.book) if replay else LIVE_BOOK
        ensure_account(connection, book, symbol, replay.cursor if replay else None)
        active = connection.execute("SELECT id FROM paper_automation_runs WHERE book = ? AND enabled = 1", (book,)).fetchone()
        if active:
            _stop(connection, active["id"], "A new automation run replaced this run.")
        run_id = str(uuid4())
        connection.execute(
            """INSERT INTO paper_automation_runs
               (id, book, symbol, interval, strategy, short_window, long_window, started_at,
                replay_start, replay_ceiling, test_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, book, symbol, settings.interval, STRATEGY_ID, settings.short, settings.long, started,
             stamp(replay.start.astimezone(UTC)) if replay else None, replay.through_id if replay else None,
             int(bool(replay and test_mode))),
        )
    return automation_state(db_path, run_id)


def stop_automation(db_path, run_id):
    with transaction(db_path) as connection:
        _run(connection, run_id)
        _stop(connection, run_id, "Paper automation stopped.")
    return automation_state(db_path, run_id)


def _scope_clock(connection, run, request):
    if (request.symbol.upper(), request.interval, request.short, request.long) != (
        run["symbol"], run["interval"], run["short_window"], run["long_window"],
    ):
        raise ValueError("The candle must match the current automation symbol, interval, and SMA windows.")
    replay = request.replay
    if run["book"] == LIVE_BOOK and not replay:
        return stamp(datetime.now(UTC))
    if (not replay or str(replay.book) != run["book"] or replay.through_id != run["replay_ceiling"]
            or stamp(replay.start.astimezone(UTC)) != run["replay_start"]):
        raise ValueError("The candle belongs to a different paper replay.")
    if _account(connection, run["book"])["cursor_id"] != replay.cursor:
        raise ValueError("The replay cursor has changed.")
    return _replay_clock(connection, run["symbol"], replay)


def claim_candle(db_path, run_id, request, source):
    start = request.timestamp.astimezone(UTC)
    cutoff = stamp(_candle_end(start, request.interval))
    candle = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    with transaction(db_path) as connection:
        run = _run(connection, run_id)
        if not run["enabled"]:
            return {"status": "stopped"}
        clock = _scope_clock(connection, run, request)
        existing = connection.execute(
            """SELECT 1 FROM paper_automation_jobs WHERE book = ? AND strategy = ? AND symbol = ?
               AND interval = ? AND candle = ?""",
            (run["book"], run["strategy"], run["symbol"], run["interval"], candle),
        ).fetchone()
        if existing:
            return {"status": "already_handled"}
        if cutoff > clock or cutoff <= run["started_at"]:
            return {"status": "outside_run"}
        job_id = str(uuid4())
        connection.execute(
            """INSERT INTO paper_automation_jobs
               (id, run_id, book, symbol, interval, strategy, candle, cutoff, cursor, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'preparing')""",
            (job_id, run_id, run["book"], run["symbol"], run["interval"], run["strategy"], candle, cutoff,
             request.replay.cursor if request.replay else None),
        )
        if datetime.fromisoformat(clock) - datetime.fromisoformat(cutoff) > MAX_DECISION_AGE:
            connection.execute("UPDATE paper_automation_jobs SET status = 'skipped', reason = ? WHERE id = ?",
                               ("The candle was already too old for automation.", job_id))
            return {"id": job_id, "status": "skipped"}

    try:
        snapshot = create_snapshot(db_path, request, source)
    except (ValueError, sqlite3.Error):
        with transaction(db_path) as connection:
            connection.execute(
                """UPDATE paper_automation_jobs SET status = 'skipped', reason = ?
                   WHERE id = ? AND status = 'preparing'""",
                ("The frozen chart snapshot could not be created.", job_id),
            )
        return {"id": job_id, "status": "skipped"}
    with transaction(db_path) as connection:
        row = connection.execute(
            """UPDATE paper_automation_jobs SET snapshot_id = ?, status = 'awaiting_capture'
               WHERE id = ? AND status = 'preparing' RETURNING id""", (snapshot["id"], job_id),
        ).fetchone()
    return {"id": job_id, "status": "awaiting_capture", "snapshot": snapshot} if row else {"status": "stopped"}


def _skip(connection, job_id, reason, status="skipped"):
    connection.execute("UPDATE paper_automation_jobs SET status = ?, reason = ? WHERE id = ?",
                       (status, reason, job_id))


def apply_saved_decision(db_path, run_id, job_id, decision_id):
    """Link the saved result and, if approved, its pending simulator order atomically."""
    with transaction(db_path) as connection:
        run = _run(connection, run_id)
        job = _job(connection, run_id, job_id)
        if job["decision_id"]:
            return
        row = connection.execute("SELECT decision_json FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        decision = json.loads(row[0]) if row else None
        if not decision or decision["snapshot_id"] != job["snapshot_id"]:
            raise ValueError("The saved decision belongs to a different candle.")
        connection.execute("UPDATE paper_automation_jobs SET decision_id = ? WHERE id = ?", (decision_id, job_id))
        if not run["enabled"] or job["status"] != "analyzing":
            return
        facts = decision["facts"]
        account = facts.get("account") if facts else None
        if not account or (account["book"], facts["symbol"], facts["interval"], facts["candle_timestamp"]) != (
            job["book"], job["symbol"], job["interval"], job["candle"],
        ):
            _skip(connection, job_id, "The decision does not match this paper account and candle.")
            return
        if decision["status"] != "valid" or decision["reviewer"]["answers"].get("verdict") != "accept":
            _skip(connection, job_id, decision["explanation"])
            return
        if not decision["capture"] or decision["visual_comparison"]["status"] != "matches":
            _skip(connection, job_id, "Automation requires matching chart evidence.")
            return
        if facts["market_data"] == "constructed" and not run["test_mode"]:
            _skip(connection, job_id, "Constructed decisions require a disposable replay account.")
            return
        action = decision["proposal"]
        if action in ("hold", "wait"):
            _skip(connection, job_id, f"{action.capitalize()} creates no order.", "no_order")
            return
        test_allowed = bool(run["test_mode"] and facts["market_data"] == "constructed")
        if not decision["eligible_for_paper_order"] and not test_allowed:
            _skip(connection, job_id, "This decision is ineligible for a paper order.")
            return
        if job["book"] != LIVE_BOOK:
            if _account(connection, job["book"])["cursor_id"] != job["cursor"]:
                _skip(connection, job_id, "Replay advanced before the decision was reviewed.")
                return
        elif datetime.now(UTC) - datetime.fromisoformat(job["cutoff"]) > MAX_DECISION_AGE:
            _skip(connection, job_id, "The decision expired before the order could be submitted.")
            return
        order = submit_order(connection, job["book"], job["symbol"], action, decision_id,
                             decision["quantity"], job["cursor"])
        connection.execute("UPDATE paper_automation_jobs SET order_id = ?, status = ?, reason = ? WHERE id = ?",
                           (order["id"], "pending" if order["status"] == "pending" else "skipped",
                            order["reason"], job_id))


def analyze_candle(db_path, run_id, job_id, request, decide):
    with transaction(db_path) as connection:
        run = _run(connection, run_id)
        job = _job(connection, run_id, job_id)
        if not run["enabled"] or job["status"] != "awaiting_capture":
            return dict(job)
        if not request.image_url:
            _skip(connection, job_id, request.capture_error or "The chart PNG was unavailable.")
            return dict(_job(connection, run_id, job_id))
        connection.execute("UPDATE paper_automation_jobs SET status = 'analyzing' WHERE id = ?", (job_id,))
    try:
        decision = decide(db_path, job["snapshot_id"], image_url=request.image_url)
        apply_saved_decision(db_path, run_id, job_id, decision["id"])
    except Exception:
        with transaction(db_path) as connection:
            if _job(connection, run_id, job_id)["status"] == "analyzing":
                _skip(connection, job_id, "Analysis failed before a paper order could be submitted.")
        raise
    with _reader(db_path) as connection:
        return dict(_job(connection, run_id, job_id))


def automation_state(db_path, run_id):
    with _reader(db_path) as connection, connection:
        connection.execute("BEGIN")
        run = _run(connection, run_id)
        account = _account(connection, run["book"])
        jobs = []
        has_decisions = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'decisions'"
        ).fetchone()
        rows = connection.execute(
            """SELECT j.*, f.price AS fill_price, f.executed_at, f.observation_id, o.action, o.quantity
               FROM paper_automation_jobs j LEFT JOIN paper_orders o ON o.id = j.order_id
               LEFT JOIN paper_fills f ON f.order_id = o.id WHERE j.run_id = ? ORDER BY j.rowid DESC LIMIT 50""",
            (run_id,),
        ).fetchall()
        for row in rows:
            job = dict(row)
            if has_decisions and job["decision_id"]:
                data, = connection.execute("SELECT decision_json FROM decisions WHERE id = ?", (job["decision_id"],)).fetchone()
                decision = json.loads(data)
                job.update(proposal=decision["proposal"], verdict=decision["reviewer"]["answers"].get("verdict"),
                           crossover=(decision["facts"] or {}).get("sma", {}).get("crossover"),
                           analysis_status=decision["status"], explanation=decision["explanation"],
                           capture=decision["capture"], provider=decision["provider"])
            jobs.append(job)
    return {"run": dict(run), "account": {"cash": account["cash"], "shares": account["shares"]}, "jobs": jobs}
