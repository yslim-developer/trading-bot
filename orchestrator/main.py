import argparse
import os
from datetime import date

from orchestrator.config import load_config
from orchestrator.notifier import send_telegram_message
from orchestrator.risk_engine import Decision, evaluate_decisions
from orchestrator.state import (
    has_run_completed_today,
    init_db,
    is_circuit_breaker_triggered_today,
    mark_circuit_breaker_triggered,
    mark_run_completed,
    mark_run_started,
    record_order,
)
from orchestrator.toss_client import TossClient
from orchestrator.trading_agents_client import run_analysis

STOP_FILE = "STOP"
DB_PATH = "data/trading.db"


def run(dry_run):
    config = load_config()
    conn = init_db(DB_PATH)
    run_date = date.today().isoformat()

    try:
        if os.path.exists(STOP_FILE):
            send_telegram_message(config, f"[{run_date}] STOP 파일 발견 - 자동매매를 건너뜁니다.")
            return

        if has_run_completed_today(conn, run_date):
            send_telegram_message(config, f"[{run_date}] 오늘 실행이 이미 완료되어 재실행을 건너뜁니다.")
            return

        mark_run_started(conn, run_date)

        toss = TossClient(config.toss_client_id, config.toss_client_secret)
        account = toss.get_account_state(config.watchlist)

        if account.daily_pnl_pct <= config.risk_limits.daily_loss_circuit_pct:
            mark_circuit_breaker_triggered(conn, run_date)

        if is_circuit_breaker_triggered_today(conn, run_date):
            send_telegram_message(
                config,
                f"[{run_date}] 서킷브레이커 발동 (당일 손익 {account.daily_pnl_pct:.2f}%) - 신규 주문 없음.",
            )
            mark_run_completed(conn, run_date)
            return

        decisions = []
        for ticker in config.watchlist:
            try:
                action = run_analysis(ticker, run_date)
            except Exception as exc:
                send_telegram_message(config, f"[{run_date}] {ticker} 분석 실패: {exc}")
                continue
            amount = config.risk_limits.per_ticker_max_usd if action == "BUY" else 0.0
            decisions.append(Decision(ticker=ticker, action=action, amount_usd=amount))

        risk_results = evaluate_decisions(decisions, account, config.risk_limits)

        summary_lines = [f"[{run_date}] 자동매매 결과{' (드라이런)' if dry_run else ''}"]
        for r in risk_results:
            record_order(conn, run_date, r.ticker, r.action, r.amount_usd, r.approved, r.reason)
            summary_lines.append(
                f"- {r.ticker} {r.action}: {'승인' if r.approved else '거부'} ({r.reason})"
            )

            if not r.approved or dry_run:
                continue

            client_order_id = f"{run_date}-{r.ticker}-{r.action}"
            try:
                if r.action == "BUY":
                    toss.place_market_buy(r.ticker, r.amount_usd, client_order_id)
                elif r.action == "SELL":
                    quantity = toss.get_holding_quantity(r.ticker)
                    if float(quantity) > 0:
                        toss.place_market_sell(r.ticker, quantity, client_order_id)
            except Exception as exc:
                summary_lines.append(f"  주문 실패: {exc}")

        send_telegram_message(config, "\n".join(summary_lines))
        mark_run_completed(conn, run_date)

    except Exception as exc:
        send_telegram_message(config, f"[{run_date}] 실행 중 예외 발생: {exc}")
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
