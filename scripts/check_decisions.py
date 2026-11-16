"""Exercise the local Decisions API over HTTP with a separate, constructed database."""

import argparse
from base64 import b64encode
from binascii import crc32
from contextlib import closing
from functools import partial
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
from struct import pack
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.request import Request, urlopen
from uuid import uuid4
import zlib

import uvicorn

from backend import main
from backend.chart_capture import DATA_PREFIX, PNG_SIGNATURE, TAG, decode_capture
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


def setup_review_market(database, hour, scale):
    ids = []
    for index, price in enumerate((13, 10, 7, 13, 19, 7)):
        received_at = at(f"{hour}:{index:02d}:05")
        ids.append(record_observation(database, {
            "id": "AAPL", "time": int(received_at.timestamp() * 1000), "price": price * scale,
        }, received_at))
    book = str(uuid4())
    execute_action(database, book, "AAPL", "buy", str(uuid4()), 1, ids[0])
    return {"timestamp": f"2026-11-09T{hour}:04:00Z",
            "replay": {"start": f"2026-11-09T{hour}:00:00Z", "cursor": ids[-1],
                       "through_id": ids[-1], "book": book}}


def fixture_png(snapshot_id):
    def chunk(kind, content):
        return pack(">I", len(content)) + kind + content + pack(">I", crc32(kind + content))

    png = (PNG_SIGNATURE + chunk(b"IHDR", pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
           + chunk(b"tEXt", TAG + snapshot_id.encode())
           + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b""))
    return DATA_PREFIX + b64encode(png).decode()


def serve(port, live, chart=False):
    if live:
        uvicorn.run(main.app, host="127.0.0.1", port=port)
        return
    legacy_answers = iter(("hold", "timeout", "wrong_question", "wait", "sell"))
    affordable_verdicts = iter(("accept", "reject_unsupported_signal"))
    images = {}
    snapshot_facts = {}

    def transport(request, timeout):
        assert request.full_url == "https://api.openai.com/v1/decisions" and timeout > 0
        assert request.get_method() == "POST"
        payload = json.loads(request.data)
        assert payload["model"] == "gpt-6-luna"
        message, = payload["input"]
        assert message["role"] == "user" and message["content"][0]["type"] == "input_json"
        evidence = message["content"][0]["data"]
        snapshot_id = evidence["snapshot_id"]
        names = [question["name"] for question in payload["questions"]]
        is_review = names == ["verdict"]
        if is_review:
            assert {key: value for key, value in evidence.items() if key != "review"} == snapshot_facts.pop(snapshot_id)
        else:
            snapshot_facts[snapshot_id] = evidence
        digest = ""
        if len(message["content"]) == 2:
            image_part = message["content"][1]
            assert image_part["type"] == "input_image"
            png, tag = decode_capture(image_part["image_url"])
            assert tag == snapshot_id
            assert evidence["chart_range"]["to"] == evidence["candle_timestamp"]
            digest = sha256(png).hexdigest()
            if is_review:
                assert images.pop(snapshot_id) == (digest, evidence["chart_range"])
                assert evidence["review"]["analyst_answers"]["action"] == evidence["review"]["proposal"]
            else:
                assert names == ["action", "crossover", "evidence"]
                images[snapshot_id] = (digest, evidence["chart_range"])

        if chart:
            assert digest and evidence["close"] == 19 and evidence["sma"]["crossover"] == "up"
            choices = {"verdict": "accept"} if is_review else {
                "action": "buy", "crossover": "up", "evidence": "sufficient",
            }
        elif digest:
            case = evidence["sma"]["short_window"]
            if is_review and case == 5:
                raise TimeoutError
            choices = {"verdict": "accept"} if is_review else {
                "action": "wait", "crossover": "down" if case == 2 else "unreadable" if case == 3 else "none",
                **({"evidence": "sufficient"} if case != 4 else {}),
            }
        elif is_review:
            assert evidence["review"]["analyst_answers"]["paper_action"] == evidence["review"]["proposal"]
            verdict = next(affordable_verdicts) if evidence["close"] == 19 else "accept"
            choices = {"verdict": verdict}
        else:
            question, = payload["questions"]
            assert names == ["paper_action"] and question["type"] == "choice"
            assert {choice["value"] for choice in question["choices"]} == {"buy", "sell", "hold", "wait", "close"}
            if evidence["close"] == 95:
                assert evidence["account"]["shares"] == 3
                action = next(legacy_answers)
            else:
                assert evidence["close"] in (19, 1900) and evidence["sma"]["crossover"] == "up"
                action = "buy"
            if action == "timeout":
                raise TimeoutError
            choices = {"unexpected" if action == "wrong_question" else "paper_action": action}
        role = "reviewer" if is_review else "analyst"
        return BytesIO(json.dumps({
            "id": f"fixture-{role}-{digest or 'text'}",
            "answers": [{"type": "choice", "name": name, "choice": value} for name, value in choices.items()],
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


def check(database, url, selection, live, review_selections):
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
            "valid", "timeout", "malformed_answer", "invalid_proposal", "invalid_proposal",
        ]
        assert results[0]["proposal"] == "hold" and results[0]["quantity"] is None
        assert results[-1]["proposal"] == "sell" and "confirmed down" in results[-1]["explanation"]
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
        print(f"Provider {result['provider']}, model {result['model']}, local decision {result['id']}.")
        for name in ("analyst", "reviewer"):
            role = result[name]
            print(f"{name}: {role['call']}, {role['answers']}, provider ID {role['provider_decision_id']}")
        return 0 if all(result[role]["call"] == "returned" for role in ("analyst", "reviewer")) else 2
    affordable, expensive = [call(url, "/api/decision-snapshots", selection) for selection in review_selections]
    decisions = [call(url, "/api/decisions", {"snapshot_id": item["id"]})
                 for item in (affordable, affordable, expensive)]
    assert [row["status"] for row in decisions] == ["valid", "review_rejected", "invalid_proposal"]
    assert [row["eligible_for_paper_order"] for row in decisions] == [True, False, False]
    assert "1900" in decisions[-1]["explanation"] and "Cash" in decisions[-1]["explanation"]
    for decision in decisions:
        assert call(url, f"/api/decisions/{decision['id']}") == decision
        assert decision["analyst"]["answers"]["paper_action"] == "buy"
        assert decision["reviewer"]["call"] == "returned"
    bad_input = (
        ("data:image/png;base64,YmFk", "invalid_capture"),
        (fixture_png(str(uuid4())), "capture_mismatch"),
    )
    for image, status in bad_input:
        invalid = call(url, "/api/decisions", {"snapshot_id": snapshot["id"], "image_url": image})
        assert invalid["status"] == status and invalid["model_call"] == "not_sent"
    for window, expected in [(2, "conflicting_signal"), (3, "insufficient_visual_evidence"),
                             (4, "malformed_answer"), (5, "review_timeout")]:
        long = max(3, window + 1)
        visual = call(url, "/api/decision-snapshots", {**review_selections[0], "short": window, "long": long,
                                                       "timestamp": review_selections[0]["timestamp"] if window < 5
                                                       else "2026-11-09T15:05:00Z",
                                                       "replay": None})
        if window == 5:
            # A later recorded observation and historical context permit a six-candle window.
            assert not visual["missing"]
        result = call(url, "/api/decisions", {"snapshot_id": visual["id"], "image_url": fixture_png(visual["id"])})
        assert result["status"] == expected, result
        assert not result["eligible_for_paper_order"]
    assert ledger(database) == before
    print("Review readback verified: agreed eligible buy, reviewer rejection, and unaffordable accepted buy.")
    print("Capture failures verified: invalid PNG, mismatch, conflicting/unreadable signal, missing answer, reviewer timeout.")
    return 0


def run(live):
    with TemporaryDirectory(prefix="invest-decisions-", dir=ROOT / "backend/data") as directory:
        database = Path(directory) / "observations.sqlite3"
        selection = setup_account(database)
        review_selections = [] if live else [
            setup_review_market(database, "15", 1), setup_review_market(database, "16", 100),
        ]
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
                return check(database, url, selection, live, review_selections)
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
    parser.add_argument("--chart", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.serve:
        serve(options.serve, options.live, options.chart)
    else:
        raise SystemExit(run(options.live))
