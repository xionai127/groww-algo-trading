"""Centralized Risk Management Orchestrator."""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from config import logger, settings
from database import log_risk_event
from risk_management.circuit_breaker import CircuitBreaker
from risk_management.journal import TradeJournalManager
from risk_management.models import (
    DailyRiskStatus,
    PreTradeValidationRequest,
    RiskAssessment,
    RiskDecision,
    RuleViolation,
)
from risk_management.rules import (
    calculate_risk_and_sizing,
    check_capital_and_funds,
    check_daily_max_loss,
    check_market_hours,
    check_max_daily_trades,
    check_max_open_positions,
    check_options_buying_only,
    check_square_off_restriction,
    get_ist_datetime,
)


class RiskManager:
    """Singleton-style Centralized Risk Manager coordinating safety, limits, and journaling."""

    def __init__(self) -> None:
        self.circuit_breaker = CircuitBreaker()
        self.journal_manager = TradeJournalManager()
        self._liquidation_handlers: List[Callable[[], Any]] = []

    def register_liquidation_handler(self, handler: Callable[[], Any]) -> None:
        """Registers a callback to be invoked during panic liquidation."""
        self._liquidation_handlers.append(handler)

    def validate_order(
        self,
        request: PreTradeValidationRequest,
        account_state: Optional[Dict[str, Any]] = None,
        enforce_hours: bool = True,
    ) -> RiskAssessment:
        """Pre-flight validation ensuring every trade strictly conforms to platform risk rules."""
        now = request.current_time or datetime.now(timezone.utc)
        mode = request.mode

        # 1. Circuit Breaker Active Check
        if self.circuit_breaker.is_active():
            reason = f"Circuit breaker active: {self.circuit_breaker.trip_reason}"
            log_risk_event("CIRCUIT_BREAKER_ACTIVE", reason, mode, {"request": request.model_dump(mode="json")})
            return RiskAssessment(
                allowed=False,
                decision=RiskDecision.REJECTED,
                rejection_reason=reason,
                violation_type=RuleViolation.CIRCUIT_BREAKER_ACTIVE,
                circuit_breaker_active=True,
                timestamp=now,
            )

        # 2. Options Buying Only Validation
        opt_err = check_options_buying_only(request.transaction_type, request.direction)
        if opt_err:
            violation, reason = opt_err
            log_risk_event(violation.value, reason, mode, {"request": request.model_dump(mode="json")})
            return RiskAssessment(
                allowed=False,
                decision=RiskDecision.REJECTED,
                rejection_reason=reason,
                violation_type=violation,
                timestamp=now,
            )

        # 3. Market Hours Check
        hours_err = check_market_hours(now, enforce=enforce_hours)
        if hours_err:
            violation, reason = hours_err
            log_risk_event(violation.value, reason, mode, {"now": now.isoformat()})
            return RiskAssessment(
                allowed=False,
                decision=RiskDecision.REJECTED,
                rejection_reason=reason,
                violation_type=violation,
                timestamp=now,
            )

        # 4. Square-Off Restriction Window Check (Past 15:10 IST)
        sq_err = check_square_off_restriction(now, enforce=enforce_hours)
        if sq_err:
            violation, reason = sq_err
            log_risk_event(violation.value, reason, mode, {"now": now.isoformat()})
            return RiskAssessment(
                allowed=False,
                decision=RiskDecision.REJECTED,
                rejection_reason=reason,
                violation_type=violation,
                timestamp=now,
            )

        # Account-dependent Checks
        if account_state:
            # 5. Daily Max Loss Check
            current_loss = float(account_state.get("daily_loss", 0.0))
            loss_err = check_daily_max_loss(current_loss, settings.max_daily_loss)
            if loss_err:
                violation, reason = loss_err
                # Automatically trip breaker on max loss
                if not self.circuit_breaker.is_active():
                    self.circuit_breaker.trip(reason=reason, auto=True, mode=mode)
                log_risk_event(violation.value, reason, mode, {"daily_loss": current_loss})
                return RiskAssessment(
                    allowed=False,
                    decision=RiskDecision.REJECTED,
                    rejection_reason=reason,
                    violation_type=violation,
                    circuit_breaker_active=True,
                    timestamp=now,
                )

            # 6. Max Open Positions Check
            open_count = int(account_state.get("open_positions_count", 0))
            pos_err = check_max_open_positions(open_count, settings.max_open_positions)
            if pos_err:
                violation, reason = pos_err
                log_risk_event(violation.value, reason, mode, {"open_count": open_count})
                return RiskAssessment(
                    allowed=False,
                    decision=RiskDecision.REJECTED,
                    rejection_reason=reason,
                    violation_type=violation,
                    timestamp=now,
                )

            # 7. Max Daily Trades Check
            daily_trades = int(account_state.get("daily_trades_count", 0))
            trades_err = check_max_daily_trades(daily_trades, settings.max_trades_per_day)
            if trades_err:
                violation, reason = trades_err
                log_risk_event(violation.value, reason, mode, {"daily_trades": daily_trades})
                return RiskAssessment(
                    allowed=False,
                    decision=RiskDecision.REJECTED,
                    rejection_reason=reason,
                    violation_type=violation,
                    timestamp=now,
                )

            # 8. Available Cash & Capital Cap Check
            available_cash = float(account_state.get("cash", settings.starting_capital))
            estimated_cost = round(request.estimated_price * request.quantity, 2)
            cap_err = check_capital_and_funds(estimated_cost, available_cash, settings.starting_capital)
            if cap_err:
                violation, reason = cap_err
                log_risk_event(violation.value, reason, mode, {"estimated_cost": estimated_cost, "cash": available_cash})
                return RiskAssessment(
                    allowed=False,
                    decision=RiskDecision.REJECTED,
                    rejection_reason=reason,
                    violation_type=violation,
                    timestamp=now,
                )

        # 9. Risk Per Trade Sizing & Warning
        risk_amount, max_qty, is_risk_exceeded = calculate_risk_and_sizing(
            estimated_price=request.estimated_price,
            quantity=request.quantity,
            stop_loss_pct=request.stop_loss_pct,
            risk_per_trade_cap=settings.risk_per_trade,
        )

        warnings: List[str] = []
        if is_risk_exceeded:
            warnings.append(
                f"Per-trade risk ₹{risk_amount:.2f} exceeds recommended threshold ₹{settings.risk_per_trade:.2f}. "
                f"Suggested max quantity: {max_qty}."
            )

        return RiskAssessment(
            allowed=True,
            decision=RiskDecision.WARNING if warnings else RiskDecision.APPROVED,
            max_allowed_quantity=max_qty,
            risk_amount=risk_amount,
            circuit_breaker_active=False,
            warnings=warnings,
            timestamp=now,
        )

    def trip_circuit_breaker(self, reason: str = "Manual Emergency Kill Switch Activated", mode: str = "PAPER") -> None:
        """Trips the circuit breaker."""
        self.circuit_breaker.trip(reason=reason, auto=False, mode=mode)

    def reset_circuit_breaker(self, reason: str = "Manual Breaker Reset", mode: str = "PAPER") -> None:
        """Resets the circuit breaker."""
        self.circuit_breaker.reset(reason=reason, mode=mode)

    def panic_square_off(self, reason: str = "PANIC EMERGENCY LIQUIDATION", mode: str = "PAPER") -> Dict[str, Any]:
        """Trips the circuit breaker and triggers registered liquidation handlers."""
        self.trip_circuit_breaker(reason=reason, mode=mode)
        results = []
        for handler in self._liquidation_handlers:
            try:
                res = handler()
                results.append(res)
            except Exception as e:
                logger.error(f"Error executing liquidation handler: {e}", exc_info=True)
                results.append({"status": "error", "error": str(e)})

        logger.critical(f"🚨 PANIC LIQUIDATION executed across {len(self._liquidation_handlers)} handlers.")
        return {"circuit_breaker_tripped": True, "reason": reason, "handler_results": results}

    def record_trade_exit(self, trade_data: Dict[str, Any]) -> str:
        """Processes trade closure: updates circuit breaker, checks loss breach, creates journal entry."""
        net_pnl = float(trade_data.get("net_pnl", 0.0))
        mode = trade_data.get("mode", "PAPER")

        # 1. Update consecutive loss counters
        self.circuit_breaker.record_trade_result(net_pnl, mode=mode)

        # 2. Automatically log to trade journal
        entry_id = self.journal_manager.create_journal_entry(trade_data)

        # 3. Check cumulative loss breach if provided
        daily_loss = trade_data.get("daily_loss")
        if daily_loss is not None and float(daily_loss) >= settings.max_daily_loss:
            if not self.circuit_breaker.is_active():
                self.circuit_breaker.trip(
                    reason=f"Daily loss ₹{float(daily_loss):.2f} reached max limit ₹{settings.max_daily_loss:.2f}",
                    auto=True,
                    mode=mode,
                )

        return entry_id

    def get_daily_status(
        self,
        mode: str = "PAPER",
        account_data: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> DailyRiskStatus:
        """Constructs an integrated daily risk summary."""
        ist = get_ist_datetime(now)
        date_str = ist.strftime("%Y-%m-%d")

        starting_capital = settings.starting_capital
        cash = starting_capital
        equity = starting_capital
        realized_pnl = 0.0
        unrealized_pnl = 0.0
        daily_loss = 0.0
        daily_trades = 0
        open_positions = 0

        if account_data:
            cash = float(account_data.get("cash", starting_capital))
            equity = float(account_data.get("total_equity", starting_capital))
            realized_pnl = float(account_data.get("daily_realized_pnl", 0.0))
            unrealized_pnl = float(account_data.get("unrealized_pnl", 0.0))
            daily_loss = float(account_data.get("daily_loss", 0.0))
            daily_trades = int(account_data.get("daily_trades_count", 0))
            open_positions = int(account_data.get("open_positions_count", 0))

        net_pnl = round(realized_pnl + unrealized_pnl, 2)
        loss_limit = settings.max_daily_loss
        loss_consumed = min(loss_limit, max(0.0, daily_loss))
        loss_remaining = max(0.0, round(loss_limit - loss_consumed, 2))
        loss_reached = daily_loss >= loss_limit

        is_breaker = self.circuit_breaker.is_active()
        is_trading_allowed = (
            not is_breaker
            and not loss_reached
            and daily_trades < settings.max_trades_per_day
            and open_positions < settings.max_open_positions
        )

        return DailyRiskStatus(
            date=date_str,
            mode=mode,
            starting_capital=starting_capital,
            current_equity=equity,
            cash=cash,
            daily_realized_pnl=realized_pnl,
            daily_unrealized_pnl=unrealized_pnl,
            daily_net_pnl=net_pnl,
            daily_loss_limit=loss_limit,
            daily_loss_consumed=loss_consumed,
            daily_loss_remaining=loss_remaining,
            daily_loss_limit_reached=loss_reached,
            daily_trades_count=daily_trades,
            max_trades_per_day=settings.max_trades_per_day,
            open_positions_count=open_positions,
            max_open_positions=settings.max_open_positions,
            circuit_breaker_tripped=is_breaker,
            breaker_reason=self.circuit_breaker.trip_reason,
            is_trading_allowed=is_trading_allowed,
        )


# Global singleton instance
risk_manager = RiskManager()
