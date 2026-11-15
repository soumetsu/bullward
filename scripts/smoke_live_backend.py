"""Run the dashboard backend with a controllable upstream for the browser smoke check."""

import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
from unittest.mock import patch

import uvicorn

from backend.live_feed import LiveFeed, yahoo_client
from backend.main import app


def frame(time, price, volume):
    stamp = datetime.fromisoformat(f"2026-11-09T{time}Z")
    return {"id": "AAPL", "time": int(stamp.timestamp() * 1000), "price": price, "day_volume": volume}


class FixtureClient:
    def __init__(self, directory):
        self.directory = directory
        self._ws = self
        self.frames = iter([
            (None, frame("14:30:05", 100, 1_000)),
            (None, frame("14:31:05", 102, 1_010)),
            ("update", frame("14:31:35", 104, 1_015)),
            ("next", frame("14:32:05", 101, 1_025)),
        ])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def subscribe(self, _):
        pass

    def _decode_message(self, payload):
        return payload

    async def recv(self):
        marker, message = next(self.frames, ("finish", None))
        while marker and not (self.directory / marker).exists():
            await asyncio.sleep(0.2)
        return json.dumps({"message": message})


async def run_until_disconnected(feed, directory, fixture, run_feed):
    factory = (lambda: FixtureClient(directory)) if fixture else yahoo_client
    task = asyncio.create_task(run_feed(feed, factory))
    try:
        while not (directory / "disconnect").exists():
            await asyncio.sleep(0.2)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        feed.status = "disconnected"


if __name__ == "__main__":
    port, directory, mode = sys.argv[1:]
    fixture = mode == "fixture"
    if fixture:
        app.state.source_label = "Constructed smoke test data"
    original_run = LiveFeed.run

    async def run(feed):
        await run_until_disconnected(feed, Path(directory), fixture, original_run)

    with patch.object(LiveFeed, "run", run):
        uvicorn.run(app, host="127.0.0.1", port=int(port))
