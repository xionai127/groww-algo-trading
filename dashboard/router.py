"""FastAPI router for Dashboard endpoints, system status, and dynamic credentials."""

from datetime import datetime, timezone
import json
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from config import logger, settings
from database import get_db_connection
from market_data import market_engine
from paper_trading import paper_engine
from risk_management import risk_manager
from strategies import strategy_manager

dashboard_router = APIRouter(prefix="/api/system", tags=["System & Dashboard"])


class CredentialsPayload(BaseModel):
    api_key: Optional[str] = Field(None, description="Groww API Key")
    api_secret: Optional[str] = Field(None, description="Groww API Secret")
    totp_key: Optional[str] = Field(None, description="Groww TOTP Secret / Seed")
    access_token: Optional[str] = Field(None, description="Direct Session Access Token")


def _mask_secret(val: Optional[str]) -> Optional[str]:
    """Returns masked version of credential for safe display."""
    if not val:
        return None
    val_str = str(val).strip()
    if len(val_str) <= 6:
        return "******"
    return f"{val_str[:3]}...{val_str[-3:]}"


@dashboard_router.get("/credentials")
def get_credentials_status() -> Dict[str, Any]:
    """Returns configuration status and masked values for broker credentials."""
    # Check SQLite app_state for dynamic credentials first, fallback to settings
    api_key = settings.groww_api_key
    api_secret = settings.groww_api_secret
    totp_key = settings.groww_totp_key
    access_token = settings.groww_access_token

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM app_state WHERE key = 'groww_credentials';")
            row = cursor.fetchone()
            if row:
                saved = json.loads(row[0])
                api_key = saved.get("api_key") or api_key
                api_secret = saved.get("api_secret") or api_secret
                totp_key = saved.get("totp_key") or totp_key
                access_token = saved.get("access_token") or access_token
    except Exception as e:
        logger.warning(f"Could not read dynamic credentials from app_state: {e}")

    return {
        "is_configured": bool(api_key and (api_secret or access_token)),
        "has_api_key": bool(api_key),
        "api_key_masked": _mask_secret(api_key),
        "has_api_secret": bool(api_secret),
        "api_secret_masked": _mask_secret(api_secret),
        "has_totp": bool(totp_key),
        "totp_key_masked": _mask_secret(totp_key),
        "has_access_token": bool(access_token),
        "access_token_masked": _mask_secret(access_token),
    }


@dashboard_router.post("/credentials")
def save_credentials(payload: CredentialsPayload) -> Dict[str, Any]:
    """Dynamically updates Groww credentials in memory and persistent storage."""
    try:
        current_data = {}
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM app_state WHERE key = 'groww_credentials';")
            row = cursor.fetchone()
            if row:
                try:
                    current_data = json.loads(row[0])
                except Exception:
                    pass

            if payload.api_key is not None:
                current_data["api_key"] = payload.api_key.strip()
                settings.groww_api_key = payload.api_key.strip()
            if payload.api_secret is not None:
                current_data["api_secret"] = payload.api_secret.strip()
                settings.groww_api_secret = payload.api_secret.strip()
            if payload.totp_key is not None:
                current_data["totp_key"] = payload.totp_key.strip()
                settings.groww_totp_key = payload.totp_key.strip()
            if payload.access_token is not None:
                current_data["access_token"] = payload.access_token.strip()
                settings.groww_access_token = payload.access_token.strip()

            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                """
                INSERT INTO app_state (key, value, updated_at)
                VALUES ('groww_credentials', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at;
                """,
                (json.dumps(current_data), now_iso),
            )

        logger.info("Broker credentials updated successfully via Dashboard.")
        return {
            "status": "success",
            "message": "Credentials securely stored.",
            "is_configured": bool(settings.groww_api_key and (settings.groww_api_secret or settings.groww_access_token)),
        }
    except Exception as e:
        logger.error(f"Failed to update credentials: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not persist credentials: {e}",
        )


@dashboard_router.get("/summary")
def get_dashboard_summary() -> Dict[str, Any]:
    """Provides a consolidated real-time summary for dashboard status headers."""
    breaker_status = risk_manager.circuit_breaker.get_status()
    market_status = market_engine.get_status()
    paper_acct = paper_engine.get_account_summary()
    open_positions = paper_engine.get_positions(open_only=True)
    all_strategies = strategy_manager.list_strategies()
    active_strategies = [s for s in all_strategies if s.is_active]

    return {
        "app": {
            "name": settings.app_name,
            "version": settings.app_version,
            "mode": settings.execution_mode.value,
            "env": settings.app_env,
        },
        "breaker": {
            "state": breaker_status.state.value,
            "is_tripped": breaker_status.is_tripped,
            "reason": breaker_status.trip_reason,
            "consecutive_losses": breaker_status.consecutive_losses,
        },
        "market": {
            "running": market_status.get("running", False),
            "ticks_received": market_status.get("ticks_received", 0),
            "tracked_symbols": market_status.get("tracked_symbols", []),
        },
        "account": {
            "balance": paper_acct.cash,
            "equity": paper_acct.total_equity,
            "cash": paper_acct.cash,
            "total_equity": paper_acct.total_equity,
            "realized_pnl": paper_acct.realized_pnl,
            "unrealized_pnl": paper_acct.unrealized_pnl,
            "daily_loss": paper_acct.daily_loss,
            "max_daily_loss": settings.max_daily_loss,
            "daily_trades": paper_acct.daily_trades_count,
            "max_daily_trades": settings.max_trades_per_day,
            "open_positions_count": len(open_positions),
        },
        "strategies": {
            "active_count": len(active_strategies),
            "active_names": [s.name for s in active_strategies],
        },
    }
