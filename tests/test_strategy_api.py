"""Integration tests for Strategy management endpoints and versioning."""

import os
import tempfile
import pytest
from starlette.testclient import TestClient

from app import app
from config import settings
from database.db import init_db
from market_data.history_manager import init_candle_table
from strategies.manager import strategy_manager


@pytest.fixture(autouse=True)
def test_env(monkeypatch):
    """Isolate DB for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_path = tmp.name

    monkeypatch.setattr(settings, "database_path", temp_path)
    init_db()
    init_candle_table()
    strategy_manager.initialize_default_strategies()

    yield

    if os.path.exists(temp_path):
        os.remove(temp_path)


def test_list_strategies_endpoint():
    """Verify default seeded strategies are listed with status."""
    with TestClient(app) as client:
        resp = client.get("/api/strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2
        strat_ids = [s["config"]["id"] for s in data]
        assert "builtin_ema_crossover" in strat_ids


def test_meta_indicators_catalog():
    """Verify supported indicator catalog returns valid types and operators."""
    with TestClient(app) as client:
        resp = client.get("/api/strategies/meta/indicators")
        assert resp.status_code == 200
        data = resp.json()
        assert "indicators" in data
        assert "operators" in data
        ind_names = [i["type"] for i in data["indicators"]]
        assert "EMA" in ind_names
        assert "SUPERTREND" in ind_names


def test_save_and_activate_custom_strategy():
    """Verify creating a custom strategy and activating it."""
    with TestClient(app) as client:
        payload = {
            "id": "test_vwap_scalper",
            "name": "VWAP Scalper",
            "description": "Scalping NIFTY using VWAP",
            "underlying": "NIFTY",
            "timeframe": "5m",
            "is_active": False,
            "direction": "CE",
            "strike_preference": "ATM",
            "stop_loss_pct": 10.0,
            "target_pct": 20.0,
            "max_trades_per_day": 3,
            "indicators": {},
        }
        create_resp = client.post("/api/strategies", json=payload)
        assert create_resp.status_code == 200

        # Activate
        act_resp = client.post("/api/strategies/test_vwap_scalper/activate")
        assert act_resp.status_code == 200
        assert act_resp.json()["is_active"] is True

        # Deactivate
        deact_resp = client.post("/api/strategies/test_vwap_scalper/deactivate")
        assert deact_resp.status_code == 200
        assert deact_resp.json()["is_active"] is False

        # Version history
        v_resp = client.get("/api/strategies/test_vwap_scalper/versions")
        assert v_resp.status_code == 200
        versions = v_resp.json()
        assert len(versions) >= 2
