"""Unit tests for dynamic RuleEngine condition evaluation and RuleBasedStrategy."""

import pandas as pd
import pytest
from market_data.models import Candle, Timeframe
from strategies.models import (
    ConditionOperator,
    IndicatorCondition,
    RuleBlock,
    StrategyConfig,
    StrikePreference,
)
from strategies.rule_engine import RuleBasedStrategy, RuleEngine


def test_rule_engine_condition_evaluation():
    """Verify >, <, and CROSSES_ABOVE logic."""
    df = pd.DataFrame(
        {
            "fast": [10.0, 12.0, 15.0],
            "slow": [14.0, 13.0, 12.0],
            "rsi": [45.0, 48.0, 55.0],
        }
    )

    # Condition: rsi > 50 -> True (55 > 50)
    cond_gt = IndicatorCondition(left="rsi", operator=ConditionOperator.GREATER_THAN, right=50.0)
    assert RuleEngine.evaluate_condition(cond_gt, df) is True

    # Condition: fast CROSSES_ABOVE slow -> True (at -2: 12 <= 13, at -1: 15 > 12)
    cond_cross = IndicatorCondition(left="fast", operator=ConditionOperator.CROSSES_ABOVE, right="slow")
    assert RuleEngine.evaluate_condition(cond_cross, df) is True

    # Combined Rule Block with AND
    block = RuleBlock(logic="AND", conditions=[cond_gt, cond_cross])
    assert RuleEngine.evaluate_rule_block(block, df) is True


def test_rule_based_strategy_signal_generation():
    """Verify dynamic RuleBasedStrategy produces CE signal when JSON conditions match."""
    cfg = StrategyConfig(
        id="custom_rsi_strategy",
        name="Custom RSI Momentum",
        underlying="NIFTY",
        timeframe="5m",
        is_active=True,
        direction="CE",
        indicators={"rsi_ind": {"type": "RSI", "period": 14}},
        entry_rules_ce=RuleBlock(
            logic="AND",
            conditions=[
                IndicatorCondition(left="rsi_ind", operator=ConditionOperator.GREATER_THAN, right=50.0)
            ],
        ),
    )

    strat = RuleBasedStrategy(cfg)

    # Generate bullish price series where RSI is well above 50
    n = 30
    prices = [25000.0 + (i * 20.0) for i in range(n)]
    df = pd.DataFrame(
        {
            "open": prices,
            "high": [p + 5 for p in prices],
            "low": [p - 5 for p in prices],
            "close": prices,
            "volume": [1000] * n,
        },
        index=pd.date_range("2026-10-25 09:15", periods=n, freq="5min"),
    )

    last_candle = Candle(
        symbol="NIFTY",
        timeframe=Timeframe.M5,
        timestamp=df.index[-1].to_pydatetime(),
        open=df["open"].iloc[-1],
        high=df["high"].iloc[-1],
        low=df["low"].iloc[-1],
        close=df["close"].iloc[-1],
        volume=1000,
        is_complete=True,
    )

    signal = strat.on_candle(last_candle, df)
    assert signal is not None
    assert signal.direction == "CE"
    assert signal.underlying == "NIFTY"
