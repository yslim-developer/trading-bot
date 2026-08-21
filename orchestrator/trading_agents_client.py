import json
import subprocess


def normalize_decision(raw):
    """host-side copy, kept for fast unit testing without spinning up Docker.

    NOT called by run_analysis() below — the copy that actually executes in
    production is docker/run_analysis.py's normalize_decision, which runs
    inside the container. If the BUY/SELL/HOLD mapping logic ever changes,
    update both copies manually; tests here don't cover the container copy.
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


def run_analysis(ticker, run_date, timeout_seconds=1800):
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
    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)["action"]
    raise ValueError(
        f"{ticker} 분석 결과에서 JSON 출력을 찾지 못했습니다: {result.stdout[-500:]}"
    )
