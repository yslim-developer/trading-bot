import json
import subprocess

from orchestrator.decision_mapping import normalize_decision

__all__ = [
    "FallbackExhaustedError",
    "QuotaExhaustedError",
    "normalize_decision",
    "run_analysis",
]


class QuotaExhaustedError(Exception):
    """LLM provider's daily/rate quota was exhausted for this analysis run."""


class FallbackExhaustedError(Exception):
    """Gemini's quota was exhausted AND every configured fallback (Claude/groq) also failed.

    Distinct from QuotaExhaustedError so callers don't tell the operator
    "내일 리셋되면 괜찮다" when the groq fallback itself is broken (e.g. a
    groq free-tier TPM limit smaller than the analyst prompt) and will keep
    failing on the same node regardless of Gemini's quota resetting.
    """


def run_analysis(ticker, run_date, timeout_seconds=1800):
    try:
        result = subprocess.run(
            [
                "docker", "compose", "run", "--rm", "tradingagents",
                "python", "/app/run_analysis.py", ticker, run_date,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        # docker/run_analysis.py 가 폴백(Claude/groq)까지 실패하면 이 마커를 붙여
        # 재발생시킨다 (RESOURCE_EXHAUSTED 문자열도 같이 섞여 있으므로 이
        # 체크가 아래 QuotaExhaustedError 체크보다 먼저 와야 한다).
        if "FALLBACK_FAILED" in stderr:
            raise FallbackExhaustedError(
                f"{ticker}: Gemini 한도 초과 + Claude/groq 폴백도 실패"
            ) from exc
        if "RESOURCE_EXHAUSTED" in stderr:
            raise QuotaExhaustedError(
                f"{ticker}: Gemini 일일 요청 한도 초과"
            ) from exc
        raise
    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            payload = json.loads(line)
            return payload["action"], payload.get("reasoning", "")
    raise ValueError(
        f"{ticker} 분석 결과에서 JSON 출력을 찾지 못했습니다: {result.stdout[-500:]}"
    )
