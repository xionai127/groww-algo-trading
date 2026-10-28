"""Domain schemas and models for realistic paper trading."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
import uuid
from pydantic import BaseModel, Field

from strategies.models import StrikePreference


class OrderStatus(str, Enum):
    """Lifecycle status of a paper order."""
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class OrderType(str, Enum):
    """Execution type for paper orders."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TransactionType(str, Enum):
    """Order transaction side."""
    BUY = "BUY"
    SELL = "SELL"


class ExitReason(str, Enum):
    """Reason for closing an active paper position."""
    TARGET = "TARGET"
    STOP_LOSS = "STOP_LOSS"
    SQUARE_OFF = "SQUARE_OFF"          # Auto 15:15 IST intraday close
    EXIT_SIGNAL = "EXIT_SIGNAL"        # Strategy-triggered exit
    MANUAL = "MANUAL"                  # User manually closed from UI/API
    RISK_BREACH = "RISK_BREACH"        # Closed due to hard risk breach (daily loss)


class PaperOrder(BaseModel):
    """Record of an order submitted in PAPER mode."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    broker_order_id: Optional[str] = None
    mode: str = "PAPER"
    symbol: str
    instrument_type: str = Field(description="Option contract type: CE or PE")
    contract_symbol: str
    strike_price: float
    expiry_date: str
    transaction_type: TransactionType
    order_type: OrderType = OrderType.MARKET
    quantity: int
    price: float
    status: OrderStatus = OrderStatus.PENDING
    rejection_reason: Optional[str] = None
    strategy_id: Optional[str] = None
    executed_price: Optional[float] = None
    brokerage: float = 0.0
    taxes: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PaperTrade(BaseModel):
    """Record of an executed fill in PAPER mode."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str
    symbol: str
    contract_symbol: str
    mode: str = "PAPER"
    transaction_type: TransactionType
    quantity: int
    executed_price: float
    brokerage: float = 0.0
    taxes: float = 0.0
    net_pnl: float = 0.0
    strategy_id: Optional[str] = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PaperPosition(BaseModel):
    """Active or historical options position in PAPER mode."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: Optional[str] = None
    symbol: str
    contract_symbol: str
    direction: Literal["CE", "PE"]
    strike_price: float
    expiry_date: str
    quantity: int

    entry_price: float
    entry_spot: float
    entry_time: datetime

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


class PaperAccount(BaseModel):
    """Snapshot of virtual account capital, margins, and performance."""
    initial_capital: float = 10000.0
    cash: float = 10000.0
    allocated_margin: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_equity: float = 10000.0

    daily_trades_count: int = 0
    daily_realized_pnl: float = 0.0
    daily_loss: float = 0.0
    is_trading_blocked: bool = False
    blocked_reason: Optional[str] = None

    open_positions_count: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PaperOrderRequest(BaseModel):
    """Parameters to submit a manual or automated paper order."""
    symbol: str = Field(default="NIFTY", description="Underlying index e.g. NIFTY, SENSEX")
    direction: Literal["CE", "PE"]
    strike_preference: Optional[StrikePreference] = StrikePreference.ATM
    strike_price: Optional[float] = None
    expiry_date: Optional[str] = None
    quantity: Optional[int] = None
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    target_pct: Optional[float] = Field(default=None, description="Target percent profit (e.g. 20.0)")
    stop_loss_pct: Optional[float] = Field(default=None, description="Stop loss percent (e.g. 10.0)")
    strategy_id: Optional[str] = "manual"
