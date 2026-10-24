"""Unit tests for SQLite database schema, connections, and audit logging."""

import os
import sqlite3
import tempfile
import pytest
from database.db import CURRENT_SCHEMA_VERSION, get_db_connection, init_db, log_risk_event, get_schema_version
from config import settings


@pytest.fixture(autouse=True)
def temporary_database(monkeypatch):
    """Run tests against a temporary test database to keep local db clean."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_db_path = tmp.name

    monkeypatch.setattr(settings, "database_path", temp_db_path)
    init_db()

    yield temp_db_path

    if os.path.exists(temp_db_path):
        os.remove(temp_db_path)


def test_schema_initialization(temporary_database):
    """Verify all critical tables exist and migration is recorded."""
    assert get_schema_version() == CURRENT_SCHEMA_VERSION

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row["name"] for row in cursor.fetchall()]

        required_tables = [
            "schema_migrations",
            "app_state",
            "strategies",
            "strategy_versions",
            "orders",
            "trades",
            "risk_events",
            "audit_logs",
        ]
        for tbl in required_tables:
            assert tbl in tables, f"Expected table '{tbl}' was not created"


def test_log_risk_event(temporary_database):
    """Verify risk rejection logs are persisted correctly."""
    log_risk_event(
        event_type="DAILY_LOSS_LIMIT",
        reason="Daily loss threshold reached: ₹500",
        mode="PAPER",
        details={"current_loss": 520.0, "threshold": 500.0}
    )

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM risk_events WHERE event_type = ?", ("DAILY_LOSS_LIMIT",))
        event = cursor.fetchone()

        assert event is not None
        assert event["mode"] == "PAPER"
        assert "500" in event["reason"]


def test_transaction_rollback_on_error(temporary_database):
    """Verify that failed transactions roll back cleanly."""
    with pytest.raises(sqlite3.IntegrityError):
        with get_db_connection() as conn:
            # Insert valid record
            conn.execute(
                "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)",
                ("test_key", "val1", "2026-10-24T00:00:00Z")
            )
            # Insert duplicate primary key, forcing IntegrityError
            conn.execute(
                "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)",
                ("test_key", "val2", "2026-10-24T00:00:00Z")
            )

    # Verify that the entire transaction was rolled back and key was not saved
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM app_state WHERE key = ?", ("test_key",))
        assert cursor.fetchone() is None
