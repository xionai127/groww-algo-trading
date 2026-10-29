"""Unit tests for Centralized Risk Management, Rules Engine, and Circuit Breaker."""

from datetime import datetime, timezone
import os
import tempfile
import pytest

from config import settings
from database.db import init_db
from risk_management.circuit_breaker import CircuitBreaker
from risk_management.manager import RiskManager
from risk_management.models import (
    BreakerState,
    PreTradeValidationRequest,
    RiskDecision,
    RuleViolation,
)
from risk_management.rules import (
    calculate_risk_and_sizing,
    check_capital_and_funds,
    check_daily_max_loss,
    check_market_hours,
    check_max_daily_trades,
    check_max_open_positions,
    check_options_buying_only,
    check_square_off_restriction,
)


@pytest.fixture(autouse=True)
def test_db(monkeypatch):
    """Isolate DB for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_path = tmp.name

    monkeypatch.setattr(settings, "database_path", temp_path)
    init_db()

    yield

    if os.path.exists(temp_path):
        os.remove(temp_path)


def test_rule_options_buying_only():
    """Verify options buying only rule blocks SELL / Short transactions."""
    # BUY CE and BUY PE are valid
    assert check_options_buying_only("BUY", "CE") is None
    assert check_options_buying_only("BUY", "PE") is None

    # SELL transactions must be rejected
    sell_res = check_options_buying_only("SELL", "CE")
    assert sell_res is not None
    assert sell_res[0] == RuleViolation.OPTIONS_BUYING_ONLY

    # Invalid direction
    invalid_dir = check_options_buying_only("BUY", "FUT")
    assert invalid_dir is not None
    assert invalid_dir[0] == RuleViolation.OPTIONS_BUYING_ONLY


def test_rule_market_hours():
    """Verify Indian trading hours (09:15 - 15:30 IST weekdays)."""
    # 2026-10-26 is Monday. 04:00 UTC = 09:30 IST (Open)
    open_dt = datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc)
    assert check_market_hours(open_dt, enforce=True) is None

    # 2026-10-26 03:00 UTC = 08:30 IST (Pre-market / closed)
    pre_dt = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
    res_pre = check_market_hours(pre_dt, enforce=True)
    assert res_pre is not None
    assert res_pre[0] == RuleViolation.MARKET_HOURS_CLOSED

    # 2026-10-25 is Sunday (Weekend closed)
    weekend_dt = datetime(2026, 10, 25, 5, 0, tzinfo=timezone.utc)
    res_weekend = check_market_hours(weekend_dt, enforce=True)
    assert res_weekend is not None
    assert res_weekend[0] == RuleViolation.MARKET_HOURS_CLOSED


def test_rule_square_off_restriction():
    """Verify new entries blocked past 15:10 IST."""
    # 2026-10-26 09:35 UTC = 15:05 IST (Allowed)
    allowed_dt = datetime(2026, 10, 26, 9, 35, tzinfo=timezone.utc)
    assert check_square_off_restriction(allowed_dt, enforce=True) is None

    # 2026-10-26 09:42 UTC = 15:12 IST (Blocked)
    blocked_dt = datetime(2026, 10, 26, 9, 42, tzinfo=timezone.utc)
    res = check_square_off_restriction(blocked_dt, enforce=True)
    assert res is not None
    assert res[0] == RuleViolation.SQUARE_OFF_RESTRICTION


def test_risk_manager_pre_trade_validation():
    """Verify RiskManager.validate_order checks all layers."""
    rm = RiskManager()
    valid_dt = datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc)

    # 1. Valid order
    req = PreTradeValidationRequest(
        symbol="NIFTY",
        direction="CE",
        quantity=25,
        estimated_price=40.0,
        stop_loss_pct=10.0,
        current_time=valid_dt,
    )
    account_state = {
        "cash": 9000.0,
        "daily_loss": 0.0,
        "daily_trades_count": 1,
        "open_positions_count": 0,
    }
    assessment = rm.validate_order(req, account_state=account_state)
    assert assessment.allowed is True
    assert assessment.decision == RiskDecision.APPROVED

    # 2. Blocked on Daily Loss Limit
    account_loss = {
        "cash": 9000.0,
        "daily_loss": 550.0,  # > ₹500
        "daily_trades_count": 2,
        "open_positions_count": 0,
    }
    loss_assessment = rm.validate_order(req, account_state=account_loss)
    assert loss_assessment.allowed is False
    assert loss_assessment.violation_type == RuleViolation.DAILY_LOSS_LIMIT
    assert loss_assessment.circuit_breaker_active is True

    # 3. Blocked on Max Positions Limit
    rm.circuit_breaker.reset()
    account_pos = {
        "cash": 9000.0,
        "daily_loss": 0.0,
        "daily_trades_count": 1,
        "open_positions_count": 1,  # Max 1 open position
    }
    pos_assessment = rm.validate_order(req, account_state=account_pos)
    assert pos_assessment.allowed is False
    assert pos_assessment.violation_type == RuleViolation.MAX_POSITIONS_LIMIT

    # 4. Blocked on Max Daily Trades
    account_trades = {
        "cash": 9000.0,
        "daily_loss": 0.0,
        "daily_trades_count": 5,  # Max 5 trades
        "open_positions_count": 0,
    }
    trades_assessment = rm.validate_order(req, account_state=account_trades)
    assert trades_assessment.allowed is False
    assert trades_assessment.violation_type == RuleViolation.MAX_DAILY_TRADES_LIMIT


def test_circuit_breaker_lifecycle_and_panic():
    """Verify tripping, resetting, and panic liquidation handlers."""
    rm = RiskManager()
    rm.circuit_breaker.reset()

    assert rm.circuit_breaker.is_active() is False
    assert rm.circuit_breaker.state == BreakerState.NORMAL

    # Register liquidation mock handler
    liquidated = []

    def mock_liquidate():
        liquidated.append(True)
        return {"positions_closed": 1}

    rm.register_liquidation_handler(mock_liquidate)

    # Panic square off
    res = rm.panic_square_off(reason="Emergency market crash test")
    assert res["circuit_breaker_tripped"] is True
    assert rm.circuit_breaker.is_active() is True
    assert len(liquidated) == 1

    # Reset
    rm.reset_circuit_breaker(reason="System restored")
    assert rm.circuit_breaker.is_active() is False


def test_risk_and_position_sizing():
    """Verify position sizing calculation and warnings against risk cap."""
    # Price = ₹50, SL = 10% (₹5/unit), Cap = ₹100
    # Max allowed quantity = 100 / 5 = 20
    risk, max_qty, is_exceeded = calculate_risk_and_sizing(
        estimated_price=50.0,
        quantity=25,
        stop_loss_pct=10.0,
        risk_per_trade_cap=100.0,
    )
    assert risk == 125.0
    assert max_qty == 20
    assert is_exceeded is True
