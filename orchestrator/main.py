import argparse
import os
import time
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


def _mark_order_placed_with_retry(
    conn, run_date, ticker, action, order_id, quantity=None, attempts=3, delay_seconds=0.2
):
    """mark_order_placed를 몇 번 재시도한다.

    체결 직후 로컬 DB 기록 실패는 대개 일시적(락 경합 등)이라, 몇 번의 짧은
    재시도로 조용히 성공하는 경우가 많다. 재시도가 모두 실패하면 마지막
    예외를 그대로 전파해 호출부가 "체결됐으나 로컬 기록 실패" 알림을 보내게 한다.
    """
    last_exc = None
    for attempt in range(attempts):
        try:
            mark_order_placed(conn, run_date, ticker, action, order_id, quantity=quantity)
            return
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
    raise last_exc


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

        if not toss.is_us_market_open_today():
            send_telegram_message(config, f"[{run_date}] 오늘은 미국 증시 휴장일 - 분석/주문을 건너뜁니다.")
            ping_healthcheck(config)
            mark_run_completed(conn, run_date)
            return

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
                action, reasoning = run_analysis(ticker, run_date)
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
            decisions.append(
                Decision(ticker=ticker, action=action, amount_usd=amount, reasoning=reasoning)
            )

        risk_results = evaluate_decisions(decisions, account, config.risk_limits)

        approval_granted = True
        if not dry_run and config.approval_enabled and any(r.approved for r in risk_results):
            proposal_lines = [f"[{run_date}] 제안 주문 (승인 대기)"]
            for r in risk_results:
                if r.approved:
                    proposal_lines.append(f"- {r.ticker} {r.action}: ${r.amount_usd:.2f}")
                    if r.reasoning:
                        proposal_lines.append(f"  이유: {r.reasoning}")
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
            if r.reasoning:
                summary_lines.append(f"  이유: {r.reasoning}")

            if not r.approved or dry_run or not approval_granted:
                continue

            if has_order_been_placed(conn, run_date, r.ticker, r.action):
                summary_lines.append("  이미 주문 처리됨 - 재실행에서 건너뜀")
                continue

            client_order_id = f"{run_date}-{r.ticker}-{r.action}"
            result = None
            sold_quantity = None
            try:
                if r.action == "BUY":
                    result = toss.place_market_buy(r.ticker, r.amount_usd, client_order_id)
                elif r.action == "SELL":
                    sold_quantity = toss.get_holding_quantity(r.ticker)
                    if float(sold_quantity) > 0:
                        result = toss.place_market_sell(r.ticker, sold_quantity, client_order_id)
            except TossApiError as exc:
                # Order was never confirmed placed. Isolated to this ticker so
                # one rejected/failed order (e.g. insufficient funds, invalid
                # symbol) doesn't block the remaining tickers' orders.
                summary_lines.append(f"  주문 실패 (토스 API 오류): {exc}")
                continue
            except Exception as exc:
                summary_lines.append(f"  주문 실패: {exc}")
                continue

            if result is None:
                continue

            try:
                _mark_order_placed_with_retry(
                    conn, run_date, r.ticker, r.action, result["orderId"],
                    quantity=sold_quantity if r.action == "SELL" else None,
                )
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
        if approval_granted:
            mark_run_completed(conn, run_date)
        else:
            send_telegram_message(
                config,
                f"[{run_date}] 승인되지 않아 오늘 다시 실행하면 재승인을 받을 수 있습니다.",
            )

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
