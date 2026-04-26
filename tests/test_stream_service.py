import asyncio

import pytest

from app.schemas.market import RealtimeTick
from app.services.stream_service import MarketStreamBroker


def test_market_stream_broker_publish_and_subscribe() -> None:
    async def _run() -> None:
        broker = MarketStreamBroker(max_queue_size=2)
        queue = await broker.subscribe("AAPL")
        tick = RealtimeTick(
            ticker="AAPL",
            price=212.45,
            timestamp="2026-04-19T10:00:00Z",
            source="test_feed",
        )
        delivered = await broker.publish(tick)
        assert delivered == 1
        received = await queue.get()
        assert received.ticker == "AAPL"
        assert received.price == pytest.approx(212.45)
        await broker.unsubscribe("AAPL", queue)

    asyncio.run(_run())
