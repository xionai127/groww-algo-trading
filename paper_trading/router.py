"""FastAPI router for Paper Trading account management, positions, and order routing."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from paper_trading.engine import PaperTradingEngine
from paper_trading.models import PaperAccount, PaperOrder, PaperOrderRequest, PaperPosition

paper_router = APIRouter(prefix="/api/paper", tags=["Paper Trading Engine"])
paper_engine = PaperTradingEngine()


class ResetAccountRequest(BaseModel):
    initial_capital: float = Field(default=10000.0, ge=1000.0, description="Virtual capital to reset account")


class ManualCloseRequest(BaseModel):
    reason: str = Field(default="MANUAL", description="Reason for closing position")
    exit_price: Optional[float] = Field(default=None, description="Optional override for exit price")


class MtmUpdateRequest(BaseModel):
    symbol: str = "NIFTY"
    spot: float
    option_quote_price: Optional[float] = None


@paper_router.get("/account", response_model=PaperAccount)
def get_paper_account() -> PaperAccount:
    """Returns virtual account summary, available cash, equity, and daily metrics."""
    return paper_engine.get_account_summary()


@paper_router.get("/positions", response_model=List[PaperPosition])
def list_positions(open_only: bool = Query(default=False)) -> List[PaperPosition]:
    """Returns open or historical paper trading positions."""
    return paper_engine.get_positions(open_only=open_only)


@paper_router.get("/positions/{position_id}", response_model=PaperPosition)
def get_position(position_id: str) -> PaperPosition:
    """Fetches details for a specific paper position."""
    pos = paper_engine.open_positions.get(position_id)
    if not pos:
        positions = paper_engine.get_positions(open_only=False)
        matched = [p for p in positions if p.id == position_id]
        if not matched:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Paper position {position_id} not found")
        return matched[0]
    return pos


@paper_router.post("/positions/{position_id}/close", response_model=PaperPosition)
def close_paper_position(position_id: str, payload: ManualCloseRequest = ManualCloseRequest()) -> PaperPosition:
    """Manually squares off an active open paper position."""
    try:
        closed = paper_engine.close_position(position_id, reason=payload.reason, exit_price=payload.exit_price)
        return closed
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to close position: {e}")


@paper_router.post("/orders")
def place_paper_order(request: PaperOrderRequest) -> Dict[str, Any]:
    """Submits a manual paper trading order."""
    order, position = paper_engine.submit_order(request)
    return {
        "order": order.model_dump(mode="json"),
        "position": position.model_dump(mode="json") if position else None,
        "is_filled": order.status.value == "FILLED",
        "message": f"Order {order.status.value}" + (f": {order.rejection_reason}" if order.rejection_reason else ""),
    }


@paper_router.get("/orders", response_model=List[Dict[str, Any]])
def list_paper_orders(limit: int = Query(default=50, ge=1, le=200)) -> List[Dict[str, Any]]:
    """Lists submitted paper orders."""
    return paper_engine.get_orders(limit=limit)


@paper_router.get("/trades", response_model=List[Dict[str, Any]])
def list_paper_trades(limit: int = Query(default=50, ge=1, le=200)) -> List[Dict[str, Any]]:
    """Lists executed paper fills."""
    return paper_engine.get_trades(limit=limit)


@paper_router.post("/reset", response_model=PaperAccount)
def reset_paper_account(payload: ResetAccountRequest = ResetAccountRequest()) -> PaperAccount:
    """Resets paper account balance to initial capital and squares off open positions."""
    return paper_engine.reset_account(initial_capital=payload.initial_capital)


@paper_router.post("/mtm-update")
def update_mtm(payload: MtmUpdateRequest) -> Dict[str, Any]:
    """Manually feeds spot/quote to update position MTM and check SL/TP/Square-off."""
    closed = paper_engine.update_market_price(
        symbol=payload.symbol,
        spot=payload.spot,
        option_quote_price=payload.option_quote_price,
    )
    return {
        "status": "success",
        "positions_closed": len(closed),
        "closed_details": [c.model_dump(mode="json") for c in closed],
        "account": paper_engine.get_account_summary().model_dump(mode="json"),
    }
