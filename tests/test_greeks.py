"""Unit tests for Black-Scholes option pricing and Greeks calculation."""

import pytest
from market_data.greeks import (
    black_scholes_price,
    calculate_greeks,
    calculate_implied_volatility,
    calculate_time_to_expiry,
)
from market_data.models import OptionType


def test_black_scholes_call_and_put_parity():
    """Verify standard European Black-Scholes pricing."""
    spot = 25000.0
    strike = 25000.0
    tte = 7.0 / 365.0  # 1 week to expiry
    vol = 0.15  # 15% IV
    rate = 0.065

    ce_price = black_scholes_price(spot, strike, tte, vol, OptionType.CE, rate)
    pe_price = black_scholes_price(spot, strike, tte, vol, OptionType.PE, rate)

    assert ce_price > 0, "Call price should be positive"
    assert pe_price > 0, "Put price should be positive"

    # Put-Call Parity: C - P = S - K * exp(-r * T)
    import math
    parity_diff = (ce_price - pe_price) - (spot - strike * math.exp(-rate * tte))
    assert abs(parity_diff) < 1e-4, f"Put-Call Parity violated: {parity_diff}"


def test_calculate_greeks_bounds():
    """Verify Delta, Gamma, Theta, and Vega are within theoretical bounds."""
    spot = 25000.0
    strike = 25000.0
    tte = 5.0 / 365.0
    vol = 0.14

    ce_greeks = calculate_greeks(spot, strike, tte, vol, OptionType.CE)
    pe_greeks = calculate_greeks(spot, strike, tte, vol, OptionType.PE)

    # ATM Call Delta ~ 0.5, Put Delta ~ -0.5
    assert 0.45 <= ce_greeks.delta <= 0.55
    assert -0.55 <= pe_greeks.delta <= -0.45

    # Gamma must be positive and equal for Call and Put
    assert ce_greeks.gamma > 0
    assert abs(ce_greeks.gamma - pe_greeks.gamma) < 1e-5

    # Theta should be negative (time decay hurts option buyer)
    assert ce_greeks.theta < 0
    assert pe_greeks.theta < 0

    # Vega must be positive (higher vol increases option value)
    assert ce_greeks.vega > 0
    assert pe_greeks.vega > 0


def test_calculate_implied_volatility_solver():
    """Verify solver recovers IV accurately from known market price."""
    spot = 25000.0
    strike = 25000.0
    tte = 14.0 / 365.0
    target_iv = 0.1650

    known_price = black_scholes_price(spot, strike, tte, target_iv, OptionType.CE)
    recovered_iv = calculate_implied_volatility(known_price, spot, strike, tte, OptionType.CE)

    assert recovered_iv is not None
    assert abs(recovered_iv - target_iv) < 0.001


def test_time_to_expiry_calculation():
    """Verify fractional year conversion from date string."""
    tte = calculate_time_to_expiry("2030-01-01")
    assert tte > 0, "Future date must have positive time to expiry"

    # Floor handles past or invalid gracefully
    tte_past = calculate_time_to_expiry("2020-01-01")
    assert tte_past > 0
