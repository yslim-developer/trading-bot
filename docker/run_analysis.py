import argparse
import json

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG


def normalize_decision(raw):
    """propagate()의 decision 반환값을 BUY/SELL/HOLD 문자열로 정규화한다."""
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
