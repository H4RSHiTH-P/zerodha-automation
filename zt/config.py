"""Configuration: one frozen dataclass built from the environment (.env keeps the existing key names)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


class ConfigError(Exception):
    """Missing or malformed configuration. The CLI maps it to exit code 78."""


@dataclass(frozen=True)
class Charges:
    max_brokerage: float
    brokerage: float
    stt: float
    nse: float
    bse: float
    sebi: float
    gst: float
    stamp_duty: float


@dataclass(frozen=True)
class Performance:
    equity_balance: float
    historic_duration: int
    stability_sharpe_ratio: float
    stability_profit_factor: float
    min_positions: int
    min_profit_factor: float
    min_sharpe_ratio: float
    max_drawdown_percentage: float
    min_recovery_factor: float
    min_expectancy_rate: float
    min_score: float


@dataclass(frozen=True)
class Config:
    api_key: str
    api_secret: str
    data_dir: Path
    logs_dir: Path
    db_path: Path
    session_file: Path
    charges: Charges
    performance: Performance

    @property
    def secrets(self) -> tuple[str, ...]:
        return tuple(s for s in (self.api_secret,) if s)


def _get(env: dict, key: str, cast, default=None):
    raw = env.get(key)
    if raw is None or raw == "":
        if default is None:
            raise ConfigError(f"{key} is not set")
        return default
    try:
        return cast(raw)
    except ValueError as e:
        raise ConfigError(f"{key}={raw!r} is not a valid {cast.__name__}") from e


def load(env_file: str | os.PathLike = ".env", environ: dict | None = None) -> Config:
    """Process environment wins over the .env file. `environ` overrides both (tests)."""
    env: dict = {}
    if env_file and Path(env_file).exists():
        env.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    env.update(os.environ)
    if environ is not None:
        env.update(environ)

    data_dir = Path(_get(env, "ZT_DATA_DIR", str, "data"))
    return Config(
        api_key=_get(env, "KITE_API_KEY", str),
        api_secret=_get(env, "KITE_API_SECRET_TOKEN", str),
        data_dir=data_dir,
        logs_dir=Path(_get(env, "ZT_LOGS_DIR", str, "logs")),
        db_path=Path(_get(env, "ZT_DB", str, str(data_dir / "zt.db"))),
        session_file=Path(_get(env, "ZT_SESSION_FILE", str, str(data_dir / "session.json"))),
        charges=Charges(
            max_brokerage=_get(env, "ZERODHA_MAX_BROKERAGE", float),
            brokerage=_get(env, "ZERODHA_BROKERAGE", float),
            stt=_get(env, "ZERODHA_STT", float),
            nse=_get(env, "ZERODHA_NSE", float),
            bse=_get(env, "ZERODHA_BSE", float),
            sebi=_get(env, "ZERODHA_SEBI", float),
            gst=_get(env, "ZERODHA_GST", float),
            stamp_duty=_get(env, "ZERODHA_STAMP_DUTY", float),
        ),
        performance=Performance(
            equity_balance=_get(env, "PERFORMANCE_EQUITY_BALANCE", float),
            historic_duration=_get(env, "PERFORMANCE_HISTORIC_DURATION", int),
            stability_sharpe_ratio=_get(env, "PERFORMANCE_STABILITY_SHARPE_RATIO", float),
            stability_profit_factor=_get(env, "PERFORMANCE_STABILITY_PROFIT_FACTOR", float),
            min_positions=_get(env, "PERFORMANCE_MIN_POSITIONS", int),
            min_profit_factor=_get(env, "PERFORMANCE_MIN_PROFIT_FACTOR", float),
            min_sharpe_ratio=_get(env, "PERFORMANCE_MIN_SHARPE_RATIO", float),
            max_drawdown_percentage=_get(env, "PERFORMANCE_MAX_DRAWDOWN_PERCENTAGE", float),
            min_recovery_factor=_get(env, "PERFORMANCE_MIN_RECOVERY_FACTOR", float),
            min_expectancy_rate=_get(env, "PERFORMANCE_MIN_EXPECTANCY_RATE", float),
            min_score=_get(env, "PERFORMANCE_MIN_SCORE", float),
        ),
    )
