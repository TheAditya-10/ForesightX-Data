from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import FastAPI

from shared import ServiceHealth, configure_logging, get_logger

from app.db.session import check_database_connection, close_database, get_session_factory
from app.routers.market import router as market_router
from app.services.cache_service import CacheService
from app.utils.config import DataServiceSettings


@lru_cache(maxsize=1)
def get_settings() -> DataServiceSettings:
    return DataServiceSettings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.service_name, settings.log_level)
    logger = get_logger(settings.service_name, "startup")
    cache_service = CacheService(settings=settings)
    session_factory = get_session_factory(settings.database_url)
    await cache_service.connect()
    await check_database_connection(settings.database_url)
    logger.info("Data service startup complete")
    app.state.cache_service = cache_service
    app.state.session_factory = session_factory
    app.state.settings = settings
    try:
        yield
    finally:
        await cache_service.close()
        await close_database()
        logger.info("Data service shutdown complete")


app = FastAPI(
    title="ForesightX Data Service",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(market_router)


@app.get("/health", response_model=ServiceHealth)
async def healthcheck() -> ServiceHealth:
    settings = get_settings()
    return ServiceHealth(
        service=settings.service_name,
        status="ok",
        timestamp=datetime.now(timezone.utc),
    )
