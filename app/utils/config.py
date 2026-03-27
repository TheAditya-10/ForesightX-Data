from pathlib import Path

from pydantic import field_validator
from pydantic_settings import SettingsConfigDict

from shared import BaseServiceSettings, normalize_postgres_async_url


class DataServiceSettings(BaseServiceSettings):
    service_name: str = "foresightx-data"
    port: int = 8001
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/foresightx_data"
    redis_url: str = "redis://redis:6379/0"
    cache_ttl_seconds: int = 60
    news_cache_ttl_seconds: int = 180
    history_cache_ttl_seconds: int = 300
    finnhub_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        return normalize_postgres_async_url(value)
