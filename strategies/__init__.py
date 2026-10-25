"""Strategies package for Groww Algo Trading Platform."""

from strategies.base import BaseStrategy
from strategies.builtin.ema_crossover import EMACrossoverStrategy
from strategies.builtin.supertrend_rsi import SuperTrendRSIStrategy
from strategies.manager import StrategyManager, strategy_manager
from strategies.models import (
    ConditionOperator,
    IndicatorCondition,
    RuleBlock,
    Signal,
    SignalAction,
    StrategyConfig,
    StrikePreference,
)
from strategies.router import router as strategy_router
from strategies.rule_engine import RuleBasedStrategy, RuleEngine
from strategies.strike_selector import StrikeSelector

__all__ = [
    "BaseStrategy",
    "EMACrossoverStrategy",
    "SuperTrendRSIStrategy",
    "RuleBasedStrategy",
    "RuleEngine",
    "StrategyManager",
    "strategy_manager",
    "StrikeSelector",
    "Signal",
    "SignalAction",
    "StrategyConfig",
    "StrikePreference",
    "ConditionOperator",
    "IndicatorCondition",
    "RuleBlock",
    "strategy_router",
]
