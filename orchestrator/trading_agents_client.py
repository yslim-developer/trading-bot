import json
import subprocess

from orchestrator.decision_mapping import normalize_decision

__all__ = ["QuotaExhaustedError", "normalize_decision", "run_analysis"]


class QuotaExhaustedError(Exception):
    """LLM provider's daily/rate quota was exhausted for this analysis run."""


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
        if "RESOURCE_EXHAUSTED" in (exc.stderr or ""):
            raise QuotaExhaustedError(
                f"{ticker}: Gemini 일일 요청 한도 초과"
            ) from exc
        raise
    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)["action"]
    raise ValueError(
        f"{ticker} 분석 결과에서 JSON 출력을 찾지 못했습니다: {result.stdout[-500:]}"
    )
