"""Core historical backtesting engine for options buying strategies."""

from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional
import pandas as pd

from config import logger, settings
from market_data.models import Candle, Timeframe
from strategies.base import BaseStrategy
from strategies.builtin.ema_crossover import EMACrossoverStrategy
from strategies.builtin.supertrend_rsi import SuperTrendRSIStrategy
from strategies.models import Signal, SignalAction, StrategyConfig, StrikePreference
from strategy_lab.builder import StrategyBuilder
from backtesting.data_feed import BacktestDataFeed
from backtesting.metrics import calculate_backtest_metrics
from backtesting.models import (
    BacktestRequest,
    BacktestResult,
    BacktestSummary,
    BacktestTrade,
    EquityPoint,
    ExitReason,
)
from backtesting.option_simulator import OptionSimulator
from backtesting.repository import BacktestRepository


class BacktestEngine:
    """Executes tick-by-tick or candle-by-candle simulation of options buying strategies."""

    def __init__(self, simulator: Optional[OptionSimulator] = None):
        self.simulator = simulator

    def _resolve_strategy(self, request: BacktestRequest) -> BaseStrategy:
        """Instantiates and activates the target strategy."""
        if request.strategy_config:
            strat = StrategyBuilder.instantiate_strategy(request.strategy_config)
            strat.is_active = True
            return strat

        strat_id = request.strategy_id or "builtin_ema_crossover"
        saved = StrategyBuilder.get_strategy(strat_id)
        if saved:
            strat = StrategyBuilder.instantiate_strategy(saved)
            strat.is_active = True
            return strat

        if strat_id == "builtin_supertrend_rsi":
            strat = SuperTrendRSIStrategy()
        else:
            strat = EMACrossoverStrategy()

        strat.is_active = True
        return strat

    def run(self, request: BacktestRequest, persist: bool = True) -> BacktestResult:
        """Executes a historical backtest run."""
        strategy = self._resolve_strategy(request)
        simulator = self.simulator or OptionSimulator(
            symbol=request.symbol,
            default_iv=request.iv_default,
            slippage_pct=request.slippage_pct,
            brokerage_per_order=request.brokerage_per_order,
            stt_rate=request.stt_rate,
            turnover_charge_rate=request.turnover_tax_rate,
            gst_rate=request.gst_rate,
            stamp_duty_rate=request.stamp_duty_rate,
        )

        lot_size = request.lot_size or simulator.lot_size

        # Ingest candles: Cache first, synthetic fallback if empty
        candles = BacktestDataFeed.load_candles_from_db(
            symbol=request.symbol,
            timeframe=request.timeframe,
            from_date=request.from_date,
            to_date=request.to_date,
        )

        if len(candles) < 30:
            logger.info("Insufficient candles in database cache; generating synthetic candle series for backtest.")
            candles = BacktestDataFeed.generate_synthetic_candles(
                symbol=request.symbol,
                timeframe_str=request.timeframe,
                from_date=request.from_date,
                to_date=request.to_date,
            )

        if not candles:
            raise ValueError(f"No market data available for {request.symbol} between {request.from_date} and {request.to_date}")

        # State tracking
        capital = request.initial_capital
        cash = request.initial_capital
        peak_equity = capital

        trades: List[BacktestTrade] = []
        equity_curve: List[EquityPoint] = []
        daily_pnl: Dict[str, float] = {}
        daily_loss_tracker: Dict[str, float] = {}

        open_trade: Optional[BacktestTrade] = None
        open_expiry_dt: Optional[datetime] = None
        open_target_pct = strategy.config.target_pct or settings.target_percent
        open_stop_loss_pct = strategy.config.stop_loss_pct or settings.stop_loss_percent

        # Rolling candle history for vectorized indicator calculation
        history_rows: List[Dict] = []
        last_day_str: Optional[str] = None

        for candle in candles:
            # Maintain daily session state
            c_time = candle.timestamp
            ist_dt = c_time + timedelta(hours=5, minutes=30)
            day_str = ist_dt.strftime("%Y-%m-%d")

            if day_str != last_day_str:
                strategy.reset_daily_counters_if_needed()
                last_day_str = day_str
                if day_str not in daily_pnl:
                    daily_pnl[day_str] = 0.0
                if day_str not in daily_loss_tracker:
                    daily_loss_tracker[day_str] = 0.0

            history_rows.append({
                "timestamp": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            })
            history_df = pd.DataFrame(history_rows)

            # Auto square-off at 15:15 IST
            is_square_off_time = (ist_dt.hour == 15 and ist_dt.minute >= 15)

            # Check open position management
            if open_trade is not None:
                open_trade.holding_candles += 1

                # Calculate theoretical option prices at candle close, high, and low
                curr_price_close = simulator.calculate_option_price(
                    spot=candle.close,
                    strike=open_trade.strike_price,
                    direction=open_trade.direction,
                    current_time=c_time,
                    expiry_time=open_expiry_dt,
                    iv=request.iv_default,
                )
                curr_price_high = simulator.calculate_option_price(
                    spot=candle.high if open_trade.direction == "CE" else candle.low,
                    strike=open_trade.strike_price,
                    direction=open_trade.direction,
                    current_time=c_time,
                    expiry_time=open_expiry_dt,
                    iv=request.iv_default,
                )
                curr_price_low = simulator.calculate_option_price(
                    spot=candle.low if open_trade.direction == "CE" else candle.high,
                    strike=open_trade.strike_price,
                    direction=open_trade.direction,
                    current_time=c_time,
                    expiry_time=open_expiry_dt,
                    iv=request.iv_default,
                )

                entry_price = open_trade.entry_price
                target_price = entry_price * (1.0 + (open_target_pct / 100.0))
                stop_loss_price = entry_price * (1.0 - (open_stop_loss_pct / 100.0))

                exit_triggered = False
                exit_price = curr_price_close
                exit_reason = ExitReason.EXIT_SIGNAL

                if is_square_off_time:
                    exit_triggered = True
                    exit_price = curr_price_close
                    exit_reason = ExitReason.SQUARE_OFF

                elif curr_price_low <= stop_loss_price:
                    exit_triggered = True
                    exit_price = stop_loss_price
                    exit_reason = ExitReason.STOP_LOSS

                elif curr_price_high >= target_price:
                    exit_triggered = True
                    exit_price = target_price
                    exit_reason = ExitReason.TARGET

                else:
                    # Check strategy exit signal
                    strat_sig = strategy.on_candle(candle, history_df)
                    if strat_sig and strat_sig.action == SignalAction.EXIT:
                        exit_triggered = True
                        exit_price = curr_price_close
                        exit_reason = ExitReason.EXIT_SIGNAL

                if exit_triggered:
                    executed_exit = simulator.apply_slippage(exit_price, is_buy=False)
                    brokerage, taxes, charges = simulator.calculate_trade_charges(
                        buy_price=open_trade.entry_price,
                        sell_price=executed_exit,
                        quantity=open_trade.quantity,
                    )
                    gross = round((executed_exit - open_trade.entry_price) * open_trade.quantity, 2)
                    net = round(gross - charges, 2)
                    ret_pct = round(((executed_exit - open_trade.entry_price) / open_trade.entry_price) * 100.0, 2)

                    open_trade.exit_time = c_time
                    open_trade.exit_price = executed_exit
                    open_trade.exit_spot = candle.close
                    open_trade.exit_reason = exit_reason
                    open_trade.gross_pnl = gross
                    open_trade.brokerage = brokerage
                    open_trade.taxes = taxes
                    open_trade.net_pnl = net
                    open_trade.return_pct = ret_pct

                    cash += (executed_exit * open_trade.quantity) - charges
                    trades.append(open_trade)

                    daily_pnl[day_str] = round(daily_pnl[day_str] + net, 2)
                    if net < 0:
                        daily_loss_tracker[day_str] = round(daily_loss_tracker[day_str] + abs(net), 2)

                    strategy.record_signal_dispatched(Signal(
                        strategy_id=strategy.strategy_id,
                        action=SignalAction.EXIT,
                        underlying=strategy.underlying,
                        direction=open_trade.direction,
                        spot_reference_price=candle.close,
                    ))
                    open_trade = None
                    open_expiry_dt = None

            # Check new entry condition
            elif not is_square_off_time and daily_loss_tracker.get(day_str, 0.0) < settings.max_daily_loss:
                if strategy.can_trade():
                    signal = strategy.on_candle(candle, history_df)
                    if signal and signal.action == SignalAction.ENTRY:
                        expiry_dt, expiry_str = simulator.get_upcoming_expiry(c_time)
                        strike = simulator.resolve_strike(
                            spot=candle.close,
                            direction=signal.direction,
                            preference=signal.strike_preference,
                        )
                        raw_prem = simulator.calculate_option_price(
                            spot=candle.close,
                            strike=strike,
                            direction=signal.direction,
                            current_time=c_time,
                            expiry_time=expiry_dt,
                            iv=request.iv_default,
                        )
                        buy_prem = simulator.apply_slippage(raw_prem, is_buy=True)
                        required_funds = buy_prem * lot_size

                        # Strict capital check (₹10,000 maximum / cash available)
                        if required_funds <= cash and required_funds <= settings.starting_capital:
                            contract_symbol = simulator.generate_contract_symbol(strike, signal.direction, expiry_str)
                            cash -= required_funds

                            open_trade = BacktestTrade(
                                strategy_id=strategy.strategy_id,
                                symbol=request.symbol,
                                contract_symbol=contract_symbol,
                                direction=signal.direction,
                                strike_price=strike,
                                expiry_date=expiry_str,
                                entry_time=c_time,
                                entry_price=buy_prem,
                                entry_spot=candle.close,
                                quantity=lot_size,
                            )
                            open_expiry_dt = expiry_dt
                            open_target_pct = signal.target_pct
                            open_stop_loss_pct = signal.stop_loss_pct
                            strategy.record_signal_dispatched(signal)

            # Record portfolio equity at each candle interval
            if open_trade is not None:
                current_val = simulator.calculate_option_price(
                    spot=candle.close,
                    strike=open_trade.strike_price,
                    direction=open_trade.direction,
                    current_time=c_time,
                    expiry_time=open_expiry_dt,
                    iv=request.iv_default,
                ) * open_trade.quantity
                current_equity = round(cash + current_val, 2)
            else:
                current_equity = round(cash, 2)

            if current_equity > peak_equity:
                peak_equity = current_equity

            drawdown = max(0.0, peak_equity - current_equity)
            drawdown_pct = (drawdown / peak_equity) * 100.0 if peak_equity > 0 else 0.0

            equity_curve.append(
                EquityPoint(
                    timestamp=c_time,
                    equity=current_equity,
                    cash=round(cash, 2),
                    drawdown=round(drawdown, 2),
                    drawdown_pct=round(drawdown_pct, 2),
                )
            )

        # Close position if still open at end of backtest data
        if open_trade is not None:
            last_c = candles[-1]
            close_prem = simulator.calculate_option_price(
                spot=last_c.close,
                strike=open_trade.strike_price,
                direction=open_trade.direction,
                current_time=last_c.timestamp,
                expiry_time=open_expiry_dt,
                iv=request.iv_default,
            )
            executed_exit = simulator.apply_slippage(close_prem, is_buy=False)
            brokerage, taxes, charges = simulator.calculate_trade_charges(
                buy_price=open_trade.entry_price,
                sell_price=executed_exit,
                quantity=open_trade.quantity,
            )
            gross = round((executed_exit - open_trade.entry_price) * open_trade.quantity, 2)
            net = round(gross - charges, 2)
            ret_pct = round(((executed_exit - open_trade.entry_price) / open_trade.entry_price) * 100.0, 2)

            open_trade.exit_time = last_c.timestamp
            open_trade.exit_price = executed_exit
            open_trade.exit_spot = last_c.close
            open_trade.exit_reason = ExitReason.END_OF_DATA
            open_trade.gross_pnl = gross
            open_trade.brokerage = brokerage
            open_trade.taxes = taxes
            open_trade.net_pnl = net
            open_trade.return_pct = ret_pct
            trades.append(open_trade)

        # Calculate final summary
        summary = calculate_backtest_metrics(
            initial_capital=request.initial_capital,
            trades=trades,
            equity_curve=equity_curve,
            daily_pnl=daily_pnl,
        )

        result = BacktestResult(
            strategy_id=strategy.strategy_id,
            strategy_name=strategy.name,
            symbol=request.symbol.upper(),
            timeframe=request.timeframe,
            from_date=request.from_date,
            to_date=request.to_date,
            summary=summary,
            trades=trades,
            equity_curve=equity_curve,
            daily_pnl=daily_pnl,
        )

        if persist:
            try:
                BacktestRepository.save(result)
            except Exception as e:
                logger.error(f"Failed to persist backtest result {result.run_id}: {e}", exc_info=True)

        return result
