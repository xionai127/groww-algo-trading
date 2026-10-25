"""Application entry point and FastAPI server for Groww Algo Trading Platform."""

import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from config import ExecutionMode, logger, settings
from database import get_schema_version, init_db
from market_data import market_engine, market_router
from strategies import strategy_manager, strategy_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle management."""
    logger.info(f"Starting {settings.app_name} (v{settings.app_version}) in {settings.execution_mode.value} mode")
    try:
        init_db()
        schema_v = get_schema_version()
        logger.info(f"Database ready. Current schema version: {schema_v}")
        await market_engine.start()
        strategy_manager.initialize_default_strategies()
    except Exception as e:
        logger.critical(f"Failed to initialize database or market engine: {e}", exc_info=True)
        sys.exit(1)

    yield

    logger.info("Application shutting down safely.")
    await market_engine.stop()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Algorithmic Trading Platform for Groww API",
    lifespan=lifespan,
)

# Enable CORS for local browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(market_router)
app.include_router(strategy_router)


@app.get("/health")
def health_check() -> Dict[str, Any]:
    """System health check endpoint."""
    schema_v = get_schema_version()
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": settings.app_version,
        "execution_mode": settings.execution_mode.value,
        "database_connected": schema_v is not None,
        "schema_version": schema_v,
    }


@app.get("/api/status")
def get_system_status() -> Dict[str, Any]:
    """Returns active mode, capital limits, and trading boundaries."""
    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "execution_mode": settings.execution_mode.value,
        "capital": {
            "starting_capital": settings.starting_capital,
            "max_daily_loss": settings.max_daily_loss,
            "risk_per_trade": settings.risk_per_trade,
            "max_trades_per_day": settings.max_trades_per_day,
            "max_open_positions": settings.max_open_positions,
        },
        "rules": {
            "allow_option_selling": settings.allow_option_selling,
            "allowed_instruments": settings.allowed_instruments,
            "allowed_directions": settings.allowed_directions,
            "stop_loss_percent": settings.stop_loss_percent,
            "target_percent": settings.target_percent,
        },
        "session": {
            "market_open": settings.market_open_time,
            "market_close": settings.market_close_time,
            "square_off": settings.square_off_time,
        },
    }


def main() -> None:
    """Entry point for manual or CLI execution."""
    logger.info("Launching server on http://127.0.0.1:8000")
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
