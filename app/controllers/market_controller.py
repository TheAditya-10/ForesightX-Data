from fastapi import HTTPException, status

from app.schemas.market import HistoryResponse, IndicatorResponse, NewsResponse, PriceResponse
from app.services.market_data_service import MarketDataService, MarketDataServiceError


class MarketController:
    def __init__(self, service: MarketDataService) -> None:
        self.service = service

    async def get_price(self, ticker: str) -> PriceResponse:
        try:
            return await self.service.get_price(ticker)
        except MarketDataServiceError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    async def get_indicators(self, ticker: str) -> IndicatorResponse:
        try:
            return await self.service.get_indicators(ticker)
        except MarketDataServiceError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    async def get_news(self, ticker: str) -> NewsResponse:
        try:
            return await self.service.get_news(ticker)
        except MarketDataServiceError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    async def get_history(self, ticker: str, limit: int) -> HistoryResponse:
        try:
            return await self.service.get_history(ticker=ticker, limit=limit)
        except MarketDataServiceError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
