"""SQLite database repository for persisting and querying backtest runs."""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

from config import logger
from database.db import get_db_connection
from backtesting.models import (
    BacktestResult,
    BacktestSummary,
    BacktestTrade,
    EquityPoint,
)


class BacktestRepository:
    """Manages database persistence for historical backtesting experiments."""

    @staticmethod
    def save(result: BacktestResult) -> None:
        """Persists a full backtest result into SQLite."""
        summary_json = result.summary.model_dump_json()
        trades_json = json.dumps([t.model_dump(mode="json") for t in result.trades])
        equity_json = json.dumps([e.model_dump(mode="json") for e in result.equity_curve])
        now_iso = datetime.now(timezone.utc).isoformat()

        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO backtest_runs (
                    id, strategy_id, strategy_name, symbol, timeframe,
                    from_date, to_date, initial_capital, net_pnl, win_rate,
                    total_trades, max_drawdown_pct, summary_json, trades_json,
                    equity_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    result.run_id,
                    result.strategy_id,
                    result.strategy_name,
                    result.symbol,
                    result.timeframe,
                    result.from_date.isoformat(),
                    result.to_date.isoformat(),
                    result.summary.initial_capital,
                    result.summary.net_pnl,
                    result.summary.win_rate,
                    result.summary.total_trades,
                    result.summary.max_drawdown_pct,
                    summary_json,
                    trades_json,
                    equity_json,
                    now_iso,
                ),
            )
        logger.info(f"Saved backtest run {result.run_id} ({result.strategy_name}) to database.")

    @staticmethod
    def get(run_id: str) -> Optional[BacktestResult]:
        """Retrieves and deserializes a backtest run by ID."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, strategy_id, strategy_name, symbol, timeframe,
                       from_date, to_date, summary_json, trades_json,
                       equity_json, created_at
                FROM backtest_runs
                WHERE id = ?;
                """,
                (run_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

        summary = BacktestSummary.model_validate_json(row["summary_json"])
        trades_raw = json.loads(row["trades_json"])
        trades = [BacktestTrade.model_validate(t) for t in trades_raw]
        equity_raw = json.loads(row["equity_json"])
        equity_curve = [EquityPoint.model_validate(e) for e in equity_raw]

        return BacktestResult(
            run_id=row["id"],
            strategy_id=row["strategy_id"],
            strategy_name=row["strategy_name"],
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            from_date=datetime.fromisoformat(row["from_date"]),
            to_date=datetime.fromisoformat(row["to_date"]),
            summary=summary,
            trades=trades,
            equity_curve=equity_curve,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def list_all(limit: int = 50) -> List[Dict[str, Any]]:
        """Returns metadata list of recent backtest runs."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, strategy_id, strategy_name, symbol, timeframe,
                       from_date, to_date, initial_capital, net_pnl, win_rate,
                       total_trades, max_drawdown_pct, created_at
                FROM backtest_runs
                ORDER BY created_at DESC
                LIMIT ?;
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def delete(run_id: str) -> bool:
        """Deletes a backtest record by ID."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM backtest_runs WHERE id = ?", (run_id,))
            return cursor.rowcount > 0
