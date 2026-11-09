"""Official Groww API broker client wrapper with authentication, margin, and order management."""

import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
import pyotp

from config import logger, settings
from database.db import get_db_connection


class GrowwBrokerClient:
    """Production client wrapping GrowwAPI for live F&O operations."""

    def __init__(self, api_instance: Optional[Any] = None):
        self._api = api_instance
        self._token: Optional[str] = None
        self._user_profile: Optional[Dict[str, Any]] = None
        self._load_credentials_from_db()

    def _load_credentials_from_db(self) -> None:
        """Loads stored broker credentials from database if available."""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM app_state WHERE key = 'groww_credentials';")
                row = cursor.fetchone()
                if row:
                    creds = json.loads(row["value"])
                    if creds.get("access_token"):
                        self._token = creds["access_token"]
                        settings.groww_access_token = self._token
                    if creds.get("api_key"):
                        settings.groww_api_key = creds["api_key"]
                    if creds.get("api_secret"):
                        settings.groww_api_secret = creds["api_secret"]
                    if creds.get("totp_key"):
                        settings.groww_totp_key = creds["totp_key"]
        except Exception as e:
            logger.warning(f"GrowwBrokerClient: Could not read credentials from database: {e}")

    @property
    def is_connected(self) -> bool:
        """Indicates whether an active authenticated API instance is initialized."""
        return self._api is not None

    @property
    def token(self) -> Optional[str]:
        return self._token or settings.groww_access_token

    def set_api_instance(self, api: Any) -> None:
        """Injects custom or mock GrowwAPI instance directly (used for testing)."""
        self._api = api

    async def authenticate(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        totp_key: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> bool:
        """Establishes authenticated session with Groww, generating access token if needed."""
        self._load_credentials_from_db()

        key = api_key or settings.groww_api_key
        secret = api_secret or settings.groww_api_secret
        totp_seed = totp_key or settings.groww_totp_key
        token = access_token or self._token or settings.groww_access_token

        # 1. If explicit access_token provided, instantiate GrowwAPI directly
        if token:
            try:
                from growwapi import GrowwAPI

                def _init():
                    return GrowwAPI(token=token)

                self._api = await asyncio.to_thread(_init)
                self._token = token
                logger.info("GrowwBrokerClient initialized using access token.")
                return True
            except Exception as e:
                logger.error(f"Failed to initialize GrowwAPI with access token: {e}")
                return False

        # 2. If api_key and (totp_seed or secret) provided, request new session token
        if key and (totp_seed or secret):
            try:
                from growwapi import GrowwAPI

                totp_code = None
                if totp_seed:
                    totp_code = pyotp.TOTP(totp_seed.strip()).now()
                    logger.info("Generated TOTP code from configured secret seed.")

                def _fetch_token():
                    return GrowwAPI.get_access_token(
                        api_key=key.strip(),
                        totp=totp_code,
                        secret=secret.strip() if secret else None,
                    )

                new_token = await asyncio.to_thread(_fetch_token)
                if isinstance(new_token, dict) and "token" in new_token:
                    new_token = new_token["token"]

                self._token = str(new_token)
                settings.groww_access_token = self._token

                # Save new token to DB
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT value FROM app_state WHERE key = 'groww_credentials';")
                    row = cursor.fetchone()
                    current = json.loads(row["value"]) if row else {}
                    current["access_token"] = self._token
                    now_iso = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        """
                        INSERT INTO app_state (key, value, updated_at)
                        VALUES ('groww_credentials', ?, ?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at;
                        """,
                        (json.dumps(current), now_iso),
                    )

                self._api = GrowwAPI(token=self._token)
                logger.info("GrowwBrokerClient successfully authenticated and obtained session token.")
                return True
            except Exception as e:
                logger.error(f"Failed to authenticate with Groww API: {e}", exc_info=True)
                return False

        logger.warning("GrowwBrokerClient: Insufficient credentials to authenticate.")
        return False

    async def test_connection(self) -> Dict[str, Any]:
        """Tests live connection by querying user profile or margin details."""
        if not self.is_connected:
            ok = await self.authenticate()
            if not ok or not self.is_connected:
                return {
                    "is_connected": False,
                    "error": "Not authenticated. Please configure valid Groww API credentials.",
                }

        try:
            def _test():
                # Attempt user profile first, fallback to margins
                try:
                    profile = self._api.get_user_profile(timeout=10)
                    return {"profile": profile}
                except Exception:
                    margin = self._api.get_available_margin_details(timeout=10)
                    return {"margin": margin}

            res = await asyncio.to_thread(_test)
            profile_data = res.get("profile", {})
            self._user_profile = profile_data

            return {
                "is_connected": True,
                "profile": profile_data,
                "message": "Connection to Groww API verified successfully.",
            }
        except Exception as e:
            logger.error(f"Groww connection test failed: {e}", exc_info=True)
            return {
                "is_connected": False,
                "error": str(e),
            }

    async def get_available_margin(self) -> float:
        """Fetches total available trading margin in INR."""
        if not self.is_connected:
            ok = await self.authenticate()
            if not ok or not self.is_connected:
                raise RuntimeError("Broker not connected.")

        def _fetch():
            return self._api.get_available_margin_details(timeout=10)

        data = await asyncio.to_thread(_fetch)
        # Parse standard Groww margin structure
        # Typically {'available_margin': float} or {'equity': {'available_cash': float}}
        if isinstance(data, dict):
            if "available_margin" in data:
                return float(data["available_margin"])
            if "net_available_margin" in data:
                return float(data["net_available_margin"])
            if "available_cash" in data:
                return float(data["available_cash"])
            # Nested check
            for key in ["equity", "fno", "derivatives"]:
                sub = data.get(key)
                if isinstance(sub, dict):
                    for subk in ["available_margin", "available_cash", "net_margin"]:
                        if subk in sub:
                            return float(sub[subk])
            # Check for any numerical top-level value
            for v in data.values():
                if isinstance(v, (int, float)):
                    return float(v)

        return 0.0

    async def place_option_buy_order(
        self,
        trading_symbol: str,
        quantity: int,
        order_type: str = "MARKET",
        price: float = 0.0,
        trigger_price: Optional[float] = None,
        product: str = "MIS",
        exchange: str = "NSE",
        validity: str = "DAY",
    ) -> Dict[str, Any]:
        """Places a strict options BUY order on Groww F&O. Rejects any non-BUY intent."""
        if not self.is_connected:
            ok = await self.authenticate()
            if not ok or not self.is_connected:
                raise RuntimeError("Broker client is not authenticated with Groww API.")

        logger.info(
            f"Placing LIVE F&O BUY order: {quantity}x {trading_symbol} "
            f"({order_type}, {product}, price={price})"
        )

        def _place():
            return self._api.place_order(
                validity=validity,
                exchange=exchange,
                order_type=order_type,
                product=product,
                quantity=quantity,
                segment="FNO",
                trading_symbol=trading_symbol,
                transaction_type="BUY",
                price=price if order_type == "LIMIT" else 0.0,
                trigger_price=trigger_price,
                timeout=15,
            )

        return await asyncio.to_thread(_place)

    async def place_option_sell_exit_order(
        self,
        trading_symbol: str,
        quantity: int,
        order_type: str = "MARKET",
        price: float = 0.0,
        product: str = "MIS",
        exchange: str = "NSE",
        validity: str = "DAY",
    ) -> Dict[str, Any]:
        """Places a SELL order strictly used to close/exit an existing long bought option position."""
        if not self.is_connected:
            ok = await self.authenticate()
            if not ok or not self.is_connected:
                raise RuntimeError("Broker client is not authenticated with Groww API.")

        logger.info(
            f"Placing LIVE F&O SELL EXIT order: {quantity}x {trading_symbol} "
            f"({order_type}, {product}, price={price})"
        )

        def _place():
            return self._api.place_order(
                validity=validity,
                exchange=exchange,
                order_type=order_type,
                product=product,
                quantity=quantity,
                segment="FNO",
                trading_symbol=trading_symbol,
                transaction_type="SELL",
                price=price if order_type == "LIMIT" else 0.0,
                timeout=15,
            )

        return await asyncio.to_thread(_place)

    async def cancel_order(self, groww_order_id: str, segment: str = "FNO") -> Dict[str, Any]:
        """Cancels an open order."""
        if not self.is_connected:
            raise RuntimeError("Broker not connected.")

        def _cancel():
            return self._api.cancel_order(groww_order_id=groww_order_id, segment=segment, timeout=10)

        return await asyncio.to_thread(_cancel)

    async def get_order_detail(self, groww_order_id: str, segment: str = "FNO") -> Dict[str, Any]:
        """Fetches details and status of an order."""
        if not self.is_connected:
            raise RuntimeError("Broker not connected.")

        def _detail():
            return self._api.get_order_detail(segment=segment, groww_order_id=groww_order_id, timeout=10)

        return await asyncio.to_thread(_detail)

    async def get_live_positions(self, segment: str = "FNO") -> List[Dict[str, Any]]:
        """Queries broker for active positions."""
        if not self.is_connected:
            ok = await self.authenticate()
            if not ok or not self.is_connected:
                return []

        def _positions():
            return self._api.get_positions_for_user(segment=segment, timeout=10)

        data = await asyncio.to_thread(_positions)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("positions", data.get("net_positions", []))
        return []

    async def get_live_orders(self, segment: str = "FNO") -> List[Dict[str, Any]]:
        """Queries broker for recent orders."""
        if not self.is_connected:
            ok = await self.authenticate()
            if not ok or not self.is_connected:
                return []

        def _orders():
            return self._api.get_order_list(segment=segment, timeout=10)

        data = await asyncio.to_thread(_orders)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("orders", [])
        return []


# Global singleton instance
broker_client = GrowwBrokerClient()
