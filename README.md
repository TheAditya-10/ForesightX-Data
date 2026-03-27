# ForesightX Data Service

This service owns market data retrieval and technical computations.

## Responsibilities

- fetch latest prices
- return recent historical closes for charts
- compute Relative Strength Index (RSI) and Moving Average Convergence Divergence (MACD)
- expose recent news headlines
- cache short-lived market responses in Redis
- persist canonical market records in its own PostgreSQL schema

## API

- `GET /price/{ticker}`
- `GET /history/{ticker}?limit=30`
- `GET /indicators/{ticker}`
- `GET /news/{ticker}`
- `GET /health`

## Internal Layout

- `app/db/`: service-owned market schema and async session management
- `alembic/`: schema migrations
- `app/controllers/`: HTTP-to-service translation and error mapping
- `app/routers/`: FastAPI route declarations
- `app/schemas/`: request and response models
- `app/services/`: market-fetching and cache logic
- `app/utils/`: env-driven service settings

## Configuration

This service is independently configured from `ForesightX-data/.env`.

Key variables:

- `DATABASE_URL`
- `REDIS_URL`
- `CACHE_TTL_SECONDS`
- `NEWS_CACHE_TTL_SECONDS`
- `HISTORY_CACHE_TTL_SECONDS`

Schema ownership:

- `instruments`: canonical ticker catalog
- `daily_price_snapshots`: one row per instrument/date/source
- `technical_indicator_snapshots`: computed indicator outputs owned by this service
- `news_articles` and `instrument_news`: normalized article storage and instrument linkage

Before first startup:

```bash
alembic upgrade head
```

The service no longer reads fallback CSV data from the pattern repository. It now falls back to its own persisted market/news records before failing upstream requests.
