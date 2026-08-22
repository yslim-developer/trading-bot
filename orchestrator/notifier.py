import time

import requests

APPROVE_WORDS = {"승인", "approve", "yes", "y"}
REJECT_WORDS = {"거부", "reject", "no", "n"}


def send_telegram_message(config, text):
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    try:
        requests.post(
            url, data={"chat_id": config.telegram_chat_id, "text": text}, timeout=10
        )
    except requests.RequestException as exc:
        print(f"텔레그램 알림 전송 실패: {type(exc).__name__}")


def ping_healthcheck(config, suffix=""):
    url = getattr(config, "healthcheck_ping_url", "")
    if not url:
        return
    try:
        requests.get(f"{url}{suffix}", timeout=10)
    except requests.RequestException as exc:
        print(f"헬스체크 핑 전송 실패: {type(exc).__name__}")


def wait_for_telegram_reply(config, timeout_seconds, poll_interval_seconds=5):
    """텔레그램 답장을 기다려 승인/거부를 판단한다.

    폴링 시작 시점에 이미 쌓여 있던 메시지(백로그)는 무시하고, 그 이후 도착한
    메시지만 승인/거부 판단에 사용한다.
    """
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/getUpdates"
    offset = None
    first_poll = True
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        params = {"timeout": 0}
        if offset is not None:
            params["offset"] = offset
        try:
            resp = requests.get(url, params=params, timeout=10)
            updates = resp.json().get("result", [])
        except requests.RequestException:
            time.sleep(poll_interval_seconds)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            if first_poll:
                continue

            message = update.get("message", {})
            chat_id = str(message.get("chat", {}).get("id", ""))
            if chat_id != str(config.telegram_chat_id):
                continue

            text = message.get("text", "").strip().lower()
            if text in APPROVE_WORDS:
                return "approved"
            if text in REJECT_WORDS:
                return "rejected"

        first_poll = False
        time.sleep(poll_interval_seconds)

    return "timeout"
