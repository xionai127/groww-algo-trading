"""Unit tests for the Backtesting Engine, OptionSimulator, and Performance Metrics."""

from datetime import datetime, timedelta, timezone
import pytest

from backtesting.data_feed import BacktestDataFeed, is_market_session
from backtesting.engine import BacktestEngine
from backtesting.metrics import calculate_backtest_metrics
from backtesting.models import (
    BacktestRequest,
    BacktestTrade,
    EquityPoint,
    ExitReason,
)
from backtesting.option_simulator import OptionSimulator
from backtesting.repository import BacktestRepository
from database.db import init_db
from strategies.builtin.ema_crossover import EMACrossoverStrategy
from strategies.models import StrikePreference


@pytest.fixture(autouse=True)
def setup_database():
    """Ensure database schema is fresh for each test run."""
    init_db()


def test_option_simulator_strike_resolution():
    """Test ATM, ITM, and OTM strike resolution for NIFTY and SENSEX."""
    sim_nifty = OptionSimulator(symbol="NIFTY")
    assert sim_nifty.strike_interval == 50.0

    # Spot 24530 -> ATM strike = 24550
    assert sim_nifty.resolve_strike(24530, "CE", StrikePreference.ATM) == 24550.0
    assert sim_nifty.resolve_strike(24530, "CE", StrikePreference.ITM_1) == 24500.0
    assert sim_nifty.resolve_strike(24530, "CE", StrikePreference.OTM_1) == 24600.0

    assert sim_nifty.resolve_strike(24530, "PE", StrikePreference.ATM) == 24550.0
    assert sim_nifty.resolve_strike(24530, "PE", StrikePreference.ITM_1) == 24600.0
    assert sim_nifty.resolve_strike(24530, "PE", StrikePreference.OTM_1) == 24500.0

    sim_sensex = OptionSimulator(symbol="SENSEX")
    assert sim_sensex.strike_interval == 100.0
    assert sim_sensex.resolve_strike(81480, "CE", StrikePreference.ATM) == 81500.0


def test_option_simulator_pricing_and_slippage():
    """Test Black-Scholes pricing and slippage mechanics."""
    sim = OptionSimulator(symbol="NIFTY", slippage_pct=1.0)
    now = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
    expiry = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)

    price_ce = sim.calculate_option_price(
        spot=25000,
        strike=25000,
        direction="CE",
        current_time=now,
        expiry_time=expiry,
        iv=0.15,
    )
    assert price_ce > 10.0

    # Test slippage
    buy_executed = sim.apply_slippage(price_ce, is_buy=True)
    assert buy_executed > price_ce
    assert buy_executed == round(price_ce * 1.01, 2)

    sell_executed = sim.apply_slippage(price_ce, is_buy=False)
    assert sell_executed < price_ce
    assert sell_executed == round(price_ce * 0.99, 2)


def test_option_simulator_charges_calculation():
    """Test Groww brokerage and Indian regulatory taxes."""
    sim = OptionSimulator(brokerage_per_order=20.0)
    brokerage, taxes, total_charges = sim.calculate_trade_charges(
        buy_price=100.0,
        sell_price=120.0,
        quantity=25,
    )
    assert brokerage == 40.0  # Buy + Sell = 20 + 20
    assert taxes > 0.0
    assert total_charges == round(brokerage + taxes, 2)


def test_metrics_calculation_accuracy():
    """Test precision of Sharpe, drawdown, and win-rate analytics."""
    initial_cap = 10000.0
    trades = [
        BacktestTrade(
            strategy_id="test",
            symbol="NIFTY",
            contract_symbol="NIFTY-25000-CE",
            direction="CE",
            strike_price=25000,
            expiry_date="2026-10-29",
            entry_time=datetime(2026, 10, 26, 9, 30, tzinfo=timezone.utc),
            entry_price=100.0,
            entry_spot=25000.0,
            exit_time=datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc),
            exit_price=120.0,
            exit_spot=25050.0,
            exit_reason=ExitReason.TARGET,
            quantity=25,
            gross_pnl=500.0,
            brokerage=40.0,
            taxes=10.0,
            net_pnl=450.0,
            return_pct=20.0,
        ),
        BacktestTrade(
            strategy_id="test",
            symbol="NIFTY",
            contract_symbol="NIFTY-25000-PE",
            direction="PE",
            strike_price=25000,
            expiry_date="2026-10-29",
            entry_time=datetime(2026, 10, 27, 9, 30, tzinfo=timezone.utc),
            entry_price=100.0,
            entry_spot=25000.0,
            exit_time=datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc),
            exit_price=90.0,
            exit_spot=25050.0,
            exit_reason=ExitReason.STOP_LOSS,
            quantity=25,
            gross_pnl=-250.0,
            brokerage=40.0,
            taxes=10.0,
            net_pnl=-300.0,
            return_pct=-10.0,
        ),
    ]

    equity_curve = [
        EquityPoint(timestamp=datetime(2026, 10, 26, 9, 30, tzinfo=timezone.utc), equity=10000.0, cash=10000.0, drawdown=0.0, drawdown_pct=0.0),
        EquityPoint(timestamp=datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc), equity=10450.0, cash=10450.0, drawdown=0.0, drawdown_pct=0.0),
        EquityPoint(timestamp=datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc), equity=10150.0, cash=10150.0, drawdown=300.0, drawdown_pct=2.87),
    ]

    daily_pnl = {"2026-10-26": 450.0, "2026-10-27": -300.0}

    summary = calculate_backtest_metrics(
        initial_capital=initial_cap,
        trades=trades,
        equity_curve=equity_curve,
        daily_pnl=daily_pnl,
    )

    assert summary.total_trades == 2
    assert summary.winning_trades == 1
    assert summary.losing_trades == 1
    assert summary.win_rate == 50.0
    assert summary.net_pnl == 150.0
    assert summary.final_capital == 10150.0
    assert summary.profit_factor == 1.5  # 450 / 300
    assert summary.max_drawdown == 300.0


def test_backtest_engine_run():
    """Test full BacktestEngine execution using generated candles."""
    now = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
    from_date = now - timedelta(days=7)

    req = BacktestRequest(
        strategy_id="builtin_ema_crossover",
        symbol="NIFTY",
        timeframe="5m",
        from_date=from_date,
        to_date=now,
        initial_capital=10000.0,
    )

    engine = BacktestEngine()
    result = engine.run(req, persist=True)

    assert result.strategy_id == "builtin_ema_crossover"
    assert result.symbol == "NIFTY"
    assert result.summary.initial_capital == 10000.0
    assert len(result.equity_curve) > 0

    # Check persistence
    fetched = BacktestRepository.get(result.run_id)
    assert fetched is not None
    assert fetched.run_id == result.run_id
    assert fetched.summary.total_trades == result.summary.total_trades


def test_backtest_repository_crud():
    """Test repository save, retrieve, list, and delete."""
    now = datetime.now(timezone.utc)
    req = BacktestRequest(
        strategy_id="builtin_supertrend_rsi",
        symbol="NIFTY",
        timeframe="5m",
        from_date=now - timedelta(days=3),
        to_date=now,
    )

    engine = BacktestEngine()
    res = engine.run(req, persist=True)

    runs = BacktestRepository.list_all(limit=10)
    assert any(r["id"] == res.run_id for r in runs)

    del_res = BacktestRepository.delete(res.run_id)
    assert del_res is True

    assert BacktestRepository.get(res.run_id) is None
