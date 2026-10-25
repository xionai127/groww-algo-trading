"""Domain models for trading signals, strategy configurations, and strike selection."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
import uuid
from pydantic import BaseModel, Field


class SignalAction(str, Enum):
    """Trading signal action."""
    ENTRY = "ENTRY"
    EXIT = "EXIT"


class StrikePreference(str, Enum):
    """Strike selection preference relative to spot."""
    ATM = "ATM"       # At the money (closest strike)
    ITM_1 = "ITM_1"   # 1 strike In The Money
    ITM_2 = "ITM_2"   # 2 strikes In The Money
    OTM_1 = "OTM_1"   # 1 strike Out of The Money
    OTM_2 = "OTM_2"   # 2 strikes Out of The Money


class Signal(BaseModel):
    """Signal produced by a strategy upon market trigger."""
    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str
    action: SignalAction = SignalAction.ENTRY
    underlying: str = Field(description="Underlying index, e.g. NIFTY, SENSEX")
    direction: Literal["CE", "PE"] = Field(description="Strictly options buying: CE or PE")
    strike_preference: StrikePreference = StrikePreference.ATM
    spot_reference_price: float = Field(gt=0)
    stop_loss_pct: float = Field(default=10.0, gt=0, le=100.0, description="Stop loss percent on option premium")
    target_pct: float = Field(default=20.0, gt=0, description="Take profit target percent on option premium")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    rationale: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConditionOperator(str, Enum):
    """Comparison operator for rule-based strategy conditions."""
    GREATER_THAN = ">"
    LESS_THAN = "<"
    GREATER_THAN_EQUAL = ">="
    LESS_THAN_EQUAL = "<="
    CROSSES_ABOVE = "CROSSES_ABOVE"
    CROSSES_BELOW = "CROSSES_BELOW"


class IndicatorCondition(BaseModel):
    """Single conditional expression evaluated on indicators/prices."""
    left: str = Field(description="Indicator or price source, e.g. 'ema_fast', 'rsi', 'close'")
    operator: ConditionOperator
    right: Union[str, float] = Field(description="Indicator or scalar, e.g. 'ema_slow' or 50.0")


class RuleBlock(BaseModel):
    """A logical combination of indicator conditions."""
    logic: Literal["AND", "OR"] = "AND"
    conditions: List[IndicatorCondition] = Field(default_factory=list)


class StrategyConfig(BaseModel):
    """Schema for defining and persisting algorithmic strategies."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    version: int = 1
    description: str = ""
    underlying: str = "NIFTY"
    timeframe: str = "5m"
    is_active: bool = False
    is_primary: bool = False

    # Strike selection & options buying rules
    direction: Literal["CE", "PE", "BOTH"] = "BOTH"
    strike_preference: StrikePreference = StrikePreference.ATM

    # Financial & Risk parameters
    stop_loss_pct: float = Field(default=10.0, gt=0)
    target_pct: float = Field(default=20.0, gt=0)
    max_trades_per_day: int = Field(default=3, ge=1, le=20)

    # Indicator configurations (e.g. {"ema_fast": {"type": "EMA", "period": 9}})
    indicators: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    # Entry rules
    entry_rules_ce: Optional[RuleBlock] = None
    entry_rules_pe: Optional[RuleBlock] = None

    # Exit rules
    exit_rules_ce: Optional[RuleBlock] = None
    exit_rules_pe: Optional[RuleBlock] = None
