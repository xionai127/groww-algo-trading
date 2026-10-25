"""Unit tests for option strike selector (ATM, ITM, OTM for CE & PE)."""

from datetime import datetime, timezone
import pytest
from market_data.models import OptionChain, OptionGreek, OptionQuote, OptionType
from strategies.models import StrikePreference
from strategies.strike_selector import StrikeSelector


@pytest.fixture
def mock_chain():
    """Builds a test option chain around NIFTY spot 25010 (ATM 25000)."""
    spot = 25010.0
    strikes = [24800.0, 24850.0, 24900.0, 24950.0, 25000.0, 25050.0, 25100.0, 25150.0, 25200.0]

    calls = {}
    puts = {}

    for k in strikes:
        k_str = str(int(k))
        calls[k_str] = OptionQuote(
            symbol=f"NIFTY26OCT{k_str}CE",
            underlying="NIFTY",
            strike=k,
            option_type=OptionType.CE,
            expiry="2026-10-29",
            ltp=max(5.0, 25010.0 - k + 100.0),
            greeks=OptionGreek(delta=0.5),
        )
        puts[k_str] = OptionQuote(
            symbol=f"NIFTY26OCT{k_str}PE",
            underlying="NIFTY",
            strike=k,
            option_type=OptionType.PE,
            expiry="2026-10-29",
            ltp=max(5.0, k - 25010.0 + 100.0),
            greeks=OptionGreek(delta=-0.5),
        )

    return OptionChain(
        underlying="NIFTY",
        spot_price=spot,
        expiry="2026-10-29",
        strikes=strikes,
        calls=calls,
        puts=puts,
    )


def test_select_atm_strike(mock_chain):
    """ATM strike should be 25000 for spot 25010."""
    ce_quote = StrikeSelector.select_strike(mock_chain, "CE", StrikePreference.ATM)
    pe_quote = StrikeSelector.select_strike(mock_chain, "PE", StrikePreference.ATM)

    assert ce_quote is not None
    assert ce_quote.strike == 25000.0
    assert ce_quote.option_type == OptionType.CE

    assert pe_quote is not None
    assert pe_quote.strike == 25000.0
    assert pe_quote.option_type == OptionType.PE


def test_select_call_itm_and_otm(mock_chain):
    """For Calls, ITM_1 is lower (24950), OTM_1 is higher (25050)."""
    itm1_ce = StrikeSelector.select_strike(mock_chain, "CE", StrikePreference.ITM_1)
    otm1_ce = StrikeSelector.select_strike(mock_chain, "CE", StrikePreference.OTM_1)

    assert itm1_ce is not None
    assert itm1_ce.strike == 24950.0

    assert otm1_ce is not None
    assert otm1_ce.strike == 25050.0


def test_select_put_itm_and_otm(mock_chain):
    """For Puts, ITM_1 is higher (25050), OTM_1 is lower (24950)."""
    itm1_pe = StrikeSelector.select_strike(mock_chain, "PE", StrikePreference.ITM_1)
    otm1_pe = StrikeSelector.select_strike(mock_chain, "PE", StrikePreference.OTM_1)

    assert itm1_pe is not None
    assert itm1_pe.strike == 25050.0

    assert otm1_pe is not None
    assert otm1_pe.strike == 24950.0
