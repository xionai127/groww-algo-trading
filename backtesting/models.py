"""Domain schemas for algorithmic options backtesting."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
import uuid
from pydantic import BaseModel, Field

from strategies.models import StrategyConfig, StrikePreference


class ExitReason(str, Enum):
    """Reason for closing an options position during backtesting."""
    TARGET = "TARGET"
    STOP_LOSS = "STOP_LOSS"
    SQUARE_OFF = "SQUARE_OFF"          # Auto 15:15 IST intraday close
    EXIT_SIGNAL = "EXIT_SIGNAL"        # Strategy explicit exit signal
    END_OF_DATA = "END_OF_DATA"        # Backtest duration ended with open position


class BacktestRequest(BaseModel):
    """Parameters to launch a historical backtest run."""
    strategy_id: Optional[str] = None
    strategy_config: Optional[StrategyConfig] = None
    symbol: str = Field(default="NIFTY", description="Underlying index e.g. NIFTY, SENSEX")
    timeframe: str = Field(default="5m", description="Candle interval e.g. 1m, 5m, 15m")
    from_date: datetime
    to_date: datetime
    initial_capital: float = Field(default=10000.0, ge=1000.0)
    lot_size: Optional[int] = Field(default=None, description="Contract lot size (defaults to 25 for NIFTY, 10 for SENSEX)")
    slippage_pct: float = Field(default=0.5, ge=0.0, le=10.0, description="Slippage percentage per trade")
    brokerage_per_order: float = Field(default=20.0, ge=0.0, description="Brokerage fee per leg in INR")
    stt_rate: float = Field(default=0.000625, description="Securities Transaction Tax on sell (0.0625%)")
    turnover_tax_rate: float = Field(default=0.00003, description="Exchange turnover charge")
    gst_rate: float = Field(default=0.18, description="GST on brokerage + turnover fees (18%)")
    stamp_duty_rate: float = Field(default=0.00003, description="Stamp duty on buy (0.003%)")
    iv_default: float = Field(default=0.16, ge=0.01, le=2.0, description="Default IV for option pricing proxy (16%)")


class BacktestTrade(BaseModel):
    """Record of an individual options trade executed during the backtest."""
    trade_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str
    symbol: str
    contract_symbol: str
    direction: Literal["CE", "PE"]
    strike_price: float
    expiry_date: str

    entry_time: datetime
    entry_price: float
    entry_spot: float

    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_spot: Optional[float] = None
    exit_reason: Optional[ExitReason] = None

    quantity: int
    gross_pnl: float = 0.0
    brokerage: float = 0.0
    taxes: float = 0.0
    net_pnl: float = 0.0
    return_pct: float = 0.0
    holding_candles: int = 0


class EquityPoint(BaseModel):
    """Snapshot of portfolio equity and drawdown over time."""
    timestamp: datetime
    equity: float
    cash: float
    drawdown: float
    drawdown_pct: float


class BacktestSummary(BaseModel):
    """Statistical and performance metrics for a backtest run."""
    initial_capital: float
    final_capital: float
    gross_pnl: float
    total_charges: float
    net_pnl: float
    roi_pct: float

    total_trades: int
    winning_trades: int
    losing_trades: int
    break_even_trades: int
    win_rate: float
    loss_rate: float

    profit_factor: float
    max_drawdown: float
    max_drawdown_pct: float

    avg_trade_pnl: float
    avg_win: float
    avg_loss: float
    risk_reward_ratio: float

    max_consecutive_wins: int
    max_consecutive_losses: int

    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float


class BacktestResult(BaseModel):
    """Complete backtest output including metadata, stats, trades, and equity curve."""
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str
    strategy_name: str
    symbol: str
    timeframe: str
    from_date: datetime
    to_date: datetime
    summary: BacktestSummary
    trades: List[BacktestTrade]
    equity_curve: List[EquityPoint]
    daily_pnl: Dict[str, float] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
