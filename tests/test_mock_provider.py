"""Unit tests for MockMarketDataProvider simulation and option chains."""

import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from market_data.models import OptionType, Timeframe
from market_data.provider import MockMarketDataProvider


@pytest.mark.anyio
async def test_mock_provider_connection_and_ltp():
    """Verify provider connect, LTP fetch, and price overrides."""
    provider = MockMarketDataProvider()
    connected = await provider.connect()
    assert connected is True
    assert provider.is_connected is True

    nifty_ltp = await provider.get_ltp("NIFTY")
    assert nifty_ltp > 20000

    sensex_ltp = await provider.get_ltp("SENSEX")
    assert sensex_ltp > 70000

    # Test override
    provider.set_price("NIFTY", 25555.0)
    assert await provider.get_ltp("NIFTY") == 25555.0

    await provider.disconnect()
    assert provider.is_connected is False


@pytest.mark.anyio
async def test_mock_provider_historical_candles():
    """Verify historical candles generation with realistic wicks."""
    provider = MockMarketDataProvider()
    await provider.connect()

    start = datetime(2026, 10, 25, 9, 15, tzinfo=timezone.utc)
    end = datetime(2026, 10, 25, 10, 15, tzinfo=timezone.utc)

    candles = await provider.get_historical_candles("NIFTY", Timeframe.M5, start, end)
    assert len(candles) == 12  # 60 mins / 5 mins = 12 candles

    for c in candles:
        assert c.high >= c.open
        assert c.high >= c.close
        assert c.low <= c.open
        assert c.low <= c.close
        assert c.volume > 0

    await provider.disconnect()


@pytest.mark.anyio
async def test_mock_option_chain_structure():
    """Verify option chain generation, strike count, ATM detection and Greeks."""
    provider = MockMarketDataProvider()
    provider.set_price("NIFTY", 25020.0)

    chain = await provider.get_option_chain("NIFTY")
    assert chain.underlying == "NIFTY"
    assert chain.spot_price == 25020.0
    assert len(chain.strikes) == 11

    # ATM strike should be 25000 (nearest 50 interval)
    atm = chain.get_atm_strike(50.0)
    assert atm == 25000.0

    atm_str = str(int(atm))
    assert atm_str in chain.calls
    assert atm_str in chain.puts

    ce_quote = chain.calls[atm_str]
    pe_quote = chain.puts[atm_str]

    assert ce_quote.option_type == OptionType.CE
    assert pe_quote.option_type == OptionType.PE
    assert ce_quote.greeks.delta is not None
    assert pe_quote.greeks.delta is not None
