import os
from dataclasses import dataclass

import yaml
from dotenv import load_dotenv

from orchestrator.risk_engine import RiskLimits


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


def load_config(config_path="config.yaml", env_path=".env"):
    load_dotenv(env_path)
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    risk_limits = RiskLimits(
        per_ticker_max_usd=raw["risk_limits"]["per_ticker_max_usd"],
        daily_max_buy_usd=raw["risk_limits"]["daily_max_buy_usd"],
        daily_loss_circuit_pct=raw["risk_limits"]["daily_loss_circuit_pct"],
        max_concurrent_positions=raw["risk_limits"]["max_concurrent_positions"],
    )

    return AppConfig(
        watchlist=raw["watchlist"],
        risk_limits=risk_limits,
        run_time_kst=raw["schedule"]["run_time_kst"],
        toss_client_id=os.environ["TOSS_CLIENT_ID"],
        toss_client_secret=os.environ["TOSS_CLIENT_SECRET"],
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
        healthcheck_ping_url=os.environ.get("HEALTHCHECK_PING_URL", ""),
    )
