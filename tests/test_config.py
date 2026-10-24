"""Unit tests for configuration and risk management constants."""

import pytest
from config import ExecutionMode, settings


def test_default_capital_and_risk_configuration():
    """Verify default capital parameters match the master project specification."""
    assert settings.starting_capital == 10000.0, "Starting capital must be 10,000 INR"
    assert settings.max_daily_loss == 500.0, "Max daily loss must be 500 INR"
    assert settings.risk_per_trade == 100.0, "Risk per trade must default to 100 INR"
    assert settings.max_trades_per_day == 5
    assert settings.max_open_positions == 1


def test_trading_rules_restrictions():
    """Verify option selling is strictly disabled and directions are options buying only."""
    assert settings.allow_option_selling is False, "Option selling must be strictly disabled"
    assert "CE" in settings.allowed_directions
    assert "PE" in settings.allowed_directions
    assert "NIFTY" in settings.allowed_instruments
    assert "SENSEX" in settings.allowed_instruments


def test_execution_mode_default_and_isolation():
    """Verify application defaults to ANALYSIS mode and prevents unauthorized live mode."""
    assert settings.execution_mode == ExecutionMode.ANALYSIS, "Default mode must be ANALYSIS"
    assert settings.execution_mode != ExecutionMode.LIVE, "Live mode must never be default"


def test_market_timings():
    """Verify market hours and auto square-off parameters."""
    assert settings.market_open_time == "09:15"
    assert settings.market_close_time == "15:30"
    assert settings.square_off_time == "15:15"
