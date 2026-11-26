import argparse
import os
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from orchestrator.config import load_config
from orchestrator.notifier import (
    ping_healthcheck,
    send_telegram_message,
    wait_for_telegram_reply,
)
from orchestrator import regime as regime_module
from orchestrator.risk_engine import AccountState, Decision, evaluate_decisions
from orchestrator.state import (
    count_consecutive_signal,
    get_current_regime,
    has_order_been_placed,
    has_run_completed_today,
    init_db,
    mark_order_placed_with_retry,
    mark_run_completed,
    mark_run_started,
    record_holdings,
    record_order,
    record_regime_signal,
)
from orchestrator.toss_client import TossApiError, TossClient, daily_pnl_pct_by_ticker
from orchestrator.trading_agents_client import (
    FallbackExhaustedError,
    QuotaExhaustedError,
    run_analysis,
)

STOP_FILE = "STOP"
DB_PATH = "data/trading.db"
SELL_SETTLE_SECONDS = 10
# TradingAgents 5단계 등급 중 BUY 로 매핑되는 것들의 배분 가중치 (그 외는 1)
BUY_WEIGHTS = {"buy": 2.0, "overweight": 1.0}


def _wait_until_trade_time(config):
    """분석이 일찍 끝났으면 trade_time_et(미국 동부 HH:MM)까지 기다린다. 이미 지났으면 바로 진행."""
    if not getattr(config, "trade_time_et", ""):
        return
    hour, minute = map(int, config.trade_time_et.split(":"))
    now = datetime.now(ZoneInfo("America/New_York"))
    delay = (now.replace(hour=hour, minute=minute, second=0, microsecond=0) - now).total_seconds()
    if 0 < delay < 8 * 3600:
        time.sleep(delay)


def _process_order(conn, toss, run_date, r, execute, summary_lines):
    """RiskDecision 하나를 기록하고 (execute 면) 주문한다. 실제 주문이 나갔으면 True."""
    record_order(conn, run_date, r.ticker, r.action, r.amount_usd, r.approved, r.reason)
    amount = f" ${r.amount_usd:,.2f}" if r.action == "BUY" and r.approved else ""
    summary_lines.append(
        f"- {r.ticker} {r.action}{amount}: {'승인' if r.approved else '거부'} ({r.reason})"
    )
    if r.reasoning:
        summary_lines.append(f"  이유: {r.reasoning}")

    if not r.approved or not execute:
        return False

    if has_order_been_placed(conn, run_date, r.ticker, r.action):
        summary_lines.append("  이미 주문 처리됨 - 재실행에서 건너뜀")
        return False

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
        return False
    except Exception as exc:
        summary_lines.append(f"  주문 실패: {exc}")
        return False

    if result is None:
        return False

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
    return True


def _handle_regime(conn, toss, config, run_date, account, dry_run):
    """8.5단계: 레짐 판정 + 스위치.

    반환: True 이면 이 실행은 레짐 스위치로 완결됨 (롱 분석/주문 단계를 건너뜀).
          False 이면 호출부가 기존 롱 흐름을 계속 진행.
    """
    rs = config.regime_switch
    prev_regime, pending = get_current_regime(conn)

    data = regime_module.fetch_regime_data(rs.proxy_ticker, rs.sma_long, rs.sma_short)

    if data is None:
        new_regime, transitioned = prev_regime, False
        lines = [f"[{run_date}] 레짐 판정 스킵 (데이터 조회 실패) — 이전 레짐({prev_regime}) 유지"]
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
        record_holdings(conn, run_date, toss.get_holdings())

        if config.regime_switch.enabled:
            if _handle_regime(conn, toss, config, run_date, account, dry_run):
                ping_healthcheck(config)
                if not dry_run:
                    mark_run_completed(conn, run_date)
                return

        decisions = []
        for ticker in config.watchlist:
            try:
                action, reasoning, raw_decision = run_analysis(ticker, run_date)
            except FallbackExhaustedError:
                send_telegram_message(
                    config,
                    f"[{run_date}] {ticker} 분석 실패 — Gemini 한도 초과 + Claude/groq 폴백도 모두 실패"
                    " (Gemini 한도가 내일 리셋돼도 재발 가능, 폴백 자체 점검 필요)",
                )
                continue
            except QuotaExhaustedError:
                send_telegram_message(
                    config,
                    f"[{run_date}] {ticker} 분석 실패 — Gemini 일일 요청 한도 초과 (내일 자동 리셋됨)",
                )
                continue
            except Exception as exc:
                send_telegram_message(config, f"[{run_date}] {ticker} 분석 실패: {exc}")
                continue
            decisions.append(
                Decision(
                    ticker=ticker, action=action, reasoning=reasoning,
                    weight=BUY_WEIGHTS.get(str(raw_decision).strip().lower(), 1.0),
                )
            )

        # 분석은 장 시작 전에 미리 돌리고, 매매는 trade_time_et(장 시작 직후)에 한다.
        _wait_until_trade_time(config)
        # 장 시작 후 종목별 당일 손익률로 서킷브레이커를 판단 (evaluate_decisions)
        account = toss.get_account_state(config.watchlist)

        # 매도를 먼저 내고, 그 뒤 USD 현금으로 매수를 배분한다 (매도 대금도 매수에 쓰기 위해).
        sell_side = [d for d in decisions if d.action != "BUY"]
        buy_side = [d for d in decisions if d.action == "BUY"]
        sell_results = evaluate_decisions(sell_side, account, config.risk_limits)

        approval_granted = True
        if not dry_run and config.approval_enabled and (
            buy_side or any(r.approved for r in sell_results)
        ):
            proposal_lines = [f"[{run_date}] 제안 주문 (승인 대기)"]
            for r in sell_results:
                if r.approved:
                    proposal_lines.append(f"- {r.ticker} {r.action}")
                    if r.reasoning:
                        proposal_lines.append(f"  이유: {r.reasoning}")
            for d in buy_side:
                proposal_lines.append(f"- {d.ticker} BUY (가중치 {d.weight:g}, 금액은 매도 후 USD 현금으로 배분)")
                if d.reasoning:
                    proposal_lines.append(f"  이유: {d.reasoning}")
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
        execute = not dry_run and approval_granted
        sells_placed = [_process_order(conn, toss, run_date, r, execute, summary_lines) for r in sell_results]

        if buy_side:
            if any(sells_placed):
                time.sleep(SELL_SETTLE_SECONDS)  # 시장가 매도 체결/매수가능금액 반영 대기
            holdings = toss.get_holdings()
            cash_usd = toss.get_purchasing_power_usd()
            buy_account = AccountState(
                positions_usd={h["symbol"]: float(h["marketValue"]["amount"]) for h in holdings},
                daily_pnl_pct=account.daily_pnl_pct,
                cash_usd=cash_usd,
                daily_pnl_pct_by_ticker=daily_pnl_pct_by_ticker(holdings),
            )
            equity = sum(buy_account.positions_usd.values()) + cash_usd
            summary_lines.append(
                f"매수 배분: USD 현금 ${cash_usd:,.2f} x {config.risk_limits.buy_cash_ratio:.0%}"
                f" / 종목당 상한 ${equity * config.risk_limits.max_position_pct / 100:,.2f}"
                f" (총자산 ${equity:,.2f}의 {config.risk_limits.max_position_pct:g}%)"
            )
            for r in evaluate_decisions(buy_side, buy_account, config.risk_limits):
                _process_order(conn, toss, run_date, r, execute, summary_lines)

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
