import textwrap

from orchestrator.config import load_config


def test_load_config_parses_yaml_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("HEALTHCHECK_PING_URL", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            watchlist:
              - AAPL
              - MSFT
            risk_limits:
              per_ticker_max_usd: 100
              daily_max_buy_usd: 300
              daily_loss_circuit_pct: -3.0
              max_concurrent_positions: 5
            schedule:
              run_time_kst: "22:40"
            """
        ),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TOSS_CLIENT_ID=test-id\n"
        "TOSS_CLIENT_SECRET=test-secret\n"
        "TELEGRAM_BOT_TOKEN=test-token\n"
        "TELEGRAM_CHAT_ID=test-chat\n",
        encoding="utf-8",
    )

    config = load_config(config_path=str(config_path), env_path=str(env_path))

    assert config.watchlist == ["AAPL", "MSFT"]
    assert config.risk_limits.per_ticker_max_usd == 100
    assert config.risk_limits.daily_loss_circuit_pct == -3.0
    assert config.run_time_kst == "22:40"
    assert config.toss_client_id == "test-id"
    assert config.toss_client_secret == "test-secret"
    assert config.telegram_bot_token == "test-token"
    assert config.telegram_chat_id == "test-chat"
    assert config.healthcheck_ping_url == ""


def test_load_config_reads_optional_healthcheck_url_when_set(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            watchlist:
              - AAPL
            risk_limits:
              per_ticker_max_usd: 100
              daily_max_buy_usd: 300
              daily_loss_circuit_pct: -3.0
              max_concurrent_positions: 5
            schedule:
              run_time_kst: "22:40"
            """
        ),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TOSS_CLIENT_ID=test-id\n"
        "TOSS_CLIENT_SECRET=test-secret\n"
        "TELEGRAM_BOT_TOKEN=test-token\n"
        "TELEGRAM_CHAT_ID=test-chat\n"
        "HEALTHCHECK_PING_URL=https://hc-ping.com/abc123\n",
        encoding="utf-8",
    )

    config = load_config(config_path=str(config_path), env_path=str(env_path))

    assert config.healthcheck_ping_url == "https://hc-ping.com/abc123"
