import asyncio
import hashlib
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared import get_logger

from app.db.models import DailyPriceSnapshot, Instrument, InstrumentNews, NewsArticle, TechnicalIndicatorSnapshot
from app.schemas.market import HistoryPoint, HistoryResponse, IndicatorResponse, NewsItem, NewsResponse, PriceResponse
from app.services.cache_service import CacheService
from app.utils.config import DataServiceSettings


class MarketDataServiceError(RuntimeError):
    """Raised when the market data service cannot assemble a response."""


class TickerInput(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=20)


class MarketDataService:
    def __init__(self, settings: DataServiceSettings, cache_service: CacheService, session: AsyncSession) -> None:
        self.settings = settings
        self.cache_service = cache_service
        self.session = session
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
        await self._persist_indicator(response)
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
        await self._persist_news(validated, response.headlines)
        await self.cache_service.set_json(cache_key, response.model_dump(mode="json"), self.settings.news_cache_ttl_seconds)
        return response

    async def _get_history_frame(self, ticker: str, period: str) -> tuple[pd.DataFrame, str]:
        yahoo_history = await self._fetch_yfinance_history(ticker=ticker, period=period)
        if not yahoo_history.empty:
            await self._persist_history_frame(ticker=ticker, history=yahoo_history, source="yahoo_finance")
            return yahoo_history, "yahoo_finance"

        persisted_history = await self._load_persisted_history(ticker=ticker, period=period)
        if not persisted_history.empty:
            return persisted_history, "service_database"

        self.logger.warning("Falling back to mock market history", extra={"ticker": ticker, "period": period})
        return self._generate_mock_history(ticker=ticker, period=period), "mock_market"

    def _generate_mock_history(self, ticker: str, period: str) -> pd.DataFrame:
        points = {"7d": 7, "6mo": 183}.get(period, 30)
        seed = int(hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)

        # Keep mock series deterministic per ticker so repeated local tests are stable.
        base_price = 90 + (seed % 120)
        drift = rng.normal(0.0004, 0.0012, points)
        noise = rng.normal(0.0, 0.008, points)
        returns = drift + noise
        close = base_price * np.cumprod(1 + returns)
        close = np.clip(close, 5.0, None)

        open_price = close * (1 + rng.normal(0.0, 0.003, points))
        high = np.maximum(open_price, close) * (1 + rng.uniform(0.0005, 0.012, points))
        low = np.minimum(open_price, close) * (1 - rng.uniform(0.0005, 0.012, points))
        volume = rng.integers(900_000, 8_500_000, points)

        index = pd.date_range(end=datetime.now(timezone.utc), periods=points, freq="D", tz="UTC")
        return pd.DataFrame(
            {
                "Open": open_price,
                "High": high,
                "Low": low,
                "Close": close,
                "Volume": volume,
            },
            index=index,
        )

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

    async def _fetch_news(self, ticker: str) -> list[NewsItem]:
        # Try Finnhub first (if API key configured)
        if self.settings.finnhub_api_key:
            finnhub_news = await self._fetch_finnhub_news(ticker)
            if finnhub_news:
                return finnhub_news

        # Fallback to Yahoo Finance
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
                        url=item.get("link"),
                    )
                )
            return parsed_items

        try:
            headlines = await asyncio.to_thread(_load_news)
            if headlines:
                return headlines
        except Exception as exc:
            self.logger.warning(f"Yahoo Finance news fetch failed for {ticker}: {exc}")

        persisted = await self._load_persisted_news(ticker)
        if persisted:
            return persisted

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

    async def _fetch_finnhub_news(self, ticker: str) -> list[NewsItem]:
        """Fetch news from Finnhub API. Requires FINNHUB_API_KEY environment variable."""
        import urllib.request
        import json

        def _load_finnhub_news() -> list[NewsItem]:
            try:
                url = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&limit=5&token={self.settings.finnhub_api_key}"
                req = urllib.request.Request(url, headers={"User-Agent": "ForesightX/1.0"})
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.loads(response.read().decode())
                    
                if not isinstance(data, list):
                    return []
                    
                parsed_items: list[NewsItem] = []
                for item in data[:5]:
                    headline = item.get("headline")
                    timestamp_unix = item.get("datetime")
                    if not headline or not timestamp_unix:
                        continue
                    
                    parsed_items.append(
                        NewsItem(
                            headline=headline,
                            timestamp=datetime.fromtimestamp(int(timestamp_unix), tz=timezone.utc),
                            source=item.get("source", "finnhub"),
                            url=item.get("url"),
                        )
                    )
                return parsed_items
            except Exception as exc:
                self.logger.warning(f"Finnhub news fetch failed for {ticker}: {exc}")
                return []

        return await asyncio.to_thread(_load_finnhub_news)

    async def _persist_history_frame(self, ticker: str, history: pd.DataFrame, source: str) -> None:
        rows = history.dropna(subset=["Close"])
        if rows.empty:
            return

        await self._ensure_instrument(ticker)
        values: list[dict] = []
        for index, row in rows.iterrows():
            values.append(
                {
                    "instrument_ticker": ticker,
                    "observed_at": self._normalize_timestamp(index),
                    "open_price": self._optional_float(row.get("Open")),
                    "high_price": self._optional_float(row.get("High")),
                    "low_price": self._optional_float(row.get("Low")),
                    "close_price": float(row["Close"]),
                    "volume": self._optional_int(row.get("Volume")),
                    "source": source,
                }
            )

        statement = insert(DailyPriceSnapshot).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=["instrument_ticker", "observed_at", "source"],
            set_={
                "open_price": statement.excluded.open_price,
                "high_price": statement.excluded.high_price,
                "low_price": statement.excluded.low_price,
                "close_price": statement.excluded.close_price,
                "volume": statement.excluded.volume,
            },
        )
        await self.session.execute(statement)
        await self.session.commit()

    async def _load_persisted_history(self, ticker: str, period: str) -> pd.DataFrame:
        lookback_days = {"7d": 7, "6mo": 183}.get(period, 30)
        cutoff = datetime.now(timezone.utc) - pd.Timedelta(days=lookback_days)
        result = await self.session.execute(
            select(DailyPriceSnapshot)
            .where(
                DailyPriceSnapshot.instrument_ticker == ticker,
                DailyPriceSnapshot.observed_at >= cutoff,
            )
            .order_by(DailyPriceSnapshot.observed_at.asc())
        )
        rows = result.scalars().all()
        if not rows:
            return pd.DataFrame()

        frame = pd.DataFrame(
            [
                {
                    "Date": row.observed_at,
                    "Open": float(row.open_price) if row.open_price is not None else np.nan,
                    "High": float(row.high_price) if row.high_price is not None else np.nan,
                    "Low": float(row.low_price) if row.low_price is not None else np.nan,
                    "Close": float(row.close_price),
                    "Volume": row.volume,
                }
                for row in rows
            ]
        )
        frame["Date"] = pd.to_datetime(frame["Date"], utc=True)
        return frame.set_index("Date")

    async def _persist_indicator(self, response: IndicatorResponse) -> None:
        await self._ensure_instrument(response.ticker)
        statement = insert(TechnicalIndicatorSnapshot).values(
            {
                "instrument_ticker": response.ticker,
                "rsi": response.rsi,
                "macd": response.macd,
                "macd_signal": response.macd_signal,
                "macd_histogram": response.macd_histogram,
                "signal": response.signal,
                "computed_at": response.computed_at,
                "source": response.source,
            }
        )
        statement = statement.on_conflict_do_update(
            index_elements=["instrument_ticker", "computed_at", "source"],
            set_={
                "rsi": statement.excluded.rsi,
                "macd": statement.excluded.macd,
                "macd_signal": statement.excluded.macd_signal,
                "macd_histogram": statement.excluded.macd_histogram,
                "signal": statement.excluded.signal,
            },
        )
        await self.session.execute(statement)
        await self.session.commit()

    async def _persist_news(self, ticker: str, headlines: list[NewsItem]) -> None:
        if not headlines:
            return

        await self._ensure_instrument(ticker)
        for item in headlines:
            if item.source == "mock_news":
                continue
            article_key = self._article_external_id(ticker, item)
            article_statement = insert(NewsArticle).values(
                {
                    "external_id": article_key,
                    "headline": item.headline,
                    "url": item.url,
                    "published_at": item.timestamp,
                    "source": item.source,
                }
            )
            article_statement = article_statement.on_conflict_do_update(
                index_elements=["external_id"],
                set_={
                    "headline": article_statement.excluded.headline,
                    "url": article_statement.excluded.url,
                    "published_at": article_statement.excluded.published_at,
                    "source": article_statement.excluded.source,
                },
            ).returning(NewsArticle.id)
            article_id = await self.session.scalar(article_statement)
            if article_id is None:
                article_id = await self.session.scalar(
                    select(NewsArticle.id).where(NewsArticle.external_id == article_key)
                )
            link_statement = insert(InstrumentNews).values(
                {
                    "instrument_ticker": ticker,
                    "article_id": article_id,
                }
            )
            link_statement = link_statement.on_conflict_do_nothing(
                index_elements=["instrument_ticker", "article_id"]
            )
            await self.session.execute(link_statement)
        await self.session.commit()

    async def _load_persisted_news(self, ticker: str) -> list[NewsItem]:
        result = await self.session.execute(
            select(NewsArticle)
            .join(InstrumentNews, InstrumentNews.article_id == NewsArticle.id)
            .where(InstrumentNews.instrument_ticker == ticker)
            .order_by(desc(NewsArticle.published_at))
            .limit(5)
        )
        articles = result.scalars().all()
        return [
            NewsItem(
                headline=article.headline,
                timestamp=article.published_at,
                source=article.source,
                url=article.url,
            )
            for article in articles
        ]

    async def _ensure_instrument(self, ticker: str) -> None:
        statement = insert(Instrument).values(
            {
                "ticker": ticker,
                "currency": "USD",
                "is_active": True,
            }
        )
        statement = statement.on_conflict_do_nothing(index_elements=["ticker"])
        await self.session.execute(statement)

    @staticmethod
    def _optional_float(value) -> float | None:
        if value is None or pd.isna(value):
            return None
        return float(value)

    @staticmethod
    def _optional_int(value) -> int | None:
        if value is None or pd.isna(value):
            return None
        return int(value)

    @staticmethod
    def _article_external_id(ticker: str, item: NewsItem) -> str:
        raw = f"{ticker}|{item.headline}|{item.timestamp.isoformat()}|{item.source}|{item.url or ''}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

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
