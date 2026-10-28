"""SQLite Database foundation with migration tracking and safe schema initialization."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
from config import logger, settings

CURRENT_SCHEMA_VERSION = 1


def get_db_path() -> Path:
    """Returns the resolved Path for the SQLite database."""
    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def get_db_connection() -> Generator[sqlite3.Connection, None, None]:
    """Yields a context-managed SQLite connection with WAL mode enabled."""
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error, rolled back transaction: {e}", exc_info=True)
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initializes tables and records schema version if not already present."""
    logger.info("Initializing database schema...")
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Migrations / Version tracking table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL,
                description TEXT NOT NULL
            );
        """)

        # 2. Key-Value App Settings / State table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        # 3. Strategy Definitions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS strategies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 0,
                is_primary INTEGER NOT NULL DEFAULT 0,
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        # 4. Strategy Version History (Rollback and Diff support)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS strategy_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                config_json TEXT NOT NULL,
                change_summary TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (strategy_id) REFERENCES strategies (id) ON DELETE CASCADE
            );
        """)

        # 5. Orders table (Both PAPER and LIVE with clear mode isolation)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                broker_order_id TEXT,
                mode TEXT NOT NULL,              -- 'PAPER' or 'LIVE'
                symbol TEXT NOT NULL,
                instrument_type TEXT NOT NULL,   -- 'CE' or 'PE'
                strike_price REAL,
                expiry_date TEXT,
                transaction_type TEXT NOT NULL,  -- 'BUY' or 'SELL'
                order_type TEXT NOT NULL,        -- 'MARKET' or 'LIMIT'
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                status TEXT NOT NULL,            -- 'PENDING', 'FILLED', 'REJECTED', 'CANCELLED'
                rejection_reason TEXT,
                strategy_id TEXT,
                raw_response TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        # 6. Trades / Fills table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                mode TEXT NOT NULL,
                transaction_type TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                executed_price REAL NOT NULL,
                brokerage REAL NOT NULL DEFAULT 0.0,
                taxes REAL NOT NULL DEFAULT 0.0,
                net_pnl REAL DEFAULT 0.0,
                strategy_id TEXT,
                executed_at TEXT NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders (id)
            );
        """)

        # 7. Risk Events (Auditing risk rejections & emergency stops)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                reason TEXT NOT NULL,
                details_json TEXT,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)

        # 8. Audit & System logs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL
            );
        """)

        # 9. Backtest Runs & Performance History
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id TEXT PRIMARY KEY,
                strategy_id TEXT,
                strategy_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                from_date TEXT NOT NULL,
                to_date TEXT NOT NULL,
                initial_capital REAL NOT NULL,
                net_pnl REAL NOT NULL,
                win_rate REAL NOT NULL,
                total_trades INTEGER NOT NULL,
                max_drawdown_pct REAL NOT NULL,
                summary_json TEXT NOT NULL,
                trades_json TEXT NOT NULL,
                equity_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)

        # Record migration version if empty
        cursor.execute("SELECT COUNT(*) as count FROM schema_migrations WHERE version = ?", (CURRENT_SCHEMA_VERSION,))
        if cursor.fetchone()["count"] == 0:
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
                (CURRENT_SCHEMA_VERSION, now_iso, "Phase 1 initial schema")
            )

    logger.info("Database schema initialized successfully.")


def log_risk_event(event_type: str, reason: str, mode: str, details: Optional[Dict[str, Any]] = None) -> None:
    """Helper to record a risk rejection or safety event."""
    now_iso = datetime.now(timezone.utc).isoformat()
    details_str = json.dumps(details or {})
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO risk_events (event_type, reason, details_json, mode, created_at) VALUES (?, ?, ?, ?, ?)",
            (event_type, reason, details_str, mode, now_iso)
        )


def get_schema_version() -> Optional[int]:
    """Returns the highest applied schema version."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(version) as max_v FROM schema_migrations;")
        row = cursor.fetchone()
        return row["max_v"] if row and row["max_v"] is not None else None
