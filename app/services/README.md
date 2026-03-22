# Data Services

This directory contains the business logic of the data service.

## Modules

- `cache_service.py`: Redis connection and JSON cache helpers
- `market_data_service.py`: yfinance fetches, CSV fallback loading, indicator calculations, and response assembly

## Design

The service prefers live data but keeps CSV fallback behavior because the wider system should still operate when external data sources fail.
