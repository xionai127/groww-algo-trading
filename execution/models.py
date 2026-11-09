"""Domain models and schemas for live broker execution via Groww API."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field

from paper_trading.models import OrderStatus, OrderType, TransactionType
from strategies.models import StrikePreference


class LiveOrderRequest(BaseModel):
    """Client request model for initiating a live order."""
    symbol: str = Field(..., description="Underlying index symbol (e.g., NIFTY, SENSEX)")
    direction: str = Field(..., description="Option direction ('CE' or 'PE')")
    strike_preference: StrikePreference = Field(
        default=StrikePreference.ATM,
        description="Strike selection relative to spot (ATM, ITM1, ITM2, OTM1, OTM2)"
    )
    order_type: OrderType = Field(default=OrderType.MARKET, description="Order type")
    price: Optional[float] = Field(default=0.0, ge=0.0, description="Limit price if LIMIT order")
    quantity: Optional[int] = Field(None, gt=0, description="Contract quantity. If None, derived from risk rule.")
    product_type: str = Field(default="MIS", description="Groww product type ('MIS' for intraday, 'NRML' for delivery)")
    strategy_id: Optional[str] = Field(default="MANUAL", description="Source strategy ID")
    target_pct: Optional[float] = Field(None, gt=0.0, description="Take-profit percentage override")
    stop_loss_pct: Optional[float] = Field(None, gt=0.0, description="Stop-loss percentage override")


class LiveOrderResponse(BaseModel):
    """Response returned upon live order submission."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    broker_order_id: Optional[str] = None
    symbol: str
    contract_symbol: str
    transaction_type: TransactionType = TransactionType.BUY
    order_type: OrderType = OrderType.MARKET
    product_type: str = "MIS"
    quantity: int
    price: float
    status: OrderStatus
    rejection_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LivePosition(BaseModel):
    """Tracks an active or closed live F&O option contract."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    broker_order_id: Optional[str] = None
    strategy_id: Optional[str] = None
    symbol: str
    contract_symbol: str
    direction: str
    strike_price: float
    expiry_date: str
    quantity: int
    entry_price: float
    entry_spot: float
    entry_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    current_price: float
    current_spot: float
    unrealized_pnl: float = 0.0
    return_pct: float = 0.0
    target_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    target_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    is_open: bool = True
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    gross_pnl: float = 0.0
    brokerage: float = 0.0
    taxes: float = 0.0
    net_pnl: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LiveAccountSummary(BaseModel):
    """Live broker balance, risk parameters, and position summary."""
    broker_connected: bool
    execution_mode: str
    available_margin: float = 0.0
    used_margin: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    open_positions_count: int = 0
    daily_trades_count: int = 0
    daily_loss: float = 0.0
    is_trading_blocked: bool = False
    blocked_reason: Optional[str] = None


class ExecutionModeToggleRequest(BaseModel):
    """Request payload to switch platform execution mode."""
    mode: str = Field(..., description="Target mode ('ANALYSIS', 'PAPER', 'LIVE')")
    confirmation: Optional[str] = Field(
        None,
        description="Explicit safety confirmation string ('CONFIRM_LIVE_TRADING' required for LIVE mode)"
    )


class BrokerAuthRequest(BaseModel):
    """Request payload for updating and testing Groww credentials."""
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    totp_key: Optional[str] = None
    access_token: Optional[str] = None


class BrokerAuthStatus(BaseModel):
    """Authentication state and connectivity status with Groww API."""
    is_authenticated: bool
    has_api_key: bool
    has_access_token: bool
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    error_message: Optional[str] = None
