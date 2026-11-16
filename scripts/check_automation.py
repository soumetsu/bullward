"""Exercise the automation ledger with constructed observations and fixed Decisions replies."""

import argparse
from functools import partial
from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import uvicorn

from backend import main
from backend.chart_capture import decode_capture
from backend.decisions import SnapshotRequest, request_decision
from backend.observations import initialize_database
from backend.paper import advance_replay_account, paper_state
from backend.paper_automation import (
    AutomationAnalysis, AutomationStart, analyze_candle, apply_saved_decision, automation_state,
    claim_candle, start_automation, stop_automation, stop_previous_runs,
)
from backend.replay import replay_step
from scripts.check_decisions import fixture_png
from scripts.check_prompt9 import at, save

LABEL = "Constructed automation fixture"
START = at("14:30:00")
END = at("14:45:00")
SYMBOL = "SMA"


def seed(db):
    initialize_database(db)
    for index, (time, price) in enumerate([
        ("14:30:05", 13), ("14:31:05", 10), ("14:32:05", 7), ("14:33:05", 13),
        ("14:34:05", 19), ("14:35:05", 7), ("14:35:15", 7), ("14:36:05", 1),
        ("14:36:15", 2), ("14:42:05", 50),
    ]):
        save(db, SYMBOL, time, price, 1000 + index * 50)


def fixture_ask(snapshot, questions, image, context):
    png, tag = decode_capture(image)
    assert tag == snapshot["id"] and snapshot["facts"]["market_data"] == "constructed"
    digest = sha256(png).hexdigest()
    facts = snapshot["facts"]
    crossover = facts["sma"]["crossover"] or "none"
    if context:
        assert context["analyst_answers"]["crossover"] == crossover
        role = "reviewer"
        choices = {"verdict": "reject_unsupported_signal" if facts["candle_timestamp"].endswith("14:35:00Z") else "accept"}
    else:
        role = "analyst"
        action = "buy" if crossover == "up" else "hold" if facts["account"]["shares"] else "wait"
        choices = {"action": action, "crossover": crossover, "evidence": "sufficient"}
    assert [item["name"] for item in questions] == list(choices)
    return {"id": f"fixture-{role}-{digest}",
            "answers": [{"name": name, "type": "choice", "choice": value} for name, value in choices.items()]}


fixture_decision = partial(request_decision, ask=fixture_ask, provider="Controlled fixture")


def next_frame(db, frame=None):
    return replay_step(db, SYMBOL, START, END, "1m", frame["observation"]["id"] if frame else None,
                       frame["through_id"] if frame else None)


def selection(book, frame):
    return {"symbol": SYMBOL, "replay": {"book": book, "start": START, "cursor": frame["observation"]["id"],
                                       "through_id": frame["through_id"]}}


def claim(db, run, book, frame, time):
    return claim_candle(db, run, SnapshotRequest(**selection(book, frame), timestamp=at(time)), LABEL)


def analyze(db, run, job, decide=fixture_decision):
    return analyze_candle(db, run, job["id"],
                          AutomationAnalysis(image_url=fixture_png(job["snapshot"]["id"])), decide)


def advance(db, book, frame):
    following = next_frame(db, frame)
    advance_replay_account(db, book, SYMBOL, frame["observation"]["id"],
                           following["observation"]["id"], following["candles"])
    return following


def begin(db):
    frame = None
    for _ in range(5):
        frame = next_frame(db, frame)
    book = str(uuid4())
    run = start_automation(db, AutomationStart(**selection(book, frame)))["run"]["id"]
    assert claim(db, run, book, frame, "14:33:00")["status"] == "outside_run"
    frame = advance(db, book, frame)
    job = claim(db, run, book, frame, "14:34:00")
    assert job["snapshot"]["facts"]["sma"]["crossover"] == "up"
    return book, run, frame, job


def check(db):
    seed(db)
    book, run, frame, job = begin(db)
    # The manual chart question is read-only even with a run enabled.
    manual = fixture_decision(db, job["snapshot"]["id"], image_url=fixture_png(job["snapshot"]["id"]))
    assert manual["status"] == "valid" and not paper_state(db, book, SYMBOL)["orders"]
    approved = analyze(db, run, job)
    assert approved["status"] == "pending"
    apply_saved_decision(db, run, job["id"], approved["decision_id"])
    assert analyze(db, run, job)["order_id"] == approved["order_id"]
    frame = advance(db, book, frame)
    apply_saved_decision(db, run, job["id"], approved["decision_id"])
    state = automation_state(db, run)
    assert state["jobs"][0]["status"] == "filled" and state["jobs"][0]["fill_price"] == "7"
    assert state["account"] == {"cash": "993", "shares": 1}
    frame = advance(db, book, frame)
    vetoed = claim(db, run, book, frame, "14:35:00")
    assert analyze(db, run, vetoed)["status"] == "skipped"
    assert automation_state(db, run)["jobs"][0]["verdict"] == "reject_unsupported_signal"
    assert len(paper_state(db, book, SYMBOL)["orders"]) == 1
    stop_previous_runs(db)
    assert not automation_state(db, run)["run"]["enabled"]
    new_run = start_automation(db, AutomationStart(**selection(book, frame)))["run"]["id"]
    assert claim(db, new_run, book, frame, "14:34:00")["status"] == "already_handled"
    print("PASS: approved decision replayed once, 1 fill at 7, cash 993, 1 share; veto and restart preserved the ledger.")

    book, run, frame, job = begin(db)

    def stopped_reply(database, snapshot_id, image_url):
        stop_automation(db, run)
        return fixture_decision(database, snapshot_id, image_url=image_url)

    result = analyze(db, run, job, stopped_reply)
    assert result["status"] == "skipped" and result["decision_id"] is not None
    assert not paper_state(db, book, SYMBOL)["orders"]

    book, run, frame, job = begin(db)
    assert analyze(db, run, job)["status"] == "pending"
    stop_automation(db, run)
    advance(db, book, frame)
    state = paper_state(db, book, SYMBOL)
    assert state["orders"][0]["status"] == "rejected" and not state["orders"][0]["observation_id"]

    book, run, frame, job = begin(db)
    failed = analyze_candle(db, run, job["id"], AutomationAnalysis(capture_error="PNG unavailable."), fixture_decision)
    assert failed["status"] == "skipped" and failed["reason"] == "PNG unavailable."
    assert not paper_state(db, book, SYMBOL)["orders"]

    book, run, frame, job = begin(db)
    analyze(db, run, job)
    later = frame
    while not later["done"]:
        later = next_frame(db, later)
    advance_replay_account(db, book, SYMBOL, frame["observation"]["id"], later["observation"]["id"], later["candles"])
    assert automation_state(db, run)["jobs"][0]["reason"] == "The decision expired before the fill."

    with patch.dict(os.environ, {"INVEST_PAPER_TEST_MODE": "0"}):
        book, run, frame, job = begin(db)
        assert analyze(db, run, job)["reason"] == "Constructed decisions require a disposable replay account."
        assert not paper_state(db, book, SYMBOL)["orders"]
    print("PASS: late reply, queued-order stop, missing capture, expiry, and constructed data isolation.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--serve", type=int)
    args = parser.parse_args()
    if args.seed:
        if args.seed.exists():
            parser.error("Choose a fresh fixture database.")
        seed(args.seed)
    elif args.serve:
        with patch.object(main, "request_decision", fixture_decision):
            uvicorn.run(main.app, host="127.0.0.1", port=args.serve)
    else:
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory(prefix="invest-automation-", dir=root / "backend/data") as directory:
            with patch.dict(os.environ, {"INVEST_PAPER_TEST_MODE": "1"}):
                check(Path(directory) / "observations.sqlite3")
