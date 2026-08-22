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


def test_wait_for_telegram_reply_does_not_treat_backlog_as_fresh_after_initial_network_failure(
    monkeypatch,
):
    # 첫 폴링이 네트워크 오류로 실패해도, 그다음 폴링에서 받은 응답은 여전히
    # "백로그"로 취급되어야 한다 (오래된 메시지를 오늘의 승인으로 오인하면 안 됨).
    call_count = {"n": 0}

    def fake_get(url, params, timeout):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise requests.RequestException("network blip")
        if call_count["n"] == 2:
            return make_updates_response([make_update(1, "chat-1", "승인")])  # 사실 백로그
        return make_updates_response([])  # offset 전진 이후 재조회, 더 이상 안 옴

    monkeypatch.setattr(requests, "get", fake_get)

    result = wait_for_telegram_reply(
        make_config(), timeout_seconds=0.05, poll_interval_seconds=0.01
    )

    assert result == "timeout"  # 백로그를 승인으로 오인하면 안 됨


def test_wait_for_telegram_reply_times_out_without_response(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda url, params, timeout: make_updates_response([])
    )

    result = wait_for_telegram_reply(
        make_config(), timeout_seconds=0.03, poll_interval_seconds=0.01
    )

    assert result == "timeout"
