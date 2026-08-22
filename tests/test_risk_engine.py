from orchestrator.risk_engine import (
    AccountState,
    Decision,
    RiskLimits,
    evaluate_decisions,
)


def make_limits(**overrides):
    defaults = dict(
        per_ticker_max_usd=100.0,
        per_ticker_max_position_usd=100000.0,
        daily_max_buy_usd=300.0,
        daily_loss_circuit_pct=-3.0,
        max_concurrent_positions=5,
    )
    defaults.update(overrides)
    return RiskLimits(**defaults)


def test_circuit_breaker_blocks_all_orders_when_daily_loss_exceeds_threshold():
    decisions = [Decision(ticker="AAPL", action="BUY", amount_usd=100.0)]
    account = AccountState(positions_usd={}, daily_pnl_pct=-5.0)
    results = evaluate_decisions(decisions, account, make_limits())

    assert len(results) == 1
    assert results[0].approved is False
    assert results[0].reason == "circuit_breaker"


def test_buy_amount_capped_at_per_ticker_limit():
    decisions = [Decision(ticker="AAPL", action="BUY", amount_usd=500.0)]
    account = AccountState(positions_usd={}, daily_pnl_pct=0.0)
    results = evaluate_decisions(decisions, account, make_limits(per_ticker_max_usd=100.0))

    assert results[0].approved is True
    assert results[0].amount_usd == 100.0


def test_buy_amount_capped_at_remaining_daily_budget():
    decisions = [
        Decision(ticker="AAPL", action="BUY", amount_usd=100.0),
        Decision(ticker="MSFT", action="BUY", amount_usd=100.0),
        Decision(ticker="NVDA", action="BUY", amount_usd=100.0),
    ]
    account = AccountState(positions_usd={}, daily_pnl_pct=0.0)
    results = evaluate_decisions(
        decisions, account, make_limits(per_ticker_max_usd=100.0, daily_max_buy_usd=150.0)
    )

    assert [r.amount_usd for r in results] == [100.0, 50.0, 0.0]
    assert results[2].approved is False
    assert results[2].reason == "no_budget_remaining"


def test_new_buy_rejected_when_max_concurrent_positions_reached():
    decisions = [Decision(ticker="NVDA", action="BUY", amount_usd=100.0)]
    account = AccountState(
        positions_usd={"AAPL": 500.0, "MSFT": 500.0}, daily_pnl_pct=0.0
    )
    results = evaluate_decisions(decisions, account, make_limits(max_concurrent_positions=2))

    assert results[0].approved is False
    assert results[0].reason == "max_positions"


def test_addon_buy_to_existing_position_not_blocked_by_max_positions():
    decisions = [Decision(ticker="AAPL", action="BUY", amount_usd=100.0)]
    account = AccountState(positions_usd={"AAPL": 500.0, "MSFT": 500.0}, daily_pnl_pct=0.0)
    results = evaluate_decisions(decisions, account, make_limits(max_concurrent_positions=2))

    assert results[0].approved is True


def test_buy_amount_capped_by_remaining_position_room():
    decisions = [Decision(ticker="AAPL", action="BUY", amount_usd=100.0)]
    account = AccountState(positions_usd={"AAPL": 45.0}, daily_pnl_pct=0.0)
    results = evaluate_decisions(
        decisions, account, make_limits(per_ticker_max_position_usd=50.0)
    )

    assert results[0].approved is True
    assert results[0].amount_usd == 5.0


def test_buy_rejected_when_position_cap_already_reached():
    decisions = [Decision(ticker="AAPL", action="BUY", amount_usd=100.0)]
    account = AccountState(positions_usd={"AAPL": 50.0}, daily_pnl_pct=0.0)
    results = evaluate_decisions(
        decisions, account, make_limits(per_ticker_max_position_usd=50.0)
    )

    assert results[0].approved is False
    assert results[0].reason == "position_cap_reached"


def test_sell_always_approved_without_amount_cap():
    decisions = [Decision(ticker="AAPL", action="SELL", amount_usd=0.0)]
    account = AccountState(positions_usd={"AAPL": 500.0}, daily_pnl_pct=0.0)
    results = evaluate_decisions(decisions, account, make_limits())

    assert results[0].approved is True
    assert results[0].reason == "approved"


def test_hold_never_approved():
    decisions = [Decision(ticker="AAPL", action="HOLD", amount_usd=0.0)]
    account = AccountState(positions_usd={}, daily_pnl_pct=0.0)
    results = evaluate_decisions(decisions, account, make_limits())

    assert results[0].approved is False
    assert results[0].reason == "hold"
