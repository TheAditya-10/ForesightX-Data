import json

from redis.asyncio import Redis
from redis.exceptions import RedisError

from shared import get_logger

from app.utils.config import DataServiceSettings


class CacheService:
    def __init__(self, settings: DataServiceSettings) -> None:
        self.settings = settings
        self.logger = get_logger(settings.service_name, "cache")
        self.redis: Redis | None = None

    async def connect(self) -> None:
        try:
            self.redis = Redis.from_url(self.settings.redis_url, decode_responses=True)
            await self.redis.ping()
            self.logger.info("Redis cache connected")
        except RedisError:
            self.redis = None
            self.logger.warning("Redis unavailable, continuing without cache")

    async def close(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()

    async def get_json(self, key: str) -> dict | None:
        if self.redis is None:
            return None
        try:
            payload = await self.redis.get(key)
            return json.loads(payload) if payload else None
        except (RedisError, json.JSONDecodeError):
            self.logger.warning("Failed to read cache key", extra={"key": key})
            return None

    async def set_json(self, key: str, value: dict, ttl_seconds: int) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.set(key, json.dumps(value, default=str), ex=ttl_seconds)
        except RedisError:
            self.logger.warning("Failed to write cache key", extra={"key": key})
