import argparse
import json

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

# docker-compose.yml이 orchestrator/decision_mapping.py를 이 경로로 마운트한다 --
# 호스트(orchestrator.trading_agents_client)와 정확히 같은 파일을 임포트하므로
# 매핑 로직을 두 곳에 따로 유지/동기화할 필요가 없다.
from decision_mapping import normalize_decision


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
