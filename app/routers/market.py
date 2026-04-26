import asyncio
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.market_controller import MarketController
from app.schemas.market import (
    BarsResponse,
    HistoryResponse,
    IndicatorResponse,
    InstrumentSearchResponse,
    NewsResponse,
    PriceResponse,
    RealtimeTick,
    RealtimeTickIn,
)
from app.services.market_data_service import MarketDataService
from app.utils.config import DataServiceSettings


router = APIRouter(tags=["market"])


@lru_cache(maxsize=1)
def get_settings() -> DataServiceSettings:
    return DataServiceSettings()


async def get_session(request: Request):
    async with request.app.state.session_factory() as session:
        yield session


def get_market_controller(request: Request, session: AsyncSession = Depends(get_session)) -> MarketController:
    service = MarketDataService(
        settings=request.app.state.settings,
        cache_service=request.app.state.cache_service,
        session=session,
    )
    return MarketController(service=service)


@router.get("/price/{ticker}", response_model=PriceResponse)
async def get_price(
    ticker: str,
    controller: MarketController = Depends(get_market_controller),
) -> PriceResponse:
    return await controller.get_price(ticker)


@router.get("/indicators/{ticker}", response_model=IndicatorResponse)
async def get_indicators(
    ticker: str,
    controller: MarketController = Depends(get_market_controller),
) -> IndicatorResponse:
    return await controller.get_indicators(ticker)


@router.get("/news/{ticker}", response_model=NewsResponse)
async def get_news(
    ticker: str,
    controller: MarketController = Depends(get_market_controller),
) -> NewsResponse:
    return await controller.get_news(ticker)


@router.get("/history/{ticker}", response_model=HistoryResponse)
async def get_history(
    ticker: str,
    limit: int = Query(default=30, ge=5, le=120),
    controller: MarketController = Depends(get_market_controller),
) -> HistoryResponse:
    return await controller.get_history(ticker=ticker, limit=limit)


@router.get("/bars/{ticker}", response_model=BarsResponse)
async def get_bars(
    ticker: str,
    limit: int = Query(default=240, ge=48, le=720),
    interval: str = Query(default="1h"),
    controller: MarketController = Depends(get_market_controller),
) -> BarsResponse:
    return await controller.get_bars(ticker=ticker, limit=limit, interval=interval)


@router.post("/stream/ingest", response_model=RealtimeTick)
async def ingest_realtime_tick(
    payload: RealtimeTickIn,
    request: Request,
    controller: MarketController = Depends(get_market_controller),
) -> RealtimeTick:
    tick = await controller.ingest_tick(payload)
    await request.app.state.stream_broker.publish(tick)
    return tick


@router.get("/instruments/search", response_model=InstrumentSearchResponse)
async def search_instruments(
    q: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(default=15, ge=1, le=30),
    controller: MarketController = Depends(get_market_controller),
) -> InstrumentSearchResponse:
    return await controller.search_instruments(query=q, limit=limit)


@router.websocket("/stream/{ticker}")
async def stream_ticker(websocket: WebSocket, ticker: str) -> None:
    symbol = ticker.strip().upper()
    await websocket.accept()
    broker = websocket.app.state.stream_broker
    queue = await broker.subscribe(symbol)
    yahoo_stream = websocket.app.state.yahoo_stream_service
    settings = websocket.app.state.settings
    await yahoo_stream.ensure_subscription(symbol)

    await websocket.send_json(
        {
            "type": "subscribed",
            "ticker": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )

    async with websocket.app.state.session_factory() as session:
        service = MarketDataService(
            settings=websocket.app.state.settings,
            cache_service=websocket.app.state.cache_service,
            session=session,
        )
        try:
            latest = await service.get_price(symbol)
            await websocket.send_json(
                {
                    "type": "tick",
                    "data": {
                        "ticker": latest.ticker,
                        "price": latest.price,
                        "timestamp": latest.timestamp.isoformat(),
                        "source": latest.source,
                    },
                }
            )
        except Exception:
            pass

    try:
        while True:
            try:
                tick = await asyncio.wait_for(queue.get(), timeout=settings.stream_heartbeat_seconds)
                await websocket.send_json({"type": "tick", "data": tick.model_dump(mode="json")})
            except asyncio.TimeoutError:
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "ticker": symbol,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
    except WebSocketDisconnect:
        pass
    finally:
        await yahoo_stream.release_subscription(symbol)
        await broker.unsubscribe(symbol, queue)
