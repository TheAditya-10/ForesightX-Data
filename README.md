# ForesightX Data Service

This service owns market data retrieval and technical computations.

## Responsibilities

- fetch latest prices
- return recent historical closes for charts
- compute Relative Strength Index (RSI) and Moving Average Convergence Divergence (MACD)
- expose recent news headlines
<!-- - cache short-lived market responses in Redis -->

## API

- `GET /price/{ticker}`
- `GET /history/{ticker}?limit=30`
- `GET /indicators/{ticker}`
- `GET /news/{ticker}`
- `GET /health`

## Internal Layout

- `app/controllers/`: HTTP-to-service translation and error mapping
- `app/routers/`: FastAPI route declarations
- `app/schemas/`: request and response models
- `app/services/`: market-fetching and cache logic
- `app/utils/`: env-driven service settings
