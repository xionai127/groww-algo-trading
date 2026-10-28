"""Integration tests for Backtest API endpoints."""

from datetime import datetime, timedelta, timezone
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


def test_seed_candles_endpoint():
    """Verify historical candle generation and caching via API."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/backtest/seed-candles",
            json={"symbol": "NIFTY", "timeframe": "5m", "days": 2},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["candles_cached"] > 0


def test_backtest_run_and_query_endpoints():
    """Verify executing backtest, listing runs, fetching run details, and deleting runs."""
    with TestClient(app) as client:
        now = datetime.now(timezone.utc)
        from_date = now - timedelta(days=3)
        payload = {
            "strategy_id": "builtin_ema_crossover",
            "symbol": "NIFTY",
            "timeframe": "5m",
            "from_date": from_date.isoformat(),
            "to_date": now.isoformat(),
            "initial_capital": 10000.0,
        }

        # Run backtest
        run_resp = client.post("/api/backtest/run", json=payload)
        assert run_resp.status_code == 200
        run_data = run_resp.json()
        run_id = run_data["run_id"]
        assert run_data["strategy_id"] == "builtin_ema_crossover"
        assert "summary" in run_data
        assert "equity_curve" in run_data

        # List runs
        list_resp = client.get("/api/backtest/runs")
        assert list_resp.status_code == 200
        runs = list_resp.json()
        assert any(r["id"] == run_id for r in runs)

        # Get specific run
        detail_resp = client.get(f"/api/backtest/runs/{run_id}")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["run_id"] == run_id

        # Delete run
        del_resp = client.delete(f"/api/backtest/runs/{run_id}")
        assert del_resp.status_code == 200

        # Verify not found after deletion
        not_found_resp = client.get(f"/api/backtest/runs/{run_id}")
        assert not_found_resp.status_code == 404
