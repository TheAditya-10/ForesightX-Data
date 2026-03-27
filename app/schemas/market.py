from datetime import datetime

from pydantic import BaseModel, Field


class PriceResponse(BaseModel):
    ticker: str
    price: float = Field(..., gt=0)
    timestamp: datetime
    currency: str = "USD"
    source: str


class IndicatorResponse(BaseModel):
    ticker: str
    rsi: float = Field(..., ge=0, le=100)
    macd: float
    signal: str
    macd_signal: float
    macd_histogram: float
    computed_at: datetime
    source: str


class NewsItem(BaseModel):
    headline: str
    timestamp: datetime
    source: str
    url: str | None = None


class NewsResponse(BaseModel):
    ticker: str
    headlines: list[NewsItem]


class HistoryPoint(BaseModel):
    timestamp: datetime
    close: float = Field(..., gt=0)


class HistoryResponse(BaseModel):
    ticker: str
    points: list[HistoryPoint]
    source: str
