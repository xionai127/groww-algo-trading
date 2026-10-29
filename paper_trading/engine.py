"""Realistic paper trading engine for Indian index options."""

from datetime import datetime, time, timedelta, timezone
import json
from typing import Any, Dict, List, Optional, Tuple
import uuid

from config import logger, settings
from database.db import get_db_connection
from market_data.models import Candle, OptionQuote, Tick
from paper_trading.execution import PaperExecutionSimulator
from paper_trading.models import (
    ExitReason,
    OrderStatus,
    OrderType,
    PaperAccount,
    PaperOrder,
    PaperOrderRequest,
    PaperPosition,
    PaperTrade,
    TransactionType,
)
from strategies.models import Signal, SignalAction, StrikePreference


class PaperTradingEngine:
    """Manages virtual capital, order fills, active positions, and risk rule enforcement."""

    def __init__(
        self,
        simulator: Optional[PaperExecutionSimulator] = None,
        enforce_market_hours: bool = False,
    ):
        self.simulator = simulator or PaperExecutionSimulator()
        self.enforce_market_hours = enforce_market_hours
        self.open_positions: Dict[str, PaperPosition] = {}
        self.account = PaperAccount(
            initial_capital=settings.starting_capital,
            cash=settings.starting_capital,
            total_equity=settings.starting_capital,
        )
        self.last_day_str: Optional[str] = None
        self._load_state()

    def _get_ist_datetime(self, dt: Optional[datetime] = None) -> datetime:
        """Converts UTC datetime to IST (+5:30)."""
        if dt is None:
            dt = datetime.now(timezone.utc)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt + timedelta(hours=5, minutes=30)

    def _is_square_off_window(self, dt: Optional[datetime] = None) -> bool:
        """Checks if time in IST is strictly within intraday square-off window (15:15 to 15:30 IST)."""
        ist = self._get_ist_datetime(dt)
        return time(15, 15) <= ist.time() <= time(15, 30)

    def _is_market_open(self, dt: Optional[datetime] = None) -> bool:
        """Checks if current time falls within active market hours (09:15 to 15:30 IST)."""
        ist = self._get_ist_datetime(dt)
        if ist.weekday() >= 5:  # Weekend
            return False
        return time(9, 15) <= ist.time() <= time(15, 30)

    def _rollover_daily_counters_if_needed(self, dt: Optional[datetime] = None) -> None:
        """Resets intraday counters at the start of a new trading session."""
        ist = self._get_ist_datetime(dt)
        day_str = ist.strftime("%Y-%m-%d")

        if self.last_day_str != day_str:
            self.last_day_str = day_str
            self.account.daily_trades_count = 0
            self.account.daily_realized_pnl = 0.0
            self.account.daily_loss = 0.0
            self.account.is_trading_blocked = False
            self.account.blocked_reason = None
            self._save_account_state()
            try:
                from risk_management import risk_manager
                if risk_manager.circuit_breaker.auto_tripped:
                    risk_manager.reset_circuit_breaker(reason="Daily session rollover")
            except Exception:
                pass
            logger.info(f"Paper trading intraday counters rolled over for {day_str}.")

    def _load_state(self) -> None:
        """Restores account balance and active open positions from SQLite."""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()

                # 1. Restore account
                cursor.execute("SELECT value FROM app_state WHERE key = 'paper_account_state';")
                row = cursor.fetchone()
                if row:
                    data = json.loads(row["value"])
                    self.account = PaperAccount.model_validate(data)
                    logger.info(f"Loaded existing paper account: Cash ₹{self.account.cash:.2f}, Equity ₹{self.account.total_equity:.2f}")

                # 2. Restore open positions
                cursor.execute("""
                    SELECT id, strategy_id, symbol, contract_symbol, direction, strike_price,
                           expiry_date, quantity, entry_price, entry_spot, entry_time,
                           current_price, current_spot, unrealized_pnl, return_pct,
                           target_price, stop_loss_price, is_open, gross_pnl, brokerage,
                           taxes, net_pnl, created_at, updated_at
                    FROM paper_positions
                    WHERE is_open = 1;
                """)
                pos_rows = cursor.fetchall()
                for r in pos_rows:
                    pos = PaperPosition(
                        id=r["id"],
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
                        is_open=bool(r["is_open"]),
                        gross_pnl=float(r["gross_pnl"]),
                        brokerage=float(r["brokerage"]),
                        taxes=float(r["taxes"]),
                        net_pnl=float(r["net_pnl"]),
                        created_at=datetime.fromisoformat(r["created_at"]),
                        updated_at=datetime.fromisoformat(r["updated_at"]),
                    )
                    self.open_positions[pos.id] = pos

                self.account.open_positions_count = len(self.open_positions)
                self._recalculate_equity()
        except Exception as e:
            logger.error(f"Error loading paper state from DB: {e}", exc_info=True)

    def _save_account_state(self) -> None:
        """Persists account state to app_state key-value store."""
        now_iso = datetime.now(timezone.utc).isoformat()
        self.account.updated_at = datetime.now(timezone.utc)
        self.account.open_positions_count = len(self.open_positions)
        acc_json = self.account.model_dump_json()

        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO app_state (key, value, updated_at)
                VALUES ('paper_account_state', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
                """,
                (acc_json, now_iso),
            )

    def _save_position(self, pos: PaperPosition) -> None:
        """Inserts or updates a paper position in SQLite."""
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO paper_positions (
                    id, strategy_id, symbol, contract_symbol, direction, strike_price,
                    expiry_date, quantity, entry_price, entry_spot, entry_time,
                    current_price, current_spot, unrealized_pnl, return_pct,
                    target_price, stop_loss_price, is_open, exit_time, exit_price,
                    exit_reason, gross_pnl, brokerage, taxes, net_pnl, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        """Records risk rejection or breach to risk_events table."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO risk_events (event_type, reason, details_json, mode, created_at)
                VALUES (?, ?, ?, 'PAPER', ?);
                """,
                (event_type, reason, json.dumps(details), now_iso),
            )
        logger.warning(f"[RISK EVENT - PAPER] {event_type}: {reason} | {details}")

    def _recalculate_equity(self) -> None:
        """Recalculates account total equity and unrealized PnL."""
        unrealized = sum(p.unrealized_pnl for p in self.open_positions.values())
        allocated = sum(p.entry_price * p.quantity for p in self.open_positions.values())

        self.account.unrealized_pnl = round(unrealized, 2)
        self.account.allocated_margin = round(allocated, 2)
        self.account.total_equity = round(self.account.cash + self.account.allocated_margin + self.account.unrealized_pnl, 2)
        self.account.open_positions_count = len(self.open_positions)

    def submit_order(
        self,
        request: PaperOrderRequest,
        spot_price: Optional[float] = None,
        quote_price: Optional[float] = None,
        dt: Optional[datetime] = None,
    ) -> Tuple[PaperOrder, Optional[PaperPosition]]:
        """Validates and executes a paper order with realistic simulation."""
        now = dt or datetime.now(timezone.utc)
        self._rollover_daily_counters_if_needed(now)

        symbol = request.symbol.upper()
        _, lot_size, _ = self.simulator.get_instrument_specs(symbol)
        quantity = request.quantity or lot_size

        # Resolve strike and expiry
        if spot_price is None:
            spot_price = 82000.0 if "SENSEX" in symbol else 25000.0

        if request.strike_price is not None:
            strike = request.strike_price
        else:
            pref = request.strike_preference or StrikePreference.ATM
            strike = self.simulator.resolve_strike(symbol, spot_price, request.direction, pref)

        if request.expiry_date:
            expiry_str = request.expiry_date
            expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d").replace(hour=10, minute=0, tzinfo=timezone.utc)
        else:
            expiry_dt, expiry_str = self.simulator.get_upcoming_expiry(symbol, now)

        contract_symbol = self.simulator.generate_contract_symbol(symbol, strike, request.direction, expiry_str)

        order = PaperOrder(
            symbol=symbol,
            instrument_type=request.direction,
            contract_symbol=contract_symbol,
            strike_price=strike,
            expiry_date=expiry_str,
            transaction_type=TransactionType.BUY,
            order_type=request.order_type,
            quantity=quantity,
            price=0.0,
            status=OrderStatus.PENDING,
            strategy_id=request.strategy_id,
        )

        # 1. Check Hard Risk: Daily Block
        if self.account.is_trading_blocked:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = f"Trading blocked for the day: {self.account.blocked_reason}"
            self._record_order(order)
            self._log_risk_event("ORDER_REJECTED", order.rejection_reason, {"order_id": order.id, "symbol": symbol})
            return order, None

        # 2. Check Circuit Breaker
        from risk_management import risk_manager
        if risk_manager.circuit_breaker.is_active():
            order.status = OrderStatus.REJECTED
            order.rejection_reason = f"Circuit breaker active: {risk_manager.circuit_breaker.trip_reason}"
            self._record_order(order)
            self._log_risk_event("CIRCUIT_BREAKER_ACTIVE", order.rejection_reason, {"order_id": order.id, "symbol": symbol})
            return order, None

        # 3. Check Hard Risk: Auto Square-Off Window
        if self._is_square_off_window(now):
            order.status = OrderStatus.REJECTED
            order.rejection_reason = "Square-off window active (15:15 - 15:30 IST); new orders prohibited."
            self._record_order(order)
            return order, None

        if self.enforce_market_hours and not self._is_market_open(now):
            order.status = OrderStatus.REJECTED
            order.rejection_reason = "Market is closed. Trading session is 09:15 to 15:30 IST."
            self._record_order(order)
            return order, None

        # 4. Check Hard Risk: Max Open Positions
        if len(self.open_positions) >= settings.max_open_positions:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = f"Max open positions limit ({settings.max_open_positions}) reached."
            self._record_order(order)
            self._log_risk_event("MAX_POSITIONS_BREACH", order.rejection_reason, {"open_count": len(self.open_positions)})
            return order, None

        # 5. Check Hard Risk: Max Daily Trades
        if self.account.daily_trades_count >= settings.max_trades_per_day:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = f"Max daily trades limit ({settings.max_trades_per_day}) reached."
            self._record_order(order)
            self._log_risk_event("MAX_TRADES_BREACH", order.rejection_reason, {"daily_trades": self.account.daily_trades_count})
            return order, None

        # 6. Check Hard Risk: Max Daily Loss
        if self.account.daily_loss >= settings.max_daily_loss:
            self.account.is_trading_blocked = True
            self.account.blocked_reason = f"Daily max loss (₹{settings.max_daily_loss}) reached."
            order.status = OrderStatus.REJECTED
            order.rejection_reason = self.account.blocked_reason
            self._record_order(order)
            self._save_account_state()
            self._log_risk_event("DAILY_LOSS_BREACH", order.rejection_reason, {"daily_loss": self.account.daily_loss})
            return order, None

        # Calculate execution price
        if quote_price is not None:
            raw_price = quote_price
        else:
            raw_price = self.simulator.calculate_theoretical_price(
                spot=spot_price,
                strike=strike,
                direction=request.direction,
                current_time=now,
                expiry_time=expiry_dt,
            )

        executed_buy = self.simulator.apply_slippage(raw_price, is_buy=True)
        brokerage, taxes, total_charges = self.simulator.calculate_order_charges("BUY", executed_buy, quantity)
        required_funds = round((executed_buy * quantity) + total_charges, 2)

        # 6. Check Available Cash & Capital Cap
        if required_funds > self.account.cash or required_funds > settings.starting_capital:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = f"Insufficient funds: Required ₹{required_funds:.2f}, Available cash ₹{self.account.cash:.2f}"
            self._record_order(order)
            self._log_risk_event("INSUFFICIENT_FUNDS", order.rejection_reason, {"required": required_funds, "cash": self.account.cash})
            return order, None

        # Execute Fill
        order.status = OrderStatus.FILLED
        order.price = executed_buy
        order.executed_price = executed_buy
        order.brokerage = brokerage
        order.taxes = taxes
        order.updated_at = now
        self._record_order(order)

        # Create Trade record
        trade = PaperTrade(
            order_id=order.id,
            symbol=symbol,
            contract_symbol=contract_symbol,
            transaction_type=TransactionType.BUY,
            quantity=quantity,
            executed_price=executed_buy,
            brokerage=brokerage,
            taxes=taxes,
            strategy_id=request.strategy_id,
            executed_at=now,
        )
        self._record_trade(trade)

        # Deduct cash & update daily trades count
        self.account.cash = round(self.account.cash - (executed_buy * quantity) - total_charges, 2)
        self.account.daily_trades_count += 1

        # Target and Stop Loss setup
        target_pct = request.target_pct if request.target_pct is not None else settings.target_percent
        stop_loss_pct = request.stop_loss_pct if request.stop_loss_pct is not None else settings.stop_loss_percent
        target_price = round(executed_buy * (1.0 + (target_pct / 100.0)), 2)
        stop_loss_price = round(executed_buy * (1.0 - (stop_loss_pct / 100.0)), 2)

        # Create Position
        position = PaperPosition(
            strategy_id=request.strategy_id,
            symbol=symbol,
            contract_symbol=contract_symbol,
            direction=request.direction,
            strike_price=strike,
            expiry_date=expiry_str,
            quantity=quantity,
            entry_price=executed_buy,
            entry_spot=spot_price,
            entry_time=now,
            current_price=executed_buy,
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

        self.open_positions[position.id] = position
        self._save_position(position)
        self._recalculate_equity()
        self._save_account_state()

        logger.info(
            f"[PAPER EXECUTION] BOUGHT {quantity}x {contract_symbol} @ ₹{executed_buy:.2f} "
            f"(Target: ₹{target_price:.2f}, SL: ₹{stop_loss_price:.2f})"
        )
        return order, position

    def close_position(
        self,
        position_id: str,
        reason: str = "MANUAL",
        exit_spot: Optional[float] = None,
        exit_price: Optional[float] = None,
        dt: Optional[datetime] = None,
    ) -> PaperPosition:
        """Closes an active paper position and realizes PnL."""
        now = dt or datetime.now(timezone.utc)
        self._rollover_daily_counters_if_needed(now)

        pos = self.open_positions.get(position_id)
        if not pos or not pos.is_open:
            raise ValueError(f"Open paper position {position_id} not found")

        curr_spot = exit_spot or pos.current_spot
        if exit_price is None:
            exit_price = pos.current_price

        executed_exit = self.simulator.apply_slippage(exit_price, is_buy=False)
        sell_brokerage, sell_taxes, sell_charges = self.simulator.calculate_order_charges("SELL", executed_exit, pos.quantity)

        # PnL accounting
        total_brokerage = round(pos.brokerage + sell_brokerage, 2)
        total_taxes = round(pos.taxes + sell_taxes, 2)
        total_charges = round(total_brokerage + total_taxes, 2)

        gross_pnl = round((executed_exit - pos.entry_price) * pos.quantity, 2)
        net_pnl = round(gross_pnl - total_charges, 2)
        ret_pct = round(((executed_exit - pos.entry_price) / pos.entry_price) * 100.0, 2)

        # Create Sell Order
        sell_order = PaperOrder(
            symbol=pos.symbol,
            instrument_type=pos.direction,
            contract_symbol=pos.contract_symbol,
            strike_price=pos.strike_price,
            expiry_date=pos.expiry_date,
            transaction_type=TransactionType.SELL,
            order_type=OrderType.MARKET,
            quantity=pos.quantity,
            price=executed_exit,
            executed_price=executed_exit,
            status=OrderStatus.FILLED,
            brokerage=sell_brokerage,
            taxes=sell_taxes,
            strategy_id=pos.strategy_id,
            created_at=now,
            updated_at=now,
        )
        self._record_order(sell_order)

        # Create Sell Trade
        trade = PaperTrade(
            order_id=sell_order.id,
            symbol=pos.symbol,
            contract_symbol=pos.contract_symbol,
            transaction_type=TransactionType.SELL,
            quantity=pos.quantity,
            executed_price=executed_exit,
            brokerage=sell_brokerage,
            taxes=sell_taxes,
            net_pnl=net_pnl,
            strategy_id=pos.strategy_id,
            executed_at=now,
        )
        self._record_trade(trade)

        # Credit cash: return principal invested + gross_pnl - sell_charges
        return_capital = round((executed_exit * pos.quantity) - sell_charges, 2)
        self.account.cash = round(self.account.cash + return_capital, 2)
        self.account.realized_pnl = round(self.account.realized_pnl + net_pnl, 2)
        self.account.daily_realized_pnl = round(self.account.daily_realized_pnl + net_pnl, 2)

        if net_pnl < 0:
            self.account.daily_loss = round(self.account.daily_loss + abs(net_pnl), 2)
            if self.account.daily_loss >= settings.max_daily_loss:
                self.account.is_trading_blocked = True
                self.account.blocked_reason = f"Daily max loss (₹{settings.max_daily_loss}) reached."
                self._log_risk_event("DAILY_LOSS_BREACH", self.account.blocked_reason, {"daily_loss": self.account.daily_loss})

        # Update position record
        pos.is_open = False
        pos.exit_time = now
        pos.exit_price = executed_exit
        pos.exit_reason = reason
        pos.current_price = executed_exit
        pos.current_spot = curr_spot
        pos.gross_pnl = gross_pnl
        pos.brokerage = total_brokerage
        pos.taxes = total_taxes
        pos.net_pnl = net_pnl
        pos.unrealized_pnl = 0.0
        pos.return_pct = ret_pct
        pos.updated_at = now

        self._save_position(pos)
        self.open_positions.pop(position_id, None)
        self._recalculate_equity()
        self._save_account_state()

        # Record trade exit in Centralized Risk Manager & Trade Journal
        try:
            from risk_management import risk_manager
            risk_manager.record_trade_exit({
                "trade_id": pos.id,
                "order_id": None,
                "mode": "PAPER",
                "symbol": pos.symbol,
                "contract_symbol": pos.contract_symbol,
                "direction": pos.direction,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "exit_price": executed_exit,
                "entry_time": pos.entry_time.isoformat() if isinstance(pos.entry_time, datetime) else str(pos.entry_time),
                "exit_time": now.isoformat() if isinstance(now, datetime) else str(now),
                "gross_pnl": gross_pnl,
                "brokerage": total_brokerage,
                "taxes": total_taxes,
                "net_pnl": net_pnl,
                "return_pct": ret_pct,
                "exit_reason": reason,
                "strategy_id": pos.strategy_id,
                "daily_loss": self.account.daily_loss,
            })
        except Exception as e:
            logger.error(f"Error logging trade exit to RiskManager: {e}", exc_info=True)

        logger.info(
            f"[PAPER EXECUTION] CLOSED {pos.contract_symbol} @ ₹{executed_exit:.2f} "
            f"Reason: {reason} | Net PnL: ₹{net_pnl:+.2f} ({ret_pct:+.2f}%)"
        )
        return pos

    def close_all_open_positions(self, reason: str = "EMERGENCY_LIQUIDATION") -> List[PaperPosition]:
        """Liquidates all open paper positions immediately."""
        closed = []
        for pos_id in list(self.open_positions.keys()):
            try:
                c = self.close_position(pos_id, reason=reason)
                closed.append(c)
            except Exception as e:
                logger.error(f"Error liquidating position {pos_id}: {e}", exc_info=True)
        return closed

    def update_market_price(
        self,
        symbol: str,
        spot: float,
        dt: Optional[datetime] = None,
        option_quote_price: Optional[float] = None,
    ) -> List[PaperPosition]:
        """Updates MTM prices for open positions and checks SL/TP/Square-off triggers."""
        now = dt or datetime.now(timezone.utc)
        self._rollover_daily_counters_if_needed(now)

        is_sqoff = self._is_square_off_window(now)
        closed_positions: List[PaperPosition] = []

        # Iterate over copy since close_position mutates open_positions
        for pos_id, pos in list(self.open_positions.items()):
            if pos.symbol != symbol.upper():
                continue

            # Calculate current option premium
            if option_quote_price is not None:
                curr_price = option_quote_price
            else:
                curr_price = self.simulator.calculate_theoretical_price(
                    spot=spot,
                    strike=pos.strike_price,
                    direction=pos.direction,
                    current_time=now,
                )

            pos.current_spot = spot
            pos.current_price = curr_price
            pos.unrealized_pnl = round((curr_price - pos.entry_price) * pos.quantity, 2)
            pos.return_pct = round(((curr_price - pos.entry_price) / pos.entry_price) * 100.0, 2)
            pos.updated_at = now

            # Check Auto Square-off at 15:15 IST
            if is_sqoff:
                logger.info(f"Auto square-off triggered for {pos.contract_symbol} at 15:15 IST.")
                closed = self.close_position(pos_id, reason=ExitReason.SQUARE_OFF.value, exit_spot=spot, exit_price=curr_price, dt=now)
                closed_positions.append(closed)
                continue

            # Check Stop Loss
            if pos.stop_loss_price is not None and curr_price <= pos.stop_loss_price:
                logger.info(f"Stop-loss triggered for {pos.contract_symbol} (Current: ₹{curr_price:.2f} <= SL: ₹{pos.stop_loss_price:.2f}).")
                closed = self.close_position(pos_id, reason=ExitReason.STOP_LOSS.value, exit_spot=spot, exit_price=pos.stop_loss_price, dt=now)
                closed_positions.append(closed)
                continue

            # Check Target
            if pos.target_price is not None and curr_price >= pos.target_price:
                logger.info(f"Target profit hit for {pos.contract_symbol} (Current: ₹{curr_price:.2f} >= Target: ₹{pos.target_price:.2f}).")
                closed = self.close_position(pos_id, reason=ExitReason.TARGET.value, exit_spot=spot, exit_price=pos.target_price, dt=now)
                closed_positions.append(closed)
                continue

            # Check Daily Loss Breach on open unrealized drawdown
            if pos.unrealized_pnl < 0:
                potential_daily_loss = self.account.daily_loss + abs(pos.unrealized_pnl)
                if potential_daily_loss >= settings.max_daily_loss:
                    logger.warning(f"Intraday unrealized drawdown breached daily max loss ₹{settings.max_daily_loss}. Emergency close.")
                    closed = self.close_position(pos_id, reason=ExitReason.RISK_BREACH.value, exit_spot=spot, exit_price=curr_price, dt=now)
                    closed_positions.append(closed)
                    continue

            # If still open, persist MTM update
            self._save_position(pos)

        self._recalculate_equity()
        self._save_account_state()
        return closed_positions

    def on_tick(self, tick: Tick) -> None:
        """Processes incoming real-time market tick."""
        self.update_market_price(tick.symbol, tick.ltp, dt=tick.timestamp)

    def on_candle(self, candle: Candle) -> None:
        """Processes closed candle interval."""
        self.update_market_price(candle.symbol, candle.close, dt=candle.timestamp)

    def on_strategy_signal(self, signal: Signal, quote: Optional[OptionQuote] = None) -> None:
        """Receives and executes automated signals from StrategyManager."""
        symbol = signal.underlying.upper()
        spot = signal.spot_reference_price

        if signal.action == SignalAction.ENTRY:
            req = PaperOrderRequest(
                symbol=symbol,
                direction=signal.direction,
                strike_preference=signal.strike_preference,
                strategy_id=signal.strategy_id,
                target_pct=signal.target_pct,
                stop_loss_pct=signal.stop_loss_pct,
            )
            quote_prem = quote.ltp if quote else None
            self.submit_order(req, spot_price=spot, quote_price=quote_prem)

        elif signal.action == SignalAction.EXIT:
            # Find open position for strategy and close it
            for pos_id, pos in list(self.open_positions.items()):
                if pos.symbol == symbol and pos.direction == signal.direction:
                    quote_prem = quote.ltp if quote else None
                    self.close_position(pos_id, reason=ExitReason.EXIT_SIGNAL.value, exit_spot=spot, exit_price=quote_prem)

    def reset_account(self, initial_capital: float = 10000.0) -> PaperAccount:
        """Resets virtual account, closes open positions, and resets balances."""
        logger.info(f"Resetting paper trading account with capital ₹{initial_capital:.2f}...")

        # Close any open positions
        for pos_id in list(self.open_positions.keys()):
            try:
                self.close_position(pos_id, reason="RESET")
            except Exception:
                pass
        self.open_positions.clear()

        self.account = PaperAccount(
            initial_capital=initial_capital,
            cash=initial_capital,
            allocated_margin=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            total_equity=initial_capital,
            daily_trades_count=0,
            daily_realized_pnl=0.0,
            daily_loss=0.0,
            is_trading_blocked=False,
            blocked_reason=None,
            open_positions_count=0,
            updated_at=datetime.now(timezone.utc),
        )

        with get_db_connection() as conn:
            conn.execute("UPDATE paper_positions SET is_open = 0 WHERE is_open = 1;")
            conn.execute("DELETE FROM app_state WHERE key = 'paper_account_state';")

        self._save_account_state()
        try:
            from risk_management import risk_manager
            risk_manager.reset_circuit_breaker(reason="Account reset")
        except Exception:
            pass
        return self.account

    def _record_order(self, order: PaperOrder) -> None:
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
                    order.mode,
                    order.symbol,
                    order.instrument_type,
                    order.strike_price,
                    order.expiry_date,
                    order.transaction_type.value,
                    order.order_type.value,
                    order.quantity,
                    order.price,
                    order.status.value,
                    order.rejection_reason,
                    order.strategy_id,
                    order.created_at.isoformat(),
                    order.updated_at.isoformat(),
                ),
            )

    def _record_trade(self, trade: PaperTrade) -> None:
        """Persists trade fill to SQLite trades table."""
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO trades (
                    id, order_id, symbol, mode, transaction_type, quantity,
                    executed_price, brokerage, taxes, net_pnl, strategy_id, executed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    trade.id,
                    trade.order_id,
                    trade.symbol,
                    trade.mode,
                    trade.transaction_type.value,
                    trade.quantity,
                    trade.executed_price,
                    trade.brokerage,
                    trade.taxes,
                    trade.net_pnl,
                    trade.strategy_id,
                    trade.executed_at.isoformat(),
                ),
            )

    def get_account_summary(self) -> PaperAccount:
        self._recalculate_equity()
        return self.account

    def get_positions(self, open_only: bool = False) -> List[PaperPosition]:
        if open_only:
            return list(self.open_positions.values())

        # Load all from DB
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM paper_positions ORDER BY created_at DESC LIMIT 100;")
            rows = cursor.fetchall()

        positions: List[PaperPosition] = []
        for r in rows:
            positions.append(
                PaperPosition(
                    id=r["id"],
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
                    is_open=bool(r["is_open"]),
                    exit_time=datetime.fromisoformat(r["exit_time"]) if r["exit_time"] else None,
                    exit_price=float(r["exit_price"]) if r["exit_price"] is not None else None,
                    exit_reason=r["exit_reason"],
                    gross_pnl=float(r["gross_pnl"]),
                    brokerage=float(r["brokerage"]),
                    taxes=float(r["taxes"]),
                    net_pnl=float(r["net_pnl"]),
                    created_at=datetime.fromisoformat(r["created_at"]),
                    updated_at=datetime.fromisoformat(r["updated_at"]),
                )
            )
        return positions

    def get_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE mode = 'PAPER' ORDER BY created_at DESC LIMIT ?;", (limit,))
            return [dict(r) for r in cursor.fetchall()]

    def get_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE mode = 'PAPER' ORDER BY executed_at DESC LIMIT ?;", (limit,))
            return [dict(r) for r in cursor.fetchall()]
