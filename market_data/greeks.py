"""Black-Scholes option pricing and Greeks engine for Indian options."""

import math
from datetime import datetime, timezone
from typing import Optional, Tuple
from market_data.models import OptionGreek, OptionType

# Default Indian Risk-Free Rate (RBI repo/treasury proxy ~6.5%)
DEFAULT_RISK_FREE_RATE = 0.065


def _norm_cdf(x: float) -> float:
    """Standard Normal Cumulative Distribution Function using math.erf."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _norm_pdf(x: float) -> float:
    """Standard Normal Probability Density Function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def calculate_time_to_expiry(expiry_date_str: str, current_time: Optional[datetime] = None) -> float:
    """Calculates time to expiry in years.

    Args:
        expiry_date_str: ISO date string 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'
        current_time: Reference datetime (defaults to now UTC)

    Returns:
        Fractional years to expiry (min 1 minute / 0.0001 year)
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    # Groww / NSE expiry time is 15:30 IST (10:00 UTC) on the expiry date
    try:
        if "T" in expiry_date_str or " " in expiry_date_str:
            clean_str = expiry_date_str.replace("Z", "+00:00")
            expiry_dt = datetime.fromisoformat(clean_str)
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
        else:
            # Date only: Set to 15:30 IST (10:00 UTC)
            expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d")
            expiry_dt = datetime(
                year=expiry_date.year,
                month=expiry_date.month,
                day=expiry_date.day,
                hour=10,
                minute=0,
                second=0,
                tzinfo=timezone.utc,
            )
    except Exception:
        # Fallback to 1 day minimum
        return 1.0 / 365.0

    delta_seconds = (expiry_dt - current_time).total_seconds()
    # Floor at 60 seconds to avoid division by zero
    seconds = max(delta_seconds, 60.0)
    return seconds / (365.0 * 24.0 * 3600.0)


def black_scholes_price(
    spot: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    option_type: OptionType,
    rate: float = DEFAULT_RISK_FREE_RATE,
) -> float:
    """Calculates theoretical Black-Scholes price for European options."""
    if spot <= 0 or strike <= 0 or time_to_expiry <= 0 or volatility <= 0:
        return 0.0

    t_sqrt = math.sqrt(time_to_expiry)
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility * volatility) * time_to_expiry) / (volatility * t_sqrt)
    d2 = d1 - volatility * t_sqrt

    if option_type == OptionType.CE:
        price = spot * _norm_cdf(d1) - strike * math.exp(-rate * time_to_expiry) * _norm_cdf(d2)
    else:
        price = strike * math.exp(-rate * time_to_expiry) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)

    return max(0.0, price)


def calculate_implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    time_to_expiry: float,
    option_type: OptionType,
    rate: float = DEFAULT_RISK_FREE_RATE,
    max_iterations: int = 100,
    precision: float = 1e-4,
) -> Optional[float]:
    """Calculates Implied Volatility (IV) using Bisection search."""
    if market_price <= 0 or spot <= 0 or strike <= 0 or time_to_expiry <= 0:
        return None

    # Intrinsic value bounds
    intrinsic = max(0.0, spot - strike) if option_type == OptionType.CE else max(0.0, strike - spot)
    if market_price < intrinsic:
        return None

    low_vol = 0.001
    high_vol = 4.0  # 400% max volatility

    for _ in range(max_iterations):
        mid_vol = (low_vol + high_vol) / 2.0
        price_at_mid = black_scholes_price(spot, strike, time_to_expiry, mid_vol, option_type, rate)
        diff = price_at_mid - market_price

        if abs(diff) < precision:
            return round(mid_vol, 4)

        if diff > 0:
            high_vol = mid_vol
        else:
            low_vol = mid_vol

    return round((low_vol + high_vol) / 2.0, 4)


def calculate_greeks(
    spot: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    option_type: OptionType,
    rate: float = DEFAULT_RISK_FREE_RATE,
) -> OptionGreek:
    """Calculates Delta, Gamma, daily Theta, and Vega."""
    if spot <= 0 or strike <= 0 or time_to_expiry <= 0 or volatility <= 0:
        return OptionGreek()

    t_sqrt = math.sqrt(time_to_expiry)
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility * volatility) * time_to_expiry) / (volatility * t_sqrt)
    d2 = d1 - volatility * t_sqrt

    pdf_d1 = _norm_pdf(d1)

    # Gamma (same for Call & Put)
    gamma = pdf_d1 / (spot * volatility * t_sqrt)

    # Vega (sensitivity to 1% move in IV, i.e. 0.01)
    vega = (spot * pdf_d1 * t_sqrt) / 100.0

    if option_type == OptionType.CE:
        delta = _norm_cdf(d1)
        # Annual theta
        theta_annual = -(spot * pdf_d1 * volatility) / (2.0 * t_sqrt) - rate * strike * math.exp(-rate * time_to_expiry) * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1.0
        theta_annual = -(spot * pdf_d1 * volatility) / (2.0 * t_sqrt) + rate * strike * math.exp(-rate * time_to_expiry) * _norm_cdf(-d2)

    # Daily theta in INR
    theta_daily = theta_annual / 365.0

    return OptionGreek(
        iv=round(volatility, 4),
        delta=round(delta, 4),
        gamma=round(gamma, 6),
        theta=round(theta_daily, 2),
        vega=round(vega, 2),
    )
