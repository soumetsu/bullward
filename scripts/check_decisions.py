"""Exercise the local Decisions API over HTTP with a separate, constructed database."""

import argparse
from contextlib import closing
from functools import partial
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.request import Request, urlopen
from uuid import uuid4

import uvicorn

from backend import main
from backend.decisions import ask_openai, request_decision
from backend.observations import record_observation
from backend.paper import advance_replay_account, execute_action
from scripts.check_prompt9 import at, seed, step
from scripts.smoke_test import ROOT, free_port, wait_ready


def setup_account(database):
    seed(database)
    record_observation(database, {
        "id": "AAPL", "time": int(at("14:32:55").timestamp() * 1000), "price": 80, "test_data": True,
    }, at("14:33:25"))
    frame = step(database, "AAPL")
    book = str(uuid4())
    while not frame["done"]:
        event = frame["observation"]
        source_time = event["source_time_ms"]
        for time, action, quantity in [
            ("14:30:05", "buy", 4), ("14:32:05", "sell", 1), ("14:33:01", "close", None),
        ]:
            if source_time == int(at(time).timestamp() * 1000):
                execute_action(database, book, "AAPL", action, str(uuid4()), quantity, event["id"])
        following = step(database, "AAPL", frame)
        advance_replay_account(database, book, "AAPL", event["id"],
                               following["observation"]["id"], following["candles"])
        frame = following
    return {
        "timestamp": "2026-11-09T14:32:00Z", "interval": "1m", "symbol": "AAPL",
        "replay": {"start": "2026-11-09T14:30:00Z", "cursor": frame["observation"]["id"],
                   "through_id": frame["through_id"], "book": book},
    }


def serve(port, live):
    if live:
        uvicorn.run(main.app, host="127.0.0.1", port=port)
        return
    answers = iter(("hold", "timeout", "wrong_question", "wait", "sell"))

    def transport(request, timeout):
        assert request.full_url == "https://api.openai.com/v1/decisions" and timeout > 0
        assert request.get_method() == "POST"
        payload = json.loads(request.data)
        assert payload["model"] == "gpt-6-luna"
        assert payload["input"][0]["content"][0]["type"] == "input_json"
        evidence = payload["input"][0]["content"][0]["data"]
        assert evidence["snapshot_id"] and evidence["close"] == 95 and evidence["account"]["shares"] == 3
        question, = payload["questions"]
        assert question["name"] == "paper_action" and question["type"] == "choice"
        assert {choice["value"] for choice in question["choices"]} == {"buy", "sell", "hold", "wait", "close"}
        action = next(answers)
        if action == "timeout":
            raise TimeoutError
        name = "unexpected" if action == "wrong_question" else "paper_action"
        return BytesIO(json.dumps({
            "id": "fixture_response", "answers": [{"type": "choice", "name": name, "choice": action}],
        }).encode())

    with (
        patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-only"}),
        patch("backend.decisions.urlopen", transport),
        patch.object(main, "request_decision", partial(request_decision, ask=ask_openai,
                                                      provider="Controlled fixture", model="gpt-6-luna")),
    ):
        uvicorn.run(main.app, host="127.0.0.1", port=port)


def ledger(database):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return [connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in ("paper_accounts", "paper_orders", "paper_fills", "paper_pnl")]


def call(url, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(url + path, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def check(database, url, selection, live):
    before = ledger(database)
    snapshot = call(url, "/api/decision-snapshots", selection)
    facts = snapshot["facts"]
    assert not snapshot["missing"]
    assert facts["decision_time"] == "2026-11-09T14:33:00.000000+00:00"
    assert facts["close"] == snapshot["candles"][-1]["close"] == 95
    assert facts["sma"] == {
        "short": 102.5, "long": 304 / 3, "short_window": 2, "long_window": 3, "crossover": None,
    }
    assert facts["account"]["cash"] == "710" and facts["account"]["shares"] == 3
    assert facts["market_data"] == "constructed"
    assert call(url, f"/api/decision-snapshots/{snapshot['id']}") == snapshot
    results = [call(url, "/api/decisions", {"snapshot_id": snapshot["id"]})
               for _ in range(1 if live else 5)]
    if not live:
        assert [row["status"] for row in results] == [
            "valid", "timeout", "malformed_answer", "invalid_proposal", "valid",
        ]
        assert results[0]["proposal"] == "hold" and results[0]["quantity"] is None
        assert results[-1]["proposal"] == "sell" and results[-1]["quantity"] == facts["order_size"]
        assert results[0]["model_call"] == "returned" and results[1]["model_call"] == "attempted"
        incomplete = call(url, "/api/decision-snapshots", {**selection, "long": 25})
        results.extend([
            call(url, "/api/decisions", {"snapshot_id": incomplete["id"]}),
            call(url, "/api/decisions", {}),
        ])
        assert all(row["status"] == "missing_input" and row["model_call"] == "not_sent"
                   for row in results[-2:])
    for row in results:
        assert call(url, f"/api/decisions/{row['id']}") == row
        assert not row["eligible_for_paper_order"]
    assert ledger(database) == before
    print("Frozen 14:32 UTC close 95, account cash 710 and 3 shares; later observation and close fill excluded.")
    print(f"SQLite readback and unchanged paper ledger verified. Results: {[row['status'] for row in results]}.")
    if live:
        result, = results
        print(f"Provider {result['provider']}, model {result['model']}, call {result['model_call']}, "
              f"local decision {result['id']}, provider decision {result['provider_decision_id']}.")
        return 0 if result["proposal"] and result["model_call"] == "returned" else 2
    return 0


def run(live):
    with TemporaryDirectory(prefix="invest-decisions-", dir=ROOT / "backend/data") as directory:
        database = Path(directory) / "observations.sqlite3"
        selection = setup_account(database)
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        env = dict(os.environ, INVEST_OBSERVATIONS_DB=str(database), INVEST_LIVE_FEED="0",
                   INVEST_SOURCE_LABEL="Constructed decision test data", INVEST_PAPER_ORDER_SIZE="1")
        command = [sys.executable, "-m", "scripts.check_decisions", "--serve", str(port)]
        if live:
            command.append("--live")
        with open(Path(directory) / "backend.log", "w+", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                wait_ready(url + "/api/health", process)
                return check(database, url, selection, live)
            except Exception:
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use a live provider with constructed market data.")
    parser.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.serve:
        serve(options.serve, options.live)
    else:
        raise SystemExit(run(options.live))
