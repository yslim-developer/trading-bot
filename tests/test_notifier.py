from types import SimpleNamespace

import requests

from orchestrator.notifier import send_telegram_message


def make_config():
    return SimpleNamespace(telegram_bot_token="tok", telegram_chat_id="chat-1")


def test_send_telegram_message_posts_expected_payload(monkeypatch):
    calls = []

    def fake_post(url, data, timeout):
        calls.append((url, data, timeout))

    monkeypatch.setattr(requests, "post", fake_post)

    send_telegram_message(make_config(), "hello")

    assert calls == [
        ("https://api.telegram.org/bottok/sendMessage", {"chat_id": "chat-1", "text": "hello"}, 10)
    ]


def test_send_telegram_message_swallows_request_exception(monkeypatch):
    def fake_post(url, data, timeout):
        raise requests.RequestException("network down")

    monkeypatch.setattr(requests, "post", fake_post)

    send_telegram_message(make_config(), "hello")  # 예외가 밖으로 전파되지 않아야 함
