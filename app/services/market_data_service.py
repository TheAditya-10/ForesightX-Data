import asyncio
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from pydantic import BaseModel, Field, ValidationError

from shared import get_logger

from app.schemas.market import HistoryPoint, HistoryResponse, IndicatorResponse, NewsItem, NewsResponse, PriceResponse
from app.services.cache_service import CacheService
from app.utils.config import DataServiceSettings


class MarketDataServiceError(RuntimeError):
    """Raised when the market data service cannot assemble a response."""


class TickerInput(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=20)


class MarketDataService:
    def __init__(self, settings: DataServiceSettings, cache_service: CacheService) -> None:
        self.settings = settings
        self.cache_service = cache_service
        self.logger = get_logger(settings.service_name, "market-data")

    async def get_price(self, ticker: str) -> PriceResponse:
        validated = self._validate_ticker(ticker)
        cache_key = f"price:{validated}"
        cached = await self.cache_service.get_json(cache_key)
        if cached:
            return PriceResponse.model_validate(cached)

        history, source = await self._get_history_frame(validated, period="7d")
        latest = history.dropna(subset=["Close"]).tail(1)
        if latest.empty:
            raise MarketDataServiceError(f"No price data available for {validated}")

        row = latest.iloc[0]
        response = PriceResponse(
            ticker=validated,
            price=float(row["Close"]),
            timestamp=self._normalize_timestamp(row.name),
            source=source,
        )
        await self.cache_service.set_json(cache_key, response.model_dump(mode="json"), self.settings.cache_ttl_seconds)
        return response

    async def get_history(self, ticker: str, limit: int) -> HistoryResponse:
        validated = self._validate_ticker(ticker)
        cache_key = f"history:{validated}:{limit}"
        cached = await self.cache_service.get_json(cache_key)
        if cached:
            return HistoryResponse.model_validate(cached)

        history, source = await self._get_history_frame(validated, period="6mo")
        trimmed = history.dropna(subset=["Close"]).tail(limit)
        if trimmed.empty:
            raise MarketDataServiceError(f"No history available for {validated}")

        response = HistoryResponse(
            ticker=validated,
            points=[
                HistoryPoint(timestamp=self._normalize_timestamp(index), close=float(row["Close"]))
                for index, row in trimmed.iterrows()
            ],
            source=source,
        )
        await self.cache_service.set_json(cache_key, response.model_dump(mode="json"), self.settings.history_cache_ttl_seconds)
        return response

    async def get_indicators(self, ticker: str) -> IndicatorResponse:
        validated = self._validate_ticker(ticker)
        cache_key = f"indicators:{validated}"
        cached = await self.cache_service.get_json(cache_key)
        if cached:
            return IndicatorResponse.model_validate(cached)

        history, source = await self._get_history_frame(validated, period="6mo")
        close = history["Close"].astype(float).dropna()
        if len(close) < 35:
            raise MarketDataServiceError(f"Not enough price history to compute indicators for {validated}")

        # RSI uses smoothed average gains and losses to estimate short-term momentum imbalance.
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = (100 - (100 / (1 + rs))).fillna(50.0)

        # MACD tracks convergence/divergence between fast and slow EMAs to surface trend shifts.
        ema_fast = close.ewm(span=12, adjust=False).mean()
        ema_slow = close.ewm(span=26, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        histogram = macd_line - signal_line

        latest_rsi = float(rsi.iloc[-1])
        latest_macd = float(macd_line.iloc[-1])
        latest_signal = float(signal_line.iloc[-1])

        signal = "neutral"
        if latest_rsi <= 30:
            signal = "oversold"
        elif latest_rsi >= 70:
            signal = "overbought"
        elif latest_macd > latest_signal:
            signal = "bullish"
        elif latest_macd < latest_signal:
            signal = "bearish"

        response = IndicatorResponse(
            ticker=validated,
            rsi=round(latest_rsi, 2),
            macd=round(latest_macd, 4),
            signal=signal,
            macd_signal=round(latest_signal, 4),
            macd_histogram=round(float(histogram.iloc[-1]), 4),
            computed_at=datetime.now(timezone.utc),
            source=source,
        )
        await self.cache_service.set_json(cache_key, response.model_dump(mode="json"), self.settings.cache_ttl_seconds)
        return response

    async def get_news(self, ticker: str) -> NewsResponse:
        validated = self._validate_ticker(ticker)
        cache_key = f"news:{validated}"
        cached = await self.cache_service.get_json(cache_key)
        if cached:
            return NewsResponse.model_validate(cached)

        headlines = await self._fetch_news(validated)
        response = NewsResponse(ticker=validated, headlines=headlines[:5])
        await self.cache_service.set_json(cache_key, response.model_dump(mode="json"), self.settings.news_cache_ttl_seconds)
        return response

    async def _get_history_frame(self, ticker: str, period: str) -> tuple[pd.DataFrame, str]:
        yahoo_history = await self._fetch_yfinance_history(ticker=ticker, period=period)
        if not yahoo_history.empty:
            return yahoo_history, "yahoo_finance"

        fallback_history = await self._load_legacy_history(ticker)
        if not fallback_history.empty:
            return fallback_history, "legacy_csv"

        raise MarketDataServiceError(f"Unable to fetch market data for {ticker}")

    async def _fetch_yfinance_history(self, ticker: str, period: str) -> pd.DataFrame:
        def _load() -> pd.DataFrame:
            instrument = yf.Ticker(ticker)
            history = instrument.history(period=period, interval="1d", auto_adjust=False)
            if history.empty:
                return pd.DataFrame()
            if history.index.tz is None:
                history.index = history.index.tz_localize(timezone.utc)
            else:
                history.index = history.index.tz_convert(timezone.utc)
            return history

        try:
            return await asyncio.to_thread(_load)
        except Exception as exc:
            self.logger.warning(f"Yahoo Finance fetch failed for {ticker}: {exc}")
            return pd.DataFrame()

    async def _load_legacy_history(self, ticker: str) -> pd.DataFrame:
        path = Path(self.settings.legacy_data_dir) / "raw" / f"stock_data_raw_{ticker}.csv"
        if not path.exists():
            return pd.DataFrame()

        def _load() -> pd.DataFrame:
            frame = pd.read_csv(path)
            frame["Date"] = pd.to_datetime(frame["Date"], utc=True)
            frame = frame.set_index("Date").sort_index()
            return frame

        try:
            return await asyncio.to_thread(_load)
        except Exception as exc:
            self.logger.warning(f"Legacy history load failed for {ticker}: {exc}")
            return pd.DataFrame()

    async def _fetch_news(self, ticker: str) -> list[NewsItem]:
        def _load_news() -> list[NewsItem]:
            instrument = yf.Ticker(ticker)
            raw_news = instrument.news or []
            parsed_items: list[NewsItem] = []
            for item in raw_news[:5]:
                title = item.get("title")
                published = item.get("providerPublishTime")
                if not title or not published:
                    continue
                parsed_items.append(
                    NewsItem(
                        headline=title,
                        timestamp=datetime.fromtimestamp(int(published), tz=timezone.utc),
                        source=item.get("publisher", "yahoo_finance"),
                    )
                )
            return parsed_items

        try:
            headlines = await asyncio.to_thread(_load_news)
            if headlines:
                return headlines
        except Exception as exc:
            self.logger.warning(f"Yahoo Finance news fetch failed for {ticker}: {exc}")

        now = datetime.now(timezone.utc)
        # Mock news remains explicit and traceable so downstream services can discount it if needed.
        return [
            NewsItem(
                headline=f"{ticker} sees elevated options activity as traders react to fresh market momentum",
                timestamp=now,
                source="mock_news",
            ),
            NewsItem(
                headline=f"Analysts reassess {ticker} valuation after sector-wide earnings repricing",
                timestamp=now,
                source="mock_news",
            ),
            NewsItem(
                headline=f"Institutional flows into {ticker} remain mixed ahead of the next trading session",
                timestamp=now,
                source="mock_news",
            ),
        ]

    def _validate_ticker(self, ticker: str) -> str:
        try:
            validated = TickerInput(ticker=ticker.strip().upper()).ticker
        except ValidationError as exc:
            raise MarketDataServiceError(str(exc)) from exc
        return validated

    @staticmethod
    def _normalize_timestamp(value: pd.Timestamp) -> datetime:
        python_dt = value.to_pydatetime()
        if python_dt.tzinfo is None:
            return python_dt.replace(tzinfo=timezone.utc)
        return python_dt.astimezone(timezone.utc)
