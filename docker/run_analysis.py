import argparse
import json

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG


def normalize_decision(raw):
    """propagate()의 decision 반환값을 BUY/SELL/HOLD 문자열로 정규화한다.

    이 함수가 실제 프로덕션에서 실행되는 버전이다 (컨테이너 안에서 실행됨).
    orchestrator/trading_agents_client.py에도 동일한 이름의 함수가 있지만
    그건 Docker 없이 빠른 단위 테스트를 하기 위한 호스트 쪽 복사본일 뿐,
    run_analysis()에서 호출되지 않는다. 매핑 로직을 바꿀 때는 두 곳 모두
    수동으로 동기화할 것.
    """
    if isinstance(raw, dict):
        for key in ("action", "decision", "final_decision", "signal"):
            value = raw.get(key)
            if isinstance(value, str):
                raw = value
                break
    text = str(raw).upper()
    if "OVERWEIGHT" in text:
        return "BUY"
    if "UNDERWEIGHT" in text:
        return "SELL"
    for action in ("BUY", "SELL", "HOLD"):
        if action in text:
            return action
    return "HOLD"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("date")
    args = parser.parse_args()

    config = DEFAULT_CONFIG.copy()
    # Force Gemini (free-tier GOOGLE_API_KEY, see README) instead of the
    # vendored default of OpenAI, which would fail every run with no
    # OPENAI_API_KEY set. Explicit dict overrides here (rather than relying on
    # TRADINGAGENTS_* env vars threading through docker-compose) are easier to
    # verify by reading this file alone. Keys confirmed present in
    # tradingagents/default_config.py's DEFAULT_CONFIG.
    config["llm_provider"] = "google"
    config["deep_think_llm"] = "gemini-3.5-flash"
    config["quick_think_llm"] = "gemini-3.1-flash-lite"
    graph = TradingAgentsGraph(debug=False, config=config)
    _, raw_decision = graph.propagate(args.ticker, args.date)

    result = {
        "ticker": args.ticker,
        "date": args.date,
        "action": normalize_decision(raw_decision),
        "raw_decision": str(raw_decision),
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
