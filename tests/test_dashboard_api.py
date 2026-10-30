"""Integration and Unit tests for Dashboard endpoints and frontend static assets."""

import os
import tempfile
import pytest
from starlette.testclient import TestClient
from app import app
from config import settings
from database.db import init_db


@pytest.fixture(autouse=True)
def setup_test_dashboard(monkeypatch):
    """Provide a clean temporary database for dashboard tests."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_db_path = tmp.name

    monkeypatch.setattr(settings, "database_path", temp_db_path)
    init_db()

    yield

    if os.path.exists(temp_db_path):
        os.remove(temp_db_path)


def test_serve_dashboard_html():
    """Verify that the dashboard HTML UI is served at / and /dashboard."""
    with TestClient(app) as client:
        # Test root endpoint
        res_root = client.get("/")
        assert res_root.status_code == 200
        assert "text/html" in res_root.headers.get("content-type", "")
        assert "Groww Algo Trading Platform" in res_root.text
        assert "market-candle-canvas" in res_root.text

        # Test /dashboard alias
        res_dash = client.get("/dashboard")
        assert res_dash.status_code == 200
        assert "text/html" in res_dash.headers.get("content-type", "")
        assert "GROWW ALGO" in res_dash.text


def test_serve_static_assets():
    """Verify static assets (CSS, Canvas JS, Dashboard Controller) are served properly."""
    with TestClient(app) as client:
        # CSS
        res_css = client.get("/static/css/dashboard.css")
        assert res_css.status_code == 200
        assert "GROWW ALGO TRADING" in res_css.text

        # Canvas Charts JS
        res_chart = client.get("/static/js/chart.js")
        assert res_chart.status_code == 200
        assert "CandleChart" in res_chart.text
        assert "EquityChart" in res_chart.text

        # Dashboard Logic JS
        res_js = client.get("/static/js/dashboard.js")
        assert res_js.status_code == 200
        assert "STATE" in res_js.text
        assert "initWebSocket" in res_js.text


def test_dashboard_system_summary():
    """Verify the /api/system/summary aggregation endpoint."""
    with TestClient(app) as client:
        response = client.get("/api/system/summary")
        assert response.status_code == 200
        data = response.json()

        assert "app" in data
        assert "breaker" in data
        assert "market" in data
        assert "account" in data
        assert "strategies" in data

        assert data["breaker"]["state"] == "NORMAL"
        assert data["breaker"]["is_tripped"] is False
        assert data["account"]["equity"] >= 0.0
        assert data["account"]["max_daily_loss"] == 500.0


def test_dashboard_credentials_api():
    """Verify dynamic broker credentials retrieval and updates."""
    with TestClient(app) as client:
        # Check initial status
        res_get = client.get("/api/system/credentials")
        assert res_get.status_code == 200
        data_get = res_get.json()
        assert "is_configured" in data_get
        assert "has_api_key" in data_get

        # Update credentials
        res_post = client.post(
            "/api/system/credentials",
            json={
                "api_key": "groww_api_test_key_12345",
                "api_secret": "groww_api_secret_abcdef9876",
                "totp_key": "JBSWY3DPEHPK3PXP",
            },
        )
        assert res_post.status_code == 200
        data_post = res_post.json()
        assert data_post["status"] == "success"
        assert data_post["is_configured"] is True

        # Verify masked retrieval
        res_after = client.get("/api/system/credentials")
        assert res_after.status_code == 200
        data_after = res_after.json()
        assert data_after["is_configured"] is True
        assert data_after["has_api_key"] is True
        assert data_after["api_key_masked"] == "gro...345"
        assert data_after["has_api_secret"] is True
        assert data_after["has_totp"] is True


def test_dashboard_ui_tabs_and_modals_present():
    """Verify that all functional trading terminal panes and modals exist in index.html."""
    with TestClient(app) as client:
        res = client.get("/")
        assert res.status_code == 200
        html = res.text

        # Verify all 6 tabs exist
        assert 'id="tab-terminal"' in html
        assert 'id="tab-portfolio"' in html
        assert 'id="tab-strategies"' in html
        assert 'id="tab-backtest"' in html
        assert 'id="tab-risk"' in html
        assert 'id="tab-settings"' in html

        # Verify charts exist
        assert 'id="market-candle-canvas"' in html
        assert 'id="backtest-equity-canvas"' in html

        # Verify modals exist
        assert 'id="modal-kill-switch"' in html
        assert 'id="modal-quick-order"' in html
        assert 'id="modal-journal"' in html

        # Verify pre-flight safety badges
        assert "Options Buying Only" in html
        assert "Option Selling" in html
        assert "15:15 IST" in html

