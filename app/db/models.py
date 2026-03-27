import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Instrument(Base):
    __tablename__ = "instruments"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str] = mapped_column(String(16), nullable=False, default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    prices: Mapped[list["DailyPriceSnapshot"]] = relationship(back_populates="instrument")
    indicators: Mapped[list["TechnicalIndicatorSnapshot"]] = relationship(back_populates="instrument")
    news_links: Mapped[list["InstrumentNews"]] = relationship(back_populates="instrument")


class DailyPriceSnapshot(Base):
    __tablename__ = "daily_price_snapshots"
    __table_args__ = (
        UniqueConstraint("instrument_ticker", "observed_at", "source", name="uq_daily_price_snapshots_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_ticker: Mapped[str] = mapped_column(ForeignKey("instruments.ticker", ondelete="CASCADE"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    open_price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    high_price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    low_price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    close_price: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    instrument: Mapped[Instrument] = relationship(back_populates="prices")


class TechnicalIndicatorSnapshot(Base):
    __tablename__ = "technical_indicator_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "instrument_ticker",
            "computed_at",
            "source",
            name="uq_technical_indicator_snapshots_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_ticker: Mapped[str] = mapped_column(ForeignKey("instruments.ticker", ondelete="CASCADE"), index=True)
    rsi: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    macd: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)
    macd_signal: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)
    macd_histogram: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)
    signal: Mapped[str] = mapped_column(String(32), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    instrument: Mapped[Instrument] = relationship(back_populates="indicators")


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    headline: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    instruments: Mapped[list["InstrumentNews"]] = relationship(back_populates="article")


class InstrumentNews(Base):
    __tablename__ = "instrument_news"

    instrument_ticker: Mapped[str] = mapped_column(
        ForeignKey("instruments.ticker", ondelete="CASCADE"),
        primary_key=True,
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("news_articles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    instrument: Mapped[Instrument] = relationship(back_populates="news_links")
    article: Mapped[NewsArticle] = relationship(back_populates="instruments")
