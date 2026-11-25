from orchestrator.risk_engine import (
    AccountState,
    Decision,
    RiskLimits,
    evaluate_decisions,
)


def make_limits(**overrides):
    defaults = dict(
        buy_cash_ratio=0.95,
        max_position_pct=40.0,
        daily_loss_circuit_pct=-3.0,
        max_concurrent_positions=5,
    )
    defaults.update(overrides)
    return RiskLimits(**defaults)


def buy(ticker, weight=1.0, reasoning=""):
    return Decision(ticker=ticker, action="BUY", weight=weight, reasoning=reasoning)


def test_circuit_breaker_blocks_all_orders_when_daily_loss_exceeds_threshold():
    account = AccountState(positions_usd={}, daily_pnl_pct=-5.0, cash_usd=1000.0)
    results = evaluate_decisions([buy("AAPL")], account, make_limits())

    assert results[0].approved is False
    assert results[0].reason == "circuit_breaker"


def test_buy_budget_is_cash_ratio_split_by_weight():
    account = AccountState(positions_usd={}, cash_usd=1000.0)
    results = evaluate_decisions(
        [buy("AAPL", 2.0), buy("MSFT", 1.0)], account, make_limits(max_position_pct=100.0)
    )

    # 950 을 2:1 로
    assert [r.amount_usd for r in results] == [633.33, 316.66]
    assert all(r.approved for r in results)


def test_position_cap_limits_buy_and_excess_goes_to_others():
    # 총자산 = 주식 0 + 현금 1000 -> 종목당 상한 400. 950 을 2:1:1 로 나누면 AAPL 475 > 400.
    account = AccountState(positions_usd={}, cash_usd=1000.0)
    results = evaluate_decisions(
        [buy("AAPL", 2.0), buy("MSFT"), buy("NVDA")], account, make_limits()
    )

    assert [r.amount_usd for r in results] == [400.0, 275.0, 275.0]


def test_position_cap_counts_existing_holding():
    # 총자산 = 300 + 700 = 1000 -> 상한 400, AAPL 은 이미 300 보유 -> 100 만 추가
    account = AccountState(positions_usd={"AAPL": 300.0}, cash_usd=700.0)
    results = evaluate_decisions([buy("AAPL")], account, make_limits())

    assert results[0].amount_usd == 100.0


def test_buy_rejected_when_position_cap_already_reached():
    account = AccountState(positions_usd={"AAPL": 500.0}, cash_usd=500.0)
    results = evaluate_decisions([buy("AAPL")], account, make_limits())

    assert results[0].approved is False
    assert results[0].reason == "position_cap_reached"


def test_no_cash_means_no_budget():
    account = AccountState(positions_usd={"MSFT": 100.0}, cash_usd=0.0)
    results = evaluate_decisions([buy("AAPL")], account, make_limits())

    assert results[0].approved is False
    assert results[0].reason == "no_budget_remaining"


def test_new_buy_rejected_when_max_concurrent_positions_reached():
    account = AccountState(positions_usd={"AAPL": 500.0, "MSFT": 500.0}, cash_usd=1000.0)
    results = evaluate_decisions([buy("NVDA")], account, make_limits(max_concurrent_positions=2))

    assert results[0].approved is False
    assert results[0].reason == "max_positions"


def test_selling_today_frees_a_position_slot():
    account = AccountState(positions_usd={"AAPL": 500.0, "MSFT": 500.0}, cash_usd=1000.0)
    decisions = [Decision(ticker="AAPL", action="SELL"), buy("NVDA")]
    results = evaluate_decisions(decisions, account, make_limits(max_concurrent_positions=2))

    assert results[1].approved is True


def test_addon_buy_to_existing_position_not_blocked_by_max_positions():
    account = AccountState(positions_usd={"AAPL": 50.0, "MSFT": 50.0}, cash_usd=1000.0)
    results = evaluate_decisions([buy("AAPL")], account, make_limits(max_concurrent_positions=2))

    assert results[0].approved is True


def test_sell_always_approved_and_hold_never():
    account = AccountState(positions_usd={"AAPL": 500.0}, cash_usd=0.0)
    decisions = [Decision(ticker="AAPL", action="SELL"), Decision(ticker="MSFT", action="HOLD")]
    results = evaluate_decisions(decisions, account, make_limits())

    assert (results[0].approved, results[0].reason) == (True, "approved")
    assert (results[1].approved, results[1].reason) == (False, "hold")


def test_reasoning_passes_through_unchanged_for_every_action():
    decisions = [
        buy("AAPL", reasoning="AI 근거 BUY"),
        Decision(ticker="MSFT", action="SELL", reasoning="AI 근거 SELL"),
        Decision(ticker="GOOGL", action="HOLD", reasoning="AI 근거 HOLD"),
    ]
    account = AccountState(positions_usd={"MSFT": 100.0}, cash_usd=100.0)
    results = evaluate_decisions(decisions, account, make_limits())

    assert [r.reasoning for r in results] == ["AI 근거 BUY", "AI 근거 SELL", "AI 근거 HOLD"]
