"""Unit tests for vectorized technical indicator formulas."""

import numpy as np
import pandas as pd
import pytest
from strategies.indicators import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
    calculate_supertrend,
    calculate_vwap,
)


@pytest.fixture
def sample_ohlcv():
    """Generates synthetic trend sequence for indicator testing."""
    n = 50
    dates = pd.date_range("2026-10-25 09:15", periods=n, freq="5min")
    prices = [25000.0 + (i * 10.0 if i < 25 else 250.0 - (i - 25) * 15.0) for i in range(n)]

    df = pd.DataFrame(
        {
            "open": prices,
            "high": [p + 15.0 for p in prices],
            "low": [p - 15.0 for p in prices],
            "close": prices,
            "volume": [1000 + i * 50 for i in range(n)],
        },
        index=dates,
    )
    return df


def test_calculate_sma_and_ema(sample_ohlcv):
    """Verify SMA and EMA calculation shapes and behavior."""
    close = sample_ohlcv["close"]
    sma20 = calculate_sma(close, 20)
    ema20 = calculate_ema(close, 20)

    assert len(sma20) == len(close)
    assert len(ema20) == len(close)
    assert np.isnan(sma20.iloc[0])  # First 19 rows NaN for SMA
    assert not np.isnan(sma20.iloc[20])
    assert not np.isnan(ema20.iloc[0])


def test_calculate_rsi(sample_ohlcv):
    """Verify RSI bounds between 0 and 100."""
    close = sample_ohlcv["close"]
    rsi = calculate_rsi(close, 14)

    assert len(rsi) == len(close)
    valid_rsi = rsi.dropna()
    assert (valid_rsi >= 0).all() and (valid_rsi <= 100).all()
    # In uptrend (first 25 bars), RSI should be > 50
    assert valid_rsi.iloc[20] > 50.0


def test_calculate_supertrend(sample_ohlcv):
    """Verify SuperTrend line and trend direction output."""
    st_line, st_dir = calculate_supertrend(sample_ohlcv, 10, 3.0)

    assert len(st_line) == len(sample_ohlcv)
    assert len(st_dir) == len(sample_ohlcv)
    # Direction is either +1 or -1
    assert set(st_dir.unique()).issubset({1.0, -1.0})


def test_calculate_vwap(sample_ohlcv):
    """Verify VWAP is positive and lies within price range."""
    vwap = calculate_vwap(sample_ohlcv)
    assert (vwap > 0).all()
    assert vwap.iloc[-1] >= sample_ohlcv["low"].min()
    assert vwap.iloc[-1] <= sample_ohlcv["high"].max()


def test_calculate_macd(sample_ohlcv):
    """Verify MACD components (line, signal, histogram)."""
    close = sample_ohlcv["close"]
    macd, signal, hist = calculate_macd(close, 12, 26, 9)

    assert len(macd) == len(close)
    assert len(signal) == len(close)
    assert len(hist) == len(close)
    # Histogram = macd - signal
    np.testing.assert_allclose(hist.dropna(), (macd - signal).dropna(), atol=1e-5)


def test_calculate_bollinger_bands(sample_ohlcv):
    """Verify Upper > Middle > Lower in Bollinger Bands."""
    close = sample_ohlcv["close"]
    upper, middle, lower = calculate_bollinger_bands(close, 20, 2.0)

    valid = upper.dropna().index
    assert (upper.loc[valid] >= middle.loc[valid]).all()
    assert (middle.loc[valid] >= lower.loc[valid]).all()
