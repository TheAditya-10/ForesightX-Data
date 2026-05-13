"""Shared utilities vendored into the data service.

Provides minimal implementations of helpers used across services so the
service can run independently of an external shared package.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class HTTPRequestError(Exception):
    pass


class BaseServiceSettings(BaseSettings):
    service_name: str = "foresightx-service"
    environment: str = "development"
    log_level: str = "INFO"


class ServiceHealth(BaseModel):
    service: str
    status: str
    timestamp: datetime


def configure_logging(service_name: str, level: str = "INFO") -> None:
    levelno = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        handler.setFormatter(fmt)
        root.addHandler(handler)
    root.setLevel(levelno)


def get_logger(service_name: str, component: Optional[str] = None) -> logging.Logger:
    name = f"{service_name}.{component}" if component else service_name
    return logging.getLogger(name)


def normalize_postgres_async_url(url: str) -> str:
    if not isinstance(url, str):
        return url
    normalized = url
    if normalized.startswith("postgres://"):
        normalized = "postgresql+asyncpg://" + normalized[len("postgres://") :]
    elif normalized.startswith("postgresql://") and "asyncpg" not in normalized:
        normalized = "postgresql+asyncpg://" + normalized[len("postgresql://") :]

    parsed = urlsplit(normalized)
    if parsed.scheme != "postgresql+asyncpg":
        return normalized

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    sslmode = query.pop("sslmode", None)
    if sslmode and "ssl" not in query:
        query["ssl"] = sslmode
    query.pop("channel_binding", None)
    query.pop("gssencmode", None)
    query.pop("target_session_attrs", None)

    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def build_async_client(*, timeout: float = 8.0, **kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout), **kwargs)


async def request_json(
    *,
    client: httpx.AsyncClient,
    method: str,
    url: str,
    params: Dict[str, Any] | None = None,
    json: Any | None = None,
    retries: int = 2,
    logger: Optional[logging.Logger] = None,
    timeout: float | None = None,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            resp = await client.request(method, url, params=params, json=json, timeout=timeout)
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:
                return resp.text
        except Exception as exc:  # pragma: no cover - defensive
            last_exc = exc
            if logger:
                logger.debug("request_json failed", exc_info=exc)
            if attempt == retries:
                raise HTTPRequestError(str(exc)) from exc
            await asyncio.sleep(0.5 * (2 ** attempt))
    raise HTTPRequestError(str(last_exc) if last_exc else "request failed")


def request_json_sync(
    *,
    client: httpx.Client,
    method: str,
    url: str,
    params: Dict[str, Any] | None = None,
    json: Any | None = None,
    retries: int = 2,
    logger: Optional[logging.Logger] = None,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            resp = client.request(method, url, params=params, json=json)
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:
                return resp.text
        except Exception as exc:  # pragma: no cover - defensive
            last_exc = exc
            if logger:
                logger.debug("request_json_sync failed", exc_info=exc)
            if attempt == retries:
                raise HTTPRequestError(str(exc)) from exc
    raise HTTPRequestError(str(last_exc) if last_exc else "request failed")


__all__ = [
    "configure_logging",
    "get_logger",
    "normalize_postgres_async_url",
    "BaseServiceSettings",
    "ServiceHealth",
    "build_async_client",
    "request_json",
    "request_json_sync",
    "HTTPRequestError",
]
