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


class BarPoint(BaseModel):
    timestamp: datetime
    open: float = Field(..., gt=0)
    high: float = Field(..., gt=0)
    low: float = Field(..., gt=0)
    close: float = Field(..., gt=0)
    volume: int | None = Field(default=None, ge=0)


class BarsResponse(BaseModel):
    ticker: str
    interval: str
    points: list[BarPoint]
    source: str


class RealtimeTickIn(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=20)
    price: float = Field(..., gt=0)
    timestamp: datetime | None = None
    volume: int | None = Field(default=None, ge=0)
    source: str = Field(default="external_stream", min_length=1, max_length=64)


class RealtimeTick(BaseModel):
    ticker: str
    price: float = Field(..., gt=0)
    timestamp: datetime
    volume: int | None = Field(default=None, ge=0)
    source: str


class InstrumentSearchItem(BaseModel):
    ticker: str
    name: str | None = None
    exchange: str | None = None
    score: float = Field(default=0.0, ge=0)


class InstrumentSearchResponse(BaseModel):
    query: str
    results: list[InstrumentSearchItem]
