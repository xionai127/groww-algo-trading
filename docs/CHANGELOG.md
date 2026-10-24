# Changelog

All notable changes to this project will be documented in this file.

## [Phase 1 - Foundation] - 2026-10-24

### Added
- Added `.gitignore` to protect sensitive `.env`, `*.db`, and cache files.
- Added `requirements.txt` with dependencies (`fastapi`, `uvicorn`, `pydantic-settings`, `pandas`, `pytest`, `growwapi`, `httpx`).
- Added `.env.example` defining environment variables and risk limits.
- Added `config.py` with Pydantic settings, execution modes (`ANALYSIS`, `PAPER`, `LIVE`), capital limits (₹10,000 capital, ₹500 max daily loss), and structured logging to `logs/trading.log`.
- Added `database/db.py` SQLite engine with schema version 1 tracking, WAL mode, foreign keys, and tables for migrations, app state, strategies, strategy versions, orders, trades, risk events, and audit logs.
- Added `app.py` FastAPI server with lifecycle startup/shutdown handlers and `/health` and `/api/status` endpoints.
- Added comprehensive automated test suite in `tests/test_config.py`, `tests/test_database.py`, and `tests/test_app.py` (9 tests passing).
- Updated `docs/PROJECT_SPEC.md` and `docs/DEVELOPMENT_STATUS.md`.
