import argparse
import os
from datetime import date

from orchestrator.config import load_config
from orchestrator.notifier import (
    ping_healthcheck,
    send_telegram_message,
    wait_for_telegram_reply,
)
from orchestrator.risk_engine import Decision, evaluate_decisions
from orchestrator.state import (
    has_order_been_placed,
    has_run_completed_today,
    init_db,
    is_circuit_breaker_triggered_today,
    mark_circuit_breaker_triggered,
    mark_order_placed,
    mark_run_completed,
    mark_run_started,
    record_order,
)
from orchestrator.toss_client import TossApiError, TossClient
from orchestrator.trading_agents_client import QuotaExhaustedError, run_analysis

STOP_FILE = "STOP"
DB_PATH = "data/trading.db"


def run(dry_run):
    config = load_config()
    conn = None
    run_date = date.today().isoformat()

    try:
        db_dir = os.path.dirname(DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = init_db(DB_PATH)

        if os.path.exists(STOP_FILE):
            send_telegram_message(config, f"[{run_date}] STOP 파일 발견 - 자동매매를 건너뜁니다.")
            ping_healthcheck(config)
            return

        ping_healthcheck(config, "/start")

        if has_run_completed_today(conn, run_date):
            send_telegram_message(config, f"[{run_date}] 오늘 실행이 이미 완료되어 재실행을 건너뜁니다.")
            ping_healthcheck(config)
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
            ping_healthcheck(config)
            mark_run_completed(conn, run_date)
            return

        decisions = []
        for ticker in config.watchlist:
            try:
                action = run_analysis(ticker, run_date)
            except QuotaExhaustedError:
                send_telegram_message(
                    config,
                    f"[{run_date}] {ticker} 분석 실패 — Gemini 일일 요청 한도 초과 (내일 자동 리셋됨)",
                )
                continue
            except Exception as exc:
                send_telegram_message(config, f"[{run_date}] {ticker} 분석 실패: {exc}")
                continue
            amount = config.risk_limits.per_ticker_max_usd if action == "BUY" else 0.0
            decisions.append(Decision(ticker=ticker, action=action, amount_usd=amount))

        risk_results = evaluate_decisions(decisions, account, config.risk_limits)

        approval_granted = True
        if not dry_run and config.approval_enabled and any(r.approved for r in risk_results):
            proposal_lines = [f"[{run_date}] 제안 주문 (승인 대기)"]
            for r in risk_results:
                if r.approved:
                    proposal_lines.append(f"- {r.ticker} {r.action}: ${r.amount_usd:.2f}")
            proposal_lines.append(
                f"{config.approval_timeout_minutes}분 안에 '승인' 또는 '거부'로 답장해주세요. "
                "무응답 시 자동 거부됩니다."
            )
            send_telegram_message(config, "\n".join(proposal_lines))

            reply = wait_for_telegram_reply(
                config, timeout_seconds=config.approval_timeout_minutes * 60
            )
            approval_granted = reply == "approved"
            send_telegram_message(
                config,
                f"[{run_date}] 승인 결과: {reply} — "
                + ("주문을 실행합니다." if approval_granted else "주문 없이 종료합니다."),
            )

        summary_lines = [f"[{run_date}] 자동매매 결과{' (드라이런)' if dry_run else ''}"]
        for r in risk_results:
            record_order(conn, run_date, r.ticker, r.action, r.amount_usd, r.approved, r.reason)
            summary_lines.append(
                f"- {r.ticker} {r.action}: {'승인' if r.approved else '거부'} ({r.reason})"
            )

            if not r.approved or dry_run or not approval_granted:
                continue

            if has_order_been_placed(conn, run_date, r.ticker, r.action):
                summary_lines.append("  이미 주문 처리됨 - 재실행에서 건너뜀")
                continue

            client_order_id = f"{run_date}-{r.ticker}-{r.action}"
            result = None
            try:
                if r.action == "BUY":
                    result = toss.place_market_buy(r.ticker, r.amount_usd, client_order_id)
                elif r.action == "SELL":
                    quantity = toss.get_holding_quantity(r.ticker)
                    if float(quantity) > 0:
                        result = toss.place_market_sell(r.ticker, quantity, client_order_id)
            except TossApiError as exc:
                # Order was never confirmed placed. Per design doc, a Toss API
                # auth/connection failure aborts the whole run (unlike a
                # per-ticker analysis failure, which is isolated) rather than
                # producing N confusing "주문 실패" lines for what's really
                # one broker outage.
                summary_lines.append(
                    f"[{run_date}] 토스 API 연결 실패 - 나머지 주문을 중단합니다: {exc}"
                )
                break
            except Exception as exc:
                summary_lines.append(f"  주문 실패: {exc}")
                continue

            if result is None:
                continue

            try:
                mark_order_placed(conn, run_date, r.ticker, r.action, result["orderId"])
            except Exception as exc:
                # Order WAS placed (real money moved) but the local ledger
                # write failed — must be visually distinct from a normal
                # rejection/failure since same-day retry won't happen
                # (mark_run_completed still fires below).
                summary_lines.append(
                    f"[{run_date}] {r.ticker} 주문은 체결됐으나 로컬 기록 실패 "
                    f"(order_id={result['orderId']}): {exc} - 수동 확인 필요"
                )

        send_telegram_message(config, "\n".join(summary_lines))
        ping_healthcheck(config)
        mark_run_completed(conn, run_date)

    except Exception as exc:
        send_telegram_message(config, f"[{run_date}] 실행 중 예외 발생: {exc}")
        ping_healthcheck(config, "/fail")
        raise
    finally:
        if conn:
            conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
