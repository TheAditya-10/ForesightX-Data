import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone

import yfinance as yf
from sqlalchemy.ext.asyncio import async_sessionmaker

from shared import get_logger

from app.schemas.market import PriceResponse, RealtimeTick, RealtimeTickIn
from app.services.cache_service import CacheService
from app.services.market_data_service import MarketDataService
from app.utils.config import DataServiceSettings


class MarketStreamBroker:
    def __init__(self, max_queue_size: int = 64) -> None:
        self.max_queue_size = max_queue_size
        self._subscribers: dict[str, set[asyncio.Queue[RealtimeTick]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, ticker: str) -> asyncio.Queue[RealtimeTick]:
        queue: asyncio.Queue[RealtimeTick] = asyncio.Queue(maxsize=self.max_queue_size)
        async with self._lock:
            self._subscribers[ticker.upper()].add(queue)
        return queue

    async def unsubscribe(self, ticker: str, queue: asyncio.Queue[RealtimeTick]) -> None:
        key = ticker.upper()
        async with self._lock:
            subscribers = self._subscribers.get(key)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(key, None)

    async def publish(self, tick: RealtimeTick) -> int:
        async with self._lock:
            subscribers = list(self._subscribers.get(tick.ticker.upper(), set()))
        delivered = 0
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(tick)
                delivered += 1
            except asyncio.QueueFull:
                continue
        return delivered


class YahooMarketStreamService:
    def __init__(
        self,
        *,
        settings: DataServiceSettings,
        broker: MarketStreamBroker,
        cache_service: CacheService,
        session_factory: async_sessionmaker,
    ) -> None:
        self.settings = settings
        self.broker = broker
        self.cache_service = cache_service
        self.session_factory = session_factory
        self.logger = get_logger(settings.service_name, "yahoo-stream")
        self._counts: Counter[str] = Counter()
        self._last_persisted: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        self._ws: yf.AsyncWebSocket | None = None
        self._listen_task: asyncio.Task | None = None

    async def ensure_subscription(self, ticker: str) -> None:
        if not self.settings.yahoo_stream_enabled:
            return
        symbol = ticker.upper()
        async with self._lock:
            self._counts[symbol] += 1
            if self._ws is None:
                self._ws = yf.AsyncWebSocket(verbose=False)
                self._listen_task = asyncio.create_task(self._listen_forever())
            await self._ws.subscribe([symbol])

    async def release_subscription(self, ticker: str) -> None:
        if not self.settings.yahoo_stream_enabled:
            return
        symbol = ticker.upper()
        async with self._lock:
            if self._counts[symbol] > 0:
                self._counts[symbol] -= 1
            if self._counts[symbol] <= 0:
                self._counts.pop(symbol, None)
                if self._ws is not None:
                    await self._ws.unsubscribe([symbol])
            if not self._counts and self._ws is not None:
                await self._ws.close()
                self._ws = None
                if self._listen_task is not None:
                    self._listen_task.cancel()
                    self._listen_task = None

    async def close(self) -> None:
        async with self._lock:
            self._counts.clear()
            if self._ws is not None:
                await self._ws.close()
                self._ws = None
            if self._listen_task is not None:
                self._listen_task.cancel()
                self._listen_task = None

    async def _listen_forever(self) -> None:
        while True:
            ws = self._ws
            if ws is None:
                return
            try:
                await ws.listen(self._handle_message)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.logger.warning(f"Yahoo stream listener restarting after error: {exc}")
                await asyncio.sleep(2)

    async def _handle_message(self, message: dict) -> None:
        ticker = str(message.get("id", "")).upper()
        price = message.get("price")
        if not ticker or price is None:
            return

        raw_time = message.get("time")
        try:
            if raw_time is None:
                observed_at = datetime.now(timezone.utc)
            else:
                observed_at = datetime.fromtimestamp(int(raw_time) / 1000, tz=timezone.utc)
            volume = message.get("day_volume") or message.get("last_size")
            tick = RealtimeTick(
                ticker=ticker,
                price=float(price),
                timestamp=observed_at,
                volume=int(float(volume)) if volume is not None else None,
                source="yahoo_websocket",
            )
        except Exception:
            return

        await self.cache_service.set_json(
            f"price:{ticker}",
            PriceResponse(
                ticker=ticker,
                price=tick.price,
                timestamp=tick.timestamp,
                source=tick.source,
            ).model_dump(mode="json"),
            self.settings.cache_ttl_seconds,
        )
        await self.broker.publish(tick)

        last = self._last_persisted.get(ticker)
        if last is not None and (tick.timestamp - last).total_seconds() < 60:
            return
        self._last_persisted[ticker] = tick.timestamp

        async with self.session_factory() as session:
            service = MarketDataService(
                settings=self.settings,
                cache_service=self.cache_service,
                session=session,
            )
            await service.ingest_tick(
                RealtimeTickIn(
                    ticker=ticker,
                    price=tick.price,
                    timestamp=tick.timestamp,
                    volume=tick.volume,
                    source=tick.source,
                )
            )
