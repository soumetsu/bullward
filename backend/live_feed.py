"""Keep one upstream connection for the dashboard's AAPL feed."""

import asyncio
from contextlib import AsyncExitStack
import logging

from .observations import record_observation
from .yahoo_stream import receive_quote

SYMBOL = "AAPL"
logger = logging.getLogger(__name__)


def yahoo_client():
    from yfinance import AsyncWebSocket

    return AsyncWebSocket(verbose=False)


class LiveFeed:
    def __init__(self, db_path):
        self.db_path = db_path
        self.status = "disabled"

    async def run(self, client_factory=yahoo_client):
        while True:
            self.status = "connecting"
            try:
                async with AsyncExitStack() as stack:
                    async with asyncio.timeout(15):
                        client = await stack.enter_async_context(client_factory())
                        await client.subscribe(SYMBOL)
                    self.status = "waiting"
                    while True:
                        async with asyncio.timeout(45):
                            message, received_at, _ = await receive_quote(client, SYMBOL)
                        await asyncio.to_thread(record_observation, self.db_path, message, received_at)
                        self.status = "live"
            except Exception as error:
                self.status = "disconnected"
                logger.warning("Yahoo Finance feed disconnected (%s).", type(error).__name__)
                await asyncio.sleep(10)
