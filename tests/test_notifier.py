from types import SimpleNamespace

import requests

from orchestrator.notifier import ping_healthcheck, send_telegram_message


def make_config(healthcheck_ping_url=""):
    return SimpleNamespace(
        telegram_bot_token="tok",
        telegram_chat_id="chat-1",
        healthcheck_ping_url=healthcheck_ping_url,
    )


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


def test_ping_healthcheck_does_nothing_without_url(monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append((a, k)))

    ping_healthcheck(make_config(healthcheck_ping_url=""))

    assert calls == []


def test_ping_healthcheck_sends_request_with_suffix(monkeypatch):
    calls = []

    def fake_get(url, timeout):
        calls.append((url, timeout))

    monkeypatch.setattr(requests, "get", fake_get)

    ping_healthcheck(make_config(healthcheck_ping_url="https://hc-ping.com/abc123"), "/start")

    assert calls == [("https://hc-ping.com/abc123/start", 10)]


def test_ping_healthcheck_swallows_request_exception(monkeypatch):
    def fake_get(url, timeout):
        raise requests.RequestException("network down")

    monkeypatch.setattr(requests, "get", fake_get)

    ping_healthcheck(make_config(healthcheck_ping_url="https://hc-ping.com/abc123"))  # 예외 전파 안 됨
