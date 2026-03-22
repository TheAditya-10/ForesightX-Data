from functools import lru_cache

from fastapi import APIRouter, Depends, Query, Request

from app.controllers.market_controller import MarketController
from app.schemas.market import HistoryResponse, IndicatorResponse, NewsResponse, PriceResponse
from app.services.market_data_service import MarketDataService
from app.utils.config import DataServiceSettings


router = APIRouter(tags=["market"])


@lru_cache(maxsize=1)
def get_settings() -> DataServiceSettings:
    return DataServiceSettings()


def get_market_controller(request: Request) -> MarketController:
    service = MarketDataService(
        settings=request.app.state.settings,
        cache_service=request.app.state.cache_service,
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
