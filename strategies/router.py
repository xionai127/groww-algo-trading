"""FastAPI endpoints for Strategy management, versioning, and rule builder."""

from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException, Path

from config import logger
from strategies.manager import strategy_manager
from strategies.models import StrategyConfig
from strategy_lab.builder import StrategyBuilder

router = APIRouter(prefix="/api/strategies", tags=["Strategies"])


@router.get("")
def list_strategies() -> List[Dict[str, Any]]:
    """Lists all configured strategies with live runtime status."""
    configs = StrategyBuilder.list_strategies()
    results = []
    for cfg in configs:
        instance = strategy_manager.get_strategy(cfg.id)
        results.append({
            "config": cfg.model_dump(mode="json"),
            "runtime": {
                "is_registered": instance is not None,
                "is_active": instance.is_active if instance else cfg.is_active,
                "daily_trades": instance.daily_trade_count if instance else 0,
                "has_open_position": instance.has_open_position if instance else False,
            },
        })
    return results


@router.get("/meta/indicators")
def get_supported_indicators() -> Dict[str, Any]:
    """Returns catalog of supported technical indicators and configurable parameters for UI."""
    return {
        "indicators": [
            {
                "type": "EMA",
                "name": "Exponential Moving Average",
                "default_period": 9,
                "fields": [{"name": "period", "type": "int", "default": 9}],
            },
            {
                "type": "SMA",
                "name": "Simple Moving Average",
                "default_period": 20,
                "fields": [{"name": "period", "type": "int", "default": 20}],
            },
            {
                "type": "RSI",
                "name": "Relative Strength Index",
                "default_period": 14,
                "fields": [{"name": "period", "type": "int", "default": 14}],
            },
            {
                "type": "SUPERTREND",
                "name": "SuperTrend",
                "default_period": 10,
                "default_multiplier": 3.0,
                "fields": [
                    {"name": "period", "type": "int", "default": 10},
                    {"name": "multiplier", "type": "float", "default": 3.0},
                ],
            },
            {
                "type": "VWAP",
                "name": "Volume Weighted Average Price",
                "fields": [],
            },
            {
                "type": "MACD",
                "name": "Moving Average Convergence Divergence",
                "fields": [
                    {"name": "fast", "type": "int", "default": 12},
                    {"name": "slow", "type": "int", "default": 26},
                    {"name": "signal", "type": "int", "default": 9},
                ],
            },
            {
                "type": "ATR",
                "name": "Average True Range",
                "fields": [{"name": "period", "type": "int", "default": 14}],
            },
        ],
        "operators": [">", "<", ">=", "<=", "CROSSES_ABOVE", "CROSSES_BELOW"],
        "strike_preferences": ["ATM", "ITM_1", "ITM_2", "OTM_1", "OTM_2"],
    }


@router.get("/{strategy_id}")
def get_strategy(strategy_id: str = Path(..., description="Strategy identifier")) -> Dict[str, Any]:
    """Retrieves a single strategy configuration."""
    cfg = StrategyBuilder.get_strategy(strategy_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
    return cfg.model_dump(mode="json")


@router.post("")
def save_strategy(config: StrategyConfig) -> Dict[str, Any]:
    """Creates or updates a strategy and syncs live strategy manager."""
    try:
        saved_cfg = StrategyBuilder.save_strategy(config, change_summary=f"Saved via API (v{config.version})")
        # Update or register in live manager
        instance = StrategyBuilder.instantiate_strategy(saved_cfg)
        strategy_manager.add_strategy(instance)
        return saved_cfg.model_dump(mode="json")
    except Exception as e:
        logger.error(f"Error saving strategy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{strategy_id}/activate")
def activate_strategy(strategy_id: str) -> Dict[str, Any]:
    """Activates strategy for live candle evaluation."""
    success = strategy_manager.activate_strategy(strategy_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
    return {"status": "success", "strategy_id": strategy_id, "is_active": True}


@router.post("/{strategy_id}/deactivate")
def deactivate_strategy(strategy_id: str) -> Dict[str, Any]:
    """Deactivates strategy signal generation."""
    success = strategy_manager.deactivate_strategy(strategy_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
    return {"status": "success", "strategy_id": strategy_id, "is_active": False}


@router.get("/{strategy_id}/versions")
def get_strategy_versions(strategy_id: str) -> List[Dict[str, Any]]:
    """Returns version history snapshot list for auditing and rollbacks."""
    return StrategyBuilder.get_versions(strategy_id)
