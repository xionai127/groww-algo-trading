"""Real-time tick-to-candle aggregation and multi-timeframe resampler."""

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Callable, Coroutine, Deque, Dict, List, Optional, Union
import pandas as pd

from config import logger
from market_data.models import Candle, Tick, Timeframe

# Default buffer depth per symbol/timeframe (e.g. 500 candles for indicator lookbacks)
DEFAULT_BUFFER_SIZE = 500

CandleCallback = Union[
    Callable[[Candle], None],
    Callable[[Candle], Coroutine[None, None, None]],
]


def align_timestamp_to_timeframe(dt: datetime, timeframe: Timeframe) -> datetime:
    """Floors a datetime to the start boundary of the given timeframe interval."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    total_seconds = int(dt.timestamp())
    bucket_seconds = timeframe.seconds
    floored_seconds = (total_seconds // bucket_seconds) * bucket_seconds
    return datetime.fromtimestamp(floored_seconds, tz=timezone.utc)


class CandleAggregator:
    """Manages real-time tick ingestion, candle building, and event broadcasting."""

    def __init__(
        self,
        timeframes: Optional[List[Timeframe]] = None,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
    ):
        self.timeframes = timeframes or [
            Timeframe.M1,
            Timeframe.M3,
            Timeframe.M5,
            Timeframe.M15,
        ]
        self.buffer_size = buffer_size

        # In-progress uncompleted candles: (symbol, timeframe) -> Candle
        self._current_candles: Dict[tuple[str, Timeframe], Candle] = {}

        # Completed historical candles ring buffer: (symbol, timeframe) -> Deque[Candle]
        self._completed_candles: Dict[tuple[str, Timeframe], Deque[Candle]] = defaultdict(
            lambda: deque(maxlen=self.buffer_size)
        )

        # Event callbacks
        self._on_candle_close_callbacks: List[CandleCallback] = []
        self._on_candle_update_callbacks: List[CandleCallback] = []

    def register_on_candle_close(self, callback: CandleCallback) -> None:
        """Register listener invoked when a candle interval completes."""
        self._on_candle_close_callbacks.append(callback)

    def register_on_candle_update(self, callback: CandleCallback) -> None:
        """Register listener invoked on every tick for in-progress candle."""
        self._on_candle_update_callbacks.append(callback)

    def process_tick(self, tick: Tick) -> List[Candle]:
        """Ingests a real-time tick and updates candles across all timeframes.

        Returns:
            List of completed candles that closed on this tick (if any).
        """
        completed: List[Candle] = []

        for tf in self.timeframes:
            bucket_time = align_timestamp_to_timeframe(tick.timestamp, tf)
            key = (tick.symbol, tf)
            current = self._current_candles.get(key)

            if current is None:
                # First candle for this symbol and timeframe
                new_candle = Candle(
                    symbol=tick.symbol,
                    timeframe=tf,
                    timestamp=bucket_time,
                    open=tick.ltp,
                    high=tick.ltp,
                    low=tick.ltp,
                    close=tick.ltp,
                    volume=tick.volume,
                    oi=tick.oi,
                    is_complete=False,
                )
                self._current_candles[key] = new_candle
                self._dispatch_event(self._on_candle_update_callbacks, new_candle)
            elif bucket_time > current.timestamp:
                # Time bucket changed -> Complete previous candle and start new one
                completed_candle = current.model_copy(update={"is_complete": True})
                self._completed_candles[key].append(completed_candle)
                completed.append(completed_candle)
                self._dispatch_event(self._on_candle_close_callbacks, completed_candle)

                # Initialize next bucket
                new_candle = Candle(
                    symbol=tick.symbol,
                    timeframe=tf,
                    timestamp=bucket_time,
                    open=tick.ltp,
                    high=tick.ltp,
                    low=tick.ltp,
                    close=tick.ltp,
                    volume=tick.volume,
                    oi=tick.oi,
                    is_complete=False,
                )
                self._current_candles[key] = new_candle
                self._dispatch_event(self._on_candle_update_callbacks, new_candle)
            else:
                # Update existing candle in the same interval bucket
                updated_candle = Candle(
                    symbol=current.symbol,
                    timeframe=current.timeframe,
                    timestamp=current.timestamp,
                    open=current.open,
                    high=max(current.high, tick.ltp),
                    low=min(current.low, tick.ltp),
                    close=tick.ltp,
                    volume=current.volume + tick.volume,
                    oi=tick.oi if tick.oi is not None else current.oi,
                    is_complete=False,
                )
                self._current_candles[key] = updated_candle
                self._dispatch_event(self._on_candle_update_callbacks, updated_candle)

        return completed

    def _dispatch_event(self, callbacks: List[CandleCallback], candle: Candle) -> None:
        """Dispatches event to sync or async callbacks safely."""
        for cb in callbacks:
            try:
                res = cb(candle)
                if asyncio.iscoroutine(res):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)
                    except RuntimeError:
                        # Fallback if outside event loop
                        asyncio.run(res)
            except Exception as e:
                logger.error(f"Error in candle event callback: {e}", exc_info=True)

    def load_historical_candles(self, symbol: str, timeframe: Timeframe, candles: List[Candle]) -> None:
        """Seeds the buffer with historical candles."""
        key = (symbol, timeframe)
        # Sort candles by timestamp ascending
        sorted_candles = sorted(candles, key=lambda c: c.timestamp)
        for c in sorted_candles:
            self._completed_candles[key].append(c.model_copy(update={"is_complete": True}))

    def get_completed_candles(self, symbol: str, timeframe: Timeframe, limit: Optional[int] = None) -> List[Candle]:
        """Returns completed candles for a symbol and timeframe."""
        candles = list(self._completed_candles.get((symbol, timeframe), []))
        if limit is not None and limit > 0:
            return candles[-limit:]
        return candles

    def get_current_candle(self, symbol: str, timeframe: Timeframe) -> Optional[Candle]:
        """Returns the active forming candle."""
        return self._current_candles.get((symbol, timeframe))

    def get_candles_as_dataframe(
        self,
        symbol: str,
        timeframe: Timeframe,
        include_current: bool = False,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Converts stored candles to a Pandas DataFrame indexed by timestamp."""
        candles = self.get_completed_candles(symbol, timeframe, limit=limit)
        if include_current:
            curr = self.get_current_candle(symbol, timeframe)
            if curr:
                candles = candles + [curr]

        if not candles:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])

        records = [
            {
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "oi": c.oi,
                "is_complete": c.is_complete,
            }
            for c in candles
        ]
        df = pd.DataFrame(records)
        df.set_index("timestamp", inplace=True)
        return df


def resample_candles(source_candles: List[Candle], target_timeframe: Timeframe) -> List[Candle]:
    """Resamples a list of lower-timeframe candles (e.g. 1m) into a higher timeframe."""
    if not source_candles:
        return []

    sorted_candles = sorted(source_candles, key=lambda c: c.timestamp)
    grouped: Dict[datetime, List[Candle]] = defaultdict(list)

    for c in sorted_candles:
        bucket = align_timestamp_to_timeframe(c.timestamp, target_timeframe)
        grouped[bucket].append(c)

    resampled: List[Candle] = []
    symbol = sorted_candles[0].symbol

    for bucket in sorted(grouped.keys()):
        group = grouped[bucket]
        resampled.append(
            Candle(
                symbol=symbol,
                timeframe=target_timeframe,
                timestamp=bucket,
                open=group[0].open,
                high=max(g.high for g in group),
                low=min(g.low for g in group),
                close=group[-1].close,
                volume=sum(g.volume for g in group),
                oi=group[-1].oi,
                is_complete=True,
            )
        )

    return resampled
