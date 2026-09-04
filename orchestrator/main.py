import argparse
import os
from datetime import date

from orchestrator.config import load_config
from orchestrator.notifier import (
    ping_healthcheck,
    send_telegram_message,
    wait_for_telegram_reply,
)
from orchestrator import regime as regime_module
from orchestrator.risk_engine import Decision, evaluate_decisions
from orchestrator.state import (
    count_consecutive_signal,
    get_current_regime,
    has_order_been_placed,
    has_run_completed_today,
    init_db,
    is_circuit_breaker_triggered_today,
    mark_circuit_breaker_triggered,
    mark_order_placed_with_retry,
    mark_run_completed,
    mark_run_started,
    record_order,
    record_regime_signal,
)
from orchestrator.toss_client import TossApiError, TossClient
from orchestrator.trading_agents_client import QuotaExhaustedError, run_analysis

STOP_FILE = "STOP"
DB_PATH = "data/trading.db"


def _handle_regime(conn, toss, config, run_date, account, dry_run):
    """8.5단계: 레짐 판정 + 스위치.

    반환: True 이면 이 실행은 레짐 스위치로 완결됨 (롱 분석/주문 단계를 건너뜀).
          False 이면 호출부가 기존 롱 흐름을 계속 진행.
    """
    rs = config.regime_switch
    prev_regime, pending = get_current_regime(conn)

    data = regime_module.fetch_regime_data(rs.proxy_ticker, rs.sma_long, rs.sma_short)

    if data is None:
        send_telegram_message(
            config,
            f"[{run_date}] 레짐 판정 스킵 (데이터 조회 실패) — 이전 레짐({prev_regime}) 유지",
        )
        new_regime, transitioned = prev_regime, False
        lines = []
    else:
        raw_off, raw_on = regime_module.compute_raw_signals(data, rs.vix_threshold)
        record_regime_signal(
            conn, run_date, raw_off, raw_on,
            data.qqq_close, data.sma200, data.sma50, data.vix,
        )
        off_streak = count_consecutive_signal(conn, run_date, "risk_off")
        on_streak = count_consecutive_signal(conn, run_date, "risk_on")
        new_regime, transitioned = regime_module.evaluate_regime(
            prev_regime, off_streak, on_streak, rs.confirm_days
        )
        vix_str = f"{data.vix:.1f}" if data.vix is not None else "N/A"
        head = f"[{run_date}] 레짐: {new_regime}"
        if transitioned:
            head += f" (전환: {prev_regime}→{new_regime})"
        lines = [head, f"  QQQ {data.qqq_close:.0f} / 200일선 {data.sma200:.0f} / VIX {vix_str}"]

    if new_regime == "risk_off":
        lines += regime_module.execute_switch_to_off(
            conn, toss, config, run_date, config.watchlist, account.positions_usd,
            dry_run, transitioned, pending,
        )
        send_telegram_message(config, "\n".join(lines))
        return True

    if transitioned:  # risk_off -> risk_on
        regime_module.execute_switch_to_on(conn, toss, config, run_date, dry_run, lines)
    if lines:
        send_telegram_message(config, "\n".join(lines))
    return False


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

        if config.regime_switch.enabled:
            if _handle_regime(conn, toss, config, run_date, account, dry_run):
                ping_healthcheck(config)
                if not dry_run:
                    mark_run_completed(conn, run_date)
                return

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
                mark_order_placed_with_retry(
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
