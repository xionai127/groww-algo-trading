"""Market Data Engine orchestrator coordinating providers, aggregators, and persistence."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set
from fastapi import WebSocket

from config import logger, settings
from market_data.candle_aggregator import CandleAggregator
from market_data.history_manager import HistoricalDataManager
from market_data.models import Candle, OptionChain, Tick, Timeframe
from market_data.provider import (
    BaseMarketDataProvider,
    GrowwMarketDataProvider,
    MockMarketDataProvider,
)


class MarketDataEngine:
    """Central engine managing all market feeds, candle processing, and client streaming."""

    def __init__(self, provider: Optional[BaseMarketDataProvider] = None):
        if provider:
            self.provider = provider
        elif settings.groww_access_token:
            self.provider = GrowwMarketDataProvider()
        else:
            self.provider = MockMarketDataProvider()

        self.aggregator = CandleAggregator()
        self.history = HistoricalDataManager(self.provider)

        # Active WebSocket subscriber clients
        self._ws_clients: Set[WebSocket] = set()

        # Wire aggregator events
        self.aggregator.register_on_candle_close(self._on_candle_closed)
        self.aggregator.register_on_candle_update(self._on_candle_updated)

    async def start(self) -> None:
        """Starts connection and auto-subscribes default index symbols."""
        logger.info(f"Starting MarketDataEngine with {self.provider.__class__.__name__}...")
        connected = await self.provider.connect()
        if not connected and isinstance(self.provider, GrowwMarketDataProvider):
            logger.warning("Falling back to MockMarketDataProvider for Analysis/Paper isolation.")
            self.provider = MockMarketDataProvider()
            await self.provider.connect()
            self.history.provider = self.provider

        # Subscribe default configured instruments (NIFTY, SENSEX)
        await self.provider.subscribe(settings.allowed_instruments, self._on_tick_received)
        logger.info(f"MarketDataEngine active. Subscribed to {settings.allowed_instruments}")

    async def stop(self) -> None:
        """Gracefully disconnects data feed."""
        logger.info("Stopping MarketDataEngine...")
        await self.provider.disconnect()
        logger.info("MarketDataEngine stopped.")

    def _on_tick_received(self, tick: Tick) -> None:
        """Called by provider when a new price tick arrives."""
        # 1. Update in-memory candles
        self.aggregator.process_tick(tick)

        # 2. Broadcast tick to connected WebSockets
        if self._ws_clients:
            msg = {
                "type": "TICK",
                "symbol": tick.symbol,
                "ltp": tick.ltp,
                "timestamp": tick.timestamp.isoformat(),
                "volume": tick.volume,
            }
            self._broadcast_json(msg)

    def _on_candle_closed(self, candle: Candle) -> None:
        """Callback when a candle interval is sealed."""
        logger.debug(f"Candle closed: {candle.symbol} {candle.timeframe.value} @ {candle.close}")
        if self._ws_clients:
            msg = {
                "type": "CANDLE_CLOSED",
                "symbol": candle.symbol,
                "timeframe": candle.timeframe.value,
                "timestamp": candle.timestamp.isoformat(),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            self._broadcast_json(msg)

    def _on_candle_updated(self, candle: Candle) -> None:
        """Callback on live tick updating active candle."""
        if self._ws_clients:
            msg = {
                "type": "CANDLE_UPDATE",
                "symbol": candle.symbol,
                "timeframe": candle.timeframe.value,
                "timestamp": candle.timestamp.isoformat(),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            self._broadcast_json(msg)

    def _broadcast_json(self, payload: dict) -> None:
        """Sends JSON packet to all connected browser clients."""
        disconnected = set()
        for ws in list(self._ws_clients):
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(ws.send_json(payload))
            except Exception:
                disconnected.add(ws)

        for ws in disconnected:
            self._ws_clients.discard(ws)

    def register_ws_client(self, ws: WebSocket) -> None:
        """Registers browser client for real-time WebSocket streaming."""
        self._ws_clients.add(ws)

    def unregister_ws_client(self, ws: WebSocket) -> None:
        """Unregisters browser client upon disconnect."""
        self._ws_clients.discard(ws)

    async def get_ltp(self, symbol: str) -> float:
        return await self.provider.get_ltp(symbol)

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int = 100,
    ) -> List[Candle]:
        """Returns completed candles from aggregator memory or cached history."""
        candles = self.aggregator.get_completed_candles(symbol, timeframe, limit=limit)
        if len(candles) >= limit:
            return candles

        # Not enough in memory buffer: fetch from historical manager
        now = datetime.now(timezone.utc)
        from_time = now - timedelta(seconds=timeframe.seconds * (limit + 5))
        hist_candles = await self.history.get_candles(symbol, timeframe, from_time, now)
        # Seed aggregator
        if hist_candles:
            self.aggregator.load_historical_candles(symbol, timeframe, hist_candles)
            return self.aggregator.get_completed_candles(symbol, timeframe, limit=limit)

        return candles

    async def get_option_chain(
        self,
        underlying: str,
        expiry: Optional[str] = None,
    ) -> OptionChain:
        return await self.provider.get_option_chain(underlying, expiry)

    def get_status(self) -> dict:
        return {
            "provider": self.provider.__class__.__name__,
            "is_connected": self.provider.is_connected,
            "connected_ws_clients": len(self._ws_clients),
            "tracked_instruments": settings.allowed_instruments,
        }


# Global engine instance
market_engine = MarketDataEngine()
