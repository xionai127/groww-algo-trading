"""Integration tests for FastAPI application endpoints."""

import os
import tempfile
import pytest
from starlette.testclient import TestClient
from app import app
from config import settings
from database.db import init_db


@pytest.fixture(autouse=True)
def setup_test_app(monkeypatch):
    """Provide a clean temporary database for app tests."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_db_path = tmp.name

    monkeypatch.setattr(settings, "database_path", temp_db_path)
    init_db()

    yield

    if os.path.exists(temp_db_path):
        os.remove(temp_db_path)


def test_health_check_endpoint():
    """Verify healthcheck endpoint reports healthy status and schema version."""
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["execution_mode"] == "ANALYSIS"
        assert data["database_connected"] is True
        assert data["schema_version"] == 1


def test_system_status_endpoint():
    """Verify system status returns configured parameters."""
    with TestClient(app) as client:
        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["capital"]["starting_capital"] == 10000.0
        assert data["capital"]["max_daily_loss"] == 500.0
        assert data["capital"]["max_open_positions"] == 1
        assert data["rules"]["allow_option_selling"] is False
        assert "NIFTY" in data["rules"]["allowed_instruments"]
