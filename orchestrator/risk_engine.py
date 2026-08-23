from dataclasses import dataclass, field


@dataclass
class Decision:
    ticker: str
    action: str  # "BUY" | "SELL" | "HOLD"
    amount_usd: float = 0.0  # BUY에만 의미 있음 (원하는 매수 금액)
    reasoning: str = ""  # TradingAgents의 판단 근거 (있으면)


@dataclass
class AccountState:
    positions_usd: dict = field(default_factory=dict)  # ticker -> 현재 평가금액(USD)
    daily_pnl_pct: float = 0.0  # 당일 손익률(%), 음수면 손실


@dataclass
class RiskLimits:
    per_ticker_max_usd: float
    per_ticker_max_position_usd: float  # 종목당 누적 보유금액 상한
    daily_max_buy_usd: float
    daily_loss_circuit_pct: float  # 예: -3.0 이면 -3% 이하일 때 서킷브레이커 발동
    max_concurrent_positions: int


@dataclass
class RiskDecision:
    ticker: str
    action: str
    amount_usd: float
    approved: bool
    reason: str
    reasoning: str = ""  # Decision.reasoning을 그대로 통과시킨 값


def evaluate_decisions(decisions, account, limits):
    """결정 목록을 리스크 한도와 계좌 상태에 따라 평가해 승인/거부된 RiskDecision 목록을 반환한다."""
    if account.daily_pnl_pct <= limits.daily_loss_circuit_pct:
        return [
            RiskDecision(d.ticker, d.action, 0.0, False, "circuit_breaker", d.reasoning)
            for d in decisions
        ]

    remaining_budget = limits.daily_max_buy_usd
    position_count = sum(1 for v in account.positions_usd.values() if v > 0)
    results = []

    for d in decisions:
        if d.action == "HOLD":
            results.append(RiskDecision(d.ticker, d.action, 0.0, False, "hold", d.reasoning))
            continue

        if d.action == "SELL":
            results.append(
                RiskDecision(d.ticker, d.action, d.amount_usd, True, "approved", d.reasoning)
            )
            continue

        # BUY
        is_new_position = account.positions_usd.get(d.ticker, 0.0) <= 0
        if is_new_position and position_count >= limits.max_concurrent_positions:
            results.append(
                RiskDecision(d.ticker, d.action, 0.0, False, "max_positions", d.reasoning)
            )
            continue

        current_holding = account.positions_usd.get(d.ticker, 0.0)
        remaining_position_room = limits.per_ticker_max_position_usd - current_holding
        if remaining_position_room <= 0:
            results.append(
                RiskDecision(d.ticker, d.action, 0.0, False, "position_cap_reached", d.reasoning)
            )
            continue

        capped_amount = min(
            d.amount_usd, limits.per_ticker_max_usd, remaining_budget, remaining_position_room
        )
        if capped_amount <= 0:
            results.append(
                RiskDecision(d.ticker, d.action, 0.0, False, "no_budget_remaining", d.reasoning)
            )
            continue

        remaining_budget -= capped_amount
        if is_new_position:
            position_count += 1
        results.append(
            RiskDecision(d.ticker, d.action, capped_amount, True, "approved", d.reasoning)
        )

    return results
