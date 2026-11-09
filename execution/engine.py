"""Live Execution Engine coordinating Groww broker orders, positions, and risk rules."""

import asyncio
from datetime import datetime, time, timedelta, timezone
import json
from typing import Any, Dict, List, Optional, Tuple
import uuid

from config import ExecutionMode, logger, settings
from database.db import get_db_connection
from execution.broker_client import GrowwBrokerClient, broker_client
from execution.models import (
    LiveAccountSummary,
    LiveOrderRequest,
    LiveOrderResponse,
    LivePosition,
)
from market_data.models import Candle, OptionQuote, OptionType, Tick
from paper_trading.execution import PaperExecutionSimulator
from paper_trading.models import ExitReason, OrderStatus, OrderType, TransactionType
from risk_management.models import RuleViolation
from strategies.models import Signal, SignalAction, StrikePreference
from strategies.strike_selector import StrikeSelector


class LiveExecutionEngine:
    """Manages real-money order routing to Groww, live positions, and strict safety rules."""

    def __init__(
        self,
        client: Optional[GrowwBrokerClient] = None,
        simulator_math: Optional[PaperExecutionSimulator] = None,
        enforce_market_hours: bool = True,
    ):
        self.client = client or broker_client
        self.math_helper = simulator_math or PaperExecutionSimulator()
        self.enforce_market_hours = enforce_market_hours
        self.open_positions: Dict[str, LivePosition] = {}
        self.last_day_str: Optional[str] = None

        # Intraday tracking metrics
        self.daily_trades_count: int = 0
        self.daily_realized_pnl: float = 0.0
        self.daily_loss: float = 0.0
        self.is_trading_blocked: bool = False
        self.blocked_reason: Optional[str] = None

        self._load_state()

    def _get_ist_datetime(self, dt: Optional[datetime] = None) -> datetime:
        """Converts UTC datetime to IST (+5:30)."""
        if dt is None:
            dt = datetime.now(timezone.utc)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt + timedelta(hours=5, minutes=30)

    def _is_square_off_window(self, dt: Optional[datetime] = None) -> bool:
        """Checks if current time is within auto square-off window (15:15 to 15:30 IST)."""
        ist = self._get_ist_datetime(dt)
        return time(15, 15) <= ist.time() <= time(15, 30)

    def _is_market_open(self, dt: Optional[datetime] = None) -> bool:
        """Checks if current time falls within Indian market hours (09:15 to 15:30 IST)."""
        ist = self._get_ist_datetime(dt)
        if ist.weekday() >= 5:  # Weekend
            return False
        return time(9, 15) <= ist.time() <= time(15, 30)

    def _rollover_daily_counters_if_needed(self, dt: Optional[datetime] = None) -> None:
        """Resets daily counters at the start of each session."""
        ist = self._get_ist_datetime(dt)
        day_str = ist.strftime("%Y-%m-%d")

        if self.last_day_str != day_str:
            self.last_day_str = day_str
            self.daily_trades_count = 0
            self.daily_realized_pnl = 0.0
            self.daily_loss = 0.0
            self.is_trading_blocked = False
            self.blocked_reason = None
            self._save_engine_state()
            logger.info(f"Live execution intraday counters rolled over for {day_str}.")

    def _load_state(self) -> None:
        """Restores open live positions and intraday counters from SQLite."""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()

                # 1. Restore intraday state
                cursor.execute("SELECT value FROM app_state WHERE key = 'live_engine_state';")
                row = cursor.fetchone()
                if row:
                    data = json.loads(row["value"])
                    self.daily_trades_count = data.get("daily_trades_count", 0)
                    self.daily_realized_pnl = data.get("daily_realized_pnl", 0.0)
                    self.daily_loss = data.get("daily_loss", 0.0)
                    self.is_trading_blocked = data.get("is_trading_blocked", False)
                    self.blocked_reason = data.get("blocked_reason")
                    self.last_day_str = data.get("last_day_str")

                # 2. Restore active open live positions
                cursor.execute("""
                    SELECT id, broker_order_id, strategy_id, symbol, contract_symbol, direction,
                           strike_price, expiry_date, quantity, entry_price, entry_spot, entry_time,
                           current_price, current_spot, unrealized_pnl, return_pct,
                           target_price, stop_loss_price, target_pct, stop_loss_pct,
                           is_open, gross_pnl, brokerage, taxes, net_pnl, created_at, updated_at
                    FROM live_positions
                    WHERE is_open = 1;
                """)
                for r in cursor.fetchall():
                    pos = LivePosition(
                        id=r["id"],
                        broker_order_id=r["broker_order_id"],
                        strategy_id=r["strategy_id"],
                        symbol=r["symbol"],
                        contract_symbol=r["contract_symbol"],
                        direction=r["direction"],
                        strike_price=float(r["strike_price"]),
                        expiry_date=r["expiry_date"],
                        quantity=int(r["quantity"]),
                        entry_price=float(r["entry_price"]),
                        entry_spot=float(r["entry_spot"]),
                        entry_time=datetime.fromisoformat(r["entry_time"]),
                        current_price=float(r["current_price"]),
                        current_spot=float(r["current_spot"]),
                        unrealized_pnl=float(r["unrealized_pnl"]),
                        return_pct=float(r["return_pct"]),
                        target_price=float(r["target_price"]) if r["target_price"] is not None else None,
                        stop_loss_price=float(r["stop_loss_price"]) if r["stop_loss_price"] is not None else None,
                        target_pct=float(r["target_pct"]) if r["target_pct"] is not None else None,
                        stop_loss_pct=float(r["stop_loss_pct"]) if r["stop_loss_pct"] is not None else None,
                        is_open=bool(r["is_open"]),
                        gross_pnl=float(r["gross_pnl"]),
                        brokerage=float(r["brokerage"]),
                        taxes=float(r["taxes"]),
                        net_pnl=float(r["net_pnl"]),
                        created_at=datetime.fromisoformat(r["created_at"]),
                        updated_at=datetime.fromisoformat(r["updated_at"]),
                    )
                    self.open_positions[pos.id] = pos

                logger.info(f"Loaded {len(self.open_positions)} active open live positions from DB.")
        except Exception as e:
            logger.error(f"Error loading live execution state from DB: {e}", exc_info=True)

    def _save_engine_state(self) -> None:
        """Persists intraday state to app_state."""
        data = {
            "daily_trades_count": self.daily_trades_count,
            "daily_realized_pnl": self.daily_realized_pnl,
            "daily_loss": self.daily_loss,
            "is_trading_blocked": self.is_trading_blocked,
            "blocked_reason": self.blocked_reason,
            "last_day_str": self.last_day_str,
            "open_positions_count": len(self.open_positions),
        }
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO app_state (key, value, updated_at)
                VALUES ('live_engine_state', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
                """,
                (json.dumps(data), now_iso),
            )

    def _save_position(self, pos: LivePosition) -> None:
        """Inserts or updates a live position in SQLite."""
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO live_positions (
                    id, broker_order_id, strategy_id, symbol, contract_symbol, direction, strike_price,
                    expiry_date, quantity, entry_price, entry_spot, entry_time,
                    current_price, current_spot, unrealized_pnl, return_pct,
                    target_price, stop_loss_price, target_pct, stop_loss_pct,
                    is_open, exit_time, exit_price, exit_reason, gross_pnl, brokerage,
                    taxes, net_pnl, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    current_price = excluded.current_price,
                    current_spot = excluded.current_spot,
                    unrealized_pnl = excluded.unrealized_pnl,
                    return_pct = excluded.return_pct,
                    is_open = excluded.is_open,
                    exit_time = excluded.exit_time,
                    exit_price = excluded.exit_price,
                    exit_reason = excluded.exit_reason,
                    gross_pnl = excluded.gross_pnl,
                    brokerage = excluded.brokerage,
                    taxes = excluded.taxes,
                    net_pnl = excluded.net_pnl,
                    updated_at = excluded.updated_at;
                """,
                (
                    pos.id,
                    pos.broker_order_id,
                    pos.strategy_id,
                    pos.symbol,
                    pos.contract_symbol,
                    pos.direction,
                    pos.strike_price,
                    pos.expiry_date,
                    pos.quantity,
                    pos.entry_price,
                    pos.entry_spot,
                    pos.entry_time.isoformat(),
                    pos.current_price,
                    pos.current_spot,
                    pos.unrealized_pnl,
                    pos.return_pct,
                    pos.target_price,
                    pos.stop_loss_price,
                    pos.target_pct,
                    pos.stop_loss_pct,
                    1 if pos.is_open else 0,
                    pos.exit_time.isoformat() if pos.exit_time else None,
                    pos.exit_price,
                    pos.exit_reason,
                    pos.gross_pnl,
                    pos.brokerage,
                    pos.taxes,
                    pos.net_pnl,
                    pos.created_at.isoformat(),
                    pos.updated_at.isoformat(),
                ),
            )

    def _log_risk_event(self, event_type: str, reason: str, details: Dict[str, Any]) -> None:
        """Records a risk rejection event in risk_events table."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO risk_events (event_type, reason, details_json, mode, created_at)
                VALUES (?, ?, ?, 'LIVE', ?);
                """,
                (event_type, reason, json.dumps(details), now_iso),
            )
        logger.warning(f"[RISK EVENT - LIVE] {event_type}: {reason} | {details}")

    def _build_contract_symbol(self, symbol: str, expiry_date: str, strike: float, direction: str) -> str:
        """Constructs standardized NSE FNO trading symbol (e.g. NIFTY26NOV24500CE)."""
        try:
            exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
            yy = exp_dt.strftime("%y")
            mmm = exp_dt.strftime("%b").upper()
            strike_str = str(int(strike)) if strike.is_integer() else f"{strike:.1f}"
            return f"{symbol.upper()}{yy}{mmm}{strike_str}{direction.upper()}"
        except Exception:
            return f"{symbol.upper()}_{expiry_date}_{int(strike)}_{direction.upper()}"

    def get_summary(self) -> LiveAccountSummary:
        """Returns consolidated live trading account summary."""
        unrealized = sum(p.unrealized_pnl for p in self.open_positions.values())
        return LiveAccountSummary(
            broker_connected=self.client.is_connected,
            execution_mode=settings.execution_mode.value,
            available_margin=0.0,
            used_margin=sum(p.entry_price * p.quantity for p in self.open_positions.values()),
            realized_pnl=round(self.daily_realized_pnl, 2),
            unrealized_pnl=round(unrealized, 2),
            total_pnl=round(self.daily_realized_pnl + unrealized, 2),
            open_positions_count=len(self.open_positions),
            daily_trades_count=self.daily_trades_count,
            daily_loss=round(self.daily_loss, 2),
            is_trading_blocked=self.is_trading_blocked,
            blocked_reason=self.blocked_reason,
        )

    async def submit_order(
        self,
        request: LiveOrderRequest,
        spot_price: Optional[float] = None,
        quote_price: Optional[float] = None,
        dt: Optional[datetime] = None,
    ) -> Tuple[LiveOrderResponse, Optional[LivePosition]]:
        """Validates risk rules and routes live option buying order to Groww API."""
        now = dt or datetime.now(timezone.utc)
        self._rollover_daily_counters_if_needed(now)

        symbol = request.symbol.upper()
        direction = request.direction.upper()

        # 1. STRICT MODE ISOLATION GATE: Must be ExecutionMode.LIVE
        if settings.execution_mode != ExecutionMode.LIVE:
            msg = f"Live order rejected: Platform is currently in {settings.execution_mode.value} mode, not LIVE."
            self._log_risk_event("MODE_MISMATCH", msg, {"requested_mode": "LIVE", "current_mode": settings.execution_mode.value})
            order = LiveOrderResponse(
                symbol=symbol,
                contract_symbol=symbol,
                product_type=request.product_type,
                quantity=request.quantity or 0,
                price=request.price or 0.0,
                status=OrderStatus.REJECTED,
                rejection_reason=msg,
                created_at=now,
            )
            return order, None

        # 2. Strict Options Buying validation
        if direction not in ["CE", "PE"]:
            msg = f"Invalid direction '{direction}'. Only 'CE' and 'PE' option buying is allowed."
            self._log_risk_event("INVALID_DIRECTION", msg, {"direction": direction})
            order = LiveOrderResponse(
                symbol=symbol,
                contract_symbol=symbol,
                quantity=request.quantity or 0,
                price=request.price or 0.0,
                status=OrderStatus.REJECTED,
                rejection_reason=msg,
                created_at=now,
            )
            return order, None

        # 3. Circuit breaker check
        from risk_management import risk_manager
        if risk_manager.circuit_breaker.is_tripped:
            reason = risk_manager.circuit_breaker.trip_reason or "Circuit breaker active"
            msg = f"Live order blocked: Circuit breaker is tripped ({reason})."
            self._log_risk_event("CIRCUIT_BREAKER_ACTIVE", msg, {"reason": reason})
            order = LiveOrderResponse(
                symbol=symbol,
                contract_symbol=symbol,
                quantity=request.quantity or 0,
                price=request.price or 0.0,
                status=OrderStatus.REJECTED,
                rejection_reason=msg,
                created_at=now,
            )
            return order, None

        # 4. Market hours check
        if self.enforce_market_hours:
            if not self._is_market_open(now):
                msg = "Live order rejected: Outside Indian market hours (09:15 - 15:30 IST)."
                self._log_risk_event("OUTSIDE_MARKET_HOURS", msg, {})
                order = LiveOrderResponse(
                    symbol=symbol,
                    contract_symbol=symbol,
                    quantity=request.quantity or 0,
                    price=request.price or 0.0,
                    status=OrderStatus.REJECTED,
                    rejection_reason=msg,
                    created_at=now,
                )
                return order, None

            ist = self._get_ist_datetime(now)
            if ist.time() >= time(15, 10):
                msg = "Live order rejected: Intraday entry window closed (after 15:10 IST)."
                self._log_risk_event("SQUARE_OFF_WINDOW", msg, {})
                order = LiveOrderResponse(
                    symbol=symbol,
                    contract_symbol=symbol,
                    quantity=request.quantity or 0,
                    price=request.price or 0.0,
                    status=OrderStatus.REJECTED,
                    rejection_reason=msg,
                    created_at=now,
                )
                return order, None

        # 5. Position & Trade Limit checks
        if len(self.open_positions) >= settings.max_open_positions:
            msg = f"Live order rejected: Max open positions limit ({settings.max_open_positions}) reached."
            self._log_risk_event("MAX_POSITIONS_REACHED", msg, {"current": len(self.open_positions)})
            order = LiveOrderResponse(
                symbol=symbol,
                contract_symbol=symbol,
                quantity=request.quantity or 0,
                price=request.price or 0.0,
                status=OrderStatus.REJECTED,
                rejection_reason=msg,
                created_at=now,
            )
            return order, None

        if self.daily_trades_count >= settings.max_trades_per_day:
            msg = f"Live order rejected: Max daily trades limit ({settings.max_trades_per_day}) reached."
            self._log_risk_event("MAX_TRADES_REACHED", msg, {"daily_trades": self.daily_trades_count})
            order = LiveOrderResponse(
                symbol=symbol,
                contract_symbol=symbol,
                quantity=request.quantity or 0,
                price=request.price or 0.0,
                status=OrderStatus.REJECTED,
                rejection_reason=msg,
                created_at=now,
            )
            return order, None

        if self.daily_loss >= settings.max_daily_loss:
            msg = f"Live order rejected: Daily loss limit (₹{settings.max_daily_loss:.2f}) reached (Current: ₹{self.daily_loss:.2f})."
            self._log_risk_event("DAILY_LOSS_LIMIT", msg, {"daily_loss": self.daily_loss})
            order = LiveOrderResponse(
                symbol=symbol,
                contract_symbol=symbol,
                quantity=request.quantity or 0,
                price=request.price or 0.0,
                status=OrderStatus.REJECTED,
                rejection_reason=msg,
                created_at=now,
            )
            return order, None

        # 6. Resolve spot price, strike, and contract symbol
        if spot_price is None or spot_price <= 0:
            spot_price = 24500.0 if symbol == "NIFTY" else 80000.0

        strike = self.math_helper.resolve_strike(
            symbol=symbol,
            spot=spot_price,
            direction=direction,
            preference=request.strike_preference,
        )
        expiry_dt, expiry_str = self.math_helper.get_upcoming_expiry(symbol, current_dt=now)
        contract_symbol = self._build_contract_symbol(symbol, expiry_str, strike, direction)

        # Lot sizing
        _, lot_size, _ = self.math_helper.get_instrument_specs(symbol)
        quantity = request.quantity if request.quantity and request.quantity > 0 else lot_size

        # Premium pricing
        if quote_price and quote_price > 0:
            exec_price = round(quote_price, 2)
        else:
            exec_price = self.math_helper.calculate_theoretical_price(
                spot=spot_price,
                strike=strike,
                direction=direction,
                current_time=now,
                expiry_time=expiry_dt,
            )

        # 7. Check broker authentication
        if not self.client.is_connected:
            ok = await self.client.authenticate()
            if not ok or not self.client.is_connected:
                msg = "Live order rejected: Broker client could not authenticate with Groww."
                self._log_risk_event("BROKER_AUTH_FAILED", msg, {})
                order = LiveOrderResponse(
                    symbol=symbol,
                    contract_symbol=contract_symbol,
                    quantity=quantity,
                    price=exec_price,
                    status=OrderStatus.REJECTED,
                    rejection_reason=msg,
                    created_at=now,
                )
                return order, None

        # 8. Place order with Groww API
        broker_order_id = None
        try:
            groww_res = await self.client.place_option_buy_order(
                trading_symbol=contract_symbol,
                quantity=quantity,
                order_type=request.order_type.value,
                price=request.price or 0.0,
                product=request.product_type,
                exchange="NSE",
                validity="DAY",
            )
            if isinstance(groww_res, dict):
                broker_order_id = str(
                    groww_res.get("groww_order_id")
                    or groww_res.get("order_id")
                    or groww_res.get("order_reference_id")
                    or uuid.uuid4().hex[:10]
                )
        except Exception as e:
            msg = f"Groww API order placement failed: {e}"
            logger.error(msg, exc_info=True)
            self._log_risk_event("BROKER_EXECUTION_ERROR", msg, {"error": str(e)})
            order = LiveOrderResponse(
                symbol=symbol,
                contract_symbol=contract_symbol,
                quantity=quantity,
                price=exec_price,
                status=OrderStatus.REJECTED,
                rejection_reason=msg,
                created_at=now,
            )
            return order, None

        # 9. Create filled order response
        order = LiveOrderResponse(
            broker_order_id=broker_order_id,
            symbol=symbol,
            contract_symbol=contract_symbol,
            transaction_type=TransactionType.BUY,
            order_type=request.order_type,
            product_type=request.product_type,
            quantity=quantity,
            price=exec_price,
            status=OrderStatus.FILLED,
            created_at=now,
        )
        self._record_order(order, mode="LIVE", strategy_id=request.strategy_id)

        # 10. Record trade fill in trades table
        brokerage, taxes, _ = self.math_helper.calculate_order_charges("BUY", exec_price, quantity)
        self._record_trade(
            order_id=order.id,
            symbol=contract_symbol,
            transaction_type="BUY",
            quantity=quantity,
            price=exec_price,
            brokerage=brokerage,
            taxes=taxes,
            strategy_id=request.strategy_id,
            dt=now,
        )

        # 11. Target & Stop-loss setup
        target_pct = request.target_pct if request.target_pct is not None else settings.target_percent
        stop_loss_pct = request.stop_loss_pct if request.stop_loss_pct is not None else settings.stop_loss_percent
        target_price = round(exec_price * (1.0 + (target_pct / 100.0)), 2)
        stop_loss_price = round(exec_price * (1.0 - (stop_loss_pct / 100.0)), 2)

        # 12. Create LivePosition
        pos = LivePosition(
            broker_order_id=broker_order_id,
            strategy_id=request.strategy_id,
            symbol=symbol,
            contract_symbol=contract_symbol,
            direction=direction,
            strike_price=strike,
            expiry_date=expiry_str,
            quantity=quantity,
            entry_price=exec_price,
            entry_spot=spot_price,
            entry_time=now,
            current_price=exec_price,
            current_spot=spot_price,
            target_price=target_price,
            stop_loss_price=stop_loss_price,
            target_pct=target_pct,
            stop_loss_pct=stop_loss_pct,
            brokerage=brokerage,
            taxes=taxes,
            created_at=now,
            updated_at=now,
        )

        self.open_positions[pos.id] = pos
        self.daily_trades_count += 1
        self._save_position(pos)
        self._save_engine_state()

        logger.info(
            f"[LIVE EXECUTION] BOUGHT {quantity}x {contract_symbol} @ ₹{exec_price:.2f} "
            f"(Broker Order ID: {broker_order_id}, Target: ₹{target_price:.2f}, SL: ₹{stop_loss_price:.2f})"
        )
        return order, pos

    async def close_position(
        self,
        position_id: str,
        reason: str = "MANUAL",
        exit_spot: Optional[float] = None,
        exit_price: Optional[float] = None,
        dt: Optional[datetime] = None,
    ) -> LivePosition:
        """Squares off an active live position via Groww and records journal metrics."""
        now = dt or datetime.now(timezone.utc)
        self._rollover_daily_counters_if_needed(now)

        if position_id not in self.open_positions:
            raise KeyError(f"Live position '{position_id}' not found in active open positions.")

        pos = self.open_positions.pop(position_id)

        # Determine exit price
        if exit_price and exit_price > 0:
            exec_exit = round(exit_price, 2)
        elif exit_spot and exit_spot > 0:
            try:
                exp_dt = datetime.strptime(pos.expiry_date, "%Y-%m-%d").replace(hour=10, tzinfo=timezone.utc)
            except Exception:
                exp_dt = now + timedelta(days=2)
            exec_exit = self.math_helper.calculate_theoretical_price(
                spot=exit_spot,
                strike=pos.strike_price,
                direction=pos.direction,
                current_time=now,
                expiry_time=exp_dt,
            )
        else:
            exec_exit = pos.current_price

        # Place exit SELL order on Groww
        try:
            if self.client.is_connected:
                await self.client.place_option_sell_exit_order(
                    trading_symbol=pos.contract_symbol,
                    quantity=pos.quantity,
                    order_type="MARKET",
                    product="MIS",
                )
        except Exception as e:
            logger.error(f"Error placing live sell order for {pos.contract_symbol}: {e}", exc_info=True)

        # Compute PnL and exit costs
        gross_pnl = round((exec_exit - pos.entry_price) * pos.quantity, 2)
        exit_brokerage, exit_taxes, _ = self.math_helper.calculate_order_charges("SELL", exec_exit, pos.quantity)
        total_brokerage = round(pos.brokerage + exit_brokerage, 2)
        total_taxes = round(pos.taxes + exit_taxes, 2)
        net_pnl = round(gross_pnl - total_brokerage - total_taxes, 2)
        return_pct = round(((exec_exit - pos.entry_price) / pos.entry_price) * 100.0, 2)

        # Update position
        pos.is_open = False
        pos.exit_time = now
        pos.exit_price = exec_exit
        pos.exit_reason = reason
        pos.gross_pnl = gross_pnl
        pos.brokerage = total_brokerage
        pos.taxes = total_taxes
        pos.net_pnl = net_pnl
        pos.return_pct = return_pct
        pos.unrealized_pnl = 0.0
        pos.updated_at = now

        # Update intraday counters
        self.daily_realized_pnl = round(self.daily_realized_pnl + net_pnl, 2)
        if net_pnl < 0:
            self.daily_loss = round(self.daily_loss + abs(net_pnl), 2)

        self._save_position(pos)

        # Create and record Sell Exit Order
        sell_order = LiveOrderResponse(
            symbol=pos.symbol,
            contract_symbol=pos.contract_symbol,
            transaction_type=TransactionType.SELL,
            order_type=OrderType.MARKET,
            product_type="MIS",
            quantity=pos.quantity,
            price=exec_exit,
            status=OrderStatus.FILLED,
            created_at=now,
        )
        self._record_order(sell_order, mode="LIVE", strategy_id=pos.strategy_id)

        # Record sell trade fill
        self._record_trade(
            order_id=sell_order.id,
            symbol=pos.contract_symbol,
            transaction_type="SELL",
            quantity=pos.quantity,
            price=exec_exit,
            brokerage=exit_brokerage,
            taxes=exit_taxes,
            net_pnl=net_pnl,
            strategy_id=pos.strategy_id,
            dt=now,
        )

        # Record to trade journal
        from risk_management import risk_manager
        risk_manager.record_trade_exit({
            "trade_id": pos.id,
            "order_id": sell_order.id,
            "mode": "LIVE",
            "symbol": pos.symbol,
            "contract_symbol": pos.contract_symbol,
            "direction": pos.direction,
            "quantity": pos.quantity,
            "entry_price": pos.entry_price,
            "exit_price": exec_exit,
            "entry_time": pos.entry_time.isoformat() if isinstance(pos.entry_time, datetime) else str(pos.entry_time),
            "exit_time": now.isoformat(),
            "gross_pnl": gross_pnl,
            "brokerage": total_brokerage,
            "taxes": total_taxes,
            "net_pnl": net_pnl,
            "return_pct": return_pct,
            "exit_reason": reason,
            "strategy_id": pos.strategy_id,
        })

        # Check daily loss breach
        if self.daily_loss >= settings.max_daily_loss:
            self.is_trading_blocked = True
            self.blocked_reason = f"Daily loss limit ₹{settings.max_daily_loss:.2f} breached (Loss: ₹{self.daily_loss:.2f})"
            risk_manager.trip_circuit_breaker(
                reason=f"LIVE daily loss limit exceeded: ₹{self.daily_loss:.2f}",
                mode="LIVE",
            )

        self._save_engine_state()
        logger.info(
            f"[LIVE EXIT] CLOSED {pos.contract_symbol} @ ₹{exec_exit:.2f} | Reason: {reason} | Net PnL: ₹{net_pnl:.2f}"
        )
        return pos

    async def close_all_live_positions(self, reason: str = "EMERGENCY_PANIC") -> List[LivePosition]:
        """Liquidates all active open live positions immediately."""
        closed: List[LivePosition] = []
        for pos_id in list(self.open_positions.keys()):
            try:
                p = await self.close_position(pos_id, reason=reason)
                closed.append(p)
            except Exception as e:
                logger.error(f"Failed to liquidate live position {pos_id}: {e}", exc_info=True)
        return closed

    async def update_market_price(
        self,
        symbol: str,
        spot_price: float,
        dt: Optional[datetime] = None,
    ) -> List[LivePosition]:
        """Recalculates mark-to-market valuations and checks SL/TP and 15:15 square-off."""
        now = dt or datetime.now(timezone.utc)
        self._rollover_daily_counters_if_needed(now)
        closed: List[LivePosition] = []

        is_square_off = self._is_square_off_window(now)

        for pos_id, pos in list(self.open_positions.items()):
            if pos.symbol != symbol.upper():
                continue

            # Update option valuation
            try:
                exp_dt = datetime.strptime(pos.expiry_date, "%Y-%m-%d").replace(hour=10, tzinfo=timezone.utc)
            except Exception:
                exp_dt = now + timedelta(days=2)
            current_opt_prem = self.math_helper.calculate_theoretical_price(
                spot=spot_price,
                strike=pos.strike_price,
                direction=pos.direction,
                current_time=now,
                expiry_time=exp_dt,
            )

            pos.current_price = current_opt_prem
            pos.current_spot = spot_price
            pos.unrealized_pnl = round((current_opt_prem - pos.entry_price) * pos.quantity, 2)
            pos.return_pct = round(((current_opt_prem - pos.entry_price) / pos.entry_price) * 100.0, 2)
            pos.updated_at = now

            # 1. 15:15 Auto Square-off
            if is_square_off:
                c = await self.close_position(
                    pos_id,
                    reason=ExitReason.AUTO_SQUARE_OFF.value,
                    exit_spot=spot_price,
                    exit_price=current_opt_prem,
                    dt=now,
                )
                closed.append(c)
                continue

            # 2. Target Hit
            if pos.target_price and current_opt_prem >= pos.target_price:
                c = await self.close_position(
                    pos_id,
                    reason=ExitReason.TARGET_HIT.value,
                    exit_spot=spot_price,
                    exit_price=current_opt_prem,
                    dt=now,
                )
                closed.append(c)
                continue

            # 3. Stop-Loss Hit
            if pos.stop_loss_price and current_opt_prem <= pos.stop_loss_price:
                c = await self.close_position(
                    pos_id,
                    reason=ExitReason.STOP_LOSS.value,
                    exit_spot=spot_price,
                    exit_price=current_opt_prem,
                    dt=now,
                )
                closed.append(c)
                continue

            # Persist ongoing MTM
            self._save_position(pos)

        self._save_engine_state()
        return closed

    async def on_strategy_signal(self, signal: Signal, quote: Optional[OptionQuote] = None) -> None:
        """Processes automated entry/exit signals from StrategyManager when in LIVE mode."""
        if settings.execution_mode != ExecutionMode.LIVE:
            return

        symbol = signal.underlying.upper()
        spot = signal.spot_reference_price

        if signal.action == SignalAction.ENTRY:
            req = LiveOrderRequest(
                symbol=symbol,
                direction=signal.direction,
                strike_preference=signal.strike_preference,
                strategy_id=signal.strategy_id,
                target_pct=signal.target_pct,
                stop_loss_pct=signal.stop_loss_pct,
            )
            quote_prem = quote.ltp if quote else None
            await self.submit_order(req, spot_price=spot, quote_price=quote_prem)

        elif signal.action == SignalAction.EXIT:
            for pos_id, pos in list(self.open_positions.items()):
                if pos.symbol == symbol and pos.direction == signal.direction:
                    quote_prem = quote.ltp if quote else None
                    await self.close_position(
                        pos_id,
                        reason=ExitReason.EXIT_SIGNAL.value,
                        exit_spot=spot,
                        exit_price=quote_prem,
                    )

    def _record_order(self, order: LiveOrderResponse, mode: str = "LIVE", strategy_id: Optional[str] = None) -> None:
        """Persists order to SQLite orders table."""
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO orders (
                    id, broker_order_id, mode, symbol, instrument_type, strike_price,
                    expiry_date, transaction_type, order_type, quantity, price,
                    status, rejection_reason, strategy_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    price = excluded.price,
                    rejection_reason = excluded.rejection_reason,
                    updated_at = excluded.updated_at;
                """,
                (
                    order.id,
                    order.broker_order_id,
                    mode,
                    order.symbol,
                    order.contract_symbol[-2:],
                    None,
                    None,
                    order.transaction_type.value,
                    order.order_type.value,
                    order.quantity,
                    order.price,
                    order.status.value,
                    order.rejection_reason,
                    strategy_id,
                    order.created_at.isoformat(),
                    order.created_at.isoformat(),
                ),
            )

    def _record_trade(
        self,
        order_id: str,
        symbol: str,
        transaction_type: str,
        quantity: int,
        price: float,
        brokerage: float,
        taxes: float,
        net_pnl: float = 0.0,
        strategy_id: Optional[str] = None,
        dt: Optional[datetime] = None,
    ) -> None:
        """Persists trade execution fill to SQLite trades table."""
        now_iso = (dt or datetime.now(timezone.utc)).isoformat()
        trade_id = str(uuid.uuid4())
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO trades (
                    id, order_id, symbol, mode, transaction_type, quantity,
                    executed_price, brokerage, taxes, net_pnl, strategy_id, executed_at
                )
                VALUES (?, ?, ?, 'LIVE', ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    trade_id,
                    order_id,
                    symbol,
                    transaction_type,
                    quantity,
                    price,
                    brokerage,
                    taxes,
                    net_pnl,
                    strategy_id,
                    now_iso,
                ),
            )


# Global singleton instance
live_engine = LiveExecutionEngine()
