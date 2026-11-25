import math
from dataclasses import dataclass, field

MIN_ORDER_USD = 1.0  # 이보다 작은 배분은 주문하지 않는다 (소수점 매수 최소 단위 여유)


@dataclass
class Decision:
    ticker: str
    action: str  # "BUY" | "SELL" | "HOLD"
    amount_usd: float = 0.0  # 기록용 -- BUY 금액은 evaluate_decisions 가 현금으로 배분한다
    reasoning: str = ""  # TradingAgents의 판단 근거 (있으면)
    weight: float = 1.0  # BUY 배분 가중치 (Buy=2, Overweight=1)


@dataclass
class AccountState:
    positions_usd: dict = field(default_factory=dict)  # ticker -> 현재 평가금액(USD)
    daily_pnl_pct: float = 0.0  # 당일 손익률(%), 음수면 손실
    cash_usd: float = 0.0  # USD 현금 매수 가능 금액 (원화 제외)


@dataclass
class RiskLimits:
    buy_cash_ratio: float  # 매수에 쓸 USD 현금 비율 (0.95 = 95%)
    max_position_pct: float  # 종목당 최대 비중 (총자산 = 보유 주식 + USD 현금 대비 %)
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


def _water_fill(budget, weights, rooms):
    """budget 을 weights 비율로 나누되 각자 room 을 넘지 않게 하고, 넘친 몫은 나머지에 재분배."""
    alloc = [0.0] * len(weights)
    active = set(range(len(weights)))
    remaining = budget
    while active and remaining > 1e-9:
        total_weight = sum(weights[i] for i in active)
        share = {i: remaining * weights[i] / total_weight for i in active}
        capped = [i for i in active if share[i] >= rooms[i]]
        if not capped:
            for i in active:
                alloc[i] = share[i]
            break
        for i in capped:
            alloc[i] = rooms[i]
            remaining -= rooms[i]
            active.remove(i)
    return alloc


def evaluate_decisions(decisions, account, limits):
    """결정 목록을 리스크 한도와 계좌 상태에 따라 평가해 승인/거부된 RiskDecision 목록을 반환한다.

    BUY 금액: USD 현금 x buy_cash_ratio 를 BUY 종목들에 weight 비율로 나누고, 종목당 보유액이
    총자산(보유 주식 + USD 현금)의 max_position_pct 를 넘지 않게 자른다.
    """
    if account.daily_pnl_pct <= limits.daily_loss_circuit_pct:
        return [
            RiskDecision(d.ticker, d.action, 0.0, False, "circuit_breaker", d.reasoning)
            for d in decisions
        ]

    sold = {d.ticker for d in decisions if d.action == "SELL"}
    positions = {t: v for t, v in account.positions_usd.items() if t not in sold}
    equity = sum(positions.values()) + account.cash_usd
    position_cap = equity * limits.max_position_pct / 100
    budget = max(account.cash_usd, 0.0) * limits.buy_cash_ratio
    position_count = sum(1 for v in positions.values() if v > 0)

    results = [None] * len(decisions)
    buy_idx, weights, rooms = [], [], []
    for i, d in enumerate(decisions):
        if d.action == "HOLD":
            results[i] = RiskDecision(d.ticker, d.action, 0.0, False, "hold", d.reasoning)
        elif d.action == "SELL":
            results[i] = RiskDecision(d.ticker, d.action, d.amount_usd, True, "approved", d.reasoning)
        else:  # BUY
            current = positions.get(d.ticker, 0.0)
            is_new_position = current <= 0
            if is_new_position and position_count >= limits.max_concurrent_positions:
                results[i] = RiskDecision(d.ticker, d.action, 0.0, False, "max_positions", d.reasoning)
                continue
            room = position_cap - current
            if room <= 0:
                results[i] = RiskDecision(
                    d.ticker, d.action, 0.0, False, "position_cap_reached", d.reasoning
                )
                continue
            if is_new_position:
                position_count += 1
            buy_idx.append(i)
            weights.append(d.weight)
            rooms.append(room)

    for i, amount in zip(buy_idx, _water_fill(budget, weights, rooms)):
        d = decisions[i]
        amount = math.floor(amount * 100) / 100  # 센트 내림 -- 현금 초과 주문 방지
        if amount < MIN_ORDER_USD:
            reason = "no_budget_remaining" if budget < MIN_ORDER_USD else "amount_too_small"
            results[i] = RiskDecision(d.ticker, d.action, 0.0, False, reason, d.reasoning)
        else:
            results[i] = RiskDecision(d.ticker, d.action, amount, True, "approved", d.reasoning)

    return results
