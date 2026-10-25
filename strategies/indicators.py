"""Vectorized technical analysis indicators engine for trading strategies."""

from typing import Dict, Tuple
import numpy as np
import pandas as pd


def calculate_sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period, min_periods=period).mean()


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's RSI)."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    # Wilder's smoothing (EMA with alpha = 1 / period)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # Where loss is 0, RSI is 100; where gain is 0, RSI is 0
    rsi = rsi.fillna(100.0 * (gain > 0))
    return rsi


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - close_prev).abs()
    tr3 = (low - close_prev).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    return atr


def calculate_supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
) -> Tuple[pd.Series, pd.Series]:
    """Calculates SuperTrend indicator and trend direction.

    Returns:
        (supertrend_line, direction)
        direction: +1 for Bullish (price above supertrend), -1 for Bearish (price below supertrend).
    """
    atr = calculate_atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2.0

    basic_upper = hl2 + (multiplier * atr)
    basic_lower = hl2 - (multiplier * atr)

    n = len(df)
    final_upper = np.zeros(n)
    final_lower = np.zeros(n)
    trend = np.zeros(n)

    close = df["close"].values
    basic_u = basic_upper.values
    basic_l = basic_lower.values

    # Initial row setup
    final_upper[0] = basic_u[0]
    final_lower[0] = basic_l[0]
    trend[0] = 1 if close[0] > basic_u[0] else -1

    for i in range(1, n):
        # Final Upper Band
        if basic_u[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_u[i]
        else:
            final_upper[i] = final_upper[i - 1]

        # Final Lower Band
        if basic_l[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_l[i]
        else:
            final_lower[i] = final_lower[i - 1]

        # Trend direction
        if trend[i - 1] == 1:
            if close[i] < final_lower[i]:
                trend[i] = -1
            else:
                trend[i] = 1
        else:
            if close[i] > final_upper[i]:
                trend[i] = 1
            else:
                trend[i] = -1

    supertrend = np.where(trend == 1, final_lower, final_upper)
    return pd.Series(supertrend, index=df.index), pd.Series(trend, index=df.index)


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-Weighted Average Price (Intraday calculation)."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"]
    cum_vol_price = (typical_price * vol).cumsum()
    cum_vol = vol.cumsum().replace(0, np.nan)
    vwap = cum_vol_price / cum_vol
    return vwap.ffill()


def calculate_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Moving Average Convergence Divergence."""
    fast_ema = calculate_ema(series, fast)
    slow_ema = calculate_ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands (Upper, Middle, Lower)."""
    middle = calculate_sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    upper = middle + (std_dev * std)
    lower = middle - (std_dev * std)
    return upper, middle, lower
