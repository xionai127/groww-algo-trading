"""FastAPI endpoints for initiating and analyzing strategy backtests."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from backtesting.data_feed import BacktestDataFeed
from backtesting.engine import BacktestEngine
from backtesting.models import BacktestRequest, BacktestResult
from backtesting.repository import BacktestRepository
from market_data.history_manager import HistoricalDataManager
from market_data.provider import MockMarketDataProvider

backtest_router = APIRouter(prefix="/api/backtest", tags=["Backtesting Engine"])
engine = BacktestEngine()


class SeedDataRequest(BaseModel):
    """Request to generate and cache historical sample candles."""
    symbol: str = "NIFTY"
    timeframe: str = "5m"
    days: int = Field(default=10, ge=1, le=60)


@backtest_router.post("/run", response_model=BacktestResult)
def run_backtest(request: BacktestRequest) -> BacktestResult:
    """Executes a backtest for a strategy over historical or synthetic market data."""
    try:
        result = engine.run(request, persist=True)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Backtest execution failed: {e}")


@backtest_router.get("/runs", response_model=List[Dict[str, Any]])
def list_backtest_runs(limit: int = Query(default=50, ge=1, le=200)) -> List[Dict[str, Any]]:
    """Returns a list of saved backtest experiments."""
    return BacktestRepository.list_all(limit=limit)


@backtest_router.get("/runs/{run_id}", response_model=BacktestResult)
def get_backtest_run(run_id: str) -> BacktestResult:
    """Fetches full details including trade log and equity curve for a backtest run."""
    result = BacktestRepository.get(run_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Backtest run {run_id} not found")
    return result


@backtest_router.delete("/runs/{run_id}")
def delete_backtest_run(run_id: str) -> Dict[str, Any]:
    """Deletes a saved backtest record."""
    deleted = BacktestRepository.delete(run_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Backtest run {run_id} not found")
    return {"status": "success", "message": f"Backtest run {run_id} deleted"}


@backtest_router.post("/seed-candles")
def seed_historical_candles(payload: SeedDataRequest) -> Dict[str, Any]:
    """Populates the SQLite market_candles cache with historical intraday bars for backtesting."""
    to_date = datetime.now(timezone.utc)
    from_date = to_date - timedelta(days=payload.days)

    candles = BacktestDataFeed.generate_synthetic_candles(
        symbol=payload.symbol,
        timeframe_str=payload.timeframe,
        from_date=from_date,
        to_date=to_date,
    )

    history_mgr = HistoricalDataManager(MockMarketDataProvider())
    saved_count = history_mgr.save_candles(candles)

    return {
        "status": "success",
        "symbol": payload.symbol,
        "timeframe": payload.timeframe,
        "candles_generated": len(candles),
        "candles_cached": saved_count,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
    }
