"""Strategy builder, validator, and SQLite persistence manager with versioning."""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
from config import logger
from database.db import get_db_connection
from strategies.base import BaseStrategy
from strategies.builtin.ema_crossover import EMACrossoverStrategy
from strategies.builtin.supertrend_rsi import SuperTrendRSIStrategy
from strategies.models import StrategyConfig
from strategies.rule_engine import RuleBasedStrategy


class StrategyBuilder:
    """Manages strategy creation, validation, version snapshots, and instantiation."""

    @staticmethod
    def instantiate_strategy(config: StrategyConfig) -> BaseStrategy:
        """Instantiates either a built-in strategy or a dynamic RuleBasedStrategy."""
        if config.id == "builtin_ema_crossover":
            return EMACrossoverStrategy(config)
        elif config.id == "builtin_supertrend_rsi":
            return SuperTrendRSIStrategy(config)
        else:
            return RuleBasedStrategy(config)

    @staticmethod
    def save_strategy(config: StrategyConfig, change_summary: str = "Initial creation") -> StrategyConfig:
        """Saves or updates a strategy configuration and snapshots version history."""
        now_iso = datetime.now(timezone.utc).isoformat()
        config_json = config.model_dump_json(indent=2)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Check existing
            cursor.execute("SELECT version FROM strategies WHERE id = ?", (config.id,))
            row = cursor.fetchone()

            if row:
                next_version = row["version"] + 1
                config.version = next_version
                config_json = config.model_dump_json(indent=2)
                cursor.execute(
                    """
                    UPDATE strategies
                    SET name = ?, version = ?, is_active = ?, is_primary = ?, config_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (config.name, next_version, int(config.is_active), int(config.is_primary), config_json, now_iso, config.id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO strategies (id, name, version, is_active, is_primary, config_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (config.id, config.name, config.version, int(config.is_active), int(config.is_primary), config_json, now_iso, now_iso),
                )

            # Record version snapshot
            cursor.execute(
                """
                INSERT INTO strategy_versions (strategy_id, version, config_json, change_summary, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (config.id, config.version, config_json, change_summary, now_iso),
            )

        logger.info(f"Strategy {config.name} (v{config.version}) persisted successfully.")
        return config

    @staticmethod
    def get_strategy(strategy_id: str) -> Optional[StrategyConfig]:
        """Loads a single strategy by ID from SQLite."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT config_json FROM strategies WHERE id = ?", (strategy_id,))
            row = cursor.fetchone()
            if row:
                return StrategyConfig.model_validate_json(row["config_json"])
        return None

    @staticmethod
    def list_strategies() -> List[StrategyConfig]:
        """Returns all configured strategies from database."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT config_json FROM strategies ORDER BY is_primary DESC, updated_at DESC")
            rows = cursor.fetchall()
            return [StrategyConfig.model_validate_json(r["config_json"]) for r in rows]

    @staticmethod
    def get_versions(strategy_id: str) -> List[Dict[str, Any]]:
        """Returns version history for auditing and rollbacks."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT version, change_summary, created_at FROM strategy_versions WHERE strategy_id = ? ORDER BY version DESC",
                (strategy_id,),
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
