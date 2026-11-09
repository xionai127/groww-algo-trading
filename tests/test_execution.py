"""Unit tests for GrowwBrokerClient and LiveExecutionEngine."""

from datetime import datetime, time, timedelta, timezone
import os
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from config import ExecutionMode, settings
from database.db import get_db_connection, init_db
from execution.broker_client import GrowwBrokerClient
from execution.engine import LiveExecutionEngine
from execution.models import LiveOrderRequest, LivePosition
from market_data.models import OptionType
from paper_trading.models import OrderStatus, OrderType, TransactionType
from risk_management import risk_manager


@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    """Provide a fresh isolated SQLite database for each test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_db_path = tmp.name

    monkeypatch.setattr(settings, "database_path", temp_db_path)
    init_db()
    risk_manager.circuit_breaker.reset(reason="Test setup", mode="LIVE")

    yield

    if os.path.exists(temp_db_path):
        os.remove(temp_db_path)


class MockGrowwApi:
    """Mock for growwapi.GrowwAPI client."""

    def __init__(self, token="test_token"):
        self.token = token
        self.orders = []

    def get_user_profile(self, timeout=None):
        return {"user_name": "Test Trader", "email": "trader@groww.in"}

    def get_available_margin_details(self, timeout=None):
        return {"available_margin": 25000.50}

    def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return {
            "groww_order_id": "GROWW123456",
            "order_status": "SUCCESS",
            "trading_symbol": kwargs.get("trading_symbol"),
        }

    def cancel_order(self, groww_order_id, segment="FNO", timeout=None):
        return {"groww_order_id": groww_order_id, "status": "CANCELLED"}

    def get_order_detail(self, segment, groww_order_id, timeout=None):
        return {"groww_order_id": groww_order_id, "status": "FILLED"}

    def get_positions_for_user(self, segment=None, timeout=None):
        return [{"trading_symbol": "NIFTY24NOV24500CE", "net_quantity": 25}]

    def get_order_list(self, segment=None, timeout=None):
        return [{"groww_order_id": "GROWW123456", "status": "FILLED"}]


@pytest.mark.anyio
async def test_broker_client_auth_with_token():
    """Verify broker client connection when access token is provided."""
    client = GrowwBrokerClient()
    mock_api = MockGrowwApi(token="custom_token")
    client.set_api_instance(mock_api)

    assert client.is_connected is True
    test_res = await client.test_connection()
    assert test_res["is_connected"] is True
    assert test_res["profile"]["user_name"] == "Test Trader"


@pytest.mark.anyio
async def test_broker_client_margin_and_orders():
    """Verify margin extraction and order placement with mock API."""
    client = GrowwBrokerClient()
    mock_api = MockGrowwApi()
    client.set_api_instance(mock_api)

    margin = await client.get_available_margin()
    assert margin == 25000.50

    res = await client.place_option_buy_order(
        trading_symbol="NIFTY24NOV24500CE",
        quantity=25,
        order_type="MARKET",
        product="MIS",
    )
    assert res["groww_order_id"] == "GROWW123456"
    assert len(mock_api.orders) == 1
    assert mock_api.orders[0]["transaction_type"] == "BUY"

    # Sell exit order
    res_exit = await client.place_option_sell_exit_order(
        trading_symbol="NIFTY24NOV24500CE",
        quantity=25,
    )
    assert len(mock_api.orders) == 2
    assert mock_api.orders[1]["transaction_type"] == "SELL"


@pytest.mark.anyio
async def test_live_engine_rejects_when_not_in_live_mode(monkeypatch):
    """Engine must strictly reject orders if platform is not in ExecutionMode.LIVE."""
    monkeypatch.setattr(settings, "execution_mode", ExecutionMode.PAPER)
    mock_api = MockGrowwApi()
    client = GrowwBrokerClient(api_instance=mock_api)
    engine = LiveExecutionEngine(client=client, enforce_market_hours=False)

    req = LiveOrderRequest(symbol="NIFTY", direction="CE")
    order, pos = await engine.submit_order(req)

    assert order.status == OrderStatus.REJECTED
    assert "not LIVE" in order.rejection_reason
    assert pos is None


@pytest.mark.anyio
async def test_live_engine_rejects_when_breaker_tripped(monkeypatch):
    """Engine must reject orders when circuit breaker is active."""
    monkeypatch.setattr(settings, "execution_mode", ExecutionMode.LIVE)
    mock_api = MockGrowwApi()
    client = GrowwBrokerClient(api_instance=mock_api)
    engine = LiveExecutionEngine(client=client, enforce_market_hours=False)

    risk_manager.circuit_breaker.trip(reason="Manual halt for test", auto=False, mode="LIVE")

    req = LiveOrderRequest(symbol="NIFTY", direction="CE")
    order, pos = await engine.submit_order(req)

    assert order.status == OrderStatus.REJECTED
    assert "Circuit breaker is tripped" in order.rejection_reason
    assert pos is None


@pytest.mark.anyio
async def test_live_engine_order_placement_and_fill(monkeypatch):
    """Engine places real option buying order, records position, and persists to DB."""
    monkeypatch.setattr(settings, "execution_mode", ExecutionMode.LIVE)
    mock_api = MockGrowwApi()
    client = GrowwBrokerClient(api_instance=mock_api)
    engine = LiveExecutionEngine(client=client, enforce_market_hours=False)

    req = LiveOrderRequest(
        symbol="NIFTY",
        direction="CE",
        quantity=25,
        target_pct=20.0,
        stop_loss_pct=10.0,
    )
    order, pos = await engine.submit_order(req, spot_price=24500.0, quote_price=100.0)

    assert order.status == OrderStatus.FILLED
    assert order.broker_order_id == "GROWW123456"
    assert pos is not None
    assert pos.symbol == "NIFTY"
    assert pos.entry_price == 100.0
    assert pos.target_price == 120.0
    assert pos.stop_loss_price == 90.0
    assert pos.id in engine.open_positions
    assert engine.daily_trades_count == 1

    # Verify database persistence
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM live_positions WHERE id = ?", (pos.id,))
        row = cursor.fetchone()
        assert row is not None
        assert row["is_open"] == 1
        assert row["contract_symbol"] == pos.contract_symbol


@pytest.mark.anyio
async def test_live_engine_position_close_and_journal(monkeypatch):
    """Engine squares off position, executes sell order, and logs trade journal entry."""
    monkeypatch.setattr(settings, "execution_mode", ExecutionMode.LIVE)
    mock_api = MockGrowwApi()
    client = GrowwBrokerClient(api_instance=mock_api)
    engine = LiveExecutionEngine(client=client, enforce_market_hours=False)

    req = LiveOrderRequest(symbol="NIFTY", direction="CE", quantity=25)
    order, pos = await engine.submit_order(req, spot_price=24500.0, quote_price=100.0)
    assert pos is not None

    closed = await engine.close_position(pos.id, reason="MANUAL_CLOSE", exit_price=120.0)
    assert closed.is_open is False
    assert closed.exit_price == 120.0
    assert closed.net_pnl > 0.0
    assert len(engine.open_positions) == 0

    # Verify journal entry was written
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trade_journal WHERE trade_id = ?", (pos.id,))
        row = cursor.fetchone()
        assert row is not None
        assert row["mode"] == "LIVE"
        assert row["exit_reason"] == "MANUAL_CLOSE"


@pytest.mark.anyio
async def test_live_engine_panic_liquidation(monkeypatch):
    """Engine panic liquidation closes all open live positions."""
    monkeypatch.setattr(settings, "execution_mode", ExecutionMode.LIVE)
    mock_api = MockGrowwApi()
    client = GrowwBrokerClient(api_instance=mock_api)
    engine = LiveExecutionEngine(client=client, enforce_market_hours=False)

    req = LiveOrderRequest(symbol="NIFTY", direction="PE", quantity=25)
    await engine.submit_order(req, spot_price=24500.0, quote_price=80.0)
    assert len(engine.open_positions) == 1

    liquidated = await engine.close_all_live_positions(reason="PANIC_TEST")
    assert len(liquidated) == 1
    assert len(engine.open_positions) == 0
    assert liquidated[0].exit_reason == "PANIC_TEST"


@pytest.mark.anyio
async def test_live_engine_daily_loss_trip_breaker(monkeypatch):
    """Daily loss cap breach must trip circuit breaker and block new entries."""
    monkeypatch.setattr(settings, "execution_mode", ExecutionMode.LIVE)
    monkeypatch.setattr(settings, "max_daily_loss", 200.0)
    mock_api = MockGrowwApi()
    client = GrowwBrokerClient(api_instance=mock_api)
    engine = LiveExecutionEngine(client=client, enforce_market_hours=False)

    # Buy at 100, sell at 50 (loss of 50 * 25 = 1250, breaching 200 cap)
    req = LiveOrderRequest(symbol="NIFTY", direction="CE", quantity=25)
    order, pos = await engine.submit_order(req, spot_price=24500.0, quote_price=100.0)
    await engine.close_position(pos.id, reason="STOP_LOSS", exit_price=50.0)

    assert engine.daily_loss >= 200.0
    assert engine.is_trading_blocked is True
    assert risk_manager.circuit_breaker.is_tripped is True
