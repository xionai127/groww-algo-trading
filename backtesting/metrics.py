"""Performance metrics calculation for backtest trade logs and equity curves."""

import math
from typing import Dict, List
import pandas as pd

from backtesting.models import BacktestSummary, BacktestTrade, EquityPoint


def calculate_backtest_metrics(
    initial_capital: float,
    trades: List[BacktestTrade],
    equity_curve: List[EquityPoint],
    daily_pnl: Dict[str, float],
) -> BacktestSummary:
    """Computes full financial and risk performance statistics from trade outcomes."""
    total_trades = len(trades)

    if total_trades == 0:
        return BacktestSummary(
            initial_capital=initial_capital,
            final_capital=initial_capital,
            gross_pnl=0.0,
            total_charges=0.0,
            net_pnl=0.0,
            roi_pct=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            break_even_trades=0,
            win_rate=0.0,
            loss_rate=0.0,
            profit_factor=0.0,
            max_drawdown=0.0,
            max_drawdown_pct=0.0,
            avg_trade_pnl=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            risk_reward_ratio=0.0,
            max_consecutive_wins=0,
            max_consecutive_losses=0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
        )

    winning = [t for t in trades if t.net_pnl > 0]
    losing = [t for t in trades if t.net_pnl < 0]
    break_even = [t for t in trades if t.net_pnl == 0]

    winning_count = len(winning)
    losing_count = len(losing)
    break_even_count = len(break_even)

    gross_pnl = sum(t.gross_pnl for t in trades)
    brokerage_sum = sum(t.brokerage for t in trades)
    taxes_sum = sum(t.taxes for t in trades)
    total_charges = brokerage_sum + taxes_sum
    net_pnl = sum(t.net_pnl for t in trades)

    final_capital = initial_capital + net_pnl
    roi_pct = (net_pnl / initial_capital) * 100.0 if initial_capital > 0 else 0.0

    win_rate = (winning_count / total_trades) * 100.0
    loss_rate = (losing_count / total_trades) * 100.0

    gross_wins = sum(t.net_pnl for t in winning)
    gross_losses = abs(sum(t.net_pnl for t in losing))

    if gross_losses > 0:
        profit_factor = round(gross_wins / gross_losses, 2)
    elif gross_wins > 0:
        profit_factor = 999.0
    else:
        profit_factor = 0.0

    # Max Drawdown from Equity Curve
    max_dd = 0.0
    max_dd_pct = 0.0
    peak = initial_capital

    for pt in equity_curve:
        if pt.equity > peak:
            peak = pt.equity
        dd = peak - pt.equity
        dd_pct = (dd / peak) * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    avg_trade_pnl = round(net_pnl / total_trades, 2)
    avg_win = round(gross_wins / winning_count, 2) if winning_count > 0 else 0.0
    avg_loss = round(gross_losses / losing_count, 2) if losing_count > 0 else 0.0

    if avg_loss > 0:
        risk_reward_ratio = round(avg_win / avg_loss, 2)
    elif avg_win > 0:
        risk_reward_ratio = round(avg_win, 2)
    else:
        risk_reward_ratio = 0.0

    # Consecutive Streaks
    max_wins = 0
    curr_wins = 0
    max_losses = 0
    curr_losses = 0

    for t in trades:
        if t.net_pnl > 0:
            curr_wins += 1
            curr_losses = 0
            if curr_wins > max_wins:
                max_wins = curr_wins
        elif t.net_pnl < 0:
            curr_losses += 1
            curr_wins = 0
            if curr_losses > max_losses:
                max_losses = curr_losses
        else:
            curr_wins = 0
            curr_losses = 0

    # Sharpe, Sortino, Calmar (Annualized across 252 trading days)
    daily_returns = [pnl / initial_capital for pnl in daily_pnl.values()] if daily_pnl else []
    sharpe_ratio = 0.0
    sortino_ratio = 0.0

    if len(daily_returns) >= 2:
        df_ret = pd.Series(daily_returns)
        mean_ret = df_ret.mean()
        std_ret = df_ret.std(ddof=1)

        # Indian risk-free daily rate proxy (~6.5% annual / 252)
        rf_daily = 0.065 / 252.0

        if std_ret > 1e-6:
            sharpe_ratio = round(float((mean_ret - rf_daily) / std_ret * math.sqrt(252)), 2)

        downside_diff = [r - rf_daily for r in daily_returns if (r - rf_daily) < 0]
        if len(downside_diff) > 0:
            downside_std = math.sqrt(sum(d * d for d in downside_diff) / len(downside_diff))
            if downside_std > 1e-6:
                sortino_ratio = round(float((mean_ret - rf_daily) / downside_std * math.sqrt(252)), 2)
        elif mean_ret > rf_daily:
            sortino_ratio = 99.0

    calmar_ratio = round(roi_pct / max_dd_pct, 2) if max_dd_pct > 0.01 else 0.0

    return BacktestSummary(
        initial_capital=round(initial_capital, 2),
        final_capital=round(final_capital, 2),
        gross_pnl=round(gross_pnl, 2),
        total_charges=round(total_charges, 2),
        net_pnl=round(net_pnl, 2),
        roi_pct=round(roi_pct, 2),
        total_trades=total_trades,
        winning_trades=winning_count,
        losing_trades=losing_count,
        break_even_trades=break_even_count,
        win_rate=round(win_rate, 2),
        loss_rate=round(loss_rate, 2),
        profit_factor=profit_factor,
        max_drawdown=round(max_dd, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        avg_trade_pnl=avg_trade_pnl,
        avg_win=avg_win,
        avg_loss=avg_loss,
        risk_reward_ratio=risk_reward_ratio,
        max_consecutive_wins=max_wins,
        max_consecutive_losses=max_losses,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        calmar_ratio=calmar_ratio,
    )
