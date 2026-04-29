from datetime import timezone

import pandas as pd
import pytest

from app.services.market_data_service import MarketDataService


def test_normalize_yfinance_history_collapses_duplicate_ohlcv_columns() -> None:
    index = pd.date_range("2026-04-27", periods=1, tz=timezone.utc)
    history = pd.DataFrame(
        [[100.0, 101.0, 99.0, 100.5, 100.6, 1200]],
        index=index,
        columns=pd.MultiIndex.from_tuples(
            [
                ("Open", "MSFT"),
                ("High", "MSFT"),
                ("Low", "MSFT"),
                ("Close", "MSFT"),
                ("Close", "MSFT"),
                ("Volume", "MSFT"),
            ],
            names=["Price", "Ticker"],
        ),
    )

    normalized = MarketDataService._normalize_yfinance_history(history, "MSFT")
    row = normalized.iloc[0]

    assert list(normalized.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert row["Open"] == pytest.approx(100.0)
    assert row["Close"] == pytest.approx(100.5)
    assert MarketDataService._optional_float(row.get("Open")) == pytest.approx(100.0)
    assert MarketDataService._optional_int(row.get("Volume")) == 1200


def test_optional_numeric_helpers_use_first_non_null_series_value() -> None:
    assert MarketDataService._optional_float(pd.Series([None, 42.5])) == pytest.approx(42.5)
    assert MarketDataService._optional_float(pd.Series([None])) is None
    assert MarketDataService._optional_int(pd.Series([None, 7])) == 7
