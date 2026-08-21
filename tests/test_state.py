import sqlite3

import pytest

from orchestrator.state import (
    has_run_completed_today,
    init_db,
    is_circuit_breaker_triggered_today,
    mark_circuit_breaker_triggered,
    mark_run_completed,
    mark_run_started,
    record_order,
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
