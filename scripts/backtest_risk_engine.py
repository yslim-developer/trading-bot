"""리스크 엔진(서킷브레이커/포지션 캡)이 실제 과거 시장 위기에서 어떻게 동작하는지 검증한다.

TradingAgents는 재호출하지 않는다 (LLM 비용/속도 때문에 비현실적). 대신 "매일 살 수 있으면
산다"는 최악의 탐욕적 매수 시그널로 리스크 엔진의 안전장치 자체를 스트레스 테스트한다 --
실제 TradingAgents는 BUY/SELL/HOLD를 섞어 내므로 이보다는 덜 공격적일 것이고, 따라서 이
백테스트는 "최소한 이 정도 최악의 상황도 막아내는가"를 보는 보수적인 검증이다.

사용법:
    pip install -r requirements-backtest.txt
    python scripts/backtest_risk_engine.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 콘솔 기본 코드페이지(cp949)가 한글 출력을 깨뜨리는 걸 방지 (다른 곳에서 이미
# 겪은 문제와 동일한 클래스의 버그 -- orchestrator/trading_agents_client.py 참고).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import yaml

from orchestrator.risk_engine import AccountState, Decision, RiskLimits, evaluate_decisions

STRESS_PERIODS = {
    "covid_crash_2020": ("2020-02-01", "2020-04-30"),
    "2022_bear_market": ("2022-01-01", "2022-10-31"),
}


def load_risk_limits(config_path="config.yaml"):
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    rl = raw["risk_limits"]
    limits = RiskLimits(
        buy_cash_ratio=rl["buy_cash_ratio"],
        max_position_pct=rl["max_position_pct"],
        daily_loss_circuit_pct=rl["daily_loss_circuit_pct"],
        max_concurrent_positions=rl["max_concurrent_positions"],
    )
    return raw["watchlist"], limits


def simulate(tickers, limits, price_returns):
    """일별 가격 변화율(price_returns, ticker별 열을 가진 DataFrame)로 리스크 엔진을 시뮬레이션한다.

    매일 "가능하면 종목당 한도만큼 전부 매수" 시그널을 넣고 evaluate_decisions()를 그대로
    호출한다 (실제 프로덕션 로직과 동일). 순수 함수라 합성 데이터로도 빠르게 테스트 가능하다.

    Returns: (일별 기록 DataFrame, 서킷브레이커 발동일 리스트)
    """
    positions_usd = {t: 0.0 for t in tickers}
    cash_usd = 10000.0  # 시뮬레이션 시작 USD 현금
    circuit_breaker_days = []
    history = []

    for date in price_returns.index:
        day_return = price_returns.loc[date]
        total_before = sum(positions_usd.values())
        pnl_today = sum(positions_usd[t] * day_return[t] for t in tickers)
        daily_pnl_pct = (pnl_today / total_before * 100) if total_before > 0 else 0.0

        for t in tickers:
            positions_usd[t] += positions_usd[t] * day_return[t]

        account = AccountState(
            positions_usd=dict(positions_usd), daily_pnl_pct=daily_pnl_pct, cash_usd=cash_usd
        )
        decisions = [
            Decision(ticker=t, action="BUY") for t in tickers
        ]
        results = evaluate_decisions(decisions, account, limits)

        triggered_today = any(r.reason == "circuit_breaker" for r in results)
        if triggered_today:
            circuit_breaker_days.append(date)

        for r in results:
            if r.approved:
                positions_usd[r.ticker] += r.amount_usd
                cash_usd -= r.amount_usd

        history.append(
            {
                "date": date,
                "daily_pnl_pct": daily_pnl_pct,
                "portfolio_value": sum(positions_usd.values()),
                "circuit_breaker": triggered_today,
            }
        )

    return pd.DataFrame(history).set_index("date"), circuit_breaker_days


def print_report(period_name, df, circuit_breaker_days, limits):
    worst_day_pct = df["daily_pnl_pct"].min()
    worst_day_date = df["daily_pnl_pct"].idxmin()

    print(f"\n=== {period_name} ===")
    print(f"기간: {df.index.min().date()} ~ {df.index.max().date()} ({len(df)}거래일)")
    print(f"서킷브레이커 임계값: {limits.daily_loss_circuit_pct}%")
    print(f"최악의 단일일 손익률: {worst_day_pct:.2f}% ({worst_day_date.date()})")
    print(f"서킷브레이커 발동 횟수: {len(circuit_breaker_days)}")
    if circuit_breaker_days:
        first, last = circuit_breaker_days[0], circuit_breaker_days[-1]
        print(f"  최초 발동: {first.date()}, 마지막 발동: {last.date()}")
    print(f"기간 종료 시 시뮬레이션 포트폴리오 가치: ${df['portfolio_value'].iloc[-1]:.2f}")

    try:
        import vectorbt as vbt

        returns = df["portfolio_value"].pct_change().fillna(0.0)
        stats = returns.vbt.returns(freq="1D").stats()
        print("\n-- vectorbt 통계 --")
        print(stats[["Max Drawdown [%]", "Sharpe Ratio", "Sortino Ratio"]])
    except ImportError:
        print("(vectorbt 미설치 -- pip install -r requirements-backtest.txt 로 상세 통계 확인 가능)")


def main():
    import yfinance as yf

    tickers, limits = load_risk_limits()
    print(f"워치리스트: {tickers}")
    print(f"리스크 한도: 현금 {limits.buy_cash_ratio:.0%} 배분, 종목당 총자산의 {limits.max_position_pct}%, "
          f"서킷브레이커 {limits.daily_loss_circuit_pct}%, "
          f"최대 {limits.max_concurrent_positions}종목")

    for period_name, (start, end) in STRESS_PERIODS.items():
        prices = yf.download(tickers, start=start, end=end, progress=False)["Close"].dropna()
        if isinstance(prices, pd.Series):
            prices = prices.to_frame(tickers[0])
        price_returns = prices.pct_change().fillna(0.0)

        df, circuit_breaker_days = simulate(tickers, limits, price_returns)
        print_report(period_name, df, circuit_breaker_days, limits)


if __name__ == "__main__":
    main()
