import pytest

import orchestrator.regime as regime
from orchestrator.config import RegimeSwitchConfig
from orchestrator.state import get_current_regime, init_db


@pytest.fixture
def conn():
    return init_db(":memory:")


class FakeToss:
    def __init__(self, holdings=None, purchasing_power=1000.0):
        self._holdings = holdings or {}
        self._pp = purchasing_power
        self.orders = []

    def get_holding_quantity(self, ticker):
        return str(self._holdings.get(ticker, 0))

    def get_purchasing_power_usd(self):
        return self._pp

    def place_market_sell(self, ticker, quantity, client_order_id):
        self.orders.append(("SELL", ticker, quantity))
        self._holdings[ticker] = 0
        return {"orderId": f"s-{ticker}", "clientOrderId": client_order_id}

    def place_market_buy(self, ticker, amount_usd, client_order_id):
        self.orders.append(("BUY", ticker, round(amount_usd, 2)))
        return {"orderId": f"b-{ticker}", "clientOrderId": client_order_id}


def _cfg(**kw):
    return RegimeSwitchConfig(enabled=True, inverse_ticker="PSQ", max_deploy_pct=90.0, **kw)


class _C:
    """config stub exposing .regime_switch"""
    def __init__(self, rs):
        self.regime_switch = rs


def test_switch_to_off_sells_all_longs_and_buys_inverse(conn):
    toss = FakeToss(holdings={"MSFT": 2, "MU": 5}, purchasing_power=1000.0)
    positions_usd = {"MSFT": 60.0, "MU": 40.0}
    lines = regime.execute_switch_to_off(
        conn, toss, _C(_cfg()), "2026-09-03", ["MSFT", "MU"], positions_usd,
        dry_run=False, transitioned=True, pending=0,
    )
    assert ("SELL", "MSFT", "2") in toss.orders
    assert ("SELL", "MU", "5") in toss.orders
    assert ("BUY", "PSQ", 90.0) in toss.orders  # min(1000,100)*0.9
    assert get_current_regime(conn) == ("risk_off", 0)


def test_switch_to_off_defers_inverse_buy_when_no_purchasing_power(conn):
    toss = FakeToss(holdings={"MSFT": 2}, purchasing_power=0.0)
    regime.execute_switch_to_off(
        conn, toss, _C(_cfg()), "2026-09-03", ["MSFT"], {"MSFT": 60.0},
        dry_run=False, transitioned=True, pending=0,
    )
    assert ("SELL", "MSFT", "2") in toss.orders
    assert not any(o[0] == "BUY" for o in toss.orders)
    assert get_current_regime(conn) == ("risk_off", 1)


def test_switch_to_off_pending_retry_buys_without_selling(conn):
    from orchestrator.state import set_regime, set_pending_inverse_buy
    set_regime(conn, "risk_off", "2026-09-02")
    set_pending_inverse_buy(conn, 1)
    toss = FakeToss(holdings={}, purchasing_power=200.0)
    regime.execute_switch_to_off(
        conn, toss, _C(_cfg()), "2026-09-03", ["MSFT"], {},
        dry_run=False, transitioned=False, pending=1,
    )
    assert toss.orders == [("BUY", "PSQ", 180.0)]  # 200*0.9
    assert get_current_regime(conn) == ("risk_off", 0)


def test_switch_to_off_dry_run_places_no_orders_and_no_state_change(conn):
    toss = FakeToss(holdings={"MSFT": 2})
    regime.execute_switch_to_off(
        conn, toss, _C(_cfg()), "2026-09-03", ["MSFT"], {"MSFT": 60.0},
        dry_run=True, transitioned=True, pending=0,
    )
    assert toss.orders == []
    assert get_current_regime(conn) == ("risk_on", 0)


def test_switch_to_off_isolates_per_ticker_sell_failure(conn):
    class FlakyToss(FakeToss):
        def place_market_sell(self, ticker, quantity, client_order_id):
            if ticker == "MSFT":
                raise RuntimeError("boom")
            return super().place_market_sell(ticker, quantity, client_order_id)

    toss = FlakyToss(holdings={"MSFT": 2, "MU": 5}, purchasing_power=1000.0)
    lines = regime.execute_switch_to_off(
        conn, toss, _C(_cfg()), "2026-09-03", ["MSFT", "MU"], {"MSFT": 60.0, "MU": 40.0},
        dry_run=False, transitioned=True, pending=0,
    )
    assert ("SELL", "MU", "5") in toss.orders
    assert any("MSFT" in ln and "실패" in ln for ln in lines)


def test_switch_to_on_sells_inverse(conn):
    from orchestrator.state import set_regime
    set_regime(conn, "risk_off", "2026-09-01")
    toss = FakeToss(holdings={"PSQ": 30})
    lines = []
    regime.execute_switch_to_on(conn, toss, _C(_cfg()), "2026-09-03", dry_run=False, lines=lines)
    assert ("SELL", "PSQ", "30") in toss.orders
    assert get_current_regime(conn) == ("risk_on", 0)


def test_switch_to_on_dry_run_no_orders_no_state_change(conn):
    from orchestrator.state import set_regime
    set_regime(conn, "risk_off", "2026-09-01")
    toss = FakeToss(holdings={"PSQ": 30})
    regime.execute_switch_to_on(conn, toss, _C(_cfg()), "2026-09-03", dry_run=True, lines=[])
    assert toss.orders == []
    assert get_current_regime(conn) == ("risk_off", 0)


# --- Important 2: all sells fail must not trigger a full-size inverse buy ---

def test_switch_to_off_all_sells_fail_defers_inverse_buy(conn):
    class DeadToss(FakeToss):
        def place_market_sell(self, ticker, quantity, client_order_id):
            raise RuntimeError("rejected")

    toss = DeadToss(holdings={"MSFT": 2, "MU": 5}, purchasing_power=1000.0)
    lines = regime.execute_switch_to_off(
        conn, toss, _C(_cfg()), "2026-09-03", ["MSFT", "MU"], {"MSFT": 60.0, "MU": 40.0},
        dry_run=False, transitioned=True, pending=0,
    )
    assert not any(o[0] == "BUY" for o in toss.orders)
    assert get_current_regime(conn) == ("risk_off", 1)
    assert any("매도 미완료" in ln for ln in lines)


# --- Important 3: switch_to_on must not strand the inverse position on error ---

def test_switch_to_on_holding_fetch_fails_keeps_regime_off(conn):
    from orchestrator.state import set_regime
    set_regime(conn, "risk_off", "2026-09-01")

    class BlindToss(FakeToss):
        def get_holding_quantity(self, ticker):
            raise RuntimeError("api down")

    toss = BlindToss(holdings={"PSQ": 30})
    lines = []
    regime.execute_switch_to_on(conn, toss, _C(_cfg()), "2026-09-03", dry_run=False, lines=lines)
    assert toss.orders == []
    assert get_current_regime(conn) == ("risk_off", 0)


def test_switch_to_on_sell_raises_keeps_regime_off(conn):
    from orchestrator.state import set_regime
    set_regime(conn, "risk_off", "2026-09-01")

    class FlakyToss(FakeToss):
        def place_market_sell(self, ticker, quantity, client_order_id):
            raise RuntimeError("boom")

    toss = FlakyToss(holdings={"PSQ": 30})
    lines = []
    regime.execute_switch_to_on(conn, toss, _C(_cfg()), "2026-09-03", dry_run=False, lines=lines)
    assert get_current_regime(conn) == ("risk_off", 0)
    assert any("레짐 전환 보류" in ln for ln in lines)


def test_switch_to_on_flat_position_still_flips_regime(conn):
    from orchestrator.state import set_regime
    set_regime(conn, "risk_off", "2026-09-01")
    toss = FakeToss(holdings={})
    regime.execute_switch_to_on(conn, toss, _C(_cfg()), "2026-09-03", dry_run=False, lines=[])
    assert toss.orders == []
    assert get_current_regime(conn) == ("risk_on", 0)


# --- Important 1: ledger-write failure stays isolated, does not double-order ---

def test_switch_to_off_ledger_write_failure_is_isolated(conn, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("db locked")

    monkeypatch.setattr(regime, "mark_order_placed_with_retry", boom)
    toss = FakeToss(holdings={"MSFT": 2, "MU": 5}, purchasing_power=1000.0)
    lines = regime.execute_switch_to_off(
        conn, toss, _C(_cfg()), "2026-09-03", ["MSFT", "MU"], {"MSFT": 60.0, "MU": 40.0},
        dry_run=False, transitioned=True, pending=0,
    )
    # both sells + buy attempted despite every ledger write failing (isolation held)
    assert ("SELL", "MSFT", "2") in toss.orders
    assert ("SELL", "MU", "5") in toss.orders
    assert ("BUY", "PSQ", 90.0) in toss.orders
    assert sum("수동 확인 필요" in ln for ln in lines) == 3
    # buy recorded-failure: pending left as-is (still 1) so operator resolves manually
    assert get_current_regime(conn) == ("risk_off", 1)


# --- Minor 4: extra coverage ---

def test_switch_to_off_hold_branch_no_orders(conn):
    from orchestrator.state import set_regime
    set_regime(conn, "risk_off", "2026-09-01")
    toss = FakeToss(holdings={"PSQ": 30}, purchasing_power=500.0)
    lines = regime.execute_switch_to_off(
        conn, toss, _C(_cfg()), "2026-09-03", ["MSFT"], {},
        dry_run=False, transitioned=False, pending=0,
    )
    assert toss.orders == []
    assert get_current_regime(conn) == ("risk_off", 0)
    assert any("주문 없음" in ln for ln in lines)


def test_attempt_inverse_buy_skips_when_already_placed(conn):
    from orchestrator.state import set_regime, set_pending_inverse_buy, mark_order_placed, record_order
    set_regime(conn, "risk_off", "2026-09-02")
    set_pending_inverse_buy(conn, 1)
    record_order(conn, "2026-09-03", "PSQ", "BUY", 100.0, True, "regime_switch:inverse_buy")
    mark_order_placed(conn, "2026-09-03", "PSQ", "BUY", "b-PSQ")
    toss = FakeToss(holdings={}, purchasing_power=200.0)
    regime.execute_switch_to_off(
        conn, toss, _C(_cfg()), "2026-09-03", ["MSFT"], {},
        dry_run=False, transitioned=False, pending=1,
    )
    assert toss.orders == []
    assert get_current_regime(conn) == ("risk_off", 0)  # pending cleared


def test_switch_to_off_partial_sell_failure_retried_next_run(conn):
    class FlakyToss(FakeToss):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.sell_attempts = []

        def place_market_sell(self, ticker, quantity, client_order_id):
            self.sell_attempts.append(ticker)
            if ticker == "MSFT":
                raise RuntimeError("boom")
            return super().place_market_sell(ticker, quantity, client_order_id)

    toss = FlakyToss(holdings={"MSFT": 2, "MU": 5}, purchasing_power=1000.0)
    regime.execute_switch_to_off(
        conn, toss, _C(_cfg()), "2026-09-04", ["MSFT", "MU"], {"MSFT": 60.0, "MU": 40.0},
        dry_run=False, transitioned=True, pending=0,
    )
    assert toss.sell_attempts == ["MSFT", "MU"]  # MSFT failed, MU sold

    # next run: not a transition, pending already cleared, but MSFT still held
    regime.execute_switch_to_off(
        conn, toss, _C(_cfg()), "2026-09-05", ["MSFT", "MU"], {"MSFT": 60.0, "MU": 40.0},
        dry_run=False, transitioned=False, pending=0,
    )
    assert toss.sell_attempts.count("MSFT") == 2  # stray long swept again
    assert "MU" not in toss.sell_attempts[2:]  # MU already flat, not re-sold


def test_attempt_inverse_buy_skips_when_already_holding_inverse(conn):
    from orchestrator.state import set_regime, set_pending_inverse_buy
    set_regime(conn, "risk_off", "2026-09-02")
    set_pending_inverse_buy(conn, 1)
    toss = FakeToss(holdings={"PSQ": 5}, purchasing_power=500.0)
    lines = regime.execute_switch_to_off(
        conn, toss, _C(_cfg()), "2026-09-03", ["MSFT"], {},
        dry_run=False, transitioned=False, pending=1,
    )
    assert not any(o[0] == "BUY" for o in toss.orders)
    assert get_current_regime(conn) == ("risk_off", 0)  # pending cleared
    assert any("이미 보유 중" in ln for ln in lines)


def test_switch_to_off_writes_orders_table(conn):
    toss = FakeToss(holdings={"MSFT": 2}, purchasing_power=1000.0)
    regime.execute_switch_to_off(
        conn, toss, _C(_cfg()), "2026-09-03", ["MSFT"], {"MSFT": 60.0},
        dry_run=False, transitioned=True, pending=0,
    )
    rows = conn.execute(
        "SELECT ticker, action, amount_usd, reason, placed FROM orders ORDER BY id"
    ).fetchall()
    assert ("MSFT", "SELL", 0.0, "regime_switch:on->off", 1) in rows
    assert ("PSQ", "BUY", 54.0, "regime_switch:inverse_buy", 1) in rows  # min(1000,60)*0.9
