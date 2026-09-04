"""시장 레짐 감지 + 인버스 ETF 스위치.

LLM 미사용. yfinance 로 받은 QQQ 200/50일 SMA + VIX 를 규칙으로 평가해
risk_on / risk_off 를 판정하고, 전이 시 롱 전량 매도 ↔ 인버스 ETF 매수를 실행한다.
"""

import time
from dataclasses import dataclass

import yfinance as yf

from orchestrator.state import (
    get_current_regime,
    has_order_been_placed,
    mark_order_placed_with_retry,
    record_order,
    set_pending_inverse_buy,
    set_regime,
)

MIN_ORDER_USD = 1.0  # 이 금액 미만이면 인버스 매수를 시도하지 않는다 (토스 최소 주문금액은 구현 시 확인)
_FETCH_ATTEMPTS = 3
_FETCH_BACKOFF_SECONDS = 2


@dataclass
class RegimeData:
    qqq_close: float
    sma200: float
    sma50: float
    vix: float | None  # VIX 조회 실패 시 None


def fetch_regime_data(proxy_ticker="QQQ", sma_long=200, sma_short=50):
    """지수 프록시 일봉으로 SMA 를 계산하고 VIX 최근값을 붙여 RegimeData 를 반환한다.

    조회 실패(예외 3회) 또는 데이터가 sma_long 일 미만이면 None 을 반환한다 (호출부는
    이전 레짐을 유지하는 fail-safe 로 처리).
    """
    for attempt in range(_FETCH_ATTEMPTS):
        try:
            hist = yf.Ticker(proxy_ticker).history(period="400d")
            if len(hist) < sma_long:
                return None
            close = hist["Close"]
            sma200 = float(close.rolling(sma_long).mean().iloc[-1])
            sma50 = float(close.rolling(sma_short).mean().iloc[-1])
            qqq_close = float(close.iloc[-1])

            vix = None
            try:
                vix_hist = yf.Ticker("^VIX").history(period="5d")
                if len(vix_hist) > 0:
                    vix = float(vix_hist["Close"].iloc[-1])
            except Exception:
                vix = None

            return RegimeData(qqq_close=qqq_close, sma200=sma200, sma50=sma50, vix=vix)
        except Exception:
            if attempt < _FETCH_ATTEMPTS - 1:
                time.sleep(_FETCH_BACKOFF_SECONDS)
    return None


def compute_raw_signals(data, vix_threshold):
    """오늘 하루치 원시 신호 (raw_risk_off, raw_risk_on) 를 반환한다.

    둘은 상호 배타가 아니다 — 200일선 바로 아래이면서 데드크로스도 아니고
    VIX 도 낮은 '중립' 구간에서는 둘 다 False.
    """
    below_200 = data.qqq_close < data.sma200
    death_cross = data.sma50 < data.sma200
    vix_high = data.vix is not None and data.vix > vix_threshold
    raw_risk_off = below_200 and (death_cross or vix_high)
    raw_risk_on = data.qqq_close >= data.sma200
    return raw_risk_off, raw_risk_on


def evaluate_regime(prev_regime, off_streak, on_streak, confirm_days):
    """확인봉 + 히스테리시스로 새 레짐을 확정한다.

    진입(risk_off)과 청산(risk_on)이 서로 다른 조건을 쓴다 (히스테리시스):
    청산 조건이 진입보다 느슨해 200일선 근처 휩쏘에서 빠르게 롱 복귀한다.

    반환: (new_regime, transitioned)
    """
    off_confirmed = off_streak >= confirm_days
    on_confirmed = on_streak >= confirm_days
    if prev_regime == "risk_on":
        new_regime = "risk_off" if off_confirmed else "risk_on"
    else:  # risk_off
        new_regime = "risk_on" if on_confirmed else "risk_off"
    return new_regime, (new_regime != prev_regime)


def _sell_one(conn, toss, ticker, run_date, positions_usd, dry_run, lines, reason):
    """한 종목을 시장가 전량 매도한다. 실패는 이 종목에만 격리한다.
    반환: 이 종목의 직전 시장가치(매도합 누적용). 매도를 못 했으면 0.0."""
    try:
        qty = toss.get_holding_quantity(ticker)
    except Exception as exc:
        lines.append(f"  {ticker} 보유수량 조회 실패: {exc}")
        return 0.0
    if float(qty) <= 0:
        return 0.0
    if has_order_been_placed(conn, run_date, ticker, "SELL"):
        lines.append(f"  {ticker} 이미 매도됨 - 건너뜀")
        return positions_usd.get(ticker, 0.0)

    record_order(conn, run_date, ticker, "SELL", 0.0, True, reason)
    if dry_run:
        lines.append(f"  [드라이런] {ticker} {qty}주 매도")
        return positions_usd.get(ticker, 0.0)

    client_order_id = f"{run_date}-{ticker}-SELL"
    try:
        result = toss.place_market_sell(ticker, qty, client_order_id)
    except Exception as exc:
        lines.append(f"  {ticker} 매도 실패: {exc}")
        return 0.0
    try:
        mark_order_placed_with_retry(conn, run_date, ticker, "SELL", result["orderId"], quantity=qty)
        lines.append(f"  {ticker} {qty}주 매도 (order {result['orderId']})")
    except Exception:
        # 체결은 됐고(현금 확보됨) 로컬 기록만 실패 - 재시도로 재매도되면 안 되므로
        # 예외를 삼켜 종목 격리를 지키고, 매도합에는 포함시킨다(캡 계산용).
        lines.append(
            f"  {ticker} 주문은 체결됐으나 로컬 기록 실패 (order_id={result['orderId']}) - 수동 확인 필요"
        )
    return positions_usd.get(ticker, 0.0)


def _attempt_inverse_buy(conn, toss, config, run_date, cap, dry_run, lines):
    """인버스 ETF 매수. cap 은 매수 산정 기준 상한(USD), None 이면 매수가능금액 전액 기준
    (전이 후 pending 재시도 전용)."""
    rs = config.regime_switch
    inv = rs.inverse_ticker
    if has_order_been_placed(conn, run_date, inv, "BUY"):
        lines.append(f"  {inv} 이미 매수됨 - 건너뜀")
        if not dry_run:
            set_pending_inverse_buy(conn, 0)
        return
    try:
        if float(toss.get_holding_quantity(inv)) > 0:
            lines.append(f"  {inv} 이미 보유 중 - 매수 건너뜀")
            if not dry_run:
                set_pending_inverse_buy(conn, 0)
            return
    except Exception:
        pass  # 조회 실패 시엔 아래 정상 경로 진행 (has_order_been_placed 가 1차 방어)
    try:
        pp = toss.get_purchasing_power_usd()
    except Exception as exc:
        lines.append(f"  매수가능금액 조회 실패: {exc} - {inv} 매수 보류")
        return
    budget = pp if cap is None else min(pp, cap)
    deploy = budget * rs.max_deploy_pct / 100.0
    if deploy < MIN_ORDER_USD:
        cap_str = f"${pp:.2f}" if cap is None else f"${cap:.2f}"
        lines.append(
            f"  매수 규모 부족 (가능금액 ${pp:.2f}, 상한 {cap_str}) - {inv} 매수는 결제 후 다음 실행에서"
        )
        return

    record_order(conn, run_date, inv, "BUY", deploy, True, "regime_switch:inverse_buy")
    if dry_run:
        lines.append(f"  [드라이런] {inv} ${deploy:.2f} 매수")
        return
    client_order_id = f"{run_date}-{inv}-BUY"
    try:
        result = toss.place_market_buy(inv, deploy, client_order_id)
    except Exception as exc:
        lines.append(f"  {inv} 매수 실패: {exc} - 다음 실행에서 재시도")
        return
    try:
        mark_order_placed_with_retry(conn, run_date, inv, "BUY", result["orderId"])
    except Exception:
        # 실제 매수는 체결됐으나 로컬 기록 실패. pending 을 그대로 두면 다음 실행에서
        # has_order_been_placed 가 False 라 이중 매수 위험, 0 으로 내려도 미기록 상태.
        # 어느 쪽도 안전하지 않으므로 pending 은 건드리지 않고 운영자 수동 확인에 맡긴다.
        lines.append(
            f"  {inv} 주문은 체결됐으나 로컬 기록 실패 (order_id={result['orderId']}) - 수동 확인 필요"
        )
        return
    set_pending_inverse_buy(conn, 0)
    lines.append(f"  {inv} ${deploy:.2f} 매수 (order {result['orderId']})")


def execute_switch_to_off(conn, toss, config, run_date, watchlist, positions_usd,
                          dry_run, transitioned, pending):
    """risk_off 상태에서의 스위치.

    불변식: risk_off 동안 watchlist 롱 보유량은 0. 전이든 아니든 매 실행 재확인/정리하고,
    이번 실행에서 정리한 롱 대금(또는 전이 후 pending)이 있으면 인버스 ETF 를 매수한다.
    """
    lines = []

    sold_total = 0.0
    for ticker in watchlist:
        sold_total += _sell_one(
            conn, toss, ticker, run_date, positions_usd, dry_run, lines,
            "regime_switch:on->off",
        )

    if transitioned and not dry_run:
        set_regime(conn, "risk_off", run_date)
        set_pending_inverse_buy(conn, 1)

    _, still_pending = get_current_regime(conn)
    if dry_run:
        still_pending = 1 if transitioned else pending

    if transitioned and watchlist and sold_total <= 0:
        # 전이인데 매도가 하나도 성사 안 됨 - 롱을 다 든 채 인버스 사면 이중 노출.
        # 매수 건너뛰고 pending=1 유지 → 다음 실행에서 롱 정리 + 매수 재시도.
        lines.append("  매도 미완료 - 인버스 매수 보류 (다음 실행 재시도)")
    elif still_pending or sold_total > 0:
        cap = sold_total if sold_total > 0 else None
        _attempt_inverse_buy(conn, toss, config, run_date, cap, dry_run, lines)
    else:
        lines.append("  risk_off 유지 - 주문 없음")
    return lines


def execute_switch_to_on(conn, toss, config, run_date, dry_run, lines):
    """risk_off→risk_on 전이: 인버스 ETF 전량 매도 후 레짐을 risk_on 으로.

    인버스 매도가 확인되지 않으면(수량 조회 실패 또는 매도 예외) 레짐을 그대로 두어
    다음 실행에서 스위치를 통째로 재시도한다 - 인버스를 든 채 방치되는 것 방지.
    """
    inv = config.regime_switch.inverse_ticker
    try:
        qty = toss.get_holding_quantity(inv)
    except Exception as exc:
        lines.append(f"  {inv} 보유수량 조회 실패: {exc} - 인버스 매도 실패, 레짐 전환 보류 (다음 실행 재시도)")
        return

    if float(qty) > 0 and not has_order_been_placed(conn, run_date, inv, "SELL"):
        record_order(conn, run_date, inv, "SELL", 0.0, True, "regime_switch:off->on")
        if dry_run:
            lines.append(f"  [드라이런] {inv} {qty}주 매도")
        else:
            client_order_id = f"{run_date}-{inv}-SELL"
            try:
                result = toss.place_market_sell(inv, qty, client_order_id)
            except Exception as exc:
                lines.append(f"  {inv} 매도 실패: {exc} - 레짐 전환 보류 (다음 실행 재시도)")
                return
            try:
                mark_order_placed_with_retry(conn, run_date, inv, "SELL", result["orderId"], quantity=qty)
                lines.append(f"  {inv} {qty}주 매도 (order {result['orderId']})")
            except Exception:
                lines.append(
                    f"  {inv} 주문은 체결됐으나 로컬 기록 실패 (order_id={result['orderId']}) - 수동 확인 필요"
                )

    if not dry_run:
        set_regime(conn, "risk_on", run_date)
        set_pending_inverse_buy(conn, 0)
