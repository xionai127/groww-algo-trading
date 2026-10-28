"""Historical data ingestion and sample candle dataset generator for backtesting."""

from datetime import datetime, time, timedelta, timezone
import math
import random
from typing import List, Optional
import pandas as pd

from config import logger
from database.db import get_db_connection
from market_data.models import Candle, Timeframe


def is_market_session(dt: datetime) -> bool:
    """Checks whether the datetime falls within Indian market hours (09:15 - 15:30 IST)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Convert to IST (UTC + 5:30)
    ist_dt = dt + timedelta(hours=5, minutes=30)

    # Exclude weekend (Saturday=5, Sunday=6)
    if ist_dt.weekday() >= 5:
        return False

    session_start = time(9, 15)
    session_end = time(15, 30)
    return session_start <= ist_dt.time() <= session_end


class BacktestDataFeed:
    """Provides historical candles for backtesting from cache or generates realistic trends."""

    @staticmethod
    def load_candles_from_db(
        symbol: str,
        timeframe: str,
        from_date: datetime,
        to_date: datetime,
    ) -> List[Candle]:
        """Loads historical candles from SQLite market_candles cache."""
        if from_date.tzinfo is None:
            from_date = from_date.replace(tzinfo=timezone.utc)
        if to_date.tzinfo is None:
            to_date = to_date.replace(tzinfo=timezone.utc)

        from_iso = from_date.isoformat()
        to_iso = to_date.isoformat()

        candles: List[Candle] = []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT symbol, timeframe, timestamp, open, high, low, close, volume, oi
                FROM market_candles
                WHERE symbol = ? AND timeframe = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC;
                """,
                (symbol.upper(), timeframe, from_iso, to_iso),
            )
            rows = cursor.fetchall()

        for row in rows:
            ts = datetime.fromisoformat(row["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            candles.append(
                Candle(
                    symbol=row["symbol"],
                    timeframe=Timeframe(row["timeframe"]),
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]),
                    oi=int(row["oi"]) if row["oi"] is not None else None,
                    is_complete=True,
                )
            )
        return candles

    @staticmethod
    def generate_synthetic_candles(
        symbol: str = "NIFTY",
        timeframe_str: str = "5m",
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        base_price: Optional[float] = None,
    ) -> List[Candle]:
        """Generates realistic intraday candle series with trending swings and mean reversions.

        Used when local database lacks historical data for the requested test range.
        """
        now = datetime.now(timezone.utc)
        if from_date is None:
            from_date = now - timedelta(days=5)
        if to_date is None:
            to_date = now

        if from_date.tzinfo is None:
            from_date = from_date.replace(tzinfo=timezone.utc)
        if to_date.tzinfo is None:
            to_date = to_date.replace(tzinfo=timezone.utc)

        tf = Timeframe(timeframe_str)
        step_seconds = tf.seconds

        if base_price is None:
            base_price = 82000.0 if "SENSEX" in symbol.upper() else 25000.0

        current_time = from_date
        current_close = base_price
        candles: List[Candle] = []

        # Predictable seed based on symbol and dates for repeatable tests
        seed = f"{symbol}_{timeframe_str}_{from_date.date()}_{to_date.date()}"
        rng = random.Random(seed)

        # Micro trend factor that alternates every few hours
        trend = 0.0001

        while current_time <= to_date:
            if is_market_session(current_time):
                # Reverse trend randomly
                if rng.random() < 0.08:
                    trend = -trend

                volatility = 0.0015 * math.sqrt(step_seconds / 300.0)
                pct_change = rng.gauss(trend, volatility)

                open_p = round(current_close, 2)
                close_p = round(open_p * (1.0 + pct_change), 2)
                high_p = round(max(open_p, close_p) * (1.0 + abs(rng.gauss(0, volatility * 0.4))), 2)
                low_p = round(min(open_p, close_p) * (1.0 - abs(rng.gauss(0, volatility * 0.4))), 2)
                volume = rng.randint(1500, 25000)

                candles.append(
                    Candle(
                        symbol=symbol.upper(),
                        timeframe=tf,
                        timestamp=current_time,
                        open=open_p,
                        high=high_p,
                        low=low_p,
                        close=close_p,
                        volume=volume,
                        is_complete=True,
                    )
                )
                current_close = close_p

            current_time += timedelta(seconds=step_seconds)

        return candles
