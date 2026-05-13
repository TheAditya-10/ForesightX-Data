from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.services.stream_service import MarketStreamBroker, YahooMarketStreamService
from app.utils.config import DataServiceSettings


class _CacheStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, int]] = []

    async def set_json(self, key: str, value: dict, ttl: int) -> None:
        self.calls.append((key, value, ttl))


class _SessionFactoryStub:
    def __call__(self):
        class _SessionCtx:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
                return False

        return _SessionCtx()


def test_handle_message_ignores_missing_fields() -> None:
    async def _run() -> None:
        settings = DataServiceSettings(yahoo_stream_enabled=False)
        broker = MarketStreamBroker()
        cache = _CacheStub()
        service = YahooMarketStreamService(
            settings=settings,
            broker=broker,
            cache_service=cache,  # type: ignore[arg-type]
            session_factory=_SessionFactoryStub(),  # type: ignore[arg-type]
        )

        await service._handle_message({"id": "AAPL"})
        await service._handle_message({"price": 1.0})
        assert cache.calls == []

    asyncio.run(_run())


def test_handle_message_writes_cache_and_publishes_tick(monkeypatch) -> None:
    async def _run() -> None:
        settings = DataServiceSettings(cache_ttl_seconds=5)
        broker = MarketStreamBroker()
        cache = _CacheStub()
        service = YahooMarketStreamService(
            settings=settings,
            broker=broker,
            cache_service=cache,  # type: ignore[arg-type]
            session_factory=_SessionFactoryStub(),  # type: ignore[arg-type]
        )

        published = await broker.subscribe("AAPL")

        class _FakeMarketDataService:
            def __init__(self, **kwargs):  # noqa: ANN003
                pass

            async def ingest_tick(self, payload):  # noqa: ANN001
                return None

        import app.services.stream_service as stream_module

        monkeypatch.setattr(stream_module, "MarketDataService", _FakeMarketDataService)

        await service._handle_message({"id": "AAPL", "price": 10.5, "time": int(datetime.now(timezone.utc).timestamp() * 1000)})
        assert cache.calls and cache.calls[0][0] == "price:AAPL"
        tick = await published.get()
        assert tick.ticker == "AAPL"

    asyncio.run(_run())
