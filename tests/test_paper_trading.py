"""Unit tests for Realistic Paper Trading Engine, Simulator, and Risk Controls."""

from datetime import datetime, timedelta, timezone
import os
import tempfile
import pytest

from config import settings
from database.db import init_db
from paper_trading.engine import PaperTradingEngine
from paper_trading.execution import PaperExecutionSimulator
from paper_trading.models import ExitReason, OrderStatus, PaperOrderRequest
from strategies.models import Signal, SignalAction, StrikePreference


@pytest.fixture(autouse=True)
def test_env(monkeypatch):
    """Isolate DB for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_path = tmp.name

    monkeypatch.setattr(settings, "database_path", temp_path)
    init_db()

    yield

    if os.path.exists(temp_path):
        os.remove(temp_path)


def test_paper_simulator_strike_and_expiry():
    """Verify strike resolution and weekly expiry calculation."""
    sim = PaperExecutionSimulator()

    # NIFTY Strike resolution
    assert sim.resolve_strike("NIFTY", 24980, "CE", StrikePreference.ATM) == 25000.0
    assert sim.resolve_strike("NIFTY", 24980, "CE", StrikePreference.ITM_1) == 24950.0
    assert sim.resolve_strike("NIFTY", 24980, "CE", StrikePreference.OTM_1) == 25050.0

    # SENSEX Strike resolution
    assert sim.resolve_strike("SENSEX", 81480, "PE", StrikePreference.ATM) == 81500.0
    assert sim.resolve_strike("SENSEX", 81480, "PE", StrikePreference.ITM_1) == 81600.0

    # Expiry calculation (Thursday for NIFTY, Friday for SENSEX)
    ref_dt = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)  # Monday
    expiry_dt_nifty, expiry_str_nifty = sim.get_upcoming_expiry("NIFTY", ref_dt)
    assert expiry_str_nifty == "2026-10-29"  # Thursday

    expiry_dt_sensex, expiry_str_sensex = sim.get_upcoming_expiry("SENSEX", ref_dt)
    assert expiry_str_sensex == "2026-10-30"  # Friday


def test_paper_simulator_slippage_and_charges():
    """Verify slippage adverse shift and Indian tax/brokerage formulas."""
    sim = PaperExecutionSimulator(default_slippage_pct=1.0)

    # Buy slippage (price increases)
    buy_exec = sim.apply_slippage(100.0, is_buy=True)
    assert buy_exec == 101.0

    # Sell slippage (price decreases)
    sell_exec = sim.apply_slippage(100.0, is_buy=False)
    assert sell_exec == 99.0

    # Buy charges (no STT on options buy)
    b_brok, b_tax, b_total = sim.calculate_order_charges("BUY", price=100.0, quantity=25)
    assert b_brok == 20.0
    assert b_tax > 0.0
    assert b_total == round(b_brok + b_tax, 2)

    # Sell charges (includes STT 0.0625% on sell turnover)
    s_brok, s_tax, s_total = sim.calculate_order_charges("SELL", price=100.0, quantity=25)
    assert s_brok == 20.0
    assert s_tax > b_tax  # Sell tax higher due to STT


def test_paper_engine_order_fill_and_position():
    """Verify successful buy order execution, position creation, and balance deduction."""
    engine = PaperTradingEngine()
    req = PaperOrderRequest(
        symbol="NIFTY",
        direction="CE",
        strike_preference=StrikePreference.ATM,
        target_pct=20.0,
        stop_loss_pct=10.0,
    )

    now = datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc)  # 10:00 IST (valid trading hours)
    order, pos = engine.submit_order(req, spot_price=25000.0, quote_price=100.0, dt=now)

    assert order.status == OrderStatus.FILLED
    assert pos is not None
    assert pos.is_open is True
    assert pos.symbol == "NIFTY"
    assert pos.quantity == 25
    assert pos.entry_price > 100.0  # Slippage applied

    # Check capital deduction
    acc = engine.get_account_summary()
    assert acc.cash < 10000.0
    assert acc.allocated_margin > 0.0
    assert acc.open_positions_count == 1
    assert acc.daily_trades_count == 1


def test_paper_engine_max_open_positions_risk():
    """Verify hard risk block: max 1 open position."""
    engine = PaperTradingEngine()
    req = PaperOrderRequest(symbol="NIFTY", direction="CE")
    now = datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc)

    # First order succeeds
    order1, pos1 = engine.submit_order(req, spot_price=25000.0, quote_price=50.0, dt=now)
    assert order1.status == OrderStatus.FILLED

    # Second order rejected due to max open positions = 1
    order2, pos2 = engine.submit_order(req, spot_price=25000.0, quote_price=50.0, dt=now)
    assert order2.status == OrderStatus.REJECTED
    assert "Max open positions" in order2.rejection_reason
    assert pos2 is None


def test_paper_engine_max_daily_trades_risk():
    """Verify hard risk block: max 5 trades per day."""
    engine = PaperTradingEngine()
    now = datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc)

    # Execute 5 complete trades (Buy then Close)
    for i in range(5):
        req = PaperOrderRequest(symbol="NIFTY", direction="CE")
        o, p = engine.submit_order(req, spot_price=25000.0, quote_price=20.0, dt=now)
        assert o.status == OrderStatus.FILLED
        engine.close_position(p.id, dt=now)

    # 6th trade must be rejected
    req6 = PaperOrderRequest(symbol="NIFTY", direction="CE")
    o6, p6 = engine.submit_order(req6, spot_price=25000.0, quote_price=20.0, dt=now)
    assert o6.status == OrderStatus.REJECTED
    assert "Max daily trades" in o6.rejection_reason


def test_paper_engine_daily_loss_limit_risk():
    """Verify hard risk stop when cumulative daily loss breaches ₹500."""
    engine = PaperTradingEngine()
    now = datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc)

    req = PaperOrderRequest(symbol="NIFTY", direction="CE")
    # Buy @ 100, sell @ 70 -> loss = 30 * 25 = ₹750 loss (> ₹500 limit)
    o, p = engine.submit_order(req, spot_price=25000.0, quote_price=100.0, dt=now)
    assert o.status == OrderStatus.FILLED

    closed = engine.close_position(p.id, reason="MANUAL", exit_price=70.0, dt=now)
    assert closed.net_pnl < -500.0

    acc = engine.get_account_summary()
    assert acc.daily_loss >= 500.0
    assert acc.is_trading_blocked is True

    # Subsequent orders must be rejected
    o2, p2 = engine.submit_order(req, spot_price=25000.0, quote_price=100.0, dt=now)
    assert o2.status == OrderStatus.REJECTED
    assert "Daily max loss" in o2.rejection_reason or "Trading blocked" in o2.rejection_reason


def test_paper_engine_target_and_stop_loss_mtm():
    """Verify automated target profit and stop loss exits on MTM updates."""
    engine = PaperTradingEngine()
    now = datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc)
    req = PaperOrderRequest(
        symbol="NIFTY",
        direction="CE",
        target_pct=20.0,
        stop_loss_pct=10.0,
    )
    o, p = engine.submit_order(req, spot_price=25000.0, quote_price=100.0, dt=now)
    assert o.status == OrderStatus.FILLED

    # Feed price above target (entry is ~100.5 with slippage, target is ~120.6)
    closed = engine.update_market_price("NIFTY", spot=25100.0, dt=now, option_quote_price=130.0)
    assert len(closed) == 1
    assert closed[0].exit_reason == ExitReason.TARGET.value
    assert closed[0].net_pnl > 0.0
    assert len(engine.open_positions) == 0


def test_paper_engine_auto_square_off():
    """Verify auto liquidation when time reaches 15:15 IST (09:45 UTC)."""
    engine = PaperTradingEngine()
    # 10:00 IST (04:30 UTC) - enter trade
    t_entry = datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc)
    req = PaperOrderRequest(symbol="NIFTY", direction="CE")
    o, p = engine.submit_order(req, spot_price=25000.0, quote_price=50.0, dt=t_entry)
    assert o.status == OrderStatus.FILLED

    # 15:16 IST (09:46 UTC) - auto square-off
    t_sqoff = datetime(2026, 10, 26, 9, 46, tzinfo=timezone.utc)
    closed = engine.update_market_price("NIFTY", spot=25000.0, dt=t_sqoff, option_quote_price=52.0)
    assert len(closed) == 1
    assert closed[0].exit_reason == ExitReason.SQUARE_OFF.value
    assert len(engine.open_positions) == 0


def test_paper_engine_strategy_signal_handling():
    """Verify StrategyManager Signal routing into paper engine."""
    engine = PaperTradingEngine()
    sig_entry = Signal(
        strategy_id="test_strat",
        action=SignalAction.ENTRY,
        underlying="NIFTY",
        direction="CE",
        strike_preference=StrikePreference.ATM,
        spot_reference_price=25000.0,
    )

    engine.on_strategy_signal(sig_entry)
    assert len(engine.open_positions) == 1

    # Signal exit
    sig_exit = Signal(
        strategy_id="test_strat",
        action=SignalAction.EXIT,
        underlying="NIFTY",
        direction="CE",
        spot_reference_price=25050.0,
    )
    engine.on_strategy_signal(sig_exit)
    assert len(engine.open_positions) == 0


def test_paper_engine_reset_account():
    """Verify virtual account reset restores initial capital and clears positions."""
    engine = PaperTradingEngine()
    req = PaperOrderRequest(symbol="NIFTY", direction="CE")
    now = datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc)
    engine.submit_order(req, spot_price=25000.0, quote_price=100.0, dt=now)
    assert len(engine.open_positions) == 1

    acc = engine.reset_account(initial_capital=10000.0)
    assert acc.cash == 10000.0
    assert acc.total_equity == 10000.0
    assert acc.open_positions_count == 0
    assert len(engine.open_positions) == 0
