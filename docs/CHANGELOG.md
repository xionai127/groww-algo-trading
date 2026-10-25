# Changelog

All notable changes to this project will be documented in this file.

## [Phase 2 - Market Data Engine] - 2026-10-25

### Added
- Added `market_data/models.py` with domain schemas for `Tick`, `Candle`, `OptionQuote`, `OptionChain`, and `Timeframe`.
- Added `market_data/greeks.py` with full Black-Scholes formula, bisection IV solver, and Greeks calculation (Delta, Gamma, Theta, Vega).
- Added `market_data/candle_aggregator.py` for tick-to-candle accumulation, interval boundary closure, rolling buffers, and multi-timeframe resampling (1m to 3m, 5m, 15m).
- Added `market_data/provider.py` implementing `BaseMarketDataProvider`, `MockMarketDataProvider` (with realistic drift and synthetic option chain), and `GrowwMarketDataProvider` using the official `growwapi` SDK.
- Added `market_data/history_manager.py` for SQLite historical candle caching (`market_candles` table) and pandas DataFrame conversion.
- Added `market_data/engine.py` orchestrating real-time feeds, client WebSockets, and data caching.
- Added `market_data/router.py` with FastAPI endpoints (`/api/market/status`, `/api/market/ltp`, `/api/market/candles`, `/api/market/option-chain`, and WebSocket `/api/market/ws`).
- Added unit and integration test suite (`tests/test_greeks.py`, `tests/test_candle_aggregator.py`, `tests/test_mock_provider.py`, `tests/test_history_manager.py`, `tests/test_market_api.py`) bringing passing tests to 27/27.

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
