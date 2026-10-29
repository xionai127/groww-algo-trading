"""Individual rule checks and pre-flight risk evaluation routines."""

from datetime import datetime, time, timedelta, timezone
from typing import Optional, Tuple
from config import settings
from risk_management.models import RuleViolation


def get_ist_datetime(dt: Optional[datetime] = None) -> datetime:
    """Converts UTC datetime to IST (+5:30)."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt + timedelta(hours=5, minutes=30)


def check_options_buying_only(transaction_type: str, direction: str) -> Optional[Tuple[RuleViolation, str]]:
    """Strictly enforces options buying only. Shorting / option writing is disallowed."""
    if transaction_type.upper() != "BUY":
        return (
            RuleViolation.OPTIONS_BUYING_ONLY,
            f"Option selling / writing is strictly prohibited by platform rules. Received {transaction_type}.",
        )
    if direction.upper() not in ("CE", "PE"):
        return (
            RuleViolation.OPTIONS_BUYING_ONLY,
            f"Invalid instrument direction '{direction}'. Must be CE or PE.",
        )
    return None


def check_market_hours(dt: Optional[datetime] = None, enforce: bool = True) -> Optional[Tuple[RuleViolation, str]]:
    """Checks whether timestamp is within Indian trading session (09:15 to 15:30 IST weekdays)."""
    if not enforce:
        return None
    ist = get_ist_datetime(dt)
    if ist.weekday() >= 5:
        return (
            RuleViolation.MARKET_HOURS_CLOSED,
            f"Market is closed on weekends (Current day: {ist.strftime('%A')}).",
        )
    cur_time = ist.time()
    if cur_time < time(9, 15) or cur_time > time(15, 30):
        return (
            RuleViolation.MARKET_HOURS_CLOSED,
            f"Market is closed. Trading session is 09:15 to 15:30 IST (Current IST time: {cur_time.strftime('%H:%M:%S')}).",
        )
    return None


def check_square_off_restriction(dt: Optional[datetime] = None, enforce: bool = True) -> Optional[Tuple[RuleViolation, str]]:
    """Prevents opening new positions past 15:10 IST right before auto square-off at 15:15 IST."""
    if not enforce:
        return None
    ist = get_ist_datetime(dt)
    cur_time = ist.time()
    if cur_time >= time(15, 10):
        return (
            RuleViolation.SQUARE_OFF_RESTRICTION,
            f"New entries prohibited past 15:10 IST prior to 15:15 IST auto square-off (Current IST: {cur_time.strftime('%H:%M:%S')}).",
        )
    return None


def check_daily_max_loss(current_daily_loss: float, max_daily_loss: float) -> Optional[Tuple[RuleViolation, str]]:
    """Checks if daily max loss threshold has been reached."""
    if current_daily_loss >= max_daily_loss:
        return (
            RuleViolation.DAILY_LOSS_LIMIT,
            f"Daily max loss limit (₹{max_daily_loss:.2f}) reached. Current loss: ₹{current_daily_loss:.2f}.",
        )
    return None


def check_max_open_positions(open_count: int, max_allowed: int) -> Optional[Tuple[RuleViolation, str]]:
    """Checks if maximum concurrent open positions limit is reached."""
    if open_count >= max_allowed:
        return (
            RuleViolation.MAX_POSITIONS_LIMIT,
            f"Max open positions limit ({max_allowed}) reached. Currently open: {open_count}.",
        )
    return None


def check_max_daily_trades(trades_count: int, max_trades: int) -> Optional[Tuple[RuleViolation, str]]:
    """Checks if maximum daily trade executions have been reached."""
    if trades_count >= max_trades:
        return (
            RuleViolation.MAX_DAILY_TRADES_LIMIT,
            f"Max daily trades limit ({max_trades}) reached. Today's trades: {trades_count}.",
        )
    return None


def check_capital_and_funds(
    estimated_cost: float,
    available_cash: float,
    starting_capital: float,
) -> Optional[Tuple[RuleViolation, str]]:
    """Checks if order cost exceeds available cash or max starting capital cap."""
    if estimated_cost > available_cash:
        return (
            RuleViolation.CAPITAL_EXCEEDED,
            f"Insufficient funds: Required ₹{estimated_cost:.2f}, Available cash ₹{available_cash:.2f}.",
        )
    if estimated_cost > starting_capital:
        return (
            RuleViolation.CAPITAL_EXCEEDED,
            f"Order value ₹{estimated_cost:.2f} exceeds total starting capital cap ₹{starting_capital:.2f}.",
        )
    return None


def calculate_risk_and_sizing(
    estimated_price: float,
    quantity: int,
    stop_loss_pct: Optional[float] = None,
    risk_per_trade_cap: Optional[float] = None,
) -> Tuple[float, int, bool]:
    """Calculates risk amount, recommended max quantity, and whether per-trade risk is exceeded.

    Returns:
        (risk_amount, max_allowed_quantity, is_exceeded)
    """
    sl_pct = stop_loss_pct if stop_loss_pct is not None else settings.default_stop_loss_pct
    cap = risk_per_trade_cap if risk_per_trade_cap is not None else settings.risk_per_trade

    unit_risk = estimated_price * (sl_pct / 100.0)
    total_risk = round(unit_risk * quantity, 2)

    if unit_risk > 0:
        max_allowed_qty = max(1, int(cap / unit_risk))
    else:
        max_allowed_qty = quantity

    is_exceeded = total_risk > cap
    return total_risk, max_allowed_qty, is_exceeded
