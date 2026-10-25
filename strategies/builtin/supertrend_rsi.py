"""SuperTrend + RSI Strategy for Options Buying."""

from typing import Optional
import pandas as pd

from config import logger
from market_data.models import Candle
from strategies.base import BaseStrategy
from strategies.indicators import calculate_rsi, calculate_supertrend
from strategies.models import Signal, StrategyConfig


class SuperTrendRSIStrategy(BaseStrategy):
    """SuperTrend (10, 3) trend confirmation with RSI (14) momentum filter."""

    def __init__(self, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig(
                id="builtin_supertrend_rsi",
                name="SuperTrend (10,3) + RSI (14)",
                description="SuperTrend trend reversal with RSI momentum filter for options buying",
                underlying="NIFTY",
                timeframe="5m",
                is_active=False,
                indicators={
                    "supertrend": {"period": 10, "multiplier": 3.0},
                    "rsi": {"period": 14},
                },
            )
        super().__init__(config)
        st_cfg = self.config.indicators.get("supertrend", {})
        self.st_period = st_cfg.get("period", 10)
        self.st_multiplier = st_cfg.get("multiplier", 3.0)
        self.rsi_period = self.config.indicators.get("rsi", {}).get("period", 14)

    def on_candle(self, candle: Candle, history_df: pd.DataFrame) -> Optional[Signal]:
        """Evaluates SuperTrend trend switch and RSI condition."""
        if not self.can_trade():
            return None

        min_len = max(self.st_period, self.rsi_period) + 5
        if history_df is None or len(history_df) < min_len:
            return None

        st_line, st_dir = calculate_supertrend(history_df, period=self.st_period, multiplier=self.st_multiplier)
        rsi = calculate_rsi(history_df["close"], period=self.rsi_period)

        curr_dir = st_dir.iloc[-1]
        prev_dir = st_dir.iloc[-2]
        curr_rsi = rsi.iloc[-1]

        # Trend flipped Bullish (-1 to +1) and RSI > 50 -> Buy CE
        if prev_dir == -1 and curr_dir == 1 and curr_rsi > 50.0:
            if self.config.direction in ("CE", "BOTH"):
                rationale = f"SuperTrend turned Bullish (RSI: {curr_rsi:.1f} > 50) on {self.underlying} {self.timeframe}"
                logger.info(rationale)
                return self.create_signal(
                    direction="CE",
                    spot_price=candle.close,
                    rationale=rationale,
                    metadata={"rsi": curr_rsi, "supertrend": st_line.iloc[-1]},
                )

        # Trend flipped Bearish (+1 to -1) and RSI < 50 -> Buy PE
        elif prev_dir == 1 and curr_dir == -1 and curr_rsi < 50.0:
            if self.config.direction in ("PE", "BOTH"):
                rationale = f"SuperTrend turned Bearish (RSI: {curr_rsi:.1f} < 50) on {self.underlying} {self.timeframe}"
                logger.info(rationale)
                return self.create_signal(
                    direction="PE",
                    spot_price=candle.close,
                    rationale=rationale,
                    metadata={"rsi": curr_rsi, "supertrend": st_line.iloc[-1]},
                )

        return None
