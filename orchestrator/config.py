import os
from datetime import date
from dataclasses import dataclass, fields

import yaml
from dotenv import load_dotenv

from orchestrator.risk_engine import RiskLimits


@dataclass
class RegimeSwitchConfig:
    enabled: bool = False
    proxy_ticker: str = "QQQ"
    inverse_ticker: str = "PSQ"
    sma_long: int = 200
    sma_short: int = 50
    vix_threshold: float = 25.0
    confirm_days: int = 2
    max_deploy_pct: float = 90.0


@dataclass
class AppConfig:
    watchlist: list
    risk_limits: RiskLimits
    run_time_kst: str
    toss_client_id: str
    toss_client_secret: str
    telegram_bot_token: str
    telegram_chat_id: str
    healthcheck_ping_url: str
    approval_enabled: bool
    approval_timeout_minutes: int
    regime_switch: RegimeSwitchConfig


def _active_extra_watchlist(raw, today):
    """extra_watchlist.until(YYYY-MM-DD, 당일 포함)까지만 종목을 덧붙인다 -- 기간 한정 확장이
    날짜가 지나면 손으로 되돌리지 않아도 자동으로 빠지게 한다."""
    extra = raw.get("extra_watchlist") or {}
    if extra.get("until") and today <= str(extra["until"]):
        return list(extra.get("tickers", []))
    return []


def load_config(config_path="config.yaml", env_path=".env", today=None):
    load_dotenv(env_path)
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    risk_limits = RiskLimits(
        buy_cash_ratio=raw["risk_limits"]["buy_cash_ratio"],
        max_position_pct=raw["risk_limits"]["max_position_pct"],
        daily_loss_circuit_pct=raw["risk_limits"]["daily_loss_circuit_pct"],
        max_concurrent_positions=raw["risk_limits"]["max_concurrent_positions"],
    )

    approval = raw.get("approval", {})

    regime_switch_raw = raw.get("regime_switch", {})
    known = {f.name for f in fields(RegimeSwitchConfig)}
    regime_switch = RegimeSwitchConfig(
        **{k: v for k, v in regime_switch_raw.items() if k in known}
    )

    return AppConfig(
        watchlist=raw["watchlist"] + _active_extra_watchlist(raw, today or date.today().isoformat()),
        risk_limits=risk_limits,
        run_time_kst=raw["schedule"]["run_time_kst"],
        toss_client_id=os.environ["TOSS_CLIENT_ID"],
        toss_client_secret=os.environ["TOSS_CLIENT_SECRET"],
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
        healthcheck_ping_url=os.environ.get("HEALTHCHECK_PING_URL", ""),
        approval_enabled=approval.get("enabled", False),
        approval_timeout_minutes=approval.get("timeout_minutes", 20),
        regime_switch=regime_switch,
    )
