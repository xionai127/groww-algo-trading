"""Built-in trading strategies."""

from strategies.builtin.ema_crossover import EMACrossoverStrategy
from strategies.builtin.supertrend_rsi import SuperTrendRSIStrategy

__all__ = ["EMACrossoverStrategy", "SuperTrendRSIStrategy"]
