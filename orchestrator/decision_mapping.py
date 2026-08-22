def normalize_decision(raw):
    """TradingAgents propagate()의 decision 반환값을 BUY/SELL/HOLD 문자열로 정규화한다.

    이 파일 하나가 호스트(orchestrator.trading_agents_client)와 컨테이너
    (docker/run_analysis.py, docker-compose.yml volume mount) 양쪽에서
    똑같이 임포트되는 단일 소스다 — 더 이상 두 곳에 복붙해서 수동으로
    동기화할 필요가 없다.
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
