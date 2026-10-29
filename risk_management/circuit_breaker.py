"""Centralized Circuit Breaker and Emergency Kill Switch."""

from datetime import datetime, timezone
import json
from typing import Optional
from config import logger, settings
from database import get_db_connection, log_risk_event
from risk_management.models import BreakerState, CircuitBreakerStatusResponse


class CircuitBreaker:
    """Manages system-wide trading halts, kill-switch, and safety triggers."""

    def __init__(self) -> None:
        self.state: BreakerState = BreakerState.NORMAL
        self.is_tripped: bool = False
        self.tripped_at: Optional[datetime] = None
        self.trip_reason: Optional[str] = None
        self.auto_tripped: bool = False
        self.consecutive_losses: int = 0
        self._load_state()

    def _load_state(self) -> None:
        """Restores persisted breaker state from SQLite app_state table."""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM app_state WHERE key = 'circuit_breaker_state';")
                row = cursor.fetchone()
                if row:
                    data = json.loads(row["value"])
                    self.is_tripped = data.get("is_tripped", False)
                    self.state = BreakerState(data.get("state", BreakerState.NORMAL.value))
                    self.trip_reason = data.get("trip_reason")
                    self.auto_tripped = data.get("auto_tripped", False)
                    self.consecutive_losses = data.get("consecutive_losses", 0)
                    if data.get("tripped_at"):
                        self.tripped_at = datetime.fromisoformat(data["tripped_at"])
                    if self.is_tripped:
                        logger.warning(f"CircuitBreaker restored in TRIPPED state: {self.trip_reason}")
        except Exception as e:
            logger.error(f"Error loading circuit breaker state: {e}", exc_info=True)

    def _save_state(self) -> None:
        """Persists current breaker state to SQLite app_state table."""
        try:
            data = {
                "state": self.state.value,
                "is_tripped": self.is_tripped,
                "tripped_at": self.tripped_at.isoformat() if self.tripped_at else None,
                "trip_reason": self.trip_reason,
                "auto_tripped": self.auto_tripped,
                "consecutive_losses": self.consecutive_losses,
            }
            now_iso = datetime.now(timezone.utc).isoformat()
            with get_db_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO app_state (key, value, updated_at)
                    VALUES ('circuit_breaker_state', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
                    """,
                    (json.dumps(data), now_iso),
                )
        except Exception as e:
            logger.error(f"Error saving circuit breaker state: {e}", exc_info=True)

    def is_active(self) -> bool:
        """Returns True if the circuit breaker is currently tripped (blocking orders)."""
        return self.is_tripped

    def trip(self, reason: str, auto: bool = False, mode: str = "PAPER") -> None:
        """Engages the circuit breaker, stopping all further order placements."""
        self.state = BreakerState.TRIPPED
        self.is_tripped = True
        self.tripped_at = datetime.now(timezone.utc)
        self.trip_reason = reason
        self.auto_tripped = auto
        self._save_state()

        log_risk_event(
            event_type="CIRCUIT_BREAKER_TRIPPED",
            reason=reason,
            mode=mode,
            details={
                "auto": auto,
                "consecutive_losses": self.consecutive_losses,
                "tripped_at": self.tripped_at.isoformat(),
            },
        )
        logger.critical(f"🛑 CIRCUIT BREAKER TRIPPED! Reason: {reason} (Auto: {auto})")

    def reset(self, reason: str = "Manual Breaker Reset", mode: str = "PAPER") -> None:
        """Resets the circuit breaker to NORMAL state."""
        old_reason = self.trip_reason
        self.state = BreakerState.NORMAL
        self.is_tripped = False
        self.tripped_at = None
        self.trip_reason = None
        self.auto_tripped = False
        self.consecutive_losses = 0
        self._save_state()

        log_risk_event(
            event_type="CIRCUIT_BREAKER_RESET",
            reason=reason,
            mode=mode,
            details={"previous_trip_reason": old_reason},
        )
        logger.info(f"✅ CIRCUIT BREAKER RESET. System returned to normal trading state. Reason: {reason}")

    def record_trade_result(self, net_pnl: float, mode: str = "PAPER") -> None:
        """Updates consecutive loss tracker."""
        if net_pnl < 0:
            self.consecutive_losses += 1
            logger.warning(f"Recorded loss in Risk Circuit Breaker. Consecutive losses: {self.consecutive_losses}")
        else:
            if self.consecutive_losses > 0:
                logger.info(f"Profitable trade reset consecutive losses from {self.consecutive_losses} to 0.")
            self.consecutive_losses = 0
        self._save_state()

    def get_status(self) -> CircuitBreakerStatusResponse:
        """Returns the current status model."""
        return CircuitBreakerStatusResponse(
            state=self.state,
            is_tripped=self.is_tripped,
            tripped_at=self.tripped_at,
            trip_reason=self.trip_reason,
            auto_tripped=self.auto_tripped,
            consecutive_losses=self.consecutive_losses,
        )
