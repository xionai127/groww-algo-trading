"""FastAPI Router for Centralized Risk Management and Trade Journaling."""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status

from config import logger, settings
from database import get_db_connection
from risk_management.manager import risk_manager
from risk_management.models import (
    CircuitBreakerStatusResponse,
    DailyDisciplineScorecard,
    DailyRiskStatus,
    JournalAnalytics,
    JournalEntryResponse,
    KillSwitchRequest,
    PreTradeValidationRequest,
    ResetBreakerRequest,
    RiskAssessment,
    UpdateJournalRequest,
)

router = APIRouter(prefix="/api", tags=["Risk Management & Journaling"])


# ==========================================
# Risk Management Endpoints
# ==========================================

@router.get("/risk/status", response_model=DailyRiskStatus)
async def get_risk_status(mode: str = Query("PAPER", description="Execution mode ('PAPER' or 'LIVE')")) -> DailyRiskStatus:
    """Returns current intraday risk status, loss consumed, and circuit breaker status."""
    return risk_manager.get_daily_status(mode=mode)


@router.get("/risk/breaker", response_model=CircuitBreakerStatusResponse)
async def get_breaker_status() -> CircuitBreakerStatusResponse:
    """Returns detailed circuit breaker and emergency kill switch status."""
    return risk_manager.circuit_breaker.get_status()


@router.post("/risk/validate", response_model=RiskAssessment)
async def validate_trade(
    request: PreTradeValidationRequest,
    enforce_hours: bool = Query(True, description="Whether to strictly enforce market hours"),
) -> RiskAssessment:
    """Pre-flight check to validate an order against platform risk rules."""
    return risk_manager.validate_order(request, enforce_hours=enforce_hours)


@router.post("/risk/kill-switch")
async def activate_kill_switch(payload: KillSwitchRequest) -> Dict[str, Any]:
    """Activates emergency kill switch immediately halting trading and optionally closing positions."""
    logger.critical(f"Manual emergency kill switch activated. Reason: {payload.reason}")
    if payload.liquidate_open_positions:
        result = risk_manager.panic_square_off(reason=payload.reason)
        return {
            "status": "success",
            "message": "Circuit breaker engaged and open positions liquidated.",
            "details": result,
        }
    else:
        risk_manager.trip_circuit_breaker(reason=payload.reason)
        return {
            "status": "success",
            "message": "Circuit breaker engaged. Trading halted.",
            "circuit_breaker_tripped": True,
        }


@router.post("/risk/reset-breaker")
async def reset_circuit_breaker(payload: ResetBreakerRequest) -> Dict[str, Any]:
    """Manually resets a tripped circuit breaker back to NORMAL."""
    if not payload.confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation flag must be True to reset circuit breaker.",
        )
    risk_manager.reset_circuit_breaker(reason=payload.reason)
    return {
        "status": "success",
        "message": "Circuit breaker successfully reset. Trading allowed.",
        "state": risk_manager.circuit_breaker.state.value,
    }


@router.get("/risk/events")
async def get_risk_events(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    mode: Optional[str] = Query(None, description="Filter by mode"),
) -> List[Dict[str, Any]]:
    """Retrieves paginated audit log of risk rejections and safety events."""
    query = "SELECT * FROM risk_events WHERE 1=1"
    params: List[Any] = []
    if mode:
        query += " AND mode = ?"
        params.append(mode)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?;"
    params.extend([limit, offset])

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()

    results = []
    for r in rows:
        results.append({
            "id": r["id"],
            "event_type": r["event_type"],
            "reason": r["reason"],
            "details": json.loads(r["details_json"]) if r["details_json"] else {},
            "mode": r["mode"],
            "created_at": r["created_at"],
        })
    return results


# ==========================================
# Trade Journaling Endpoints
# ==========================================

@router.get("/journal/entries", response_model=List[JournalEntryResponse])
async def list_journal_entries(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    mode: Optional[str] = Query(None, description="Filter by mode ('PAPER' or 'LIVE')"),
    date_str: Optional[str] = Query(None, description="Filter by date (YYYY-MM-DD)"),
) -> List[JournalEntryResponse]:
    """Retrieves trade journal entries with optional search and filters."""
    return risk_manager.journal_manager.get_journal_entries(
        limit=limit,
        offset=offset,
        tag=tag,
        mode=mode,
        date_str=date_str,
    )


@router.get("/journal/entries/{entry_id}", response_model=JournalEntryResponse)
async def get_journal_entry(entry_id: str) -> JournalEntryResponse:
    """Retrieves a specific trade journal entry by its ID."""
    entry = risk_manager.journal_manager.get_journal_entry(entry_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Journal entry {entry_id} not found.",
        )
    return entry


@router.put("/journal/entries/{entry_id}", response_model=JournalEntryResponse)
@router.patch("/journal/entries/{entry_id}", response_model=JournalEntryResponse)
async def update_journal_entry(entry_id: str, payload: UpdateJournalRequest) -> JournalEntryResponse:
    """Updates psychological notes, custom tags, rating, and mistake flags for a trade."""
    updated = risk_manager.journal_manager.update_journal_entry(
        entry_id=entry_id,
        tags=payload.tags,
        notes=payload.notes,
        rating=payload.rating,
        mistake_flag=payload.mistake_flag,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Journal entry {entry_id} not found.",
        )
    return updated


@router.get("/journal/analytics", response_model=JournalAnalytics)
async def get_journal_analytics(
    mode: Optional[str] = Query(None, description="Filter by mode ('PAPER' or 'LIVE')"),
) -> JournalAnalytics:
    """Aggregates trading psychology metrics, win rates by strategy tag, and fee analysis."""
    return risk_manager.journal_manager.get_journal_analytics(mode=mode)


@router.get("/journal/scorecard", response_model=DailyDisciplineScorecard)
async def get_daily_scorecard(
    date_str: Optional[str] = Query(None, description="Date in YYYY-MM-DD format (defaults to today)"),
    mode: Optional[str] = Query(None, description="Filter by mode"),
) -> DailyDisciplineScorecard:
    """Generates an automated daily discipline scorecard and rule compliance grade."""
    if not date_str:
        now_ist = datetime.now(timezone.utc)
        date_str = now_ist.strftime("%Y-%m-%d")
    return risk_manager.journal_manager.get_daily_scorecard(date_str=date_str, mode=mode)
