"""Offline market recording and Decisions transport for the workstation SmokeTest."""

from collections import Counter
from contextlib import closing
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
from struct import unpack
import sys
from unittest.mock import patch
from urllib.error import HTTPError

import uvicorn

from backend.chart_capture import decode_capture
from backend.main import app
from backend.observations import initialize_database
from scripts.check_prompt9 import at, save

SYMBOL = "SMOKE"
EVENTS = [
    ("14:30:05", 80), ("14:31:05", 70), ("14:32:05", 60), ("14:33:05", 75),
    ("14:34:05", 100), ("14:35:05", 100), ("14:35:15", 100), ("14:36:05", 80),
    ("14:37:05", 70), ("14:37:15", 110), ("14:37:25", 70), ("14:38:05", 120),
    ("14:39:05", 100), ("14:40:05", 75), ("14:41:05", 105), ("14:41:15", 105),
    ("14:42:05", 130), ("14:43:05", 110),
]
BUY_FILL = 6
ACTIONS = {"14:34": "buy", "14:36": "sell", "14:38": "buy", "14:40": "close"}
CROSSES = {"14:34": "up", "14:36": "down", "14:38": "up", "14:40": "down"}
VETO = "reject_unsupported_signal"


def record(database, index):
    time, price = EVENTS[index]
    return save(database, SYMBOL, time, price, 1000 + index * 25)


def read_database(database):
    return sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)


def serve(port, database):
    assert os.environ["INVEST_LIVE_FEED"] == "0" and not database.exists()
    initialize_database(database)
    for index in range(len(EVENTS)):
        row_id = record(database, index)
        assert row_id == index + 1
    calls = []

    @app.get("/api/workstation-smoke/state")
    def smoke_state():
        with closing(read_database(database)) as connection:
            present = {name for name, in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            counts = {
                key: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if table in present else 0
                for key, table in [("orders", "paper_orders"), ("fills", "paper_fills"), ("observations", "observations")]
            }
        return {**counts, "calls": calls}

    @app.post("/api/workstation-smoke/duplicate")
    def duplicate_buy_fill():
        return {"row_id": record(database, BUY_FILL)}

    def transport(request, timeout):
        assert request.full_url == "https://api.openai.com/v1/decisions" and request.get_method() == "POST"
        assert 0 < timeout <= 20
        payload = json.loads(request.data)
        evidence, image = payload["input"][0]["content"]
        assert evidence["type"] == "input_json" and image["type"] == "input_image"
        facts = evidence["data"]
        assert (facts["symbol"], facts["interval"], facts["market_data"]) == (SYMBOL, "1m", "constructed")
        candle = facts["candle_timestamp"][11:16]
        assert candle in {"14:33", "14:34", "14:35", "14:36", "14:37", "14:38", "14:39", "14:40"}
        cutoff = at(f"{candle}:00")
        assert cutoff.date().isoformat() == facts["candle_timestamp"][:10]
        assert (at(facts["decision_time"][11:19]) - cutoff).total_seconds() == 60
        png, tag = decode_capture(image["image_url"])
        assert tag == facts["snapshot_id"] and unpack(">II", png[16:24]) == (960, 440)
        digest = sha256(png).hexdigest()
        review = facts.get("review")
        role = "reviewer" if review else "analyst"
        crossover = CROSSES.get(candle, "none")
        assert (facts["sma"]["crossover"] or "none") == crossover
        action = ACTIONS.get(candle, "hold")
        choices = {"action": action, "crossover": crossover, "evidence": "sufficient"}
        if review:
            assert review["proposal"] == action and review["analyst_answers"] == choices
            assert review["backend_rule_result"] == "passed"
            choices = {"verdict": VETO if candle == "14:38" else "accept"}
        assert [question["name"] for question in payload["questions"]] == list(choices)
        calls.append({"candle": candle, "role": role, "snapshot_id": tag, "sha256": digest})
        if candle == "14:33":
            assert role == "analyst"
            raise HTTPError(request.full_url, 503, "Controlled provider outage", {}, None)
        return BytesIO(json.dumps({
            "id": f"smoke-{role}-{digest}",
            "answers": [{"name": name, "type": "choice", "choice": value} for name, value in choices.items()],
        }).encode())

    exercise_calls = []
    def exercise_transport(request, timeout):
        payload = json.loads(request.data)
        text, image = payload['input'][0]['content']
        context = json.loads(text['text'])
        assert set(context) == {'clock', 'signal_minute', 'candle_times'}
        assert context['signal_minute'] == '2026-11-09T14:35:00Z'
        decode_capture(image['image_url'])
        exercise_calls.append(True)
        if len(exercise_calls) == 2:
            raise HTTPError(request.full_url, 503, 'Controlled outage', {}, None)
        return BytesIO(json.dumps({'answers': [{'name': 'direction', 'type': 'choice', 'choice': 'wait'}]}).encode())

    with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-only"}), patch("backend.decisions.urlopen", transport), patch("backend.chart_exercise.urlopen", exercise_transport):
        uvicorn.run(app, host="127.0.0.1", port=port)


def check(database, calls):
    with closing(read_database(database)) as connection:
        assert connection.execute("SELECT name, type, window FROM chart_indicators").fetchall() == [
            ("Smoke SMA", "sma", 3),
        ]
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone() == (len(EVENTS),)
        book, enabled, test_mode = connection.execute("SELECT book, enabled, test_mode FROM paper_automation_runs").fetchone()
        assert (enabled, test_mode) == (0, 1)
        account, cash, shares, realized = connection.execute(
            "SELECT id, cash, shares, realized FROM paper_accounts"
        ).fetchone()
        assert (account, Decimal(cash), shares, Decimal(realized)) == (book, 1025, 0, 25)
        orders = connection.execute("""
            SELECT o.action, o.quantity, f.price, f.observation_id
            FROM paper_orders o LEFT JOIN paper_fills f ON f.order_id = o.id ORDER BY o.id
        """).fetchall()
        assert [(action, quantity, Decimal(price), mark) for action, quantity, price, mark in orders] == [
            ("buy", 4, 100, 7), ("sell", 1, 110, 10), ("close", 3, 105, 16),
        ], orders
        pnl = connection.execute("""
            SELECT cash, shares, realized, unrealized FROM paper_pnl WHERE event = 'Fill' ORDER BY id
        """).fetchall()
        assert [tuple(map(Decimal, row)) for row in pnl] == [
            (600, 4, 0, 0), (710, 3, 10, 30), (1025, 0, 25, 0),
        ], pnl
        jobs = connection.execute("""
            SELECT substr(candle, 12, 5), status, order_id FROM paper_automation_jobs ORDER BY candle
        """).fetchall()
        assert jobs == [
            ("14:34", "filled", 1), ("14:35", "no_order", None), ("14:36", "filled", 2),
            ("14:37", "no_order", None), ("14:38", "skipped", None), ("14:39", "no_order", None),
            ("14:40", "filled", 3),
        ], jobs
        captures = connection.execute("""
            SELECT c.snapshot_id, c.png, d.decision_json
            FROM decision_captures c JOIN decisions d ON d.id = c.decision_id
        """).fetchall()
    roles = Counter((call["snapshot_id"], call["role"], call["sha256"]) for call in calls)
    assert len(captures) == 7
    with closing(read_database(database)) as connection:
        exercises = [json.loads(row[0]) for row in connection.execute('SELECT result FROM chart_exercises ORDER BY rowid')]
        assert [(e['status'], e['choice']) for e in exercises] == [('completed', 'wait'), ('provider_error', None)]
    for snapshot, png, raw in captures:
        decision = json.loads(raw)
        digest = sha256(png).hexdigest()
        assert decision["capture"]["sha256"] == digest
        assert roles.pop((snapshot, "analyst", digest)) == 1
        if decision["facts"]["candle_timestamp"][11:16] == "14:33":
            assert decision["status"] == "provider_error" and decision["reviewer"]["call"] == "not_sent"
        else:
            assert roles.pop((snapshot, "reviewer", digest)) == 1
    assert not roles, roles
    print("SQLite PASS: 3 fills, $1,025 cash, 0 shares, $25 realized, $0 unrealized; 7 automation captures and 2 image-only exercises.", flush=True)


if __name__ == "__main__":
    serve(int(sys.argv[1]), Path(os.environ["INVEST_OBSERVATIONS_DB"]))
