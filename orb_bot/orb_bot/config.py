"""Loads and validates the user-adjustable bot configuration.

Everything a user should be able to tweak (trading window, opening-range
window, risk amount, reward:risk ratio, tick offsets, symbol, dry-run
flag...) lives in config.yaml. This module loads that file, applies
environment variable overrides for secrets, and validates the result.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv


class ConfigError(ValueError):
    """Raised when config.yaml (or an override) is invalid."""


def _parse_hhmm(value: str, field_name: str) -> time:
    try:
        hh, mm = value.split(":")
        return time(hour=int(hh), minute=int(mm))
    except Exception as exc:  # noqa: BLE001 - re-raise as ConfigError
        raise ConfigError(
            f"{field_name} must be an 'HH:MM' 24-hour string, got {value!r}"
        ) from exc


@dataclass
class AccountConfig:
    environment: str = "demo"       # "demo" or "live"
    account_spec: Optional[str] = None
    dry_run: bool = True

    def __post_init__(self) -> None:
        if self.environment not in ("demo", "live"):
            raise ConfigError("account.environment must be 'demo' or 'live'")


@dataclass
class ContractConfig:
    symbol: str = "MES"
    contract_name: Optional[str] = None
    tick_size: Optional[float] = None
    tick_value: Optional[float] = None


@dataclass
class SessionConfig:
    timezone: str = "America/New_York"
    opening_range_start: time = field(default_factory=lambda: time(9, 30))
    opening_range_end: time = field(default_factory=lambda: time(9, 45))
    trading_window_start: time = field(default_factory=lambda: time(9, 45))
    trading_window_end: time = field(default_factory=lambda: time(11, 30))
    max_trades_per_session: int = 1
    # Any open position is force-closed at this time regardless of P&L -
    # trades are never held past this, win or lose.
    force_close_time: time = field(default_factory=lambda: time(15, 55))

    def __post_init__(self) -> None:
        if self.opening_range_start >= self.opening_range_end:
            raise ConfigError(
                "session.opening_range_start must be before opening_range_end"
            )
        if self.trading_window_start >= self.trading_window_end:
            raise ConfigError(
                "session.trading_window_start must be before trading_window_end"
            )
        if self.trading_window_start < self.opening_range_end:
            raise ConfigError(
                "session.trading_window_start should be >= opening_range_end "
                "(the opening range must finish before you start trading it)"
            )
        if self.force_close_time < self.trading_window_end:
            raise ConfigError(
                "session.force_close_time must be >= trading_window_end "
                "(can't flatten a trade before the window that opens it closes)"
            )
        if self.max_trades_per_session < 1:
            raise ConfigError("session.max_trades_per_session must be >= 1")


@dataclass
class StrategyConfig:
    entry_trigger_ticks: int = 1
    stop_ticks: int = 1
    reward_risk_ratio: float = 1.0
    entry_order_type: str = "stop_market"   # "stop_market" | "stop_limit"
    stop_limit_offset_ticks: int = 2

    def __post_init__(self) -> None:
        if self.entry_trigger_ticks < 1:
            raise ConfigError("strategy.entry_trigger_ticks must be >= 1")
        if self.stop_ticks < 1:
            raise ConfigError("strategy.stop_ticks must be >= 1")
        if self.reward_risk_ratio <= 0:
            raise ConfigError("strategy.reward_risk_ratio must be > 0")
        if self.entry_order_type not in ("stop_market", "stop_limit"):
            raise ConfigError(
                "strategy.entry_order_type must be 'stop_market' or 'stop_limit'"
            )


@dataclass
class RiskConfig:
    risk_amount_usd: float = 600.0
    max_contracts: int = 10
    min_contracts: int = 1

    def __post_init__(self) -> None:
        if self.risk_amount_usd <= 0:
            raise ConfigError("risk.risk_amount_usd must be > 0")
        if self.min_contracts < 1:
            raise ConfigError("risk.min_contracts must be >= 1")
        if self.max_contracts < self.min_contracts:
            raise ConfigError("risk.max_contracts must be >= risk.min_contracts")


@dataclass
class LoggingConfig:
    log_dir: str = "./logs"
    trade_log_csv: str = "./logs/trades.csv"
    status_json: str = "./logs/status.json"
    level: str = "INFO"


@dataclass
class TradovateCredentials:
    username: str = ""
    password: str = ""
    app_id: str = "orb-bot"
    app_version: str = "1.0"
    cid: str = ""
    sec: str = ""
    device_id: Optional[str] = None


@dataclass
class BotConfig:
    account: AccountConfig
    contract: ContractConfig
    session: SessionConfig
    strategy: StrategyConfig
    risk: RiskConfig
    logging: LoggingConfig
    credentials: TradovateCredentials


def load_config(config_path: str | Path = "config.yaml", env_path: str | Path = ".env") -> BotConfig:
    """Load config.yaml + .env into a validated BotConfig."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise ConfigError(
            f"Config file not found: {config_path}. "
            f"Copy config.example.yaml to {config_path.name} and edit it."
        )

    env_path = Path(env_path)
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()  # fall back to whatever is already in the environment

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    account_raw = raw.get("account", {}) or {}
    contract_raw = raw.get("contract", {}) or {}
    session_raw = raw.get("session", {}) or {}
    strategy_raw = raw.get("strategy", {}) or {}
    risk_raw = raw.get("risk", {}) or {}
    logging_raw = raw.get("logging", {}) or {}

    session_kwargs = dict(session_raw)
    for key in (
        "opening_range_start",
        "opening_range_end",
        "trading_window_start",
        "trading_window_end",
        "force_close_time",
    ):
        if key in session_kwargs and session_kwargs[key] is not None:
            session_kwargs[key] = _parse_hhmm(str(session_kwargs[key]), f"session.{key}")

    credentials = TradovateCredentials(
        username=os.environ.get("TRADOVATE_USERNAME", ""),
        password=os.environ.get("TRADOVATE_PASSWORD", ""),
        app_id=os.environ.get("TRADOVATE_APP_ID", "orb-bot"),
        app_version=os.environ.get("TRADOVATE_APP_VERSION", "1.0"),
        cid=os.environ.get("TRADOVATE_CID", ""),
        sec=os.environ.get("TRADOVATE_SEC", ""),
        device_id=os.environ.get("TRADOVATE_DEVICE_ID") or None,
    )

    return BotConfig(
        account=AccountConfig(**account_raw),
        contract=ContractConfig(**contract_raw),
        session=SessionConfig(**session_kwargs),
        strategy=StrategyConfig(**strategy_raw),
        risk=RiskConfig(**risk_raw),
        logging=LoggingConfig(**logging_raw),
        credentials=credentials,
    )
