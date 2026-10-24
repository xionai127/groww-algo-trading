# Project Specification — Groww Algo Trading Platform

## 1. System Overview
An automated algorithmic trading platform targeting Indian index options (NIFTY 50 and SENSEX) using Python, FastAPI backend, and a browser-based user interface.

## 2. Core Architecture
- **Language**: Python 3.14+
- **Framework**: FastAPI (REST + WebSockets for real-time tick streaming)
- **Database**: SQLite3 (WAL mode, automated schema migration tracking)
- **Broker Interface**: Official `growwapi` SDK with dynamic credential injection via browser UI
- **Trading Modes**:
  - `ANALYSIS`: Signal generation and diagnostics only (no broker calls)
  - `PAPER`: Virtual portfolio simulation with slippage, brokerage, and latency
  - `LIVE`: Real order routing to Groww (strictly disabled by default, requires manual confirmation)

## 3. Financial & Risk Parameters
- **Initial Capital**: ₹10,000
- **Daily Max Loss Limit**: ₹500 (Hard stop; rejects all new entry signals upon breach)
- **Risk Per Trade**: ₹100
- **Max Open Positions**: 1
- **Max Trades Per Day**: 5
- **Default Stop-Loss**: 10% on option premium
- **Default Target**: 20% on option premium
- **Instruments**: NIFTY 50 and SENSEX options
- **Trade Direction**: Options Buying Only (CE and PE)
- **Option Selling**: Strictly Disabled

## 4. Market Hours
- **Market Open**: 09:15 IST
- **Market Close**: 15:30 IST
- **Auto Square-Off**: 15:15 IST
