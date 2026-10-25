"""Strategy Manager orchestrating real-time candle dispatching and signal routing."""

import asyncio
from datetime import datetime, timezone
import json
from typing import Callable, Coroutine, Dict, List, Optional, Union
import pandas as pd

from config import logger
from database.db import get_db_connection
from market_data.engine import market_engine
from market_data.models import Candle, OptionQuote, Timeframe
from strategies.base import BaseStrategy
from strategies.builtin.ema_crossover import EMACrossoverStrategy
from strategies.builtin.supertrend_rsi import SuperTrendRSIStrategy
from strategies.models import Signal, SignalAction, StrategyConfig
from strategies.strike_selector import StrikeSelector
from strategy_lab.builder import StrategyBuilder

SignalListener = Union[
    Callable[[Signal, Optional[OptionQuote]], None],
    Callable[[Signal, Optional[OptionQuote]], Coroutine[None, None, None]],
]


class StrategyManager:
    """Coordinates lifecycle of multiple strategies and dispatches market candle feeds."""

    def __init__(self):
        # strategy_id -> BaseStrategy
        self._strategies: Dict[str, BaseStrategy] = {}
        self._signal_listeners: List[SignalListener] = []

        # Wire listener to market engine aggregator
        market_engine.aggregator.register_on_candle_close(self.process_closed_candle)

    def register_signal_listener(self, listener: SignalListener) -> None:
        """Subscribes an execution or paper-trading handler to generated signals."""
        self._signal_listeners.append(listener)

    def add_strategy(self, strategy: BaseStrategy) -> None:
        """Registers a live strategy instance."""
        self._strategies[strategy.strategy_id] = strategy
        logger.info(f"Strategy registered: {strategy.name} [{strategy.strategy_id}] (Active: {strategy.is_active})")

    def get_strategy(self, strategy_id: str) -> Optional[BaseStrategy]:
        return self._strategies.get(strategy_id)

    def list_strategies(self) -> List[BaseStrategy]:
        return list(self._strategies.values())

    def activate_strategy(self, strategy_id: str) -> bool:
        """Enables strategy signal generation and syncs DB."""
        strat = self._strategies.get(strategy_id)
        if strat:
            strat.is_active = True
            strat.config.is_active = True
            StrategyBuilder.save_strategy(strat.config, change_summary="Activated")
            return True
        return False

    def deactivate_strategy(self, strategy_id: str) -> bool:
        """Disables strategy signal generation and syncs DB."""
        strat = self._strategies.get(strategy_id)
        if strat:
            strat.is_active = False
            strat.config.is_active = False
            StrategyBuilder.save_strategy(strat.config, change_summary="Deactivated")
            return True
        return False

    def initialize_default_strategies(self) -> None:
        """Seeds default strategies (EMA Crossover, SuperTrend RSI) if database is empty."""
        existing = StrategyBuilder.list_strategies()
        if not existing:
            logger.info("Seeding default built-in strategies into database...")
            ema_strat = EMACrossoverStrategy()
            StrategyBuilder.save_strategy(ema_strat.config, change_summary="Default 9/21 EMA Crossover strategy")
            self.add_strategy(ema_strat)

            st_strat = SuperTrendRSIStrategy()
            StrategyBuilder.save_strategy(st_strat.config, change_summary="Default SuperTrend + RSI strategy")
            self.add_strategy(st_strat)
        else:
            for cfg in existing:
                instance = StrategyBuilder.instantiate_strategy(cfg)
                self.add_strategy(instance)

    def process_closed_candle(self, candle: Candle) -> None:
        """Called automatically by MarketDataEngine whenever a candle interval closes."""
        # Run async evaluation in event loop
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._async_process_candle(candle))
        except RuntimeError:
            asyncio.run(self._async_process_candle(candle))

    async def _async_process_candle(self, candle: Candle) -> None:
        """Dispatches candle to matching active strategies and routes signals."""
        for strat in list(self._strategies.values()):
            if not strat.is_active:
                continue

            if strat.underlying != candle.symbol:
                continue

            if strat.timeframe != candle.timeframe.value:
                continue

            # Fetch historical DataFrame for indicator lookbacks
            try:
                tf_enum = Timeframe(candle.timeframe.value)
                df = market_engine.aggregator.get_candles_as_dataframe(candle.symbol, tf_enum, include_current=False)
                if len(df) < 20:
                    # Load more history if needed
                    await market_engine.get_candles(candle.symbol, tf_enum, limit=100)
                    df = market_engine.aggregator.get_candles_as_dataframe(candle.symbol, tf_enum, include_current=False)

                signal = strat.on_candle(candle, df)
                if signal:
                    strat.record_signal_dispatched(signal)
                    await self._route_signal(signal)
            except Exception as e:
                logger.error(f"Error executing strategy {strat.name} on candle: {e}", exc_info=True)

    async def _route_signal(self, signal: Signal) -> None:
        """Resolves target option contract via strike selector and dispatches to listeners."""
        logger.info(
            f"SIGNAL TRIGGERED -> {signal.underlying} {signal.direction} ({signal.action.value}) "
            f"via {signal.strategy_id} @ spot ₹{signal.spot_reference_price}"
        )

        # 1. Fetch Option Chain to identify target contract
        selected_contract: Optional[OptionQuote] = None
        try:
            chain = await market_engine.get_option_chain(signal.underlying)
            selected_contract = StrikeSelector.select_strike(
                chain=chain,
                direction=signal.direction,
                preference=signal.strike_preference,
            )
            if selected_contract:
                logger.info(
                    f"Selected Contract: {selected_contract.symbol} (Strike: {selected_contract.strike}, "
                    f"LTP: ₹{selected_contract.ltp}, Delta: {selected_contract.greeks.delta})"
                )
        except Exception as e:
            logger.error(f"Could not resolve option contract for signal: {e}", exc_info=True)

        # 2. Persist signal to audit logs
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO audit_logs (action, details, created_at) VALUES (?, ?, ?)",
                (
                    f"STRATEGY_SIGNAL_{signal.direction}",
                    json.dumps({
                        "signal": signal.model_dump(mode="json"),
                        "contract": selected_contract.model_dump(mode="json") if selected_contract else None,
                    }),
                    now_iso,
                ),
            )

        # 3. Notify subscribers
        for listener in self._signal_listeners:
            try:
                res = listener(signal, selected_contract)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(f"Error in signal listener: {e}", exc_info=True)


# Global StrategyManager instance
strategy_manager = StrategyManager()
