"""레짐 스위치 오프라인 백테스트.

레짐 규칙(QQQ 200/50일 SMA + VIX, confirm_days, 히스테리시스)을 과거 일봉에
매일 적용해, risk_off 구간엔 인버스(PSQ), risk_on 구간엔 QQQ 를 보유했을 때의
수익률을 바이앤홀드(QQQ)와 비교한다.

yfinance + pandas 만 필요 (requirements.txt 에 포함). 프로덕션 봇 실행에는 불필요.
    python scripts/backtest_regime_switch.py
"""

import sys

import yfinance as yf

# Windows 콘솔 기본 코드페이지(cp949)가 한글 출력을 깨뜨리는 걸 방지
# (sibling script backtest_risk_engine.py 와 동일 처리).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, ".")
from orchestrator.regime import RegimeData, compute_raw_signals, evaluate_regime

VIX_THRESHOLD = 25.0
CONFIRM_DAYS = 2
SMA_LONG = 200
SMA_SHORT = 50
START = "2015-01-01"


def _close(ticker):
    # yfinance는 티커마다 다른 tz(예: ^VIX는 America/Chicago)를 붙여 반환하므로
    # 날짜 단위로 정규화해야 join 이 어긋나지 않는다.
    s = yf.Ticker(ticker).history(start=START)["Close"]
    s.index = s.index.tz_localize(None).normalize()
    return s


def _load():
    qqq = _close("QQQ")
    psq = _close("PSQ")
    vix = _close("^VIX")
    df = qqq.to_frame("qqq").join(psq.to_frame("psq")).join(vix.to_frame("vix")).dropna()
    df["sma200"] = df["qqq"].rolling(SMA_LONG).mean()
    df["sma50"] = df["qqq"].rolling(SMA_SHORT).mean()
    return df.dropna()


def run():
    df = _load()
    regime = "risk_on"
    off_streak = on_streak = 0
    transitions = 0
    whipsaws = 0
    last_transition_idx = None

    strat_equity = 1.0
    bh_equity = 1.0
    prev_row = None
    equity_curve = []

    for i, (ts, row) in enumerate(df.iterrows()):
        if prev_row is not None:
            qqq_ret = row["qqq"] / prev_row["qqq"] - 1
            psq_ret = row["psq"] / prev_row["psq"] - 1
            strat_equity *= 1 + (psq_ret if regime == "risk_off" else qqq_ret)
            bh_equity *= 1 + qqq_ret
        equity_curve.append(strat_equity)

        data = RegimeData(row["qqq"], row["sma200"], row["sma50"], row["vix"])
        raw_off, raw_on = compute_raw_signals(data, VIX_THRESHOLD)
        off_streak = off_streak + 1 if raw_off else 0
        on_streak = on_streak + 1 if raw_on else 0
        new_regime, transitioned = evaluate_regime(regime, off_streak, on_streak, CONFIRM_DAYS)
        if transitioned:
            transitions += 1
            if last_transition_idx is not None and i - last_transition_idx <= 7:
                whipsaws += 1
            last_transition_idx = i
        regime = new_regime
        prev_row = row

    peak = 0.0
    max_dd = 0.0
    for e in equity_curve:
        peak = max(peak, e)
        max_dd = min(max_dd, e / peak - 1)

    print(f"기간: {df.index[0].date()} ~ {df.index[-1].date()}  ({len(df)}일)")
    print(f"전략 총수익률:      {strat_equity - 1:+.1%}")
    print(f"QQQ 바이앤홀드:     {bh_equity - 1:+.1%}")
    print(f"전이 횟수:          {transitions}")
    print(f"휩쏘(7일내 재전환): {whipsaws}")
    print(f"전략 최대낙폭:      {max_dd:.1%}")

    assert transitions > 0, "전이가 한 번도 없음 — 규칙/데이터 확인 필요"
    return strat_equity, bh_equity


if __name__ == "__main__":
    run()
