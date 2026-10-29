"""Trade Journaling and Post-Trade Discipline Analytics."""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
import uuid
from config import logger
from database import get_db_connection
from risk_management.models import (
    DailyDisciplineScorecard,
    JournalAnalytics,
    JournalEntryResponse,
    TradeTag,
)


def _compute_discipline_score(
    tags: List[str],
    mistake_flag: bool,
    exit_reason: Optional[str] = None,
    net_pnl: float = 0.0,
) -> int:
    """Computes a 0-100 discipline score based on execution adherence."""
    score = 100

    if mistake_flag:
        score -= 20

    tag_upper = [t.upper() for t in tags]
    if TradeTag.REVENGE_TRADE.value in tag_upper:
        score -= 35
    if TradeTag.FOMO.value in tag_upper:
        score -= 25
    if TradeTag.RULE_VIOLATION.value in tag_upper:
        score -= 30

    if exit_reason and "EMERGENCY" in exit_reason.upper():
        score -= 15

    return max(0, min(100, score))


class TradeJournalManager:
    """Manages trade reflections, tags, notes, and psychological audit trails."""

    @staticmethod
    def create_journal_entry(trade_data: Dict[str, Any]) -> str:
        """Creates a new journal entry from a closed trade or paper position."""
        entry_id = f"jnl_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        trade_id = str(trade_data.get("trade_id") or trade_data.get("id") or entry_id)
        order_id = trade_data.get("order_id")
        mode = trade_data.get("mode", "PAPER")
        symbol = trade_data.get("symbol", "NIFTY")
        contract_symbol = trade_data.get("contract_symbol", symbol)
        direction = trade_data.get("direction", "CE")
        quantity = int(trade_data.get("quantity", 0))
        entry_price = float(trade_data.get("entry_price", 0.0))
        exit_price = float(trade_data.get("exit_price")) if trade_data.get("exit_price") is not None else None
        entry_time = trade_data.get("entry_time", now_iso)
        exit_time = trade_data.get("exit_time", now_iso)
        gross_pnl = float(trade_data.get("gross_pnl", 0.0))
        brokerage = float(trade_data.get("brokerage", 0.0))
        taxes = float(trade_data.get("taxes", 0.0))
        net_pnl = float(trade_data.get("net_pnl", 0.0))
        return_pct = float(trade_data.get("return_pct", 0.0))
        exit_reason = trade_data.get("exit_reason")
        strategy_id = trade_data.get("strategy_id")
        strategy_name = trade_data.get("strategy_name")

        # Initial tags
        tags: List[str] = trade_data.get("tags", [])
        if not tags:
            if exit_reason and "TARGET" in exit_reason.upper():
                tags.append(TradeTag.DISCIPLINE_WIN.value)
            elif exit_reason and "STOP_LOSS" in exit_reason.upper():
                tags.append(TradeTag.TREND_FOLLOWING.value)
            else:
                tags.append(TradeTag.OTHER.value)

        mistake_flag = bool(trade_data.get("mistake_flag", False))
        notes = trade_data.get("notes")
        rating = trade_data.get("rating")
        discipline_score = _compute_discipline_score(tags, mistake_flag, exit_reason, net_pnl)

        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO trade_journal (
                    id, trade_id, order_id, mode, symbol, contract_symbol, direction,
                    quantity, entry_price, exit_price, entry_time, exit_time,
                    gross_pnl, brokerage, taxes, net_pnl, return_pct, exit_reason,
                    strategy_id, strategy_name, tags_json, notes, rating, mistake_flag,
                    discipline_score, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    trade_id,
                    order_id,
                    mode,
                    symbol,
                    contract_symbol,
                    direction,
                    quantity,
                    entry_price,
                    exit_price,
                    entry_time,
                    exit_time,
                    gross_pnl,
                    brokerage,
                    taxes,
                    net_pnl,
                    return_pct,
                    exit_reason,
                    strategy_id,
                    strategy_name,
                    json.dumps(tags),
                    notes,
                    rating,
                    1 if mistake_flag else 0,
                    discipline_score,
                    now_iso,
                    now_iso,
                ),
            )

        logger.info(f"Journal entry created {entry_id} for trade {trade_id} (Net PnL: ₹{net_pnl:.2f})")
        return entry_id

    @staticmethod
    def get_journal_entry(entry_id: str) -> Optional[JournalEntryResponse]:
        """Fetches a specific journal entry by ID."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trade_journal WHERE id = ?;", (entry_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return TradeJournalManager._row_to_model(row)

    @staticmethod
    def update_journal_entry(
        entry_id: str,
        tags: Optional[List[str]] = None,
        notes: Optional[str] = None,
        rating: Optional[int] = None,
        mistake_flag: Optional[bool] = None,
    ) -> Optional[JournalEntryResponse]:
        """Updates notes, tags, ratings, and recalculates discipline score."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trade_journal WHERE id = ?;", (entry_id,))
            row = cursor.fetchone()
            if not row:
                return None

            current_tags = json.loads(row["tags_json"]) if row["tags_json"] else []
            new_tags = tags if tags is not None else current_tags
            new_notes = notes if notes is not None else row["notes"]
            new_rating = rating if rating is not None else row["rating"]
            new_mistake = mistake_flag if mistake_flag is not None else bool(row["mistake_flag"])

            new_score = _compute_discipline_score(
                tags=new_tags,
                mistake_flag=new_mistake,
                exit_reason=row["exit_reason"],
                net_pnl=float(row["net_pnl"]),
            )

            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                UPDATE trade_journal
                SET tags_json = ?, notes = ?, rating = ?, mistake_flag = ?,
                    discipline_score = ?, updated_at = ?
                WHERE id = ?;
                """,
                (
                    json.dumps(new_tags),
                    new_notes,
                    new_rating,
                    1 if new_mistake else 0,
                    new_score,
                    now_iso,
                    entry_id,
                ),
            )

            cursor.execute("SELECT * FROM trade_journal WHERE id = ?;", (entry_id,))
            updated_row = cursor.fetchone()
            return TradeJournalManager._row_to_model(updated_row)

    @staticmethod
    def get_journal_entries(
        limit: int = 50,
        offset: int = 0,
        tag: Optional[str] = None,
        mode: Optional[str] = None,
        date_str: Optional[str] = None,
    ) -> List[JournalEntryResponse]:
        """Returns paginated journal entries with optional filtering."""
        query = "SELECT * FROM trade_journal WHERE 1=1"
        params: List[Any] = []

        if mode:
            query += " AND mode = ?"
            params.append(mode)

        if date_str:
            query += " AND (entry_time LIKE ? OR created_at LIKE ?)"
            params.append(f"{date_str}%")
            params.append(f"{date_str}%")

        if tag:
            query += " AND tags_json LIKE ?"
            params.append(f"%{tag}%")

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [TradeJournalManager._row_to_model(r) for r in rows]

    @staticmethod
    def get_journal_analytics(mode: Optional[str] = None) -> JournalAnalytics:
        """Computes trading psychology and tag-level PnL performance."""
        query = "SELECT * FROM trade_journal WHERE 1=1"
        params: List[Any] = []
        if mode:
            query += " AND mode = ?"
            params.append(mode)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()

        if not rows:
            return JournalAnalytics(
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                total_gross_pnl=0.0,
                total_charges=0.0,
                total_net_pnl=0.0,
                tags_breakdown={},
                mistakes_count=0,
                average_discipline_score=100.0,
            )

        total_trades = len(rows)
        winning_trades = sum(1 for r in rows if float(r["net_pnl"]) > 0)
        losing_trades = sum(1 for r in rows if float(r["net_pnl"]) < 0)
        win_rate = round((winning_trades / total_trades) * 100.0, 2) if total_trades > 0 else 0.0

        total_gross = round(sum(float(r["gross_pnl"]) for r in rows), 2)
        total_brokerage = sum(float(r["brokerage"]) for r in rows)
        total_taxes = sum(float(r["taxes"]) for r in rows)
        total_charges = round(total_brokerage + total_taxes, 2)
        total_net = round(sum(float(r["net_pnl"]) for r in rows), 2)

        mistakes_count = sum(1 for r in rows if r["mistake_flag"])
        avg_score = round(sum(r["discipline_score"] for r in rows) / total_trades, 1)

        # Tags breakdown
        tags_data: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            tags = json.loads(r["tags_json"]) if r["tags_json"] else []
            net = float(r["net_pnl"])
            for t in tags:
                if t not in tags_data:
                    tags_data[t] = {"trades": 0, "wins": 0, "net_pnl": 0.0}
                tags_data[t]["trades"] += 1
                if net > 0:
                    tags_data[t]["wins"] += 1
                tags_data[t]["net_pnl"] = round(tags_data[t]["net_pnl"] + net, 2)

        for t, stats in tags_data.items():
            cnt = stats["trades"]
            stats["win_rate_pct"] = round((stats["wins"] / cnt) * 100.0, 2) if cnt > 0 else 0.0

        return JournalAnalytics(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate_pct=win_rate,
            total_gross_pnl=total_gross,
            total_charges=total_charges,
            total_net_pnl=total_net,
            tags_breakdown=tags_data,
            mistakes_count=mistakes_count,
            average_discipline_score=avg_score,
        )

    @staticmethod
    def get_daily_scorecard(date_str: str, mode: Optional[str] = None) -> DailyDisciplineScorecard:
        """Evaluates daily execution against strict risk rules and assigns a grade."""
        entries = TradeJournalManager.get_journal_entries(limit=200, date_str=date_str, mode=mode)

        # Check risk events on this date
        with get_db_connection() as conn:
            cursor = conn.cursor()
            q = "SELECT * FROM risk_events WHERE created_at LIKE ?"
            p: List[Any] = [f"{date_str}%"]
            if mode:
                q += " AND mode = ?"
                p.append(mode)
            cursor.execute(q, p)
            risk_events = cursor.fetchall()

        violations: List[str] = []
        daily_loss_breached = False
        overtrading_breached = False

        for ev in risk_events:
            ev_type = ev["event_type"]
            reason = ev["reason"]
            if "DAILY_LOSS" in ev_type:
                daily_loss_breached = True
                violations.append(f"Daily loss limit breached: {reason}")
            elif "MAX_TRADES" in ev_type:
                overtrading_breached = True
                violations.append(f"Overtrading limit breached: {reason}")
            elif "CIRCUIT_BREAKER_TRIPPED" in ev_type:
                violations.append(f"Circuit breaker tripped: {reason}")
            else:
                violations.append(f"Risk event: {ev_type} ({reason})")

        total_trades = len(entries)
        mistakes = sum(1 for e in entries if e.mistake_flag)
        if mistakes > 0:
            violations.append(f"{mistakes} trade(s) flagged with execution mistakes.")

        # Compute grade score
        score = 100
        if daily_loss_breached:
            score -= 40
        if overtrading_breached:
            score -= 25
        score -= min(30, mistakes * 10)
        score -= min(20, len(risk_events) * 5)
        score = max(0, min(100, score))

        if score >= 90:
            grade = "A+"
        elif score >= 80:
            grade = "A"
        elif score >= 70:
            grade = "B"
        elif score >= 60:
            grade = "C"
        elif score >= 50:
            grade = "D"
        else:
            grade = "F"

        notes = (
            f"Trading session on {date_str}: {total_trades} trade(s) logged. "
            f"Adherence Score: {score}/100 (Grade: {grade}). "
        )
        if violations:
            notes += f"Identified {len(violations)} rule caution(s)."
        else:
            notes += "Flawless risk rule adherence observed."

        return DailyDisciplineScorecard(
            date=date_str,
            grade=grade,
            discipline_score=score,
            total_trades=total_trades,
            violations_count=len(violations),
            violations_list=violations,
            daily_loss_breached=daily_loss_breached,
            overtrading_breached=overtrading_breached,
            summary_notes=notes,
        )

    @staticmethod
    def _row_to_model(row: Any) -> JournalEntryResponse:
        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
        return JournalEntryResponse(
            id=row["id"],
            trade_id=row["trade_id"],
            order_id=row["order_id"],
            mode=row["mode"],
            symbol=row["symbol"],
            contract_symbol=row["contract_symbol"],
            direction=row["direction"],
            quantity=row["quantity"],
            entry_price=row["entry_price"],
            exit_price=row["exit_price"],
            entry_time=row["entry_time"],
            exit_time=row["exit_time"],
            gross_pnl=row["gross_pnl"] or 0.0,
            brokerage=row["brokerage"] or 0.0,
            taxes=row["taxes"] or 0.0,
            net_pnl=row["net_pnl"] or 0.0,
            return_pct=row["return_pct"] or 0.0,
            exit_reason=row["exit_reason"],
            strategy_id=row["strategy_id"],
            strategy_name=row["strategy_name"],
            tags=tags,
            notes=row["notes"],
            rating=row["rating"],
            mistake_flag=bool(row["mistake_flag"]),
            discipline_score=row["discipline_score"] or 100,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
