"""Centralized configuration and environment management for the Groww Algo Trading Platform."""

import logging
import os
from enum import Enum
from pathlib import Path
from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class ExecutionMode(str, Enum):
    """Execution modes for strict isolation."""
    ANALYSIS = "ANALYSIS"
    PAPER = "PAPER"
    LIVE = "LIVE"


class AppSettings(BaseSettings):
    """Application configuration with strict validation and safe defaults."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General Settings
    app_name: str = "Groww Algo Trading Platform"
    app_version: str = "0.1.0"
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Execution Mode (Strict Isolation)
    execution_mode: ExecutionMode = Field(default=ExecutionMode.ANALYSIS, alias="EXECUTION_MODE")

    # Groww API Credentials (Optional via env; can be supplied at runtime via Dashboard)
    groww_api_key: Optional[str] = Field(default=None, alias="GROWW_API_KEY")
    groww_api_secret: Optional[str] = Field(default=None, alias="GROWW_API_SECRET")
    groww_access_token: Optional[str] = Field(default=None, alias="GROWW_ACCESS_TOKEN")
    groww_totp_key: Optional[str] = Field(default=None, alias="GROWW_TOTP_KEY")

    # Capital & Risk Parameters (Initial values as per spec)
    starting_capital: float = Field(default=10000.0, ge=1000.0, alias="STARTING_CAPITAL")
    max_daily_loss: float = Field(default=500.0, gt=0.0, alias="MAX_DAILY_LOSS")
    risk_per_trade: float = Field(default=100.0, gt=0.0, alias="RISK_PER_TRADE")
    max_trades_per_day: int = Field(default=5, ge=1, le=50, alias="MAX_TRADES_PER_DAY")
    max_open_positions: int = Field(default=1, ge=1, le=5, alias="MAX_OPEN_POSITIONS")
    stop_loss_percent: float = Field(default=10.0, gt=0.0, le=100.0, alias="STOP_LOSS_PERCENT")
    target_percent: float = Field(default=20.0, gt=0.0, alias="TARGET_PERCENT")

    # Trading Rules
    allow_option_selling: bool = False  # Strictly disabled per spec
    allowed_instruments: List[str] = ["NIFTY", "SENSEX"]
    allowed_directions: List[str] = ["CE", "PE"]

    # Market Timing (24-hour HH:MM format)
    market_open_time: str = Field(default="09:15", alias="MARKET_OPEN_TIME")
    market_close_time: str = Field(default="15:30", alias="MARKET_CLOSE_TIME")
    square_off_time: str = Field(default="15:15", alias="SQUARE_OFF_TIME")

    # Database & Paths
    database_path: str = Field(
        default=str(BASE_DIR / "database" / "groww_algo.db"),
        alias="DATABASE_PATH"
    )
    logs_dir: Path = BASE_DIR / "logs"

    @field_validator("execution_mode", mode="before")
    @classmethod
    def validate_execution_mode(cls, v: str) -> ExecutionMode:
        if isinstance(v, str):
            v_upper = v.strip().upper()
            if v_upper in ExecutionMode.__members__:
                return ExecutionMode[v_upper]
        elif isinstance(v, ExecutionMode):
            return v
        raise ValueError(f"Invalid EXECUTION_MODE: {v}. Must be one of ANALYSIS, PAPER, LIVE")


# Global settings instance
settings = AppSettings()


def setup_logging(level_name: Optional[str] = None) -> logging.Logger:
    """Configures structured console and file logging."""
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = settings.logs_dir / "trading.log"

    level = getattr(logging, (level_name or settings.log_level).upper(), logging.INFO)

    logger = logging.getLogger("groww_algo")
    logger.setLevel(level)

    # Avoid duplicate handlers if setup_logging is called multiple times
    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s:%(module)s:%(lineno)d] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # File handler
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


logger = setup_logging()
