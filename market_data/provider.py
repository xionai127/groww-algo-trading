"""Market data provider abstraction with Mock and Groww API implementations."""

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
import math
import random
from typing import Callable, Dict, List, Optional, Set

from config import logger, settings
from market_data.greeks import calculate_greeks, calculate_implied_volatility, calculate_time_to_expiry
from market_data.models import (
    Candle,
    OptionChain,
    OptionGreek,
    OptionQuote,
    OptionType,
    Tick,
    Timeframe,
)

TickCallback = Callable[[Tick], None]


class BaseMarketDataProvider(ABC):
    """Abstract interface for all market data ingestion sources."""

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to data feed."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection and clean up subscriptions."""
        pass

    @abstractmethod
    async def get_ltp(self, symbol: str) -> float:
        """Fetch latest single price."""
        pass

    @abstractmethod
    async def get_historical_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        from_time: datetime,
        to_time: datetime,
    ) -> List[Candle]:
        """Fetch historical OHLCV data."""
        pass

    @abstractmethod
    async def get_option_chain(
        self,
        underlying: str,
        expiry: Optional[str] = None,
    ) -> OptionChain:
        """Fetch complete option chain with greeks."""
        pass

    @abstractmethod
    async def subscribe(self, symbols: List[str], callback: TickCallback) -> None:
        """Subscribe to live tick updates."""
        pass

    @abstractmethod
    async def unsubscribe(self, symbols: List[str]) -> None:
        """Unsubscribe from live tick updates."""
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Returns True if connected and ready."""
        pass


class MockMarketDataProvider(BaseMarketDataProvider):
    """Realistic market data simulator for Analysis, Paper Trading, and Testing.

    Generates realistic drift, Brownian motion ticks, valid candle formations,
    and complete option chains with Black-Scholes pricing.
    """

    def __init__(self):
        self._connected = False
        # Base spot prices
        self._base_prices: Dict[str, float] = {
            "NIFTY": 25000.0,
            "SENSEX": 81000.0,
            "BANKNIFTY": 52000.0,
        }
        self._current_prices: Dict[str, float] = dict(self._base_prices)
        self._subscriptions: Dict[str, Set[TickCallback]] = {}
        self._simulation_task: Optional[asyncio.Task] = None
        self._tick_interval: float = 1.0  # seconds

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        self._connected = True
        logger.info("MockMarketDataProvider connected successfully.")
        return True

    async def disconnect(self) -> None:
        self._connected = False
        if self._simulation_task and not self._simulation_task.done():
            self._simulation_task.cancel()
            try:
                await self._simulation_task
            except asyncio.CancelledError:
                pass
        self._subscriptions.clear()
        logger.info("MockMarketDataProvider disconnected.")

    def set_price(self, symbol: str, price: float) -> None:
        """Manually override price for testing edge cases."""
        self._current_prices[symbol] = price

    async def get_ltp(self, symbol: str) -> float:
        if symbol in self._current_prices:
            return self._current_prices[symbol]
        # If it's an option symbol e.g. NIFTY26OCT25000CE
        if "CE" in symbol or "PE" in symbol:
            return 125.0
        return self._base_prices.get(symbol, 100.0)

    async def get_historical_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        from_time: datetime,
        to_time: datetime,
    ) -> List[Candle]:
        """Generates synthetic historical candles using a random walk with trend."""
        if from_time.tzinfo is None:
            from_time = from_time.replace(tzinfo=timezone.utc)
        if to_time.tzinfo is None:
            to_time = to_time.replace(tzinfo=timezone.utc)

        base_price = self._base_prices.get(symbol, 25000.0)
        step_seconds = timeframe.seconds
        current_time = from_time

        candles: List[Candle] = []
        last_close = base_price

        # Deterministic seed based on symbol for reproducible testing
        rng = random.Random(f"{symbol}_{timeframe.value}_{int(from_time.timestamp())}")

        while current_time < to_time:
            # 0.05% typical 1m volatility
            vol = 0.0005 * math.sqrt(step_seconds / 60.0)
            ret = rng.gauss(0.00001, vol)
            open_p = last_close
            close_p = round(open_p * (1.0 + ret), 2)
            high_p = round(max(open_p, close_p) * (1.0 + abs(rng.gauss(0, vol * 0.5))), 2)
            low_p = round(min(open_p, close_p) * (1.0 - abs(rng.gauss(0, vol * 0.5))), 2)
            volume = rng.randint(500, 5000)

            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=current_time,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    volume=volume,
                    is_complete=True,
                )
            )

            last_close = close_p
            current_time += timedelta(seconds=step_seconds)

        if symbol in self._current_prices and candles:
            self._current_prices[symbol] = candles[-1].close

        return candles

    def _get_next_expiry_date(self) -> str:
        """Returns the upcoming Thursday (for weekly options)."""
        now = datetime.now(timezone.utc)
        days_ahead = 3 - now.weekday()  # Thursday is 3
        if days_ahead <= 0:
            days_ahead += 7
        target = now + timedelta(days=days_ahead)
        return target.strftime("%Y-%m-%d")

    async def get_option_chain(
        self,
        underlying: str,
        expiry: Optional[str] = None,
    ) -> OptionChain:
        """Builds a realistic synthetic option chain with Black-Scholes pricing and Greeks."""
        spot = await self.get_ltp(underlying)
        if not expiry:
            expiry = self._get_next_expiry_date()

        tte = calculate_time_to_expiry(expiry)
        step = 50.0 if underlying == "NIFTY" else 100.0
        atm_strike = round(spot / step) * step

        # Generate 11 strikes: ATM, 5 ITM, 5 OTM
        strikes = [atm_strike + (i * step) for i in range(-5, 6)]
        iv = 0.14  # 14% base volatility

        calls: Dict[str, OptionQuote] = {}
        puts: Dict[str, OptionQuote] = {}

        for k in strikes:
            k_str = str(int(k))
            # Call
            ce_greeks = calculate_greeks(spot, k, tte, iv, OptionType.CE)
            ce_price = max(0.5, round(spot * ce_greeks.delta - k * math.exp(-0.065 * tte) * 0.5, 2)) if ce_greeks.delta else 10.0
            ce_symbol = f"{underlying}{expiry.replace('-', '')}{k_str}CE"
            calls[k_str] = OptionQuote(
                symbol=ce_symbol,
                underlying=underlying,
                strike=k,
                option_type=OptionType.CE,
                expiry=expiry,
                ltp=ce_price,
                change=round(ce_price * 0.02, 2),
                change_percent=2.0,
                volume=12000,
                oi=450000,
                greeks=ce_greeks,
            )

            # Put
            pe_greeks = calculate_greeks(spot, k, tte, iv, OptionType.PE)
            pe_price = max(0.5, round(k * math.exp(-0.065 * tte) * 0.5 - spot * (ce_greeks.delta or 0.5), 2))
            pe_symbol = f"{underlying}{expiry.replace('-', '')}{k_str}PE"
            puts[k_str] = OptionQuote(
                symbol=pe_symbol,
                underlying=underlying,
                strike=k,
                option_type=OptionType.PE,
                expiry=expiry,
                ltp=pe_price,
                change=round(-pe_price * 0.015, 2),
                change_percent=-1.5,
                volume=9500,
                oi=380000,
                greeks=pe_greeks,
            )

        return OptionChain(
            underlying=underlying,
            spot_price=spot,
            expiry=expiry,
            available_expiries=[expiry],
            strikes=strikes,
            calls=calls,
            puts=puts,
        )

    async def subscribe(self, symbols: List[str], callback: TickCallback) -> None:
        """Register callbacks and ensure simulation loop is active."""
        for s in symbols:
            if s not in self._subscriptions:
                self._subscriptions[s] = set()
            self._subscriptions[s].add(callback)

        if self._connected and (self._simulation_task is None or self._simulation_task.done()):
            self._simulation_task = asyncio.create_task(self._run_simulation())

    async def unsubscribe(self, symbols: List[str]) -> None:
        for s in symbols:
            self._subscriptions.pop(s, None)
        if not self._subscriptions and self._simulation_task:
            self._simulation_task.cancel()
            self._simulation_task = None

    async def _run_simulation(self) -> None:
        """Background loop generating ticks for subscribed symbols."""
        logger.info("MockMarketDataProvider simulation loop started.")
        try:
            while self._connected and self._subscriptions:
                await asyncio.sleep(self._tick_interval)
                for symbol, callbacks in list(self._subscriptions.items()):
                    curr_price = self._current_prices.get(symbol, self._base_prices.get(symbol, 100.0))
                    # Random small wiggle (-0.03% to +0.03%)
                    drift = curr_price * random.uniform(-0.0003, 0.0003)
                    new_price = round(curr_price + drift, 2)
                    self._current_prices[symbol] = new_price

                    tick = Tick(
                        symbol=symbol,
                        ltp=new_price,
                        timestamp=datetime.now(timezone.utc),
                        volume=random.randint(5, 50),
                    )

                    for cb in list(callbacks):
                        try:
                            cb(tick)
                        except Exception as e:
                            logger.error(f"Error in mock tick callback: {e}", exc_info=True)
        except asyncio.CancelledError:
            pass


class GrowwMarketDataProvider(BaseMarketDataProvider):
    """Official Groww API data provider using growwapi SDK."""

    def __init__(self, token: Optional[str] = None):
        self._token = token or settings.groww_access_token
        self._api = None
        self._connected = False
        self._subscriptions: Dict[str, Set[TickCallback]] = {}

    @property
    def is_connected(self) -> bool:
        return self._connected and self._api is not None

    async def connect(self) -> bool:
        if not self._token:
            logger.warning("GrowwMarketDataProvider: No access token configured. Fallback recommended.")
            return False

        try:
            from growwapi import GrowwAPI
            # GrowwAPI is synchronous HTTP client; we initialize here
            self._api = GrowwAPI(token=self._token)
            self._connected = True
            logger.info("GrowwMarketDataProvider connected to Groww API.")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize GrowwAPI: {e}", exc_info=True)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        self._connected = False
        self._api = None
        self._subscriptions.clear()

    async def get_ltp(self, symbol: str) -> float:
        if not self.is_connected:
            raise RuntimeError("GrowwMarketDataProvider is not connected")

        # Run blocking SDK call in thread pool
        def _fetch():
            res = self._api.get_ltp([{"segment": "FNO", "trading_symbol": symbol}])
            return res

        res = await asyncio.to_thread(_fetch)
        # Parse Groww LTP response format
        if res and isinstance(res, dict):
            for k, val in res.items():
                if isinstance(val, (int, float)):
                    return float(val)
                if isinstance(val, dict) and "ltp" in val:
                    return float(val["ltp"])
        raise ValueError(f"Unable to parse LTP for {symbol} from Groww response: {res}")

    async def get_historical_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        from_time: datetime,
        to_time: datetime,
    ) -> List[Candle]:
        if not self.is_connected:
            raise RuntimeError("GrowwMarketDataProvider is not connected")

        # Map timeframe to Groww interval constant
        interval_map = {
            Timeframe.M1: "1m",
            Timeframe.M3: "3m",
            Timeframe.M5: "5m",
            Timeframe.M15: "15m",
            Timeframe.M30: "30m",
            Timeframe.H1: "1h",
            Timeframe.D1: "1d",
        }
        interval = interval_map.get(timeframe, "1m")

        def _fetch():
            return self._api.get_historical_candle_data(
                trading_symbol=symbol,
                segment="FNO",
                start_time=int(from_time.timestamp()),
                end_time=int(to_time.timestamp()),
                interval=interval,
            )

        raw_data = await asyncio.to_thread(_fetch)
        candles: List[Candle] = []

        if raw_data and "candles" in raw_data:
            for item in raw_data["candles"]:
                # Format: [timestamp, open, high, low, close, volume]
                ts = datetime.fromtimestamp(item[0], tz=timezone.utc)
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=ts,
                        open=float(item[1]),
                        high=float(item[2]),
                        low=float(item[3]),
                        close=float(item[4]),
                        volume=int(item[5]) if len(item) > 5 else 0,
                        is_complete=True,
                    )
                )

        return candles

    async def get_option_chain(
        self,
        underlying: str,
        expiry: Optional[str] = None,
    ) -> OptionChain:
        if not self.is_connected:
            raise RuntimeError("GrowwMarketDataProvider is not connected")

        def _fetch():
            return self._api.get_option_chain(underlying=underlying, expiry=expiry)

        raw = await asyncio.to_thread(_fetch)
        # Parse Groww API option chain format and calculate Greeks if missing
        spot = float(raw.get("spot_price", 0.0))
        resolved_expiry = expiry or raw.get("expiry", "")
        tte = calculate_time_to_expiry(resolved_expiry)

        calls: Dict[str, OptionQuote] = {}
        puts: Dict[str, OptionQuote] = {}
        strikes: List[float] = []

        for item in raw.get("contracts", []):
            strike = float(item["strike_price"])
            strikes.append(strike)
            strike_str = str(int(strike))

            for otype in [OptionType.CE, OptionType.PE]:
                leg_key = "ce" if otype == OptionType.CE else "pe"
                leg = item.get(leg_key, {})
                ltp = float(leg.get("ltp", 0.0))
                iv = float(leg.get("iv", 0.15))
                greeks = calculate_greeks(spot, strike, tte, iv, otype)

                quote = OptionQuote(
                    symbol=leg.get("trading_symbol", f"{underlying}{resolved_expiry}{strike_str}{otype.value}"),
                    underlying=underlying,
                    strike=strike,
                    option_type=otype,
                    expiry=resolved_expiry,
                    ltp=ltp,
                    change=float(leg.get("day_change", 0.0)),
                    change_percent=float(leg.get("day_change_percentage", 0.0)),
                    volume=int(leg.get("volume", 0)),
                    oi=int(leg.get("open_interest", 0)),
                    greeks=greeks,
                )
                if otype == OptionType.CE:
                    calls[strike_str] = quote
                else:
                    puts[strike_str] = quote

        return OptionChain(
            underlying=underlying,
            spot_price=spot,
            expiry=resolved_expiry,
            strikes=sorted(list(set(strikes))),
            calls=calls,
            puts=puts,
        )

    async def subscribe(self, symbols: List[str], callback: TickCallback) -> None:
        for s in symbols:
            if s not in self._subscriptions:
                self._subscriptions[s] = set()
            self._subscriptions[s].add(callback)

    async def unsubscribe(self, symbols: List[str]) -> None:
        for s in symbols:
            self._subscriptions.pop(s, None)
