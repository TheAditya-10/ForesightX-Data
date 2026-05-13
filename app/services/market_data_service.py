import asyncio
import hashlib
import json
import time
import urllib.parse
import urllib.request
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
from app.schemas.market import (
    BarPoint,
    BarsResponse,
    HistoryPoint,
    HistoryResponse,
    IndicatorResponse,
    NewsItem,
    NewsResponse,
    PriceResponse,
)
from app.services.cache_service import CacheService
from app.utils.config import DataServiceSettings
from app.schemas.market import InstrumentSearchItem, InstrumentSearchResponse, RealtimeTick, RealtimeTickIn


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

    async def get_bars(self, ticker: str, limit: int, interval: str) -> BarsResponse:
        validated = self._validate_ticker(ticker)
        normalized_interval = interval.strip().lower()
        if normalized_interval not in {"1h", "1d"}:
            raise MarketDataServiceError(f"Unsupported interval '{interval}'. Expected one of: 1h, 1d")
        cache_key = f"bars:{validated}:{normalized_interval}:{limit}"
        cached = await self.cache_service.get_json(cache_key)
        if cached:
            return BarsResponse.model_validate(cached)

        period = "90d" if normalized_interval == "1h" else "6mo"
        history, source = await self._get_history_frame(validated, period=period, interval=normalized_interval)
        trimmed = history.dropna(subset=["Open", "High", "Low", "Close"]).tail(limit)
        if trimmed.empty:
            raise MarketDataServiceError(f"No {normalized_interval} bar data available for {validated}")

        response = BarsResponse(
            ticker=validated,
            interval=normalized_interval,
            points=[
                BarPoint(
                    timestamp=self._normalize_timestamp(index),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=self._optional_int(row.get("Volume")),
                )
                for index, row in trimmed.iterrows()
            ],
            source=source,
        )
        await self.cache_service.set_json(cache_key, response.model_dump(mode="json"), self.settings.market_bars_cache_ttl_seconds)
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

    async def ingest_tick(self, payload: RealtimeTickIn) -> RealtimeTick:
        ticker = self._validate_ticker(payload.ticker)
        tick = RealtimeTick(
            ticker=ticker,
            price=payload.price,
            timestamp=payload.timestamp or datetime.now(timezone.utc),
            volume=payload.volume,
            source=payload.source,
        )
        await self._ensure_instrument(ticker)
        statement = insert(DailyPriceSnapshot).values(
            {
                "instrument_ticker": tick.ticker,
                "observed_at": tick.timestamp,
                "open_price": tick.price,
                "high_price": tick.price,
                "low_price": tick.price,
                "close_price": tick.price,
                "volume": tick.volume,
                "source": tick.source,
            }
        )
        statement = statement.on_conflict_do_update(
            index_elements=["instrument_ticker", "observed_at", "source"],
            set_={
                "open_price": tick.price,
                "high_price": tick.price,
                "low_price": tick.price,
                "close_price": tick.price,
                "volume": tick.volume,
            },
        )
        await self.session.execute(statement)
        await self.session.commit()
        await self.cache_service.set_json(
            f"price:{tick.ticker}",
            PriceResponse(
                ticker=tick.ticker,
                price=tick.price,
                timestamp=tick.timestamp,
                source=tick.source,
            ).model_dump(mode="json"),
            self.settings.cache_ttl_seconds,
        )
        return tick

    async def search_instruments(self, query: str, limit: int = 15) -> InstrumentSearchResponse:
        clean_query = query.strip()
        if not clean_query:
            raise MarketDataServiceError("Search query cannot be empty")
        cache_key = f"instrument_search:{clean_query.lower()}:{limit}"
        cached = await self.cache_service.get_json(cache_key)
        if cached:
            return InstrumentSearchResponse.model_validate(cached)

        yahoo_results = await self._fetch_yahoo_instrument_search(clean_query=clean_query, limit=limit)
        db_results = await self._search_persisted_instruments(clean_query=clean_query, limit=limit)

        merged: dict[str, InstrumentSearchItem] = {}
        for item in [*yahoo_results, *db_results]:
            existing = merged.get(item.ticker)
            if existing is None or item.score > existing.score:
                merged[item.ticker] = item

        ranked = sorted(merged.values(), key=lambda item: item.score, reverse=True)[:limit]
        response = InstrumentSearchResponse(query=clean_query, results=ranked)
        await self.cache_service.set_json(cache_key, response.model_dump(mode="json"), self.settings.cache_ttl_seconds)
        return response

    async def _get_history_frame(self, ticker: str, period: str, interval: str = "1d") -> tuple[pd.DataFrame, str]:
        yahoo_history = await self._fetch_yfinance_history(ticker=ticker, period=period, interval=interval)
        if not yahoo_history.empty:
            await self._persist_history_frame(ticker=ticker, history=yahoo_history, source="yahoo_finance")
            return yahoo_history, "yahoo_finance"

        persisted_history = await self._load_persisted_history(ticker=ticker, period=period)
        if not persisted_history.empty:
            return persisted_history, "service_database"

        raise MarketDataServiceError(f"Upstream market data unavailable for {ticker}")

    async def _fetch_yfinance_history(self, ticker: str, period: str, interval: str) -> pd.DataFrame:
        def _load() -> pd.DataFrame:
            attempts = 3
            for attempt in range(1, attempts + 1):
                history = yf.download(
                    ticker,
                    period=period,
                    interval=interval,
                    auto_adjust=False,
                    progress=False,
                    prepost=False,
                    threads=False,
                    multi_level_index=True,
                )
                if not history.empty:
                    history = self._normalize_yfinance_history(history, ticker)
                    if history.index.tz is None:
                        history.index = history.index.tz_localize(timezone.utc)
                    else:
                        history.index = history.index.tz_convert(timezone.utc)
                    return history
                if attempt < attempts:
                    time.sleep(attempt)
            self.logger.warning(f"Yahoo Finance returned empty history for {ticker} ({period}, {interval})")
            return pd.DataFrame()

        try:
            return await asyncio.to_thread(_load)
        except Exception as exc:
            self.logger.warning(f"Yahoo Finance fetch failed for {ticker} ({period}, {interval}): {exc}")
            return pd.DataFrame()

    async def _fetch_news(self, ticker: str) -> list[NewsItem]:
        # Try Finnhub first (if API key configured)
        if self.settings.finnhub_api_key:
            finnhub_news = await self._fetch_finnhub_news(ticker)
            if finnhub_news:
                return finnhub_news

        def _load_news() -> list[NewsItem]:
            instrument = yf.Ticker(ticker)
            raw_news = instrument.news or []
            parsed_items: list[NewsItem] = []
            for item in raw_news[:5]:
                content = item.get("content", {})
                title = item.get("title") or content.get("title")
                published = item.get("providerPublishTime") or content.get("pubDate")
                if not title or not published:
                    continue
                if isinstance(published, str):
                    timestamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
                else:
                    timestamp = datetime.fromtimestamp(int(published), tz=timezone.utc)
                provider = item.get("publisher") or content.get("provider", {}).get("displayName") or "yahoo_finance"
                canonical_url = (
                    item.get("link")
                    or content.get("canonicalUrl", {}).get("url")
                    or content.get("clickThroughUrl", {}).get("url")
                    or content.get("previewUrl")
                )
                parsed_items.append(
                    NewsItem(
                        headline=title,
                        timestamp=timestamp,
                        source=provider,
                        url=canonical_url,
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
        raise MarketDataServiceError(f"No news data available for {ticker}")

    async def _fetch_finnhub_news(self, ticker: str) -> list[NewsItem]:
        """Fetch news from Finnhub API. Requires FINNHUB_API_KEY environment variable."""
        import urllib.request
        import json

        def _load_finnhub_news() -> list[NewsItem]:
            try:
                end_date = datetime.now(timezone.utc).date()
                start_date = end_date - pd.Timedelta(days=7)
                url = (
                    "https://finnhub.io/api/v1/company-news"
                    f"?symbol={ticker}"
                    f"&from={start_date.isoformat()}"
                    f"&to={end_date.isoformat()}"
                    f"&token={self.settings.finnhub_api_key}"
                )
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

    async def _fetch_yahoo_instrument_search(self, clean_query: str, limit: int) -> list[InstrumentSearchItem]:
        def _load() -> list[InstrumentSearchItem]:
            encoded = urllib.parse.quote(clean_query)
            url = (
                f"https://query2.finance.yahoo.com/v1/finance/search"
                f"?q={encoded}&quotesCount={limit}&newsCount=0"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "ForesightX/1.0"})
            with urllib.request.urlopen(req, timeout=6) as response:
                payload = json.loads(response.read().decode())
            quotes = payload.get("quotes", [])
            items: list[InstrumentSearchItem] = []
            for rank, quote in enumerate(quotes):
                symbol = str(quote.get("symbol", "")).strip().upper()
                if not symbol:
                    continue
                name = quote.get("shortname") or quote.get("longname")
                exchange = quote.get("exchDisp") or quote.get("exchange")
                score = max(0.1, 1.0 - (rank * 0.05))
                if clean_query.lower() in symbol.lower():
                    score += 0.5
                if name and clean_query.lower() in str(name).lower():
                    score += 0.4
                items.append(
                    InstrumentSearchItem(
                        ticker=symbol,
                        name=str(name) if name else None,
                        exchange=str(exchange) if exchange else None,
                        score=round(score, 3),
                    )
                )
            return items

        try:
            return await asyncio.to_thread(_load)
        except Exception as exc:
            self.logger.warning(f"Yahoo instrument search failed for query '{clean_query}': {exc}")
            return []

    async def _search_persisted_instruments(self, clean_query: str, limit: int) -> list[InstrumentSearchItem]:
        q = f"%{clean_query.lower()}%"
        result = await self.session.execute(
            select(Instrument)
            .where(
                (Instrument.is_active.is_(True))
                & (
                    Instrument.ticker.ilike(q)
                    | Instrument.name.ilike(q)
                )
            )
            .limit(limit)
        )
        instruments = result.scalars().all()
        items: list[InstrumentSearchItem] = []
        for instrument in instruments:
            score = 0.3
            if clean_query.lower() in instrument.ticker.lower():
                score += 0.5
            if instrument.name and clean_query.lower() in instrument.name.lower():
                score += 0.4
            items.append(
                InstrumentSearchItem(
                    ticker=instrument.ticker,
                    name=instrument.name,
                    exchange=instrument.exchange,
                    score=round(score, 3),
                )
            )
        return items

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

    @staticmethod
    def _normalize_yfinance_history(history: pd.DataFrame, ticker: str) -> pd.DataFrame:
        expected_columns = ["Open", "High", "Low", "Close", "Volume"]
        if history.empty:
            return history

        normalized = history.copy()
        if isinstance(normalized.columns, pd.MultiIndex):
            ticker_upper = ticker.upper()
            price_level = None
            for level in range(normalized.columns.nlevels):
                labels = {str(value) for value in normalized.columns.get_level_values(level)}
                if any(column in labels for column in expected_columns):
                    price_level = level
                    break

            if price_level is not None:
                for level in range(normalized.columns.nlevels):
                    if level == price_level:
                        continue
                    labels = list(normalized.columns.get_level_values(level))
                    matching_label = next((label for label in labels if str(label).upper() == ticker_upper), None)
                    if matching_label is not None:
                        normalized = normalized.xs(matching_label, axis=1, level=level, drop_level=True)
                        break

            if isinstance(normalized.columns, pd.MultiIndex):
                if price_level is None:
                    normalized.columns = normalized.columns.get_level_values(0)
                else:
                    normalized.columns = [column[price_level] for column in normalized.columns]

        output = pd.DataFrame(index=normalized.index)
        for column in expected_columns:
            if column not in normalized.columns:
                continue
            values = normalized[column]
            if isinstance(values, pd.DataFrame):
                values = values.dropna(axis=1, how="all")
                if values.empty:
                    continue
                values = values.iloc[:, 0]
            output[column] = values
        return output

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
        if isinstance(value, pd.Series):
            value = value.dropna()
            if value.empty:
                return None
            value = value.iloc[0]
        if value is None or pd.isna(value):
            return None
        return float(value)

    @staticmethod
    def _optional_int(value) -> int | None:
        if isinstance(value, pd.Series):
            value = value.dropna()
            if value.empty:
                return None
            value = value.iloc[0]
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
