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
              buy_cash_ratio: 0.95
              max_position_pct: 40
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
    assert config.risk_limits.buy_cash_ratio == 0.95
    assert config.risk_limits.max_position_pct == 40
    assert config.risk_limits.daily_loss_circuit_pct == -3.0
    assert config.run_time_kst == "22:40"
    assert config.toss_client_id == "test-id"
    assert config.toss_client_secret == "test-secret"
    assert config.telegram_bot_token == "test-token"
    assert config.telegram_chat_id == "test-chat"
    assert config.healthcheck_ping_url == ""
    assert config.approval_enabled is False
    assert config.approval_timeout_minutes == 20


def test_load_config_reads_optional_healthcheck_url_when_set(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            watchlist:
              - AAPL
            risk_limits:
              buy_cash_ratio: 0.95
              max_position_pct: 40
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


def test_load_config_reads_approval_section_when_present(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            watchlist:
              - AAPL
            risk_limits:
              buy_cash_ratio: 0.95
              max_position_pct: 40
              daily_loss_circuit_pct: -3.0
              max_concurrent_positions: 5
            schedule:
              run_time_kst: "22:40"
            approval:
              enabled: true
              timeout_minutes: 15
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

    assert config.approval_enabled is True
    assert config.approval_timeout_minutes == 15


def test_regime_switch_defaults_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_ID", "x")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "x")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "watchlist: [AAPL]\n"
        "risk_limits: {buy_cash_ratio: 0.95, max_position_pct: 40, "
        "daily_loss_circuit_pct: -3.0, max_concurrent_positions: 5}\n"
        "schedule: {run_time_kst: '22:40'}\n",
        encoding="utf-8",
    )
    from orchestrator.config import load_config
    config = load_config(config_path=str(cfg_file), env_path=str(tmp_path / ".env"))
    assert config.regime_switch.enabled is False
    assert config.regime_switch.proxy_ticker == "QQQ"
    assert config.regime_switch.inverse_ticker == "PSQ"
    assert config.regime_switch.confirm_days == 2
    assert config.regime_switch.max_deploy_pct == 90.0


def test_regime_switch_parsed_from_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_ID", "x")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "x")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "watchlist: [AAPL]\n"
        "risk_limits: {buy_cash_ratio: 0.95, max_position_pct: 40, "
        "daily_loss_circuit_pct: -3.0, max_concurrent_positions: 5}\n"
        "schedule: {run_time_kst: '22:40'}\n"
        "regime_switch: {enabled: true, vix_threshold: 30.0, confirm_days: 3}\n",
        encoding="utf-8",
    )
    from orchestrator.config import load_config
    config = load_config(config_path=str(cfg_file), env_path=str(tmp_path / ".env"))
    assert config.regime_switch.enabled is True
    assert config.regime_switch.vix_threshold == 30.0
    assert config.regime_switch.confirm_days == 3
    assert config.regime_switch.inverse_ticker == "PSQ"  # 미지정 필드는 기본값


def test_extra_watchlist_applies_only_through_until_date(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            watchlist: [AAPL]
            extra_watchlist:
              until: "2026-12-02"
              tickers: [META, V]
            risk_limits:
              buy_cash_ratio: 0.95
              max_position_pct: 40
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
        "TOSS_CLIENT_ID=a\nTOSS_CLIENT_SECRET=b\nTELEGRAM_BOT_TOKEN=c\nTELEGRAM_CHAT_ID=d\n",
        encoding="utf-8",
    )

    def load(today):
        return load_config(str(config_path), str(env_path), today=today).watchlist

    assert load("2026-12-02") == ["AAPL", "META", "V"]
    assert load("2026-12-03") == ["AAPL"]
