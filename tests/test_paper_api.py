"""Integration tests for Paper Trading REST API endpoints."""

from datetime import datetime, timezone
import os
import tempfile
import pytest
from starlette.testclient import TestClient

from app import app
from config import settings
from database.db import init_db
from paper_trading import paper_engine


@pytest.fixture(autouse=True)
def test_env(monkeypatch):
    """Isolate DB for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_path = tmp.name

    monkeypatch.setattr(settings, "database_path", temp_path)
    init_db()
    paper_engine.reset_account(10000.0)

    yield

    if os.path.exists(temp_path):
        os.remove(temp_path)


def test_paper_account_endpoint():
    """Verify GET /api/paper/account returns default capital."""
    with TestClient(app) as client:
        resp = client.get("/api/paper/account")
        assert resp.status_code == 200
        data = resp.json()
        assert data["initial_capital"] == 10000.0
        assert data["cash"] == 10000.0
        assert data["total_equity"] == 10000.0
        assert data["open_positions_count"] == 0


def test_paper_order_and_positions_flow():
    """Verify placing order, viewing positions, MTM update, and closing position via API."""
    with TestClient(app) as client:
        # 1. Place Paper Order
        order_payload = {
            "symbol": "NIFTY",
            "direction": "CE",
            "strike_preference": "ATM",
            "target_pct": 25.0,
            "stop_loss_pct": 10.0,
        }
        order_resp = client.post("/api/paper/orders", json=order_payload)
        assert order_resp.status_code == 200
        data = order_resp.json()
        assert data["is_filled"] is True
        assert data["position"] is not None
        pos_id = data["position"]["id"]

        # 2. Get Open Positions
        pos_resp = client.get("/api/paper/positions?open_only=true")
        assert pos_resp.status_code == 200
        positions = pos_resp.json()
        assert len(positions) == 1
        assert positions[0]["id"] == pos_id

        # 3. Get Specific Position
        single_pos = client.get(f"/api/paper/positions/{pos_id}")
        assert single_pos.status_code == 200
        assert single_pos.json()["id"] == pos_id

        # 4. Trigger MTM update within target and SL bounds
        entry_price = data["position"]["entry_price"]
        within_bounds_price = round(entry_price * 1.05, 2)
        mtm_resp = client.post(
            "/api/paper/mtm-update",
            json={"symbol": "NIFTY", "spot": 25050.0, "option_quote_price": within_bounds_price},
        )
        assert mtm_resp.status_code == 200
        assert mtm_resp.json()["status"] == "success"
        assert mtm_resp.json()["positions_closed"] == 0

        # 5. List Orders and Trades
        orders_resp = client.get("/api/paper/orders")
        assert orders_resp.status_code == 200
        assert len(orders_resp.json()) >= 1

        trades_resp = client.get("/api/paper/trades")
        assert trades_resp.status_code == 200
        assert len(trades_resp.json()) >= 1

        # 6. Close Position Manually
        close_resp = client.post(f"/api/paper/positions/{pos_id}/close", json={"reason": "MANUAL"})
        assert close_resp.status_code == 200
        closed_data = close_resp.json()
        assert closed_data["is_open"] is False
        assert closed_data["exit_reason"] == "MANUAL"

        # 7. Check Open Positions count is 0
        pos_after = client.get("/api/paper/positions?open_only=true")
        assert pos_after.status_code == 200
        assert len(pos_after.json()) == 0

        # 8. Reset Account
        reset_resp = client.post("/api/paper/reset", json={"initial_capital": 10000.0})
        assert reset_resp.status_code == 200
        assert reset_resp.json()["cash"] == 10000.0
