"""시장 레짐 감지 + 인버스 ETF 스위치.

LLM 미사용. yfinance 로 받은 QQQ 200/50일 SMA + VIX 를 규칙으로 평가해
risk_on / risk_off 를 판정하고, 전이 시 롱 전량 매도 ↔ 인버스 ETF 매수를 실행한다.
"""

import time
from dataclasses import dataclass

import yfinance as yf

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
