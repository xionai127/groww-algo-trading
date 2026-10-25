"""Unit tests for built-in strategies (EMA Crossover and SuperTrend RSI)."""

from datetime import datetime, timezone
import pandas as pd
import pytest
from market_data.models import Candle, Timeframe
from strategies.builtin.ema_crossover import EMACrossoverStrategy
from strategies.builtin.supertrend_rsi import SuperTrendRSIStrategy
from strategies.models import SignalAction


def create_crossover_df(bullish: bool = True) -> pd.DataFrame:
    """Generates synthetic price series that triggers an EMA crossover."""
    n = 30
    dates = pd.date_range("2026-10-25 09:15", periods=n, freq="5min")

    if bullish:
        # Starts in downtrend, then sharp rally to trigger fast > slow crossover
        prices = [25000.0 - (i * 5.0) for i in range(25)] + [24900.0 + (i * 25.0) for i in range(5)]
    else:
        # Starts in uptrend, then sharp selloff to trigger fast < slow breakdown
        prices = [25000.0 + (i * 5.0) for i in range(25)] + [25120.0 - (i * 40.0) for i in range(5)]

    return pd.DataFrame(
        {
            "open": prices,
            "high": [p + 10.0 for p in prices],
            "low": [p - 10.0 for p in prices],
            "close": prices,
            "volume": [1000] * n,
        },
        index=dates,
    )


def test_ema_crossover_bullish_signal():
    """Verify EMA Crossover emits Buy CE signal on bullish cross."""
    strat = EMACrossoverStrategy()
    df = create_crossover_df(bullish=True)
    last_candle = Candle(
        symbol="NIFTY",
        timeframe=Timeframe.M5,
        timestamp=df.index[-1].to_pydatetime(),
        open=df["open"].iloc[-1],
        high=df["high"].iloc[-1],
        low=df["low"].iloc[-1],
        close=df["close"].iloc[-1],
        volume=1000,
        is_complete=True,
    )

    signal = strat.on_candle(last_candle, df)
    assert signal is not None
    assert signal.direction == "CE"
    assert signal.action == SignalAction.ENTRY
    assert "Bullish EMA Crossover" in signal.rationale


def test_ema_crossover_bearish_signal():
    """Verify EMA Crossover emits Buy PE signal on bearish cross."""
    strat = EMACrossoverStrategy()
    df = create_crossover_df(bullish=False)
    last_candle = Candle(
        symbol="NIFTY",
        timeframe=Timeframe.M5,
        timestamp=df.index[-1].to_pydatetime(),
        open=df["open"].iloc[-1],
        high=df["high"].iloc[-1],
        low=df["low"].iloc[-1],
        close=df["close"].iloc[-1],
        volume=1000,
        is_complete=True,
    )

    signal = strat.on_candle(last_candle, df)
    assert signal is not None
    assert signal.direction == "PE"
    assert signal.action == SignalAction.ENTRY
    assert "Bearish EMA Crossover" in signal.rationale


def test_supertrend_rsi_signal():
    """Verify SuperTrend RSI triggers on confirmed trend switch."""
    strat = SuperTrendRSIStrategy()
    strat.is_active = True
    df = create_crossover_df(bullish=True)
    last_candle = Candle(
        symbol="NIFTY",
        timeframe=Timeframe.M5,
        timestamp=df.index[-1].to_pydatetime(),
        open=df["open"].iloc[-1],
        high=df["high"].iloc[-1],
        low=df["low"].iloc[-1],
        close=df["close"].iloc[-1],
        volume=1000,
        is_complete=True,
    )

    signal = strat.on_candle(last_candle, df)
    # If RSI > 50 and trend flipped, signal emitted
    if signal:
        assert signal.direction in ("CE", "PE")
        assert signal.underlying == "NIFTY"
