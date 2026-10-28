"""Option contract pricing, synthetic strike selection, and Indian tax/brokerage calculation for backtesting."""

from datetime import datetime, timedelta, timezone
import math
from typing import Optional, Tuple
from market_data.greeks import black_scholes_price, calculate_time_to_expiry
from market_data.models import OptionType
from strategies.models import StrikePreference


class OptionSimulator:
    """Simulates option strike selection, Black-Scholes premium pricing, and trading charges."""

    def __init__(
        self,
        symbol: str = "NIFTY",
        default_iv: float = 0.16,
        slippage_pct: float = 0.5,
        brokerage_per_order: float = 20.0,
        stt_rate: float = 0.000625,
        turnover_charge_rate: float = 0.0005,
        gst_rate: float = 0.18,
        stamp_duty_rate: float = 0.00003,
    ):
        self.symbol = symbol.upper()
        self.default_iv = default_iv
        self.slippage_pct = slippage_pct
        self.brokerage_per_order = brokerage_per_order
        self.stt_rate = stt_rate
        self.turnover_charge_rate = turnover_charge_rate
        self.gst_rate = gst_rate
        self.stamp_duty_rate = stamp_duty_rate

        if "SENSEX" in self.symbol:
            self.strike_interval = 100.0
            self.lot_size = 10
            self.expiry_weekday = 4  # Friday (0=Mon, 4=Fri)
        else:
            self.strike_interval = 50.0
            self.lot_size = 25
            self.expiry_weekday = 3  # Thursday (3=Thu)

    def get_upcoming_expiry(self, current_dt: datetime) -> Tuple[datetime, str]:
        """Calculates the upcoming weekly expiry date at 15:30 IST (10:00 UTC)."""
        if current_dt.tzinfo is None:
            current_dt = current_dt.replace(tzinfo=timezone.utc)

        # Convert to IST approx to determine weekday properly (UTC + 5:30)
        ist_dt = current_dt + timedelta(hours=5, minutes=30)
        days_ahead = self.expiry_weekday - ist_dt.weekday()

        # If today is expiry day but past 15:30 IST, roll to next week
        if days_ahead < 0 or (days_ahead == 0 and (ist_dt.hour > 15 or (ist_dt.hour == 15 and ist_dt.minute >= 30))):
            days_ahead += 7

        expiry_date = (ist_dt + timedelta(days=days_ahead)).date()
        # 15:30 IST is 10:00 UTC
        expiry_utc = datetime(
            expiry_date.year,
            expiry_date.month,
            expiry_date.day,
            10,
            0,
            0,
            tzinfo=timezone.utc,
        )
        date_str = expiry_date.strftime("%Y-%m-%d")
        return expiry_utc, date_str

    def resolve_strike(
        self,
        spot: float,
        direction: str,
        preference: StrikePreference = StrikePreference.ATM,
    ) -> float:
        """Selects option strike relative to spot and direction."""
        base_atm = round(spot / self.strike_interval) * self.strike_interval

        if direction == "CE":
            if preference == StrikePreference.ATM:
                return base_atm
            elif preference == StrikePreference.ITM_1:
                return base_atm - self.strike_interval
            elif preference == StrikePreference.ITM_2:
                return base_atm - (2 * self.strike_interval)
            elif preference == StrikePreference.OTM_1:
                return base_atm + self.strike_interval
            elif preference == StrikePreference.OTM_2:
                return base_atm + (2 * self.strike_interval)
        elif direction == "PE":
            if preference == StrikePreference.ATM:
                return base_atm
            elif preference == StrikePreference.ITM_1:
                return base_atm + self.strike_interval
            elif preference == StrikePreference.ITM_2:
                return base_atm + (2 * self.strike_interval)
            elif preference == StrikePreference.OTM_1:
                return base_atm - self.strike_interval
            elif preference == StrikePreference.OTM_2:
                return base_atm - (2 * self.strike_interval)

        return base_atm

    def generate_contract_symbol(self, strike: float, direction: str, expiry_str: str) -> str:
        """Constructs standardized contract symbol e.g. NIFTY2026-10-29-25000-CE."""
        return f"{self.symbol}-{expiry_str}-{int(strike)}-{direction.upper()}"

    def calculate_option_price(
        self,
        spot: float,
        strike: float,
        direction: str,
        current_time: datetime,
        expiry_time: datetime,
        iv: Optional[float] = None,
    ) -> float:
        """Calculates theoretical Black-Scholes premium with minimum tick floor."""
        iv_used = iv if iv is not None else self.default_iv

        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        if expiry_time.tzinfo is None:
            expiry_time = expiry_time.replace(tzinfo=timezone.utc)

        delta_sec = max((expiry_time - current_time).total_seconds(), 60.0)
        tte = delta_sec / (365.0 * 24.0 * 3600.0)

        opt_type = OptionType.CE if direction.upper() == "CE" else OptionType.PE
        raw_price = black_scholes_price(
            spot=spot,
            strike=strike,
            time_to_expiry=tte,
            volatility=iv_used,
            option_type=opt_type,
        )
        # Minimum premium floor (e.g. 0.50 INR)
        return max(0.50, round(raw_price, 2))

    def apply_slippage(self, price: float, is_buy: bool) -> float:
        """Applies execution slippage: increases price on BUY, lowers on SELL."""
        slippage_factor = self.slippage_pct / 100.0
        if is_buy:
            executed = price * (1.0 + slippage_factor)
        else:
            executed = price * (1.0 - slippage_factor)
        return max(0.05, round(executed, 2))

    def calculate_trade_charges(
        self,
        buy_price: float,
        sell_price: float,
        quantity: int,
    ) -> Tuple[float, float, float]:
        """Calculates Indian taxes, exchange charges, and Groww brokerage.

        Returns:
            Tuple of (brokerage, taxes_and_fees, total_charges)
        """
        # ₹20 per leg (Buy + Sell = ₹40)
        brokerage = self.brokerage_per_order * 2.0

        buy_turnover = buy_price * quantity
        sell_turnover = sell_price * quantity
        total_turnover = buy_turnover + sell_turnover

        # STT is only levied on option sell turnover (0.0625%)
        stt = sell_turnover * self.stt_rate

        # Exchange transaction charges (approx 0.05% on total premium turnover)
        exchange_charges = total_turnover * self.turnover_charge_rate

        # GST 18% on (Brokerage + Exchange turnover charge)
        gst = (brokerage + exchange_charges) * self.gst_rate

        # Stamp duty on BUY side only (0.003%)
        stamp_duty = buy_turnover * self.stamp_duty_rate

        # SEBI turnover charge (₹10 per crore = 0.0001%)
        sebi_charges = total_turnover * 0.000001

        taxes_and_fees = round(stt + exchange_charges + gst + stamp_duty + sebi_charges, 2)
        total_charges = round(brokerage + taxes_and_fees, 2)

        return brokerage, taxes_and_fees, total_charges
