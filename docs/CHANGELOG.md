# Changelog

All notable changes to this project will be documented in this file.

## [Phase 6 - Centralized Risk Management & Journaling] - 2026-10-29

### Added
- Added `risk_management/models.py` defining schemas for `RiskAssessment`, `RiskDecision`, `RuleViolation`, `BreakerState`, `TradeTag`, `CircuitBreakerStatusResponse`, `DailyRiskStatus`, `JournalEntryResponse`, `JournalAnalytics`, and `DailyDisciplineScorecard`.
- Added `risk_management/circuit_breaker.py` (`CircuitBreaker`) implementing manual and automated trading halts, kill-switch with SQLite persistence (`app_state`), audit logging, and consecutive loss tracking.
- Added `risk_management/rules.py` implementing modular pre-flight evaluation: strict options buying only, Indian trading hours (09:15-15:30 IST), square-off restriction (past 15:10 IST), daily max loss limit (₹500 hard cap), max open positions (1), max daily trades (5), capital/cash verification, and risk-per-trade sizing.
- Added `risk_management/journal.py` (`TradeJournalManager`) implementing SQLite trade reflection logging (`trade_journal` table), tags, emotional/psychological note-taking, mistake detection, performance analytics by tag, and automated daily discipline scorecards (grades A+ through F).
- Added `risk_management/manager.py` (`RiskManager`) as the orchestrator linking pre-trade validation, circuit breaker, trade journal, and panic liquidation handlers.
- Added `risk_management/router.py` with FastAPI endpoints (`/api/risk/status`, `/api/risk/breaker`, `/api/risk/validate`, `/api/risk/kill-switch`, `/api/risk/reset-breaker`, `/api/risk/events`, `/api/journal/entries`, `/api/journal/analytics`, `/api/journal/scorecard`) and registered with `app.py`.
- Integrated `RiskManager` with `PaperTradingEngine` for pre-trade circuit breaker verification, automated post-trade exit journal logging, and emergency panic liquidation.
- Added comprehensive unit and API tests in `tests/test_risk_management.py` and `tests/test_risk_journal_api.py`, expanding the test suite to 74/74 passing tests.

## [Phase 5 - Realistic Paper Trading] - 2026-10-28

### Added
- Added `paper_trading/models.py` defining schemas for `PaperAccount`, `PaperPosition`, `PaperOrder`, `PaperTrade`, `OrderStatus`, `OrderType`, `TransactionType`, `ExitReason`, and `PaperOrderRequest`.
- Added `paper_trading/execution.py` (`PaperExecutionSimulator`) simulating realistic adverse slippage, index strike selection (ATM, ITM 1/2, OTM 1/2), weekly expiry derivation, Black-Scholes fallback pricing, and Indian transaction costs (Groww ₹20/order, STT on sell 0.0625%, exchange turnover charges 0.05%, GST 18%, Stamp duty 0.003%, SEBI turnover charge).
- Added `paper_trading/engine.py` (`PaperTradingEngine`) managing ₹10,000 virtual capital, active options buying positions, mark-to-market (MTM) calculations, target (TP) and stop-loss (SL) triggers, auto square-off at 15:15 IST, and hard risk limits (max 1 open position, max 5 trades/day, ₹500 max daily loss).
- Added `paper_positions` table to SQLite schema in `database/db.py` to persist position state across restarts.
- Added `paper_trading/router.py` with FastAPI endpoints (`/api/paper/account`, `/positions`, `/orders`, `/trades`, `/positions/{id}/close`, `/reset`, `/mtm-update`) and integrated into `app.py`.
- Connected `StrategyManager.register_signal_listener` to route strategy `Signal` events directly into paper trading execution.
- Added unit and API test suites in `tests/test_paper_trading.py` and `tests/test_paper_api.py`, achieving 64/64 passing tests across the entire platform.

## [Phase 4 - Backtesting Engine] - 2026-10-28

### Added
- Added `backtesting/models.py` with schemas for `BacktestRequest`, `BacktestTrade`, `EquityPoint`, `BacktestSummary`, `BacktestResult`, and `ExitReason`.
- Added `backtesting/option_simulator.py` implementing Black-Scholes premium calculation proxy, dynamic weekly expiry strike selector, configurable slippage, and Indian regulatory charges (STT on sell, GST 18%, stamp duty, exchange turnover fee, SEBI charges, and ₹20/order Groww brokerage).
- Added `backtesting/data_feed.py` with SQLite cached candle loading from `market_candles` and realistic trending synthetic candle generation for gapless simulation.
- Added `backtesting/metrics.py` computing Sharpe ratio, Sortino ratio, Calmar ratio, profit factor, max drawdown, win rate, average win/loss, and consecutive streaks.
- Added `backtesting/engine.py` simulating candle-by-candle strategy signal evaluation, SL/TP triggers, 15:15 IST auto square-off, risk budget constraints, and equity curve generation.
- Added `backtesting/repository.py` and `backtest_runs` SQLite table in `database/db.py` to persist run summaries, trade logs, and equity progression.
- Added `backtesting/router.py` with endpoints (`/api/backtest/run`, `/api/backtest/runs`, `/api/backtest/runs/{run_id}`, `/api/backtest/seed-candles`) and integrated into `app.py`.
- Added unit and API tests in `tests/test_backtest_engine.py` and `tests/test_backtest_api.py`, achieving 52/52 passing tests across the entire platform.

## [Phase 3 - Strategy Engine & Strategy Builder] - 2026-10-25

### Added
- Added `strategies/models.py` with domain schemas for `Signal`, `SignalAction`, `StrikePreference`, `IndicatorCondition`, `RuleBlock`, and `StrategyConfig`.
- Added `strategies/indicators.py` with vectorized implementations of SMA, EMA, Wilder's RSI, SuperTrend (10, 3), VWAP, MACD, Bollinger Bands, and ATR.
- Added `strategies/strike_selector.py` mapping index spot prices to exact option strikes (ATM, ITM 1/2, OTM 1/2 for CE & PE) with strict options buying constraints.
- Added `strategies/base.py` providing abstract `BaseStrategy` with lifecycle hooks, daily trade counters, and max trade validations.
- Added built-in production strategies: `EMACrossoverStrategy` (9/21 EMA) and `SuperTrendRSIStrategy` (SuperTrend 10,3 + RSI 14).
- Added `strategies/rule_engine.py` for dynamic rule-based strategy execution and conditional evaluation.
- Added `strategy_lab/builder.py` providing SQLite strategy persistence with automated version snapshots and diff histories.
- Added `strategies/manager.py` coordinating live strategy feeds, real-time candle dispatching, and strike resolution.
- Added `strategies/router.py` with FastAPI endpoints (`/api/strategies`, `/meta/indicators`, `/{id}/activate`, `/{id}/deactivate`, `/{id}/versions`).
- Added unit and integration tests (`tests/test_indicators.py`, `tests/test_strike_selector.py`, `tests/test_strategies.py`, `tests/test_rule_engine.py`, `tests/test_strategy_api.py`), bringing passing tests to 44/44.

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
