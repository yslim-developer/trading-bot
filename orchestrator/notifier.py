import requests


def send_telegram_message(config, text):
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    try:
        requests.post(
            url, data={"chat_id": config.telegram_chat_id, "text": text}, timeout=10
        )
    except requests.RequestException as exc:
        print(f"텔레그램 알림 전송 실패: {exc}")
