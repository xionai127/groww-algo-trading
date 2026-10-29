"""Data models and schemas for Centralized Risk Management and Trade Journaling."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RiskDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WARNING = "WARNING"


class BreakerState(str, Enum):
    NORMAL = "NORMAL"
    TRIPPED = "TRIPPED"
    COOLING_DOWN = "COOLING_DOWN"


class RuleViolation(str, Enum):
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    MAX_POSITIONS_LIMIT = "MAX_POSITIONS_LIMIT"
    MAX_DAILY_TRADES_LIMIT = "MAX_DAILY_TRADES_LIMIT"
    RISK_PER_TRADE_LIMIT = "RISK_PER_TRADE_LIMIT"
    MARKET_HOURS_CLOSED = "MARKET_HOURS_CLOSED"
    SQUARE_OFF_RESTRICTION = "SQUARE_OFF_RESTRICTION"
    OPTIONS_BUYING_ONLY = "OPTIONS_BUYING_ONLY"
    CAPITAL_EXCEEDED = "CAPITAL_EXCEEDED"
    CIRCUIT_BREAKER_ACTIVE = "CIRCUIT_BREAKER_ACTIVE"
    CONSECUTIVE_LOSS_LIMIT = "CONSECUTIVE_LOSS_LIMIT"


class TradeTag(str, Enum):
    TREND_FOLLOWING = "TREND_FOLLOWING"
    MOMENTUM = "MOMENTUM"
    MEAN_REVERSION = "MEAN_REVERSION"
    BREAKOUT = "BREAKOUT"
    EXPIRY_DAY = "EXPIRY_DAY"
    REVENGE_TRADE = "REVENGE_TRADE"
    FOMO = "FOMO"
    RULE_VIOLATION = "RULE_VIOLATION"
    DISCIPLINE_WIN = "DISCIPLINE_WIN"
    MANUAL_INTERVENTION = "MANUAL_INTERVENTION"
    OTHER = "OTHER"


class PreTradeValidationRequest(BaseModel):
    """Request payload for validating a trade before order placement."""
    symbol: str = Field(..., description="Underlying index symbol, e.g. NIFTY or SENSEX")
    direction: str = Field(..., description="'CE' or 'PE'")
    transaction_type: str = Field("BUY", description="'BUY' only (Short options strictly blocked)")
    quantity: int = Field(..., gt=0, description="Order quantity")
    estimated_price: float = Field(..., gt=0, description="Estimated entry price per unit")
    stop_loss_pct: Optional[float] = Field(None, ge=0.0, description="Stop loss percentage")
    target_pct: Optional[float] = Field(None, ge=0.0, description="Target profit percentage")
    strategy_id: Optional[str] = Field(None, description="Originating strategy ID")
    mode: str = Field("PAPER", description="'PAPER' or 'LIVE'")
    current_time: Optional[datetime] = Field(None, description="Optional override for evaluation timestamp")


class RiskAssessment(BaseModel):
    """Result of risk pre-flight assessment."""
    allowed: bool
    decision: RiskDecision
    rejection_reason: Optional[str] = None
    violation_type: Optional[RuleViolation] = None
    max_allowed_quantity: Optional[int] = None
    risk_amount: float = 0.0
    circuit_breaker_active: bool = False
    warnings: List[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CircuitBreakerStatusResponse(BaseModel):
    """Current state of circuit breaker and kill-switch."""
    state: BreakerState
    is_tripped: bool
    tripped_at: Optional[datetime] = None
    trip_reason: Optional[str] = None
    auto_tripped: bool = False
    consecutive_losses: int = 0


class KillSwitchRequest(BaseModel):
    """Manual emergency stop request."""
    reason: str = Field("Manual Emergency Kill Switch Activated", description="Reason for stopping trading")
    liquidate_open_positions: bool = Field(True, description="Whether to close all active open positions immediately")


class ResetBreakerRequest(BaseModel):
    """Request to reset a tripped breaker."""
    reason: str = Field("Manual Breaker Reset", description="Audit reason for breaker reset")
    confirmed: bool = Field(True, description="Confirmation flag")


class DailyRiskStatus(BaseModel):
    """Comprehensive daily risk tracking summary."""
    date: str
    mode: str
    starting_capital: float
    current_equity: float
    cash: float
    daily_realized_pnl: float
    daily_unrealized_pnl: float
    daily_net_pnl: float
    daily_loss_limit: float
    daily_loss_consumed: float
    daily_loss_remaining: float
    daily_loss_limit_reached: bool
    daily_trades_count: int
    max_trades_per_day: int
    open_positions_count: int
    max_open_positions: int
    circuit_breaker_tripped: bool
    breaker_reason: Optional[str] = None
    is_trading_allowed: bool


class JournalEntryResponse(BaseModel):
    """A single trade journal entry enriched with review and discipline metrics."""
    id: str
    trade_id: str
    order_id: Optional[str] = None
    mode: str
    symbol: str
    contract_symbol: str
    direction: str
    quantity: int
    entry_price: float
    exit_price: Optional[float] = None
    entry_time: str
    exit_time: Optional[str] = None
    gross_pnl: float = 0.0
    brokerage: float = 0.0
    taxes: float = 0.0
    net_pnl: float = 0.0
    return_pct: float = 0.0
    exit_reason: Optional[str] = None
    strategy_id: Optional[str] = None
    strategy_name: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    rating: Optional[int] = Field(None, ge=1, le=5, description="Self rating from 1 (poor) to 5 (excellent)")
    mistake_flag: bool = False
    discipline_score: int = Field(100, ge=0, le=100)
    created_at: str
    updated_at: str


class UpdateJournalRequest(BaseModel):
    """Request to add tags, notes, and rating to a journal entry."""
    tags: Optional[List[str]] = None
    notes: Optional[str] = None
    rating: Optional[int] = Field(None, ge=1, le=5)
    mistake_flag: Optional[bool] = None


class JournalAnalytics(BaseModel):
    """Overall trading psychology and tag performance analytics."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    total_gross_pnl: float
    total_charges: float
    total_net_pnl: float
    tags_breakdown: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    mistakes_count: int = 0
    average_discipline_score: float = 100.0


class DailyDisciplineScorecard(BaseModel):
    """Scorecard grading intraday execution discipline and adherence to rules."""
    date: str
    grade: str
    discipline_score: int
    total_trades: int
    violations_count: int
    violations_list: List[str] = Field(default_factory=list)
    daily_loss_breached: bool = False
    overtrading_breached: bool = False
    summary_notes: str = ""
