"""Integration tests for FastAPI market data endpoints and WebSocket."""

import os
import tempfile
import pytest
from starlette.testclient import TestClient

from app import app
from config import settings
from database.db import init_db
from market_data.history_manager import init_candle_table


@pytest.fixture(autouse=True)
def test_env(monkeypatch):
    """Isolate DB for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_path = tmp.name

    monkeypatch.setattr(settings, "database_path", temp_path)
    init_db()
    init_candle_table()

    yield

    if os.path.exists(temp_path):
        os.remove(temp_path)


def test_market_status_endpoint():
    """Verify /api/market/status returns valid provider and instruments."""
    with TestClient(app) as client:
        resp = client.get("/api/market/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "provider" in data
        assert "is_connected" in data
        assert "tracked_instruments" in data


def test_market_ltp_endpoint():
    """Verify /api/market/ltp returns spot price."""
    with TestClient(app) as client:
        resp = client.get("/api/market/ltp?symbol=NIFTY")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "NIFTY"
        assert data["ltp"] > 0


def test_market_candles_endpoint():
    """Verify /api/market/candles fetches candles successfully."""
    with TestClient(app) as client:
        resp = client.get("/api/market/candles?symbol=NIFTY&timeframe=5m&limit=10")
        assert resp.status_code == 200
        candles = resp.json()
        assert isinstance(candles, list)
        assert len(candles) > 0
        c0 = candles[0]
        assert "open" in c0
        assert "high" in c0
        assert "low" in c0
        assert "close" in c0


def test_market_option_chain_endpoint():
    """Verify /api/market/option-chain returns strikes and ATM strike."""
    with TestClient(app) as client:
        resp = client.get("/api/market/option-chain?underlying=NIFTY")
        assert resp.status_code == 200
        data = resp.json()
        assert data["underlying"] == "NIFTY"
        assert "atm_strike" in data
        assert "calls" in data
        assert "puts" in data
        assert len(data["strikes"]) > 0


def test_market_websocket_ping_pong():
    """Verify WebSocket connection and ping/pong keepalive."""
    with TestClient(app) as client:
        with client.websocket_connect("/api/market/ws") as ws:
            ws.send_text("ping")
            reply = ws.receive_text()
            assert reply == "pong"
