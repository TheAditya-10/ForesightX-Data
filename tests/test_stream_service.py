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


def test_market_stream_broker_drops_oldest_when_queue_full() -> None:
    async def _run() -> None:
        broker = MarketStreamBroker(max_queue_size=2)
        queue = await broker.subscribe("AAPL")
        await broker.publish(RealtimeTick(ticker="AAPL", price=1.0, timestamp="2026-04-19T10:00:00Z", source="t"))
        await broker.publish(RealtimeTick(ticker="AAPL", price=2.0, timestamp="2026-04-19T10:00:01Z", source="t"))
        await broker.publish(RealtimeTick(ticker="AAPL", price=3.0, timestamp="2026-04-19T10:00:02Z", source="t"))
        first = await queue.get()
        second = await queue.get()
        assert first.price == pytest.approx(2.0)
        assert second.price == pytest.approx(3.0)
        await broker.unsubscribe("AAPL", queue)

    asyncio.run(_run())

