import sqlite3

import pytest

from orchestrator.state import (
    count_consecutive_signal,
    get_current_regime,
    has_order_been_placed,
    has_run_completed_today,
    init_db,
    is_circuit_breaker_triggered_today,
    mark_circuit_breaker_triggered,
    mark_order_placed,
    mark_run_completed,
    mark_run_started,
    record_order,
    record_regime_signal,
    set_pending_inverse_buy,
    set_regime,
)


@pytest.fixture
def conn():
    return init_db(":memory:")


def test_run_not_completed_before_marked(conn):
    assert has_run_completed_today(conn, "2026-08-21") is False


def test_run_completed_after_mark_started_then_completed(conn):
    mark_run_started(conn, "2026-08-21")
    assert has_run_completed_today(conn, "2026-08-21") is False
    mark_run_completed(conn, "2026-08-21")
    assert has_run_completed_today(conn, "2026-08-21") is True


def test_circuit_breaker_not_triggered_by_default(conn):
    mark_run_started(conn, "2026-08-21")
    assert is_circuit_breaker_triggered_today(conn, "2026-08-21") is False


def test_circuit_breaker_persists_after_trigger(conn):
    mark_run_started(conn, "2026-08-21")
    mark_circuit_breaker_triggered(conn, "2026-08-21")
    assert is_circuit_breaker_triggered_today(conn, "2026-08-21") is True


def test_circuit_breaker_state_survives_second_run_started_same_day(conn):
    mark_run_started(conn, "2026-08-21")
    mark_circuit_breaker_triggered(conn, "2026-08-21")
    mark_run_started(conn, "2026-08-21")  # 같은 날 재실행
    assert is_circuit_breaker_triggered_today(conn, "2026-08-21") is True


def test_record_order_inserts_row(conn):
    mark_run_started(conn, "2026-08-21")
    record_order(conn, "2026-08-21", "AAPL", "BUY", 100.0, True, "approved")
    row = conn.execute(
        "SELECT ticker, action, amount_usd, approved, reason FROM orders"
    ).fetchone()
    assert row == ("AAPL", "BUY", 100.0, 1, "approved")


def test_order_not_placed_before_marked(conn):
    mark_run_started(conn, "2026-08-21")
    record_order(conn, "2026-08-21", "AAPL", "BUY", 100.0, True, "approved")
    assert has_order_been_placed(conn, "2026-08-21", "AAPL", "BUY") is False


def test_order_placed_after_mark(conn):
    mark_run_started(conn, "2026-08-21")
    record_order(conn, "2026-08-21", "AAPL", "BUY", 100.0, True, "approved")
    mark_order_placed(conn, "2026-08-21", "AAPL", "BUY", "order-123")
    assert has_order_been_placed(conn, "2026-08-21", "AAPL", "BUY") is True
    row = conn.execute("SELECT order_id FROM orders WHERE ticker = 'AAPL'").fetchone()
    assert row == ("order-123",)


def test_mark_order_placed_records_sell_quantity(conn):
    mark_run_started(conn, "2026-08-21")
    record_order(conn, "2026-08-21", "AAPL", "SELL", 0.0, True, "approved")
    mark_order_placed(conn, "2026-08-21", "AAPL", "SELL", "order-456", quantity="3.5")

    row = conn.execute(
        "SELECT order_id, quantity FROM orders WHERE ticker = 'AAPL' AND action = 'SELL'"
    ).fetchone()
    assert row == ("order-456", "3.5")


def test_mark_order_placed_only_stamps_one_row_when_duplicate_rows_exist(conn):
    # 크래시 후 재실행으로 같은 (run_date, ticker, action)에 대해
    # record_order가 두 번 호출된 상황을 재현한다.
    mark_run_started(conn, "2026-08-21")
    record_order(conn, "2026-08-21", "AAPL", "BUY", 100.0, True, "approved")
    record_order(conn, "2026-08-21", "AAPL", "BUY", 100.0, True, "approved")

    mark_order_placed(conn, "2026-08-21", "AAPL", "BUY", "order-123")

    rows = conn.execute("SELECT placed, order_id FROM orders WHERE ticker = 'AAPL'").fetchall()
    assert rows.count((1, "order-123")) == 1
    assert rows.count((0, None)) == 1


def test_current_regime_defaults_to_risk_on(conn):
    assert get_current_regime(conn) == ("risk_on", 0)


def test_set_regime_then_read_back(conn):
    set_regime(conn, "risk_off", "2026-09-03")
    assert get_current_regime(conn) == ("risk_off", 0)


def test_set_regime_unchanged_keeps_since_date(conn):
    set_regime(conn, "risk_off", "2026-09-03")
    set_regime(conn, "risk_off", "2026-09-10")  # 같은 레짐 재설정
    row = conn.execute("SELECT regime, since_date FROM regime_state WHERE id = 1").fetchone()
    assert row == ("risk_off", "2026-09-03")  # since_date 는 첫 전이일 유지


def test_pending_inverse_buy_flag(conn):
    set_regime(conn, "risk_off", "2026-09-03")
    set_pending_inverse_buy(conn, 1)
    assert get_current_regime(conn) == ("risk_off", 1)
    set_pending_inverse_buy(conn, 0)
    assert get_current_regime(conn) == ("risk_off", 0)


def test_record_regime_signal_is_idempotent_per_date(conn):
    record_regime_signal(conn, "2026-09-03", 1, 0, 21000.0, 21500.0, 21200.0, 28.0)
    record_regime_signal(conn, "2026-09-03", 1, 0, 21001.0, 21500.0, 21200.0, 28.5)
    rows = conn.execute("SELECT COUNT(*) FROM regime_signals").fetchone()
    assert rows[0] == 1


def test_count_consecutive_signal(conn):
    record_regime_signal(conn, "2026-09-01", 0, 1, 1, 1, 1, 15.0)
    record_regime_signal(conn, "2026-09-02", 1, 0, 1, 1, 1, 26.0)
    record_regime_signal(conn, "2026-09-03", 1, 0, 1, 1, 1, 27.0)
    record_regime_signal(conn, "2026-09-04", 1, 0, 1, 1, 1, 28.0)
    assert count_consecutive_signal(conn, "2026-09-04", "risk_off") == 3
    assert count_consecutive_signal(conn, "2026-09-04", "risk_on") == 0
    assert count_consecutive_signal(conn, "2026-09-01", "risk_on") == 1


def test_count_consecutive_signal_respects_as_of_date(conn):
    record_regime_signal(conn, "2026-09-02", 1, 0, 1, 1, 1, 26.0)
    record_regime_signal(conn, "2026-09-03", 1, 0, 1, 1, 1, 27.0)
    assert count_consecutive_signal(conn, "2026-09-02", "risk_off") == 1
