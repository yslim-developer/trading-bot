from types import SimpleNamespace
from unittest.mock import Mock

import requests

from orchestrator.notifier import (
    ping_healthcheck,
    send_telegram_message,
    wait_for_telegram_reply,
)


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


def make_updates_response(updates):
    resp = Mock()
    resp.json.return_value = {"ok": True, "result": updates}
    return resp


def make_update(update_id, chat_id, text):
    return {
        "update_id": update_id,
        "message": {"message_id": 1, "chat": {"id": chat_id}, "text": text},
    }


def test_wait_for_telegram_reply_discards_backlog_then_detects_approval(monkeypatch):
    calls = []

    responses = [
        make_updates_response([make_update(1, "chat-1", "이전에 온 메시지")]),  # 백로그, 무시돼야 함
        make_updates_response([make_update(2, "chat-1", "승인")]),
    ]

    def fake_get(url, params, timeout):
        calls.append(params)
        return responses.pop(0)

    monkeypatch.setattr(requests, "get", fake_get)

    result = wait_for_telegram_reply(
        make_config(), timeout_seconds=5, poll_interval_seconds=0.01
    )

    assert result == "approved"
    assert calls[1].get("offset") == 2  # 백로그 소비 후 offset 전진 확인


def test_wait_for_telegram_reply_detects_rejection(monkeypatch):
    responses = [
        make_updates_response([]),
        make_updates_response([make_update(1, "chat-1", "거부")]),
    ]
    monkeypatch.setattr(requests, "get", lambda url, params, timeout: responses.pop(0))

    result = wait_for_telegram_reply(
        make_config(), timeout_seconds=5, poll_interval_seconds=0.01
    )

    assert result == "rejected"


def test_wait_for_telegram_reply_ignores_unrelated_chat_and_unrecognized_text(monkeypatch):
    responses = [
        make_updates_response([]),  # 백로그 없음
        make_updates_response(
            [
                make_update(1, "other-chat", "승인"),  # 다른 채팅방, 무시
                make_update(2, "chat-1", "오늘 날씨 좋네요"),  # 인식 안 되는 텍스트, 무시
            ]
        ),
        make_updates_response([make_update(3, "chat-1", "yes")]),
    ]
    monkeypatch.setattr(requests, "get", lambda url, params, timeout: responses.pop(0))

    result = wait_for_telegram_reply(
        make_config(), timeout_seconds=5, poll_interval_seconds=0.01
    )

    assert result == "approved"


def test_wait_for_telegram_reply_times_out_without_response(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda url, params, timeout: make_updates_response([])
    )

    result = wait_for_telegram_reply(
        make_config(), timeout_seconds=0.03, poll_interval_seconds=0.01
    )

    assert result == "timeout"
