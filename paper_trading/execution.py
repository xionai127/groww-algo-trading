"""Realistic execution simulator for Indian index options paper trading."""

from datetime import datetime, timedelta, timezone
import math
from typing import Optional, Tuple
from config import logger, settings
from market_data.greeks import black_scholes_price
from market_data.models import OptionType
from strategies.models import StrikePreference


class PaperExecutionSimulator:
    """Calculates realistic fill prices with slippage, Indian taxes, and Groww brokerage."""

    def __init__(
        self,
        default_slippage_pct: float = 0.5,
        brokerage_per_order: float = 20.0,
        stt_rate: float = 0.000625,
        turnover_charge_rate: float = 0.0005,
        gst_rate: float = 0.18,
        stamp_duty_rate: float = 0.00003,
        default_iv: float = 0.16,
    ):
        self.default_slippage_pct = default_slippage_pct
        self.brokerage_per_order = brokerage_per_order
        self.stt_rate = stt_rate
        self.turnover_charge_rate = turnover_charge_rate
        self.gst_rate = gst_rate
        self.stamp_duty_rate = stamp_duty_rate
        self.default_iv = default_iv

    def get_instrument_specs(self, symbol: str) -> Tuple[float, int, int]:
        """Returns (strike_interval, lot_size, expiry_weekday) for symbol."""
        sym_upper = symbol.upper()
        if "SENSEX" in sym_upper:
            return 100.0, 10, 4  # Friday
        # Default NIFTY 50
        return 50.0, 25, 3  # Thursday

    def get_upcoming_expiry(self, symbol: str, current_dt: Optional[datetime] = None) -> Tuple[datetime, str]:
        """Calculates next weekly expiry date at 15:30 IST (10:00 UTC)."""
        if current_dt is None:
            current_dt = datetime.now(timezone.utc)
        elif current_dt.tzinfo is None:
            current_dt = current_dt.replace(tzinfo=timezone.utc)

        _, _, expiry_weekday = self.get_instrument_specs(symbol)

        ist_dt = current_dt + timedelta(hours=5, minutes=30)
        days_ahead = expiry_weekday - ist_dt.weekday()

        if days_ahead < 0 or (days_ahead == 0 and (ist_dt.hour > 15 or (ist_dt.hour == 15 and ist_dt.minute >= 30))):
            days_ahead += 7

        expiry_date = (ist_dt + timedelta(days=days_ahead)).date()
        expiry_utc = datetime(
            expiry_date.year,
            expiry_date.month,
            expiry_date.day,
            10,
            0,
            0,
            tzinfo=timezone.utc,
        )
        return expiry_utc, expiry_date.strftime("%Y-%m-%d")

    def resolve_strike(
        self,
        symbol: str,
        spot: float,
        direction: str,
        preference: StrikePreference = StrikePreference.ATM,
    ) -> float:
        """Determines option strike price relative to spot price and direction."""
        interval, _, _ = self.get_instrument_specs(symbol)
        base_atm = round(spot / interval) * interval

        dir_upper = direction.upper()
        if dir_upper == "CE":
            if preference == StrikePreference.ATM:
                return base_atm
            elif preference == StrikePreference.ITM_1:
                return base_atm - interval
            elif preference == StrikePreference.ITM_2:
                return base_atm - (2 * interval)
            elif preference == StrikePreference.OTM_1:
                return base_atm + interval
            elif preference == StrikePreference.OTM_2:
                return base_atm + (2 * interval)
        elif dir_upper == "PE":
            if preference == StrikePreference.ATM:
                return base_atm
            elif preference == StrikePreference.ITM_1:
                return base_atm + interval
            elif preference == StrikePreference.ITM_2:
                return base_atm + (2 * interval)
            elif preference == StrikePreference.OTM_1:
                return base_atm - interval
            elif preference == StrikePreference.OTM_2:
                return base_atm - (2 * interval)

        return base_atm

    def generate_contract_symbol(self, symbol: str, strike: float, direction: str, expiry_str: str) -> str:
        """Formats standardized contract symbol e.g. NIFTY-2026-10-29-25000-CE."""
        return f"{symbol.upper()}-{expiry_str}-{int(strike)}-{direction.upper()}"

    def calculate_theoretical_price(
        self,
        spot: float,
        strike: float,
        direction: str,
        current_time: Optional[datetime] = None,
        expiry_time: Optional[datetime] = None,
        iv: Optional[float] = None,
    ) -> float:
        """Computes theoretical Black-Scholes option premium."""
        if current_time is None:
            current_time = datetime.now(timezone.utc)
        if expiry_time is None:
            expiry_time, _ = self.get_upcoming_expiry("NIFTY", current_time)

        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        if expiry_time.tzinfo is None:
            expiry_time = expiry_time.replace(tzinfo=timezone.utc)

        delta_sec = max((expiry_time - current_time).total_seconds(), 60.0)
        tte = delta_sec / (365.0 * 24.0 * 3600.0)
        iv_used = iv if iv is not None else self.default_iv

        opt_type = OptionType.CE if direction.upper() == "CE" else OptionType.PE
        raw_price = black_scholes_price(
            spot=spot,
            strike=strike,
            time_to_expiry=tte,
            volatility=iv_used,
            option_type=opt_type,
        )
        return max(0.50, round(raw_price, 2))

    def apply_slippage(self, price: float, is_buy: bool, slippage_pct: Optional[float] = None) -> float:
        """Applies realistic adverse slippage: higher price on BUY, lower on SELL."""
        pct = slippage_pct if slippage_pct is not None else self.default_slippage_pct
        factor = pct / 100.0
        if is_buy:
            executed = price * (1.0 + factor)
        else:
            executed = price * (1.0 - factor)
        return max(0.05, round(executed, 2))

    def calculate_order_charges(
        self,
        transaction_type: str,
        price: float,
        quantity: int,
    ) -> Tuple[float, float, float]:
        """Calculates single order leg charges (Brokerage, Taxes, Total).

        Returns:
            Tuple of (brokerage, taxes, total_charges)
        """
        turnover = price * quantity
        brokerage = self.brokerage_per_order

        # STT only on SELL
        stt = turnover * self.stt_rate if transaction_type.upper() == "SELL" else 0.0

        # Exchange turnover charge (0.05%)
        exchange_charges = turnover * self.turnover_charge_rate

        # GST 18% on (Brokerage + Exchange turnover charge)
        gst = (brokerage + exchange_charges) * self.gst_rate

        # Stamp duty on BUY side only (0.003%)
        stamp_duty = turnover * self.stamp_duty_rate if transaction_type.upper() == "BUY" else 0.0

        # SEBI turnover charge (₹10 per crore = 0.0001%)
        sebi_charges = turnover * 0.000001

        taxes = round(stt + exchange_charges + gst + stamp_duty + sebi_charges, 2)
        total = round(brokerage + taxes, 2)
        return brokerage, taxes, total
