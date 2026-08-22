import subprocess
from unittest.mock import Mock

import pytest

from orchestrator.trading_agents_client import QuotaExhaustedError, normalize_decision, run_analysis


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("BUY", "BUY"),
        ({"action": "SELL"}, "SELL"),
        ({"final_decision": "The decision is HOLD for now."}, "HOLD"),
        ("I recommend a strong SELL position here", "SELL"),
        ({"unrelated_key": "no signal here"}, "HOLD"),
        # Real TradingAgentsGraph.propagate() output: a plain string from its
        # fixed 5-tier scale (see vendor/tradingagents/tradingagents/agents/
        # utils/rating.py and graph/signal_processing.py).
        ("Buy", "BUY"),
        ("Overweight", "BUY"),
        ("Hold", "HOLD"),
        ("Underweight", "SELL"),
        ("Sell", "SELL"),
    ],
)
def test_normalize_decision(raw, expected):
    assert normalize_decision(raw) == expected


def test_run_analysis_parses_last_json_line(monkeypatch):
    fake_result = Mock()
    fake_result.stdout = (
        "some noisy log line\n"
        "another log line\n"
        '{"ticker": "AAPL", "date": "2026-08-21", "action": "BUY", "raw_decision": "BUY"}\n'
    )

    def fake_run(cmd, capture_output, text, encoding, errors, timeout, check):
        assert cmd == [
            "docker", "compose", "run", "--rm", "tradingagents",
            "python", "/app/run_analysis.py", "AAPL", "2026-08-21",
        ]
        assert encoding == "utf-8"
        return fake_result

    monkeypatch.setattr(subprocess, "run", fake_run)

    action = run_analysis("AAPL", "2026-08-21")

    assert action == "BUY"


def test_run_analysis_raises_when_no_json_found(monkeypatch):
    fake_result = Mock()
    fake_result.stdout = "nothing but logs here\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_result)

    with pytest.raises(ValueError):
        run_analysis("AAPL", "2026-08-21")


def test_run_analysis_raises_quota_exhausted_error_on_resource_exhausted(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=["docker"],
            output="",
            stderr=(
                "langchain_google_genai.chat_models.GoogleRateLimitError: "
                "Error calling model 'gemini-3.5-flash' (RESOURCE_EXHAUSTED): "
                "429 RESOURCE_EXHAUSTED."
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(QuotaExhaustedError):
        run_analysis("AAPL", "2026-08-21")


def test_run_analysis_reraises_other_called_process_errors_unchanged(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=1, cmd=["docker"], output="", stderr="some unrelated Docker error"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        run_analysis("AAPL", "2026-08-21")
