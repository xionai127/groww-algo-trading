"""Integration tests for execution router endpoints."""

import os
import tempfile
import pytest
from starlette.testclient import TestClient

from app import app
from config import ExecutionMode, settings
from database.db import init_db
from execution.broker_client import broker_client
from execution.engine import live_engine
from risk_management import risk_manager


class MockGrowwApi:
    """Mock for GrowwAPI client."""

    def __init__(self, token="mock_token"):
        self.token = token

    def get_user_profile(self, timeout=None):
        return {"user_name": "Test Trader", "email": "trader@groww.in"}

    def get_available_margin_details(self, timeout=None):
        return {"available_margin": 15000.0}

    def place_order(self, **kwargs):
        return {
            "groww_order_id": "GROWW_API_999",
            "order_status": "SUCCESS",
            "trading_symbol": kwargs.get("trading_symbol"),
        }

    def cancel_order(self, groww_order_id, segment="FNO", timeout=None):
        return {"groww_order_id": groww_order_id, "status": "CANCELLED"}

    def get_positions_for_user(self, segment=None, timeout=None):
        return []

    def get_order_list(self, segment=None, timeout=None):
        return []


@pytest.fixture(autouse=True)
def setup_api_test(monkeypatch):
    """Setup isolated database and mock broker client."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_db_path = tmp.name

    monkeypatch.setattr(settings, "database_path", temp_db_path)
    monkeypatch.setattr(settings, "execution_mode", ExecutionMode.ANALYSIS)
    init_db()
    risk_manager.circuit_breaker.reset(reason="Test reset", mode="LIVE")

    mock_api = MockGrowwApi()
    broker_client.set_api_instance(mock_api)
    live_engine.client = broker_client
    live_engine.enforce_market_hours = False
    live_engine.open_positions.clear()

    yield

    if os.path.exists(temp_db_path):
        os.remove(temp_db_path)


def test_get_execution_status():
    """Verify GET /api/execution/status returns account metrics."""
    with TestClient(app) as client:
        res = client.get("/api/execution/status")
        assert res.status_code == 200
        data = res.json()
        assert "execution_mode" in data
        assert "broker_connected" in data
        assert data["open_positions_count"] == 0


def test_mode_switch_requires_confirmation_for_live():
    """Switching to LIVE mode without confirmation must fail with HTTP 400."""
    with TestClient(app) as client:
        # 1. Attempt switch to LIVE without confirmation
        res = client.post("/api/execution/mode", json={"mode": "LIVE"})
        assert res.status_code == 400
        assert "explicit confirmation" in res.json()["detail"]

        # 2. Attempt switch with incorrect confirmation
        res = client.post("/api/execution/mode", json={"mode": "LIVE", "confirmation": "WRONG"})
        assert res.status_code == 400

        # 3. Successful switch with exact confirmation
        res = client.post(
            "/api/execution/mode",
            json={"mode": "LIVE", "confirmation": "CONFIRM_LIVE_TRADING"},
        )
        assert res.status_code == 200
        assert res.json()["current_mode"] == "LIVE"
        assert settings.execution_mode == ExecutionMode.LIVE

        # 4. Switch back to PAPER without confirmation
        res = client.post("/api/execution/mode", json={"mode": "PAPER"})
        assert res.status_code == 200
        assert res.json()["current_mode"] == "PAPER"
        assert settings.execution_mode == ExecutionMode.PAPER


def test_auth_test_and_margin_endpoints():
    """Verify POST /api/execution/auth/test and GET /api/execution/margin."""
    with TestClient(app) as client:
        auth_res = client.post("/api/execution/auth/test", json={})
        assert auth_res.status_code == 200
        assert auth_res.json()["is_connected"] is True

        margin_res = client.get("/api/execution/margin")
        assert margin_res.status_code == 200
        assert margin_res.json()["available_margin"] == 15000.0


def test_live_order_and_position_lifecycle(monkeypatch):
    """Test full cycle: submit live order -> get positions -> close position."""
    monkeypatch.setattr(settings, "execution_mode", ExecutionMode.LIVE)

    with TestClient(app) as client:
        # Submit live order
        order_payload = {
            "symbol": "NIFTY",
            "direction": "CE",
            "quantity": 25,
            "target_pct": 20.0,
            "stop_loss_pct": 10.0,
        }
        res = client.post("/api/execution/order", json=order_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["is_filled"] is True
        pos = data["position"]
        assert pos is not None
        pos_id = pos["id"]

        # Get positions
        pos_res = client.get("/api/execution/positions?open_only=true")
        assert pos_res.status_code == 200
        positions = pos_res.json()
        assert len(positions) == 1
        assert positions[0]["id"] == pos_id

        # Close position
        close_res = client.post(f"/api/execution/positions/{pos_id}/close?reason=API_TEST")
        assert close_res.status_code == 200
        assert close_res.json()["is_open"] is False

        # Open positions should now be 0
        pos_res2 = client.get("/api/execution/positions?open_only=true")
        assert len(pos_res2.json()) == 0


def test_emergency_square_off_all_endpoint(monkeypatch):
    """Test POST /api/execution/square-off-all panic liquidation endpoint."""
    monkeypatch.setattr(settings, "execution_mode", ExecutionMode.LIVE)

    with TestClient(app) as client:
        # Submit order to open position
        client.post("/api/execution/order", json={"symbol": "NIFTY", "direction": "PE", "quantity": 25})

        status_before = client.get("/api/execution/status").json()
        assert status_before["open_positions_count"] == 1

        # Liquidate all
        liq_res = client.post("/api/execution/square-off-all")
        assert liq_res.status_code == 200
        assert liq_res.json()["closed_count"] == 1

        status_after = client.get("/api/execution/status").json()
        assert status_after["open_positions_count"] == 0
