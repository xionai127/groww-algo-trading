"""Historical market data persistence, caching, and DataFrame export."""

from datetime import datetime, timezone
from typing import List, Optional
import pandas as pd

from config import logger
from database.db import get_db_connection
from market_data.models import Candle, Timeframe
from market_data.provider import BaseMarketDataProvider


def init_candle_table() -> None:
    """Ensures market_candles table and index exist in SQLite database."""
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_candles (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                oi INTEGER,
                PRIMARY KEY (symbol, timeframe, timestamp)
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_market_candles_query
            ON market_candles (symbol, timeframe, timestamp);
        """)


class HistoricalDataManager:
    """Coordinates fetching from provider with local SQLite cache fallback."""

    def __init__(self, provider: BaseMarketDataProvider):
        self.provider = provider
        init_candle_table()

    def save_candles(self, candles: List[Candle]) -> int:
        """Upserts candles into SQLite cache.

        Returns count of rows inserted/updated.
        """
        if not candles:
            return 0

        rows = [
            (
                c.symbol,
                c.timeframe.value,
                c.timestamp.isoformat(),
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
                c.oi,
            )
            for c in candles
        ]

        with get_db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO market_candles (symbol, timeframe, timestamp, open, high, low, close, volume, oi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    oi=excluded.oi;
                """,
                rows,
            )
        return len(rows)

    def get_cached_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        from_time: datetime,
        to_time: datetime,
    ) -> List[Candle]:
        """Retrieves candles from local database cache."""
        from_iso = from_time.isoformat()
        to_iso = to_time.isoformat()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT symbol, timeframe, timestamp, open, high, low, close, volume, oi
                FROM market_candles
                WHERE symbol = ? AND timeframe = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC;
                """,
                (symbol, timeframe.value, from_iso, to_iso),
            )
            rows = cursor.fetchall()

        candles: List[Candle] = []
        for r in rows:
            ts = datetime.fromisoformat(r["timestamp"])
            candles.append(
                Candle(
                    symbol=r["symbol"],
                    timeframe=Timeframe(r["timeframe"]),
                    timestamp=ts,
                    open=r["open"],
                    high=r["high"],
                    low=r["low"],
                    close=r["close"],
                    volume=r["volume"],
                    oi=r["oi"],
                    is_complete=True,
                )
            )
        return candles

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        from_time: datetime,
        to_time: datetime,
        force_refresh: bool = False,
    ) -> List[Candle]:
        """Fetches candles from local cache or provider when cache is missing."""
        if not force_refresh:
            cached = self.get_cached_candles(symbol, timeframe, from_time, to_time)
            if cached:
                return cached

        # Fetch from provider
        candles = await self.provider.get_historical_candles(symbol, timeframe, from_time, to_time)
        if candles:
            self.save_candles(candles)
        return candles

    async def get_candles_dataframe(
        self,
        symbol: str,
        timeframe: Timeframe,
        from_time: datetime,
        to_time: datetime,
    ) -> pd.DataFrame:
        """Returns candles as a Pandas DataFrame indexed by UTC datetime."""
        candles = await self.get_candles(symbol, timeframe, from_time, to_time)
        if not candles:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "oi"])

        data = [
            {
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "oi": c.oi,
            }
            for c in candles
        ]
        df = pd.DataFrame(data)
        df.set_index("timestamp", inplace=True)
        return df
