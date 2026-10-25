"""Unit tests for candle building, time bucket alignment, and resampling."""

from datetime import datetime, timezone
import pytest
from market_data.candle_aggregator import (
    CandleAggregator,
    align_timestamp_to_timeframe,
    resample_candles,
)
from market_data.models import Candle, Tick, Timeframe


def test_align_timestamp_to_timeframe():
    """Verify bucket floor calculation across multiple timeframes."""
    dt = datetime(2026, 10, 25, 9, 17, 45, tzinfo=timezone.utc)

    # 1m -> 09:17:00
    aligned_1m = align_timestamp_to_timeframe(dt, Timeframe.M1)
    assert aligned_1m == datetime(2026, 10, 25, 9, 17, 0, tzinfo=timezone.utc)

    # 5m -> 09:15:00
    aligned_5m = align_timestamp_to_timeframe(dt, Timeframe.M5)
    assert aligned_5m == datetime(2026, 10, 25, 9, 15, 0, tzinfo=timezone.utc)

    # 15m -> 09:15:00
    aligned_15m = align_timestamp_to_timeframe(dt, Timeframe.M15)
    assert aligned_15m == datetime(2026, 10, 25, 9, 15, 0, tzinfo=timezone.utc)


def test_tick_to_candle_aggregation():
    """Verify tick OHLCV formation within same candle bucket."""
    aggregator = CandleAggregator(timeframes=[Timeframe.M1])

    t1 = Tick(symbol="NIFTY", ltp=25000.0, timestamp=datetime(2026, 10, 25, 9, 15, 5, tzinfo=timezone.utc), volume=10)
    t2 = Tick(symbol="NIFTY", ltp=25020.0, timestamp=datetime(2026, 10, 25, 9, 15, 20, tzinfo=timezone.utc), volume=15)
    t3 = Tick(symbol="NIFTY", ltp=24990.0, timestamp=datetime(2026, 10, 25, 9, 15, 40, tzinfo=timezone.utc), volume=20)
    t4 = Tick(symbol="NIFTY", ltp=25010.0, timestamp=datetime(2026, 10, 25, 9, 15, 55, tzinfo=timezone.utc), volume=5)

    for t in [t1, t2, t3, t4]:
        aggregator.process_tick(t)

    current = aggregator.get_current_candle("NIFTY", Timeframe.M1)
    assert current is not None
    assert current.open == 25000.0
    assert current.high == 25020.0
    assert current.low == 24990.0
    assert current.close == 25010.0
    assert current.volume == 50
    assert current.is_complete is False


def test_candle_interval_closure_and_event():
    """Verify that moving into next interval triggers candle closure event."""
    closed_events = []
    aggregator = CandleAggregator(timeframes=[Timeframe.M1])
    aggregator.register_on_candle_close(lambda c: closed_events.append(c))

    # Interval 1: 09:15:00 - 09:16:00
    t1 = Tick(symbol="NIFTY", ltp=25000.0, timestamp=datetime(2026, 10, 25, 9, 15, 10, tzinfo=timezone.utc), volume=10)
    aggregator.process_tick(t1)

    # Next interval: 09:16:05
    t2 = Tick(symbol="NIFTY", ltp=25050.0, timestamp=datetime(2026, 10, 25, 9, 16, 5, tzinfo=timezone.utc), volume=15)
    closed = aggregator.process_tick(t2)

    assert len(closed) == 1
    assert len(closed_events) == 1
    closed_c = closed[0]
    assert closed_c.is_complete is True
    assert closed_c.timestamp == datetime(2026, 10, 25, 9, 15, 0, tzinfo=timezone.utc)
    assert closed_c.close == 25000.0

    # Ring buffer should have 1 completed candle
    completed = aggregator.get_completed_candles("NIFTY", Timeframe.M1)
    assert len(completed) == 1


def test_resample_candles():
    """Verify resampling 1m candles into 5m candles."""
    candles_1m = []
    base_time = datetime(2026, 10, 25, 9, 15, 0, tzinfo=timezone.utc)

    # Create five 1m candles for 09:15 to 09:19
    from datetime import timedelta
    for i in range(5):
        c_time = base_time + timedelta(minutes=i)
        candles_1m.append(
            Candle(
                symbol="NIFTY",
                timeframe=Timeframe.M1,
                timestamp=c_time,
                open=25000.0 + i * 5,
                high=25000.0 + i * 5 + 10,
                low=25000.0 + i * 5 - 5,
                close=25000.0 + i * 5 + 4,
                volume=100,
                is_complete=True,
            )
        )

    resampled_5m = resample_candles(candles_1m, Timeframe.M5)
    assert len(resampled_5m) == 1
    c5 = resampled_5m[0]
    assert c5.timeframe == Timeframe.M5
    assert c5.open == 25000.0
    assert c5.high == 25000.0 + 4 * 5 + 10  # Max high of the 5th candle
    assert c5.low == 25000.0 - 5           # Min low of the 1st candle
    assert c5.close == 25000.0 + 4 * 5 + 4  # Close of the 5th candle
    assert c5.volume == 500


def test_candles_as_dataframe():
    """Verify Pandas DataFrame export format."""
    aggregator = CandleAggregator(timeframes=[Timeframe.M1])
    aggregator.load_historical_candles(
        "NIFTY",
        Timeframe.M1,
        [
            Candle(
                symbol="NIFTY",
                timeframe=Timeframe.M1,
                timestamp=datetime(2026, 10, 25, 9, 15, 0, tzinfo=timezone.utc),
                open=25000.0,
                high=25020.0,
                low=24990.0,
                close=25010.0,
                volume=150,
                is_complete=True,
            )
        ],
    )

    df = aggregator.get_candles_as_dataframe("NIFTY", Timeframe.M1)
    assert not df.empty
    assert "open" in df.columns
    assert "close" in df.columns
    assert df.iloc[0]["open"] == 25000.0
    assert df.iloc[0]["volume"] == 150
