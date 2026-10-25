"""Market Data domain models and schemas for Groww Algo Trading."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class Timeframe(str, Enum):
    """Supported candle aggregation timeframes."""
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    D1 = "1d"

    @property
    def seconds(self) -> int:
        """Returns the timeframe duration in seconds."""
        multipliers = {
            "1m": 60,
            "3m": 180,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "1d": 86400,
        }
        return multipliers[self.value]


class OptionType(str, Enum):
    """Option contract types."""
    CE = "CE"  # Call European
    PE = "PE"  # Put European


class Tick(BaseModel):
    """Real-time market price update tick."""
    symbol: str
    ltp: float = Field(gt=0, description="Last traded price")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    volume: int = Field(default=0, ge=0)
    oi: Optional[int] = Field(default=None, ge=0, description="Open interest")
    bid: Optional[float] = Field(default=None, ge=0)
    ask: Optional[float] = Field(default=None, ge=0)
    exchange: Optional[str] = "NSE"

    model_config = {"frozen": True}


class Candle(BaseModel):
    """Aggregated OHLCV candle for a specific timeframe."""
    symbol: str
    timeframe: Timeframe
    timestamp: datetime = Field(description="Candle start timestamp (ISO-8601 UTC)")
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(default=0, ge=0)
    oi: Optional[int] = Field(default=None, ge=0)
    is_complete: bool = Field(default=False, description="True once timeframe interval has closed")

    @field_validator("high")
    @classmethod
    def validate_high(cls, v: float, info) -> float:
        open_val = info.data.get("open")
        low_val = info.data.get("low")
        if open_val is not None and v < open_val:
            raise ValueError(f"High ({v}) cannot be lower than open ({open_val})")
        if low_val is not None and v < low_val:
            raise ValueError(f"High ({v}) cannot be lower than low ({low_val})")
        return v

    @field_validator("low")
    @classmethod
    def validate_low(cls, v: float, info) -> float:
        open_val = info.data.get("open")
        if open_val is not None and v > open_val:
            raise ValueError(f"Low ({v}) cannot be higher than open ({open_val})")
        return v


class OptionGreek(BaseModel):
    """Option Greeks and volatility metrics."""
    iv: Optional[float] = Field(default=None, ge=0, description="Implied Volatility (decimal, e.g. 0.15 = 15%)")
    delta: Optional[float] = Field(default=None, description="Delta (-1.0 to 1.0)")
    gamma: Optional[float] = Field(default=None, description="Gamma rate of delta change")
    theta: Optional[float] = Field(default=None, description="Daily theta decay in ₹")
    vega: Optional[float] = Field(default=None, description="Vega sensitivity to 1% IV change")


class OptionQuote(BaseModel):
    """Detailed market quote for an options strike."""
    symbol: str
    underlying: str
    strike: float
    option_type: OptionType
    expiry: str  # YYYY-MM-DD
    ltp: float = Field(ge=0)
    change: float = 0.0
    change_percent: float = 0.0
    volume: int = 0
    oi: int = 0
    greeks: OptionGreek = Field(default_factory=OptionGreek)


class OptionChain(BaseModel):
    """Complete option chain for an index/underlying."""
    underlying: str
    spot_price: float
    expiry: str
    available_expiries: List[str] = Field(default_factory=list)
    strikes: List[float] = Field(default_factory=list)
    calls: Dict[str, OptionQuote] = Field(default_factory=dict, description="Strike -> CE Quote")
    puts: Dict[str, OptionQuote] = Field(default_factory=dict, description="Strike -> PE Quote")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def get_atm_strike(self, step: float = 50.0) -> float:
        """Finds closest ATM strike based on spot price."""
        if not self.strikes:
            return round(self.spot_price / step) * step
        return min(self.strikes, key=lambda s: abs(s - self.spot_price))
