import os
import sqlite3
from types import SimpleNamespace

import pytest

import orchestrator.main as main_module
from orchestrator.risk_engine import AccountState, RiskLimits
from orchestrator.state import init_db, mark_order_placed, mark_run_started, record_order
from orchestrator.toss_client import TossApiError
from orchestrator.trading_agents_client import QuotaExhaustedError


def make_config():
    return SimpleNamespace(
        watchlist=["AAPL", "MSFT"],
        risk_limits=RiskLimits(
            per_ticker_max_usd=100.0,
            per_ticker_max_position_usd=100000.0,
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
        approval_enabled=False,
        approval_timeout_minutes=20,
    )


class FakeTossClient:
    def __init__(self, *args, **kwargs):
        self.orders = []

    def is_us_market_open_today(self):
        return True

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
    monkeypatch.setattr(main_module, "run_analysis", lambda ticker, run_date: ("BUY", ""))
    monkeypatch.setattr(main_module, "send_telegram_message", lambda config, text: None)
    monkeypatch.setattr(main_module, "ping_healthcheck", lambda config, suffix="": None)
    monkeypatch.setattr(
        main_module,
        "wait_for_telegram_reply",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("승인 모드가 꺼져 있으면 호출되면 안 됨")
        ),
    )
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


def test_telegram_summary_includes_ai_reasoning(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "run_analysis",
        lambda ticker, run_date: ("BUY", f"{ticker} 실적 모멘텀 강함"),
    )

    sent = []
    monkeypatch.setattr(
        main_module, "send_telegram_message", lambda config, text: sent.append(text)
    )

    main_module.run(dry_run=False)

    summary = "\n".join(sent)
    assert "AAPL 실적 모멘텀 강함" in summary
    assert "MSFT 실적 모멘텀 강함" in summary


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


def test_us_market_holiday_skips_analysis_and_marks_completed(monkeypatch):
    class ClosedMarketTossClient(FakeTossClient):
        def is_us_market_open_today(self):
            return False

    monkeypatch.setattr(main_module, "TossClient", ClosedMarketTossClient)
    monkeypatch.setattr(
        main_module,
        "run_analysis",
        lambda ticker, run_date: (_ for _ in ()).throw(AssertionError("should not analyze")),
    )

    sent = []
    monkeypatch.setattr(
        main_module, "send_telegram_message", lambda config, text: sent.append(text)
    )

    main_module.run(dry_run=False)

    assert any("휴장일" in text for text in sent)

    conn = init_db(main_module.DB_PATH)
    run_date = main_module.date.today().isoformat()
    row = conn.execute(
        "SELECT status FROM runs WHERE run_date = ?", (run_date,)
    ).fetchone()
    assert row[0] == "completed"


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


def test_quota_exhausted_error_produces_distinct_message_and_continues(monkeypatch):
    def fake_run_analysis(ticker, run_date):
        if ticker == "AAPL":
            raise QuotaExhaustedError(f"{ticker}: Gemini 일일 요청 한도 초과")
        return "BUY", ""

    monkeypatch.setattr(main_module, "run_analysis", fake_run_analysis)

    sent = []
    monkeypatch.setattr(
        main_module, "send_telegram_message", lambda config, text: sent.append(text)
    )

    captured_orders = {}

    class TrackingTossClient(FakeTossClient):
        def place_market_buy(self, ticker, amount_usd, client_order_id):
            captured_orders.setdefault("buys", []).append(ticker)
            return super().place_market_buy(ticker, amount_usd, client_order_id)

    monkeypatch.setattr(main_module, "TossClient", TrackingTossClient)

    main_module.run(dry_run=False)

    assert any(
        "Gemini 일일 요청 한도 초과" in text and "AAPL" in text for text in sent
    )
    # 한도 초과 종목만 스킵하고 나머지는 계속 진행 (전체 중단 아님)
    assert captured_orders["buys"] == ["MSFT"]


def make_approval_config(**overrides):
    config = make_config()
    config.approval_enabled = True
    config.approval_timeout_minutes = 20
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def test_approval_mode_places_orders_when_approved(monkeypatch):
    monkeypatch.setattr(main_module, "load_config", lambda: make_approval_config())
    monkeypatch.setattr(main_module, "wait_for_telegram_reply", lambda config, timeout_seconds: "approved")

    captured_orders = {}

    class TrackingTossClient(FakeTossClient):
        def place_market_buy(self, ticker, amount_usd, client_order_id):
            captured_orders.setdefault("buys", []).append(ticker)
            return super().place_market_buy(ticker, amount_usd, client_order_id)

    monkeypatch.setattr(main_module, "TossClient", TrackingTossClient)

    main_module.run(dry_run=False)

    assert captured_orders["buys"] == ["AAPL", "MSFT"]


def test_approval_mode_skips_orders_when_rejected(monkeypatch):
    monkeypatch.setattr(main_module, "load_config", lambda: make_approval_config())
    monkeypatch.setattr(main_module, "wait_for_telegram_reply", lambda config, timeout_seconds: "rejected")

    orders_placed = []

    class TrackingTossClient(FakeTossClient):
        def place_market_buy(self, ticker, amount_usd, client_order_id):
            orders_placed.append(ticker)
            return super().place_market_buy(ticker, amount_usd, client_order_id)

    monkeypatch.setattr(main_module, "TossClient", TrackingTossClient)

    main_module.run(dry_run=False)

    assert orders_placed == []

    # 판단 근거는 여전히 기록되어야 함 (승인 대기와 무관하게)
    conn = init_db(main_module.DB_PATH)
    run_date = main_module.date.today().isoformat()
    rows = conn.execute(
        "SELECT ticker, approved, placed FROM orders WHERE run_date = ?", (run_date,)
    ).fetchall()
    assert len(rows) == 2
    assert all(approved == 1 and placed == 0 for _, approved, placed in rows)

    # 거부됐으면 당일 재시도가 가능해야 함 (완료 처리되면 안 됨)
    run_row = conn.execute(
        "SELECT status FROM runs WHERE run_date = ?", (run_date,)
    ).fetchone()
    assert run_row[0] != "completed"


def test_approval_mode_skips_orders_on_timeout(monkeypatch):
    monkeypatch.setattr(main_module, "load_config", lambda: make_approval_config())
    monkeypatch.setattr(main_module, "wait_for_telegram_reply", lambda config, timeout_seconds: "timeout")

    orders_placed = []

    class TrackingTossClient(FakeTossClient):
        def place_market_buy(self, ticker, amount_usd, client_order_id):
            orders_placed.append(ticker)
            return super().place_market_buy(ticker, amount_usd, client_order_id)

    monkeypatch.setattr(main_module, "TossClient", TrackingTossClient)

    main_module.run(dry_run=False)

    assert orders_placed == []

    conn = init_db(main_module.DB_PATH)
    run_date = main_module.date.today().isoformat()
    run_row = conn.execute(
        "SELECT status FROM runs WHERE run_date = ?", (run_date,)
    ).fetchone()
    assert run_row[0] != "completed"


def test_approval_mode_rejection_allows_same_day_retry(monkeypatch):
    # 첫 실행: 거부됨. 두번째 실행(같은 날, 재승인): 승인되면 실제로 주문이 나가야 함.
    replies = iter(["rejected", "approved"])
    monkeypatch.setattr(main_module, "load_config", lambda: make_approval_config())
    monkeypatch.setattr(
        main_module, "wait_for_telegram_reply", lambda config, timeout_seconds: next(replies)
    )

    orders_placed = []

    class TrackingTossClient(FakeTossClient):
        def place_market_buy(self, ticker, amount_usd, client_order_id):
            orders_placed.append(ticker)
            return super().place_market_buy(ticker, amount_usd, client_order_id)

    monkeypatch.setattr(main_module, "TossClient", TrackingTossClient)

    main_module.run(dry_run=False)  # 1차: 거부
    assert orders_placed == []

    main_module.run(dry_run=False)  # 2차: 같은 날 재시도, 승인
    assert orders_placed == ["AAPL", "MSFT"]


def test_approval_mode_not_invoked_when_nothing_approved(monkeypatch):
    config = make_approval_config()
    monkeypatch.setattr(main_module, "load_config", lambda: config)
    monkeypatch.setattr(main_module, "run_analysis", lambda ticker, run_date: ("HOLD", ""))
    # wait_for_telegram_reply left unpatched-by-default (raises via autouse fixture if called)

    main_module.run(dry_run=False)  # 예외 없이 완료되어야 함 (승인 대기 안 탐)


def test_approval_mode_not_invoked_during_dry_run(monkeypatch):
    monkeypatch.setattr(main_module, "load_config", lambda: make_approval_config())
    # wait_for_telegram_reply left unpatched-by-default (raises via autouse fixture if called)

    main_module.run(dry_run=True)  # 예외 없이 완료되어야 함 (드라이런은 승인 대기 안 탐)


def test_toss_api_error_on_placement_does_not_abort_remaining_orders(monkeypatch):
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

    main_module.run(dry_run=False)  # 예외 없이 완료되어야 함

    # watchlist == ["AAPL", "MSFT"]; a failure on the first ticker must not
    # stop the second ticker's order from being attempted
    assert attempted == ["AAPL", "MSFT"]
    assert any("주문 실패" in text and "토스 API 오류" in text for text in sent)

    conn = init_db(main_module.DB_PATH)
    run_date = main_module.date.today().isoformat()
    # run must still be marked completed so it isn't blindly retried same-day
    row = conn.execute(
        "SELECT status FROM runs WHERE run_date = ?", (run_date,)
    ).fetchone()
    assert row[0] == "completed"


def test_mark_order_placed_retries_and_succeeds_without_failure_message(monkeypatch):
    call_count = {"n": 0}

    def flaky_mark_order_placed(conn, run_date, ticker, action, order_id, quantity=None):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(main_module, "mark_order_placed", flaky_mark_order_placed)

    sent = []
    monkeypatch.setattr(
        main_module, "send_telegram_message", lambda config, text: sent.append(text)
    )

    main_module.run(dry_run=False)

    summary = "\n".join(sent)
    assert "로컬 기록 실패" not in summary
    # AAPL: 1번째 실패 + 재시도 성공 (2회), MSFT: 첫 시도 성공 (1회) = 총 3회
    assert call_count["n"] == 3


def test_mark_order_placed_failure_is_reported_distinctly(monkeypatch):
    def raising_mark_order_placed(conn, run_date, ticker, action, order_id, quantity=None):
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
        lambda ticker, run_date: ("SELL", "") if ticker == "AAPL" else ("HOLD", ""),
    )

    main_module.run(dry_run=False)

    run_date = main_module.date.today().isoformat()
    assert calls["get_holding_quantity"] == ["AAPL"]
    assert calls["place_market_sell"] == [
        ("AAPL", "3.5", f"{run_date}-AAPL-SELL")
    ]

    conn = init_db(main_module.DB_PATH)
    row = conn.execute(
        "SELECT order_id, placed, quantity FROM orders WHERE run_date = ? AND ticker = ? AND action = 'SELL'",
        (run_date, "AAPL"),
    ).fetchone()
    assert row == ("sell-1", 1, "3.5")
