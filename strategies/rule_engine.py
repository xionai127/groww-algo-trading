"""Dynamic rule evaluation engine for custom no-code/low-code strategies."""

from typing import Any, Dict, Optional
import numpy as np
import pandas as pd

from config import logger
from market_data.models import Candle
from strategies.base import BaseStrategy
from strategies.indicators import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
    calculate_supertrend,
    calculate_vwap,
)
from strategies.models import (
    ConditionOperator,
    IndicatorCondition,
    RuleBlock,
    Signal,
    StrategyConfig,
)


class RuleEngine:
    """Computes dynamic indicators and evaluates boolean condition trees on DataFrames."""

    @staticmethod
    def compute_indicators(df: pd.DataFrame, indicator_configs: Dict[str, Dict[str, Any]]) -> pd.DataFrame:
        """Calculates indicators configured in strategy schema and appends them as columns."""
        calc_df = df.copy()

        for name, cfg in indicator_configs.items():
            itype = cfg.get("type", "").upper()

            if itype == "EMA":
                period = int(cfg.get("period", 9))
                calc_df[name] = calculate_ema(calc_df["close"], period)
            elif itype == "SMA":
                period = int(cfg.get("period", 20))
                calc_df[name] = calculate_sma(calc_df["close"], period)
            elif itype == "RSI":
                period = int(cfg.get("period", 14))
                calc_df[name] = calculate_rsi(calc_df["close"], period)
            elif itype == "ATR":
                period = int(cfg.get("period", 14))
                calc_df[name] = calculate_atr(calc_df, period)
            elif itype == "VWAP":
                calc_df[name] = calculate_vwap(calc_df)
            elif itype == "SUPERTREND":
                period = int(cfg.get("period", 10))
                mult = float(cfg.get("multiplier", 3.0))
                st_line, st_dir = calculate_supertrend(calc_df, period, mult)
                calc_df[f"{name}_line"] = st_line
                calc_df[f"{name}_dir"] = st_dir
            elif itype == "MACD":
                fast = int(cfg.get("fast", 12))
                slow = int(cfg.get("slow", 26))
                sig = int(cfg.get("signal", 9))
                m, s, h = calculate_macd(calc_df["close"], fast, slow, sig)
                calc_df[f"{name}_macd"] = m
                calc_df[f"{name}_signal"] = s
                calc_df[f"{name}_hist"] = h

        return calc_df

    @staticmethod
    def evaluate_condition(cond: IndicatorCondition, df: pd.DataFrame) -> bool:
        """Evaluates a single condition on the last closed bar (-1) vs previous bar (-2)."""
        if len(df) < 2:
            return False

        left_curr = df[cond.left].iloc[-1]
        left_prev = df[cond.left].iloc[-2]

        if isinstance(cond.right, (int, float)):
            right_curr = float(cond.right)
            right_prev = float(cond.right)
        elif cond.right in df.columns:
            right_curr = df[cond.right].iloc[-1]
            right_prev = df[cond.right].iloc[-2]
        else:
            try:
                val = float(cond.right)
                right_curr = val
                right_prev = val
            except ValueError:
                logger.error(f"Unknown right operand in condition: {cond.right}")
                return False

        if np.isnan(left_curr) or np.isnan(right_curr):
            return False

        op = cond.operator
        if op == ConditionOperator.GREATER_THAN:
            return bool(left_curr > right_curr)
        elif op == ConditionOperator.LESS_THAN:
            return bool(left_curr < right_curr)
        elif op == ConditionOperator.GREATER_THAN_EQUAL:
            return bool(left_curr >= right_curr)
        elif op == ConditionOperator.LESS_THAN_EQUAL:
            return bool(left_curr <= right_curr)
        elif op == ConditionOperator.CROSSES_ABOVE:
            return bool(left_prev <= right_prev and left_curr > right_curr)
        elif op == ConditionOperator.CROSSES_BELOW:
            return bool(left_prev >= right_prev and left_curr < right_curr)

        return False

    @classmethod
    def evaluate_rule_block(cls, block: Optional[RuleBlock], df: pd.DataFrame) -> bool:
        """Evaluates a block of conditions combined with AND / OR logic."""
        if not block or not block.conditions:
            return False

        results = [cls.evaluate_condition(cond, df) for cond in block.conditions]
        if block.logic == "AND":
            return all(results)
        elif block.logic == "OR":
            return any(results)

        return False


class RuleBasedStrategy(BaseStrategy):
    """Dynamically executes user-defined rule blocks on any market candle feed."""

    def __init__(self, config: StrategyConfig):
        super().__init__(config)

    def on_candle(self, candle: Candle, history_df: pd.DataFrame) -> Optional[Signal]:
        """Calculates indicators and evaluates rule blocks for CE / PE entries."""
        if not self.can_trade():
            return None

        if history_df is None or len(history_df) < 15:
            return None

        calc_df = RuleEngine.compute_indicators(history_df, self.config.indicators)

        # Check CE Entry Rule
        if self.config.entry_rules_ce and self.config.direction in ("CE", "BOTH"):
            if RuleEngine.evaluate_rule_block(self.config.entry_rules_ce, calc_df):
                rationale = f"Rule-based trigger: CE Entry met on {self.underlying} {self.timeframe}"
                logger.info(rationale)
                return self.create_signal(
                    direction="CE",
                    spot_price=candle.close,
                    rationale=rationale,
                )

        # Check PE Entry Rule
        if self.config.entry_rules_pe and self.config.direction in ("PE", "BOTH"):
            if RuleEngine.evaluate_rule_block(self.config.entry_rules_pe, calc_df):
                rationale = f"Rule-based trigger: PE Entry met on {self.underlying} {self.timeframe}"
                logger.info(rationale)
                return self.create_signal(
                    direction="PE",
                    spot_price=candle.close,
                    rationale=rationale,
                )

        return None
