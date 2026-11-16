"""Verify saved voice evidence and the GPT-Live wire contract without calling OpenAI."""

import argparse
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from uuid import uuid4

import uvicorn
from pydantic import BaseModel, Field

from backend import main
from backend.paper_automation import stop_automation
from backend.voice import VoiceConnectionError, decision_voice_summary, exchange_voice_offer
from scripts.check_automation import analyze, advance, begin, claim, fixture_decision, seed
from scripts.check_decisions import fixture_png, ledger

SDP = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
FIXTURE_ID = "live_fixture"


def seed_voice(database):
    seed(database)
    with patch.dict(os.environ, {"INVEST_PAPER_TEST_MODE": "1"}):
        book, run, frame, job = begin(database)
    snapshot = job["snapshot"]["id"]
    manual = fixture_decision(database, snapshot, image_url=fixture_png(snapshot))["id"]
    filled = analyze(database, run, job)["decision_id"]
    frame = advance(database, book, frame)
    frame = advance(database, book, frame)
    vetoed = analyze(database, run, claim(database, run, book, frame, "14:35:00"))["decision_id"]
    stop_automation(database, run)
    return {"filled": filled, "vetoed": vetoed, "manual": manual}


def validate_payload(request, timeout):
    assert request.full_url == "https://api.openai.com/v1/live/sessions" and 0 < timeout <= 20
    assert request.get_method() == "POST"
    payload = json.loads(request.data)
    assert set(payload["session"]) == {"model", "instructions"}
    assert payload["session"]["model"] == "gpt-live-1"
    instructions, record = payload["session"]["instructions"].split("\n\nSaved record:\n", 1)
    assert "evidence is missing" in instructions and "cannot place or change orders" in instructions
    assert payload["transport"]["type"] == "webrtc" and "\nm=audio " in payload["transport"]["sdp"]
    return json.loads(record), payload["transport"]["sdp"]


def provider_answer(sdp):
    return BytesIO(json.dumps({"session": {"id": FIXTURE_ID}, "transport": {"type": "webrtc", "sdp": sdp}}).encode())


class BrowserAnswer(BaseModel):
    offer: str = Field(max_length=65536)
    answer: str = Field(max_length=65536)


def serve(port):
    answers = {}
    seen = []

    @main.app.post("/api/voice-fixture/answer")
    def register(bridge: BrowserAnswer):
        answers[bridge.offer] = bridge.answer
        return {}

    @main.app.get("/api/voice-fixture/report")
    def report():
        return seen

    def transport(request, timeout):
        record, offer = validate_payload(request, timeout)
        answer = answers.pop(offer, None)
        seen.append({"summary": record, "answered": bool(answer)})
        if not answer:
            raise HTTPError(request.full_url, 503, "Fixture rejected the connection", {}, None)
        return provider_answer(answer)

    with (
        patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-only"}),
        patch("backend.voice.urlopen", transport),
    ):
        uvicorn.run(main.app, host="127.0.0.1", port=port)


def check(database):
    ids = seed_voice(database)
    before = ledger(database)
    filled = decision_voice_summary(database, ids["filled"])
    vetoed = decision_voice_summary(database, ids["vetoed"])
    manual = decision_voice_summary(database, ids["manual"])
    assert filled["proposal"] == "buy" and filled["reviewer"]["answers"]["verdict"] == "accept"
    assert filled["paper_outcome"]["status"] == "filled"
    assert filled["paper_outcome"]["fill_price"] == "7" and filled["paper_outcome"]["cash_after_fill"] == "993"
    assert filled["paper_outcome"]["shares_after_fill"] == 1
    assert vetoed["paper_outcome"]["status"] == "skipped" and vetoed["paper_outcome"]["fill_price"] is None
    assert manual["snapshot_id"] == filled["snapshot_id"] and manual["paper_outcome"]["status"] == "not_recorded"
    assert decision_voice_summary(database, str(uuid4())) is None
    assert ledger(database) == before
    for summary in (filled, vetoed):
        def transport(request, timeout):
            record, offer = validate_payload(request, timeout)
            assert record == summary and offer == SDP
            return provider_answer(SDP)

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-only"}),
            patch("backend.voice.urlopen", transport),
        ):
            assert exchange_voice_offer(summary, SDP) == {"session_id": FIXTURE_ID, "sdp": SDP}
    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch("backend.voice.urlopen") as connect:
        for offer, status in [("not an offer", 422), (SDP, 503)]:
            try:
                exchange_voice_offer(filled, offer)
            except VoiceConnectionError as error:
                assert error.status_code == status
            else:
                raise AssertionError("Invalid voice request should have failed.")
        connect.assert_not_called()
    assert ledger(database) == before
    print("PASS: one read-only decision, filled and vetoed outcomes, GPT-Live SDP contract, and missing-key failure.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--serve", type=int)
    args = parser.parse_args()
    if args.seed:
        if args.seed.exists():
            parser.error("Choose a fresh voice fixture database.")
        ids = seed_voice(args.seed)
        args.seed.with_suffix(".json").write_text(json.dumps(ids), encoding="utf-8")
    elif args.serve:
        serve(args.serve)
    else:
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory(prefix="invest-voice-", dir=root / "backend/data") as directory:
            check(Path(directory) / "observations.sqlite3")
