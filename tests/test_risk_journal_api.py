"""API integration tests for Risk Management endpoints and Trade Journaling."""

from datetime import datetime, timezone
import os
import tempfile
import pytest
from starlette.testclient import TestClient

from app import app
from config import settings
from database.db import init_db
from risk_management.manager import risk_manager
from risk_management.journal import TradeJournalManager
from risk_management.models import TradeTag


@pytest.fixture(autouse=True)
def test_db_env(monkeypatch):
    """Isolate DB for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_path = tmp.name

    monkeypatch.setattr(settings, "database_path", temp_path)
    init_db()
    risk_manager.reset_circuit_breaker()

    yield

    risk_manager.reset_circuit_breaker()
    if os.path.exists(temp_path):
        os.remove(temp_path)


def test_risk_status_and_breaker_api():
    """Verify /api/risk/status and /api/risk/breaker endpoints."""
    with TestClient(app) as client:
        # 1. Breaker status (initially normal)
        resp = client.get("/api/risk/breaker")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_tripped"] is False
        assert data["state"] == "NORMAL"

        # 2. Risk status
        status_resp = client.get("/api/risk/status?mode=PAPER")
        assert status_resp.status_code == 200
        sdata = status_resp.json()
        assert sdata["mode"] == "PAPER"
        assert sdata["daily_loss_limit"] == 500.0
        assert sdata["max_trades_per_day"] == 5
        assert sdata["max_open_positions"] == 1


def test_kill_switch_and_reset_api():
    """Verify manual kill switch activation and reset."""
    with TestClient(app) as client:
        # 1. Trigger Kill switch
        ks_resp = client.post(
            "/api/risk/kill-switch",
            json={"reason": "Manual operator intervention test", "liquidate_open_positions": False},
        )
        assert ks_resp.status_code == 200
        assert ks_resp.json()["circuit_breaker_tripped"] is True

        # Verify breaker is active
        breaker_resp = client.get("/api/risk/breaker")
        assert breaker_resp.json()["is_tripped"] is True

        # 2. Reset breaker
        reset_resp = client.post(
            "/api/risk/reset-breaker",
            json={"reason": "Market conditions stabilized", "confirmed": True},
        )
        assert reset_resp.status_code == 200
        assert reset_resp.json()["status"] == "success"

        # Verify back to normal
        breaker_resp = client.get("/api/risk/breaker")
        assert breaker_resp.json()["is_tripped"] is False


def test_risk_pre_flight_validate_api():
    """Verify /api/risk/validate pre-flight check."""
    with TestClient(app) as client:
        # 1. Valid BUY order during market hours
        payload = {
            "symbol": "NIFTY",
            "direction": "CE",
            "transaction_type": "BUY",
            "quantity": 25,
            "estimated_price": 45.0,
            "stop_loss_pct": 10.0,
            "mode": "PAPER",
            "current_time": "2026-10-26T04:30:00Z",
        }
        resp = client.post("/api/risk/validate", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True

        # 2. Forbidden SELL order
        sell_payload = dict(payload, transaction_type="SELL")
        sell_resp = client.post("/api/risk/validate", json=sell_payload)
        assert sell_resp.status_code == 200
        sdata = sell_resp.json()
        assert sdata["allowed"] is False
        assert sdata["violation_type"] == "OPTIONS_BUYING_ONLY"


def test_journal_crud_analytics_and_scorecard():
    """Verify trade journal lifecycle, analytics, and daily scorecard."""
    with TestClient(app) as client:
        # Seed trade in journal
        trade_id = "test_trd_001"
        entry_id = TradeJournalManager.create_journal_entry({
            "id": trade_id,
            "trade_id": trade_id,
            "mode": "PAPER",
            "symbol": "NIFTY",
            "contract_symbol": "NIFTY-2026-10-29-25000-CE",
            "direction": "CE",
            "quantity": 25,
            "entry_price": 50.0,
            "exit_price": 60.0,
            "gross_pnl": 250.0,
            "brokerage": 40.0,
            "taxes": 5.0,
            "net_pnl": 205.0,
            "return_pct": 20.0,
            "exit_reason": "TARGET",
            "strategy_id": "builtin_ema_crossover",
            "strategy_name": "EMA Crossover (9/21)",
        })
        assert entry_id.startswith("jnl_")

        # 1. Fetch entries list
        list_resp = client.get("/api/journal/entries?mode=PAPER")
        assert list_resp.status_code == 200
        entries = list_resp.json()
        assert len(entries) >= 1
        assert entries[0]["id"] == entry_id

        # 2. Fetch specific entry
        single_resp = client.get(f"/api/journal/entries/{entry_id}")
        assert single_resp.status_code == 200
        assert single_resp.json()["id"] == entry_id

        # 3. Update entry with notes, rating, and tags
        update_payload = {
            "tags": [TradeTag.TREND_FOLLOWING.value, TradeTag.DISCIPLINE_WIN.value],
            "notes": "Followed plan perfectly, good entry on 9/21 crossover confirmation.",
            "rating": 5,
            "mistake_flag": False,
        }
        upd_resp = client.put(f"/api/journal/entries/{entry_id}", json=update_payload)
        assert upd_resp.status_code == 200
        upd_data = upd_resp.json()
        assert upd_data["rating"] == 5
        assert upd_data["notes"] == update_payload["notes"]
        assert TradeTag.TREND_FOLLOWING.value in upd_data["tags"]

        # 4. Fetch Analytics
        analytics_resp = client.get("/api/journal/analytics?mode=PAPER")
        assert analytics_resp.status_code == 200
        adata = analytics_resp.json()
        assert adata["total_trades"] == 1
        assert adata["winning_trades"] == 1
        assert adata["total_net_pnl"] == 205.0
        assert adata["win_rate_pct"] == 100.0

        # 5. Fetch Daily Scorecard
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        scorecard_resp = client.get(f"/api/journal/scorecard?date_str={today_str}")
        assert scorecard_resp.status_code == 200
        sdata = scorecard_resp.json()
        assert sdata["date"] == today_str
        assert sdata["discipline_score"] >= 80
        assert sdata["grade"] in ("A+", "A")
