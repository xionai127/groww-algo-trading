"""Unit tests for SQLite historical candle caching and DataFrame export."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
import pytest
from config import settings
from database.db import init_db
from market_data.history_manager import HistoricalDataManager, init_candle_table
from market_data.models import Candle, Timeframe
from market_data.provider import MockMarketDataProvider


@pytest.fixture(autouse=True)
def temp_database(monkeypatch):
    """Run tests against temporary SQLite DB."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_path = tmp.name

    monkeypatch.setattr(settings, "database_path", temp_path)
    init_db()
    init_candle_table()

    yield temp_path

    if os.path.exists(temp_path):
        os.remove(temp_path)


@pytest.mark.anyio
async def test_history_cache_save_and_retrieve():
    """Verify candle upsert and cache retrieval from database."""
    provider = MockMarketDataProvider()
    manager = HistoricalDataManager(provider)

    t1 = datetime(2026, 10, 25, 9, 15, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 25, 9, 16, tzinfo=timezone.utc)

    candles = [
        Candle(symbol="NIFTY", timeframe=Timeframe.M1, timestamp=t1, open=25000, high=25010, low=24995, close=25005, volume=100, is_complete=True),
        Candle(symbol="NIFTY", timeframe=Timeframe.M1, timestamp=t2, open=25005, high=25020, low=25000, close=25015, volume=120, is_complete=True),
    ]

    saved_count = manager.save_candles(candles)
    assert saved_count == 2

    # Query cached
    cached = manager.get_cached_candles("NIFTY", Timeframe.M1, t1, t2)
    assert len(cached) == 2
    assert cached[0].open == 25000
    assert cached[1].close == 25015

    # Test DataFrame export
    df = await manager.get_candles_dataframe("NIFTY", Timeframe.M1, t1, t2)
    assert len(df) == 2
    assert "close" in df.columns
