"""FastAPI HTTP and WebSocket router for market data streaming and queries."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from config import logger
from market_data.engine import market_engine
from market_data.models import OptionChain, Timeframe

router = APIRouter(prefix="/api/market", tags=["Market Data"])


@router.get("/status")
def get_market_status() -> Dict[str, Any]:
    """Returns status of market data feeds and connections."""
    return market_engine.get_status()


@router.get("/ltp")
async def get_ltp(symbol: str = Query(..., description="Instrument symbol (e.g. NIFTY, SENSEX)")) -> Dict[str, Any]:
    """Fetches the latest traded price for a symbol."""
    try:
        price = await market_engine.get_ltp(symbol.upper())
        return {"symbol": symbol.upper(), "ltp": price}
    except Exception as e:
        logger.error(f"Error fetching LTP for {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/candles")
async def get_candles(
    symbol: str = Query(..., description="Symbol (e.g. NIFTY)"),
    timeframe: str = Query("5m", description="Candle timeframe: 1m, 3m, 5m, 15m, 30m, 1h, 1d"),
    limit: int = Query(100, ge=1, le=1000, description="Max candle count to return"),
) -> List[Dict[str, Any]]:
    """Fetches completed historical / live candles."""
    try:
        tf_enum = Timeframe(timeframe)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}. Supported: {[t.value for t in Timeframe]}")

    try:
        candles = await market_engine.get_candles(symbol.upper(), tf_enum, limit=limit)
        return [c.model_dump(mode="json") for c in candles]
    except Exception as e:
        logger.error(f"Error fetching candles: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/option-chain")
async def get_option_chain(
    underlying: str = Query("NIFTY", description="Underlying index: NIFTY or SENSEX"),
    expiry: Optional[str] = Query(None, description="Expiry date in YYYY-MM-DD format (optional)"),
) -> Dict[str, Any]:
    """Fetches option chain for an index with strikes and calculated Greeks."""
    try:
        chain: OptionChain = await market_engine.get_option_chain(underlying.upper(), expiry)
        res = chain.model_dump(mode="json")
        res["atm_strike"] = chain.get_atm_strike()
        return res
    except Exception as e:
        logger.error(f"Error fetching option chain for {underlying}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/ws")
async def websocket_market_feed(websocket: WebSocket) -> None:
    """WebSocket endpoint streaming live ticks and candle updates to browser clients."""
    await websocket.accept()
    market_engine.register_ws_client(websocket)
    logger.info("New WebSocket client connected to market feed.")
    try:
        while True:
            # Keep-alive ping / wait for incoming messages (e.g. subscribe / unsubscribe)
            data = await websocket.receive_text()
            # Respond to client ping
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    except Exception as e:
        logger.warning(f"WebSocket client terminated with error: {e}")
    finally:
        market_engine.unregister_ws_client(websocket)
