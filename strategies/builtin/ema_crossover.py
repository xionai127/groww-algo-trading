"""9 EMA / 21 EMA Crossover Strategy for Indian Index Options Buying."""

from typing import Optional
import pandas as pd

from config import logger
from market_data.models import Candle
from strategies.base import BaseStrategy
from strategies.indicators import calculate_ema
from strategies.models import Signal, SignalAction, StrategyConfig


class EMACrossoverStrategy(BaseStrategy):
    """Generates CE Buying signal when fast EMA crosses above slow EMA,

    and PE Buying signal when fast EMA crosses below slow EMA.
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig(
                id="builtin_ema_crossover",
                name="EMA Crossover (9/21)",
                description="Fast/Slow EMA crossover momentum strategy for options buying",
                underlying="NIFTY",
                timeframe="5m",
                is_active=True,
                is_primary=True,
                indicators={
                    "fast_ema": {"type": "EMA", "period": 9},
                    "slow_ema": {"type": "EMA", "period": 21},
                },
            )
        super().__init__(config)
        self.fast_period = self.config.indicators.get("fast_ema", {}).get("period", 9)
        self.slow_period = self.config.indicators.get("slow_ema", {}).get("period", 21)

    def on_candle(self, candle: Candle, history_df: pd.DataFrame) -> Optional[Signal]:
        """Calculates EMAs and checks for crossover condition on closed candle."""
        if not self.can_trade():
            return None

        # Need at least slow_period + 2 bars for valid crossover detection
        if history_df is None or len(history_df) < (self.slow_period + 2):
            return None

        close = history_df["close"]
        fast_ema = calculate_ema(close, self.fast_period)
        slow_ema = calculate_ema(close, self.slow_period)

        # Current closed bar (-1) and previous closed bar (-2)
        curr_fast = fast_ema.iloc[-1]
        curr_slow = slow_ema.iloc[-1]
        prev_fast = fast_ema.iloc[-2]
        prev_slow = slow_ema.iloc[-2]

        # Bullish Crossover: Fast crosses above Slow -> Buy CE
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            if self.config.direction in ("CE", "BOTH"):
                rationale = (
                    f"Bullish EMA Crossover: Fast ({curr_fast:.2f}) crossed above "
                    f"Slow ({curr_slow:.2f}) on {self.underlying} {self.timeframe}"
                )
                logger.info(rationale)
                return self.create_signal(
                    direction="CE",
                    spot_price=candle.close,
                    rationale=rationale,
                    metadata={"fast_ema": curr_fast, "slow_ema": curr_slow},
                )

        # Bearish Crossover: Fast crosses below Slow -> Buy PE
        elif prev_fast >= prev_slow and curr_fast < curr_slow:
            if self.config.direction in ("PE", "BOTH"):
                rationale = (
                    f"Bearish EMA Crossover: Fast ({curr_fast:.2f}) crossed below "
                    f"Slow ({curr_slow:.2f}) on {self.underlying} {self.timeframe}"
                )
                logger.info(rationale)
                return self.create_signal(
                    direction="PE",
                    spot_price=candle.close,
                    rationale=rationale,
                    metadata={"fast_ema": curr_fast, "slow_ema": curr_slow},
                )

        return None
