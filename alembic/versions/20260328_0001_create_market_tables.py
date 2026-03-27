"""create data service market tables

Revision ID: 20260328_0001
Revises:
Create Date: 2026-03-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260328_0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("exchange", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=False, server_default="USD"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ticker"),
    )
    op.create_table(
        "daily_price_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_ticker", sa.String(length=20), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_price", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("high_price", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("low_price", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("close_price", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["instrument_ticker"], ["instruments.ticker"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_ticker", "observed_at", "source", name="uq_daily_price_snapshots_key"),
    )
    op.create_index(op.f("ix_daily_price_snapshots_instrument_ticker"), "daily_price_snapshots", ["instrument_ticker"], unique=False)
    op.create_index(op.f("ix_daily_price_snapshots_observed_at"), "daily_price_snapshots", ["observed_at"], unique=False)
    op.create_table(
        "technical_indicator_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_ticker", sa.String(length=20), nullable=False),
        sa.Column("rsi", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("macd", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("macd_signal", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("macd_histogram", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("signal", sa.String(length=32), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["instrument_ticker"], ["instruments.ticker"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_ticker", "computed_at", "source", name="uq_technical_indicator_snapshots_key"),
    )
    op.create_index(
        op.f("ix_technical_indicator_snapshots_computed_at"),
        "technical_indicator_snapshots",
        ["computed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_technical_indicator_snapshots_instrument_ticker"),
        "technical_indicator_snapshots",
        ["instrument_ticker"],
        unique=False,
    )
    op.create_table(
        "news_articles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("headline", sa.String(length=512), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id"),
    )
    op.create_index(op.f("ix_news_articles_external_id"), "news_articles", ["external_id"], unique=False)
    op.create_index(op.f("ix_news_articles_published_at"), "news_articles", ["published_at"], unique=False)
    op.create_table(
        "instrument_news",
        sa.Column("instrument_ticker", sa.String(length=20), nullable=False),
        sa.Column("article_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["article_id"], ["news_articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["instrument_ticker"], ["instruments.ticker"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("instrument_ticker", "article_id"),
    )


def downgrade() -> None:
    op.drop_table("instrument_news")
    op.drop_index(op.f("ix_news_articles_published_at"), table_name="news_articles")
    op.drop_index(op.f("ix_news_articles_external_id"), table_name="news_articles")
    op.drop_table("news_articles")
    op.drop_index(
        op.f("ix_technical_indicator_snapshots_instrument_ticker"),
        table_name="technical_indicator_snapshots",
    )
    op.drop_index(op.f("ix_technical_indicator_snapshots_computed_at"), table_name="technical_indicator_snapshots")
    op.drop_table("technical_indicator_snapshots")
    op.drop_index(op.f("ix_daily_price_snapshots_observed_at"), table_name="daily_price_snapshots")
    op.drop_index(op.f("ix_daily_price_snapshots_instrument_ticker"), table_name="daily_price_snapshots")
    op.drop_table("daily_price_snapshots")
    op.drop_table("instruments")
