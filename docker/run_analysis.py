import argparse
import json
import os
import re

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from langchain_core.callbacks import UsageMetadataCallbackHandler

# docker-compose.yml이 orchestrator/decision_mapping.py를 이 경로로 마운트한다 --
# 호스트(orchestrator.trading_agents_client)와 정확히 같은 파일을 임포트하므로
# 매핑 로직을 두 곳에 따로 유지/동기화할 필요가 없다.
from decision_mapping import normalize_decision

# 종목당 여러 번 LLM을 호출하는 구조라 무료 티어 일일 한도에 걸리기 쉽다.
# 1순위: Gemini(GOOGLE_API_KEY, 무료 티어). Gemini가 RESOURCE_EXHAUSTED로
# 실패했을 때만 FALLBACK_PROVIDERS 순서대로(키가 설정된 것만) 자동 전환한다:
# Claude(ANTHROPIC_API_KEY, 유료) -> groq(GROQ_API_KEY, 무료 오픈웨이트).
# 폴백 키가 하나도 없으면 원래 예외를 그대로 전파한다(기존 동작 유지).
PROVIDER_MODELS = {
    "google": {
        "deep_think_llm": "gemini-3.5-flash",
        "quick_think_llm": "gemini-3.1-flash-lite",
    },
    "anthropic": {
        "deep_think_llm": "claude-sonnet-5-5",  # 비용: Opus 대비 deep 단계 절반
        "quick_think_llm": "claude-sonnet-5-5",
    },
    "groq": {
        "deep_think_llm": "openai/gpt-oss-120b",
        "quick_think_llm": "openai/gpt-oss-20b",
    },
}

FALLBACK_PROVIDERS = [("anthropic", "ANTHROPIC_API_KEY"), ("groq", "GROQ_API_KEY")]
FALLBACK_TRIGGERS = ("RESOURCE_EXHAUSTED", "UNAVAILABLE")

# 모델별 토큰 사용량 누적 (API 비용 산정용). 폴백 시 두 provider 사용량이 모두 남는다.
USAGE = UsageMetadataCallbackHandler()


def build_config(provider):
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = provider
    config["output_language"] = "Korean"
    config.update(PROVIDER_MODELS[provider])
    return config


def propagate_with_provider(ticker, date, provider):
    graph = TradingAgentsGraph(debug=False, config=build_config(provider), callbacks=[USAGE])
    return graph.propagate(ticker, date)


def extract_reasoning(final_state):
    """Portfolio Manager 최종 결정 markdown에서 Executive Summary만 뽑아낸다.

    tradingagents.agents.schemas.render_pm_decision()이 만드는 markdown은
    "**Rating**: X" 다음에 "**Executive Summary**: ..." 섹션을 항상 포함한다
    (vendor/tradingagents/tradingagents/agents/schemas.py 확인). 형식이 다른
    예외적인 경우에는 원문 앞부분만 잘라서 반환한다.
    """
    text = final_state.get("final_trade_decision") or ""
    match = re.search(r"\*\*Executive Summary\*\*:\s*(.+?)(?:\n\n|\Z)", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()[:300]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("date")
    args = parser.parse_args()

    # CLAUDE_PRIMARY_UNTIL(YYYY-MM-DD) 이하 날짜는 Claude 를 1순위로 쓴다 (선불 크레딧
    # 소진 기간용). 날짜가 지나면 자동으로 Gemini 1순위로 돌아간다.
    claude_first = bool(os.environ.get("ANTHROPIC_API_KEY")) and args.date <= os.environ.get(
        "CLAUDE_PRIMARY_UNTIL", ""
    )
    provider_used = "anthropic" if claude_first else "google"
    try:
        final_state, raw_decision = propagate_with_provider(args.ticker, args.date, provider_used)
    except Exception as exc:
        if claude_first:
            # 크레딧 소진 등 Claude 쪽 어떤 에러든 무료 Gemini 로 넘긴다.
            fallbacks = ["google"]
        else:
            fallbacks = [p for p, key in FALLBACK_PROVIDERS if os.environ.get(key)]
            # RESOURCE_EXHAUSTED(일일 한도) 뿐 아니라 503 UNAVAILABLE(Gemini 과부하,
            # "high demand")도 폴백한다 -- 과부하는 한도와 달리 실행 시점마다 랜덤하게
            # 터져서, 폴백 없이 두면 해당 종목 분석이 그냥 실패한다.
            if not any(m in str(exc) for m in FALLBACK_TRIGGERS) or not fallbacks:
                raise
        errors = []
        for provider_used in fallbacks:
            try:
                final_state, raw_decision = propagate_with_provider(
                    args.ticker, args.date, provider_used
                )
                break
            except Exception as fallback_exc:
                errors.append(f"{provider_used}: {fallback_exc}")
        else:
            # 폴백까지 전부 실패한 경우(예: groq 무료 티어 TPM 8000 < 분석
            # 노드 프롬프트, Claude 키 잔액 부족)를 "Gemini 한도 초과"로만
            # 보고하면 "내일 리셋되면 괜찮다"는 잘못된 안심을 준다 -- 폴백도
            # 죽었다는 사실을 LLM_FALLBACK_FAILED 마커로 명시해 상위
            # (orchestrator)가 구분해서 알리게 한다.
            raise RuntimeError(
                f"LLM_FALLBACK_FAILED: 1순위({'anthropic' if claude_first else 'google'}) 실패 후 폴백도 모두 실패: "
                + " | ".join(errors)
            ) from exc

    result = {
        "ticker": args.ticker,
        "date": args.date,
        "action": normalize_decision(raw_decision),
        "raw_decision": str(raw_decision),
        "reasoning": extract_reasoning(final_state),
        "provider": provider_used,
        "usage": USAGE.usage_metadata,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
