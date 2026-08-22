import os
import sqlite3
from types import SimpleNamespace

import pytest

import orchestrator.main as main_module
from orchestrator.risk_engine import AccountState, RiskLimits
from orchestrator.state import init_db, mark_order_placed, mark_run_started, record_order
from orchestrator.toss_client import TossApiError


def make_config():
    return SimpleNamespace(
        watchlist=["AAPL", "MSFT"],
        risk_limits=RiskLimits(
            per_ticker_max_usd=100.0,
            daily_max_buy_usd=300.0,
            daily_loss_circuit_pct=-3.0,
            max_concurrent_positions=5,
        ),
        run_time_kst="22:40",
        toss_client_id="id",
        toss_client_secret="secret",
        telegram_bot_token="tok",
        telegram_chat_id="chat",
        healthcheck_ping_url="https://hc-ping.com/test123",
    )


class FakeTossClient:
    def __init__(self, *args, **kwargs):
        self.orders = []

    def get_account_state(self, watchlist):
        return AccountState(positions_usd={}, daily_pnl_pct=0.0)

    def place_market_buy(self, ticker, amount_usd, client_order_id):
        self.orders.append(("BUY", ticker, amount_usd))
        return {"orderId": "1", "clientOrderId": client_order_id}

    def get_holding_quantity(self, ticker):
        return "0"

    def place_market_sell(self, ticker, quantity, client_order_id):
        self.orders.append(("SELL", ticker, quantity))
        return {"orderId": "1", "clientOrderId": client_order_id}


@pytest.fixture(autouse=True)
def patch_dependencies(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "load_config", lambda: make_config())
    monkeypatch.setattr(main_module, "TossClient", FakeTossClient)
    monkeypatch.setattr(main_module, "run_analysis", lambda ticker, run_date: "BUY")
    monkeypatch.setattr(main_module, "send_telegram_message", lambda config, text: None)
    monkeypatch.setattr(main_module, "ping_healthcheck", lambda config, suffix="": None)
    monkeypatch.setattr(main_module, "DB_PATH", str(tmp_path / "trading.db"))
    monkeypatch.setattr(main_module, "STOP_FILE", str(tmp_path / "STOP"))
    monkeypatch.chdir(tmp_path)


def test_stop_file_skips_everything(monkeypatch, tmp_path):
    (tmp_path / "STOP").write_text("stop")

    main_module.run(dry_run=False)

    conn = init_db(str(tmp_path / "trading.db"))
    row = conn.execute(
        "SELECT 1 FROM runs WHERE run_date = ?", (main_module.date.today().isoformat(),)
    ).fetchone()
    assert row is None


def test_normal_run_places_approved_orders(monkeypatch):
    captured_orders = {}

    class TrackingTossClient(FakeTossClient):
        def place_market_buy(self, ticker, amount_usd, client_order_id):
            captured_orders.setdefault("buys", []).append((ticker, amount_usd))
            return super().place_market_buy(ticker, amount_usd, client_order_id)

    monkeypatch.setattr(main_module, "TossClient", TrackingTossClient)

    main_module.run(dry_run=False)

    assert captured_orders["buys"] == [("AAPL", 100.0), ("MSFT", 100.0)]


def test_dry_run_does_not_place_orders(monkeypatch):
    orders_placed = []

    class TrackingTossClient(FakeTossClient):
        def place_market_buy(self, ticker, amount_usd, client_order_id):
            orders_placed.append(ticker)
            return super().place_market_buy(ticker, amount_usd, client_order_id)

    monkeypatch.setattr(main_module, "TossClient", TrackingTossClient)

    main_module.run(dry_run=True)

    assert orders_placed == []


def test_circuit_breaker_blocks_orders(monkeypatch):
    class LosingTossClient(FakeTossClient):
        def get_account_state(self, watchlist):
            return AccountState(positions_usd={}, daily_pnl_pct=-5.0)

    monkeypatch.setattr(main_module, "TossClient", LosingTossClient)
    orders_placed = []
    monkeypatch.setattr(
        main_module, "run_analysis", lambda ticker, run_date: (_ for _ in ()).throw(AssertionError("should not analyze"))
    )

    main_module.run(dry_run=False)  # 예외 없이 조기 종료되어야 함


def test_already_placed_order_is_skipped_on_retry(monkeypatch):
    # 크래시 후 재실행 시나리오: AAPL은 이전 실행에서 이미 주문이 체결됨
    run_date = main_module.date.today().isoformat()
    conn = init_db(main_module.DB_PATH)
    mark_run_started(conn, run_date)
    record_order(conn, run_date, "AAPL", "BUY", 100.0, True, "approved")
    mark_order_placed(conn, run_date, "AAPL", "BUY", "order-1")
    conn.close()

    captured = {"buys": []}

    class TrackingTossClient(FakeTossClient):
        def place_market_buy(self, ticker, amount_usd, client_order_id):
            captured["buys"].append(ticker)
            return super().place_market_buy(ticker, amount_usd, client_order_id)

    monkeypatch.setattr(main_module, "TossClient", TrackingTossClient)

    main_module.run(dry_run=False)

    assert captured["buys"] == ["MSFT"]


def test_healthcheck_pings_start_and_success_on_normal_run(monkeypatch):
    pings = []
    monkeypatch.setattr(
        main_module, "ping_healthcheck", lambda config, suffix="": pings.append(suffix)
    )

    main_module.run(dry_run=False)

    assert pings == ["/start", ""]


def test_healthcheck_pings_success_only_on_stop_file_without_start(monkeypatch, tmp_path):
    pings = []
    monkeypatch.setattr(
        main_module, "ping_healthcheck", lambda config, suffix="": pings.append(suffix)
    )
    (tmp_path / "STOP").write_text("stop")

    main_module.run(dry_run=False)

    assert pings == [""]


def test_healthcheck_pings_fail_on_unhandled_exception(monkeypatch):
    pings = []
    monkeypatch.setattr(
        main_module, "ping_healthcheck", lambda config, suffix="": pings.append(suffix)
    )
    monkeypatch.setattr(
        main_module,
        "run_analysis",
        lambda ticker, run_date: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    class BrokenTossClient(FakeTossClient):
        def get_account_state(self, watchlist):
            raise RuntimeError("account fetch broke")

    monkeypatch.setattr(main_module, "TossClient", BrokenTossClient)

    with pytest.raises(RuntimeError):
        main_module.run(dry_run=False)

    assert pings == ["/start", "/fail"]


def test_toss_api_error_on_placement_aborts_remaining_orders(monkeypatch):
    attempted = []

    class FailingTossClient(FakeTossClient):
        def place_market_buy(self, ticker, amount_usd, client_order_id):
            attempted.append(ticker)
            raise TossApiError(401, "unauthorized", "token expired")

    monkeypatch.setattr(main_module, "TossClient", FailingTossClient)

    sent = []
    monkeypatch.setattr(
        main_module, "send_telegram_message", lambda config, text: sent.append(text)
    )

    main_module.run(dry_run=False)  # 예외 없이 조기 종료되어야 함 (전체 중단이지 크래시가 아님)

    # watchlist == ["AAPL", "MSFT"]; only the first ticker's placement should be attempted
    assert attempted == ["AAPL"]
    assert any("토스 API 연결 실패" in text and "나머지 주문을 중단" in text for text in sent)

    conn = init_db(main_module.DB_PATH)
    run_date = main_module.date.today().isoformat()
    # run must still be marked completed so it isn't blindly retried same-day
    row = conn.execute(
        "SELECT status FROM runs WHERE run_date = ?", (run_date,)
    ).fetchone()
    assert row[0] == "completed"


def test_mark_order_placed_failure_is_reported_distinctly(monkeypatch):
    def raising_mark_order_placed(conn, run_date, ticker, action, order_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(main_module, "mark_order_placed", raising_mark_order_placed)

    sent = []
    monkeypatch.setattr(
        main_module, "send_telegram_message", lambda config, text: sent.append(text)
    )

    main_module.run(dry_run=False)

    summary = "\n".join(sent)
    assert "체결됐으나 로컬 기록 실패" in summary
    assert "order_id=1" in summary
    assert "수동 확인 필요" in summary
    # must be distinguishable from a normal placement failure line
    assert "주문 실패" not in summary.split("체결됐으나")[0].split("\n")[-1]


def test_sell_flow_places_order_with_holding_quantity(monkeypatch):
    calls = {"get_holding_quantity": [], "place_market_sell": []}

    class SellTossClient(FakeTossClient):
        def get_account_state(self, watchlist):
            return AccountState(positions_usd={"AAPL": 500.0}, daily_pnl_pct=0.0)

        def get_holding_quantity(self, ticker):
            calls["get_holding_quantity"].append(ticker)
            return "3.5"

        def place_market_sell(self, ticker, quantity, client_order_id):
            calls["place_market_sell"].append((ticker, quantity, client_order_id))
            return {"orderId": "sell-1", "clientOrderId": client_order_id}

    monkeypatch.setattr(main_module, "TossClient", SellTossClient)
    monkeypatch.setattr(
        main_module,
        "run_analysis",
        lambda ticker, run_date: "SELL" if ticker == "AAPL" else "HOLD",
    )

    main_module.run(dry_run=False)

    run_date = main_module.date.today().isoformat()
    assert calls["get_holding_quantity"] == ["AAPL"]
    assert calls["place_market_sell"] == [
        ("AAPL", "3.5", f"{run_date}-AAPL-SELL")
    ]

    conn = init_db(main_module.DB_PATH)
    row = conn.execute(
        "SELECT order_id, placed FROM orders WHERE run_date = ? AND ticker = ? AND action = 'SELL'",
        (run_date, "AAPL"),
    ).fetchone()
    assert row == ("sell-1", 1)
