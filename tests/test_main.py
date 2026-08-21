import os
from types import SimpleNamespace

import pytest

import orchestrator.main as main_module
from orchestrator.risk_engine import AccountState, RiskLimits
from orchestrator.state import init_db, mark_order_placed, mark_run_started, record_order


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
