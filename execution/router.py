"""FastAPI router for Groww live order execution and broker management."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from config import ExecutionMode, logger, settings
from database.db import get_db_connection
from execution.broker_client import broker_client
from execution.engine import live_engine
from execution.models import (
    BrokerAuthRequest,
    BrokerAuthStatus,
    ExecutionModeToggleRequest,
    LiveAccountSummary,
    LiveOrderRequest,
    LiveOrderResponse,
    LivePosition,
)

execution_router = APIRouter(prefix="/api/execution", tags=["Live Execution & Broker"])


@execution_router.get("/status", response_model=LiveAccountSummary)
def get_execution_status() -> LiveAccountSummary:
    """Returns current live execution state, broker connectivity, and intraday risk health."""
    return live_engine.get_summary()


@execution_router.post("/mode")
def set_execution_mode(payload: ExecutionModeToggleRequest) -> Dict[str, Any]:
    """
    Switches system execution mode between ANALYSIS, PAPER, and LIVE.
    Requires explicit confirmation payload ('CONFIRM_LIVE_TRADING') when enabling LIVE mode.
    """
    target_mode = payload.mode.strip().upper()
    if target_mode not in ExecutionMode.__members__:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid execution mode: '{payload.mode}'. Must be ANALYSIS, PAPER, or LIVE.",
        )

    # Safety confirmation gate for LIVE mode
    if target_mode == ExecutionMode.LIVE.value:
        if payload.confirmation != "CONFIRM_LIVE_TRADING":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Activating LIVE mode requires explicit confirmation: {'confirmation': 'CONFIRM_LIVE_TRADING'}",
            )

    previous_mode = settings.execution_mode.value
    settings.execution_mode = ExecutionMode[target_mode]

    # Audit log
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO audit_logs (action, details, created_at) VALUES (?, ?, ?)",
            (
                "EXECUTION_MODE_CHANGE",
                f"Mode transitioned from {previous_mode} to {target_mode}",
                now_iso,
            ),
        )

    logger.warning(f"SYSTEM EXECUTION MODE SWITCHED: {previous_mode} -> {target_mode}")
    return {
        "status": "success",
        "previous_mode": previous_mode,
        "current_mode": target_mode,
        "message": f"Execution mode successfully changed to {target_mode}.",
    }


@execution_router.post("/auth/test", response_model=Dict[str, Any])
async def test_broker_authentication(payload: Optional[BrokerAuthRequest] = None) -> Dict[str, Any]:
    """Validates Groww credentials and checks connectivity to broker endpoints."""
    if payload and (payload.api_key or payload.totp_key or payload.access_token):
        await broker_client.authenticate(
            api_key=payload.api_key,
            api_secret=payload.api_secret,
            totp_key=payload.totp_key,
            access_token=payload.access_token,
        )

    result = await broker_client.test_connection()
    return result


@execution_router.get("/margin")
async def get_broker_margin() -> Dict[str, Any]:
    """Fetches real-time available trading margin from Groww."""
    try:
        margin = await broker_client.get_available_margin()
        return {
            "status": "success",
            "available_margin": margin,
            "currency": "INR",
        }
    except Exception as e:
        logger.error(f"Error fetching broker margin: {e}")
        return {
            "status": "error",
            "message": str(e),
            "available_margin": 0.0,
        }


@execution_router.get("/positions", response_model=List[LivePosition])
def get_live_positions(open_only: bool = Query(default=True)) -> List[LivePosition]:
    """Retrieves locally tracked live option positions."""
    if open_only:
        return list(live_engine.open_positions.values())

    # Fetch all live positions including closed ones from DB
    results: List[LivePosition] = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM live_positions ORDER BY created_at DESC LIMIT 100;")
        for r in cursor.fetchall():
            pos = LivePosition(
                id=r["id"],
                broker_order_id=r["broker_order_id"],
                strategy_id=r["strategy_id"],
                symbol=r["symbol"],
                contract_symbol=r["contract_symbol"],
                direction=r["direction"],
                strike_price=float(r["strike_price"]),
                expiry_date=r["expiry_date"],
                quantity=int(r["quantity"]),
                entry_price=float(r["entry_price"]),
                entry_spot=float(r["entry_spot"]),
                entry_time=datetime.fromisoformat(r["entry_time"]),
                current_price=float(r["current_price"]),
                current_spot=float(r["current_spot"]),
                unrealized_pnl=float(r["unrealized_pnl"]),
                return_pct=float(r["return_pct"]),
                target_price=float(r["target_price"]) if r["target_price"] is not None else None,
                stop_loss_price=float(r["stop_loss_price"]) if r["stop_loss_price"] is not None else None,
                target_pct=float(r["target_pct"]) if r["target_pct"] is not None else None,
                stop_loss_pct=float(r["stop_loss_pct"]) if r["stop_loss_pct"] is not None else None,
                is_open=bool(r["is_open"]),
                exit_time=datetime.fromisoformat(r["exit_time"]) if r["exit_time"] else None,
                exit_price=float(r["exit_price"]) if r["exit_price"] is not None else None,
                exit_reason=r["exit_reason"],
                gross_pnl=float(r["gross_pnl"]),
                brokerage=float(r["brokerage"]),
                taxes=float(r["taxes"]),
                net_pnl=float(r["net_pnl"]),
                created_at=datetime.fromisoformat(r["created_at"]),
                updated_at=datetime.fromisoformat(r["updated_at"]),
            )
            results.append(pos)
    return results


@execution_router.get("/orders")
def get_live_orders(limit: int = Query(default=50, ge=1, le=200)) -> List[Dict[str, Any]]:
    """Retrieves live orders audit history from SQLite."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, broker_order_id, mode, symbol, instrument_type,
                   transaction_type, order_type, quantity, price, status,
                   rejection_reason, strategy_id, created_at
            FROM orders
            WHERE mode = 'LIVE'
            ORDER BY created_at DESC
            LIMIT ?;
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]


@execution_router.post("/order", response_model=Dict[str, Any])
async def submit_live_order(order_req: LiveOrderRequest) -> Dict[str, Any]:
    """Submits manual live options buying order with pre-flight risk checks."""
    order, pos = await live_engine.submit_order(order_req)
    return {
        "order": order.model_dump(),
        "position": pos.model_dump() if pos else None,
        "is_filled": order.status.value == "FILLED",
    }


@execution_router.post("/positions/{position_id}/close", response_model=LivePosition)
async def close_live_position(position_id: str, reason: str = Query(default="MANUAL_API")) -> LivePosition:
    """Closes an active live position immediately."""
    try:
        closed = await live_engine.close_position(position_id, reason=reason)
        return closed
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Open live position '{position_id}' not found.",
        )
    except Exception as e:
        logger.error(f"Error closing live position {position_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to close position: {e}",
        )


@execution_router.post("/square-off-all")
async def square_off_all_live_positions() -> Dict[str, Any]:
    """Emergency endpoint: Liquidates all open live positions immediately."""
    closed = await live_engine.close_all_live_positions(reason="PANIC_SQUARE_OFF")
    return {
        "status": "success",
        "closed_count": len(closed),
        "positions": [c.model_dump() for c in closed],
    }
