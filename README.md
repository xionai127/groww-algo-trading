# Groww Algo Trading

An automated, modular algorithmic trading system built for Indian index options trading (NIFTY 50 and SENSEX) using Python, FastAPI, and an interactive dark-mode web dashboard.

---

## Key Features

- **Multi-Mode Execution**:
  - `ANALYSIS`: Real-time signal generation and indicator diagnostics without placing orders.
  - `PAPER`: Realistic simulated trading environment incorporating slippage, exchange transaction charges, STT, and broker fees.
  - `LIVE`: Direct order placement via Groww API with mandatory safety confirmation checks.

- **Capital & Risk Protections**:
  - Daily hard-stop loss limiter (defaults to ₹500/day).
  - Maximum open positions ceiling (default 1 concurrent position).
  - Maximum daily trade limit (default 5 trades/day).
  - Automated intraday square-off at 15:15 IST before market closing.
  - Emergency panic liquidation kill-switch via REST API and dashboard.

- **Market Data & Quantitative Analytics**:
  - Multi-timeframe live candle aggregation (1m, 3m, 5m, 15m).
  - Analytical Black-Scholes pricing engine with Option Greeks (Delta, Gamma, Theta, Vega) and implied volatility solver.
  - Dynamic strike selector (ATM, ITM, OTM for Call and Put options).

- **Strategy Engine**:
  - Built-in technical strategies: EMA 9/21 Crossover and SuperTrend + RSI.
  - Dynamic rule-based strategy builder with version snapshots and change tracking.
  - Full-featured historical backtesting engine with equity curves, drawdown calculations, Sharpe/Sortino ratios, and trade logs.

- **Interactive Dashboard**:
  - Dark-mode web interface with HTML5 Canvas charts.
  - Live tick streaming over WebSockets.
  - Position management, account equity tracking, and manual order override.

---

## Architecture Overview

```text
groww-algo-trading/
├── app.py                  # Application entry point & FastAPI routing
├── config.py               # Centralized configuration & environment loader
├── database/               # SQLite database foundation, migrations & schema
├── market_data/            # Candle aggregation, Greeks calculation & quote feeds
├── strategies/             # Strategy engine, technical indicators & strike selector
├── strategy_lab/           # Strategy builder and version history
├── backtesting/            # Historical simulation engine & performance metrics
├── paper_trading/          # Virtual execution engine with slippage & tax modeling
├── risk_management/        # Circuit breaker, pre-flight checks & trade journal
├── execution/              # Groww API broker client & live order coordinator
├── dashboard/              # Web dashboard UI, static assets & Canvas charts
└── tests/                  # Automated test suite (Pytest)
```

---

## Getting Started

### Prerequisites

- Python 3.10+ (tested up to 3.14)
- Git
- Active Groww account (for live trading mode only)

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/<your-username>/groww-algo-trading.git
   cd groww-algo-trading
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment settings**:
   ```bash
   cp .env.example .env
   ```
   Inspect `.env` to verify your capital allocation, risk limits, and broker credentials.

5. **Start the application**:
   ```bash
   uvicorn app:app --reload --host 0.0.0.0 --port 8000
   ```
   Open your browser and navigate to `http://localhost:8000/dashboard`.

---

## Running Automated Tests

Run the test suite using `pytest`:

```bash
pytest -v
```

---

## Disclaimer

This software is developed for research, education, and paper trading purposes. Algorithmic options trading involves substantial risk of financial loss. Past performance in backtests or paper trading does not guarantee future results. Users are solely responsible for verifying strategies and monitoring live operations.
