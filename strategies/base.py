"""Base Strategy interface and lifecycle management."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import pandas as pd

from config import logger, settings
from market_data.models import Candle, Tick
from strategies.models import Signal, SignalAction, StrategyConfig, StrikePreference


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.strategy_id = config.id
        self.name = config.name
        self.version = config.version
        self.underlying = config.underlying.upper()
        self.timeframe = config.timeframe
        self.is_active = config.is_active

        # Trade tracking
        self.daily_trade_count = 0
        self.last_trade_date: Optional[str] = None
        self.has_open_position = False

    def reset_daily_counters_if_needed(self) -> None:
        """Resets trade counts at midnight UTC/IST."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.last_trade_date != today:
            self.daily_trade_count = 0
            self.last_trade_date = today

    def can_trade(self) -> bool:
        """Checks if strategy is active and within maximum daily trades."""
        if not self.is_active:
            return False

        self.reset_daily_counters_if_needed()

        # Enforce strategy-level limit and system-level limit
        max_trades = min(self.config.max_trades_per_day, settings.max_trades_per_day)
        if self.daily_trade_count >= max_trades:
            logger.info(f"Strategy {self.name} reached max trades ({self.daily_trade_count}/{max_trades})")
            return False

        if self.has_open_position and settings.max_open_positions <= 1:
            return False

        return True

    def create_signal(
        self,
        direction: str,  # "CE" or "PE"
        spot_price: float,
        rationale: str,
        action: SignalAction = SignalAction.ENTRY,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Signal:
        """Helper to construct a validated trading signal."""
        # Strictly enforce options buying rules
        if direction not in settings.allowed_directions:
            raise ValueError(f"Direction {direction} not permitted. Allowed: {settings.allowed_directions}")

        if not settings.allow_option_selling and action != SignalAction.ENTRY:
            # Note: EXIT closes the long position, which is standard
            pass

        return Signal(
            strategy_id=self.strategy_id,
            action=action,
            underlying=self.underlying,
            direction=direction,  # CE or PE
            strike_preference=self.config.strike_preference,
            spot_reference_price=spot_price,
            stop_loss_pct=self.config.stop_loss_pct or settings.stop_loss_percent,
            target_pct=self.config.target_pct or settings.target_percent,
            rationale=rationale,
            metadata=metadata or {},
        )

    def record_signal_dispatched(self, signal: Signal) -> None:
        """Updates internal state when an entry signal is accepted."""
        if signal.action == SignalAction.ENTRY:
            self.daily_trade_count += 1
            self.has_open_position = True
        elif signal.action == SignalAction.EXIT:
            self.has_open_position = False

    @abstractmethod
    def on_candle(self, candle: Candle, history_df: pd.DataFrame) -> Optional[Signal]:
        """Invoked when a candle interval completes.

        Args:
            candle: The newly closed Candle.
            history_df: Historical candles DataFrame with OHLCV data.

        Returns:
            Optional Signal if entry or exit criteria are met.
        """
        pass

    def on_tick(self, tick: Tick) -> Optional[Signal]:
        """Invoked on incoming price tick (default no-op for candle-based strategies)."""
        return None
