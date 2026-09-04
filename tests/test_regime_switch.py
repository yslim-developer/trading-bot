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
