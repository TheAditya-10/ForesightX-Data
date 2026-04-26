import asyncio
from collections import defaultdict

from app.schemas.market import RealtimeTick


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
