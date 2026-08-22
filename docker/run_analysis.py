import argparse
import json
import os

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

# docker-compose.yml이 orchestrator/decision_mapping.py를 이 경로로 마운트한다 --
# 호스트(orchestrator.trading_agents_client)와 정확히 같은 파일을 임포트하므로
# 매핑 로직을 두 곳에 따로 유지/동기화할 필요가 없다.
from decision_mapping import normalize_decision

# 종목당 여러 번 LLM을 호출하는 구조라 무료 티어 일일 한도에 걸리기 쉽다.
# 1순위: Gemini(GOOGLE_API_KEY, 무료 티어). 2순위(선택): groq(GROQ_API_KEY,
# 무료 오픈웨이트 모델) -- Gemini가 RESOURCE_EXHAUSTED로 실패했을 때만 자동
# 전환한다. groq 키가 없으면 원래 예외를 그대로 전파한다(기존 동작 유지).
PROVIDER_MODELS = {
    "google": {
        "deep_think_llm": "gemini-3.5-flash",
        "quick_think_llm": "gemini-3.1-flash-lite",
    },
    "groq": {
        "deep_think_llm": "openai/gpt-oss-120b",
        "quick_think_llm": "openai/gpt-oss-20b",
    },
}


def build_config(provider):
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = provider
    config.update(PROVIDER_MODELS[provider])
    return config


def propagate_with_provider(ticker, date, provider):
    graph = TradingAgentsGraph(debug=False, config=build_config(provider))
    return graph.propagate(ticker, date)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("date")
    args = parser.parse_args()

    provider_used = "google"
    try:
        _, raw_decision = propagate_with_provider(args.ticker, args.date, "google")
    except Exception as exc:
        if "RESOURCE_EXHAUSTED" in str(exc) and os.environ.get("GROQ_API_KEY"):
            provider_used = "groq"
            _, raw_decision = propagate_with_provider(args.ticker, args.date, "groq")
        else:
            raise

    result = {
        "ticker": args.ticker,
        "date": args.date,
        "action": normalize_decision(raw_decision),
        "raw_decision": str(raw_decision),
        "provider": provider_used,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
