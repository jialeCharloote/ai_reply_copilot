"""Tests for the sending path (offline via injected runner / FakeSlackClient)."""

from __future__ import annotations

import pytest

from ai_reply_copilot.send import (
    SendError,
    build_imessage_applescript,
    build_imessage_chat_applescript,
    send_imessage,
    send_imessage_to_chat,
    send_slack,
)
from ai_reply_copilot.slack import FakeSlackClient


def test_build_applescript_escapes_quotes_and_backslashes():
    script = build_imessage_applescript('+1555', 'He said "hi"\\bye')
    assert '\\"hi\\"' in script
    assert "\\\\bye" in script
    assert "Messages" in script


def test_send_imessage_dry_run_does_not_run():
    calls = []
    result = send_imessage(
        "+1555", "hello", dry_run=True, runner=lambda s: calls.append(s)
    )
    assert result.dry_run is True
    assert calls == []
    assert "not sent" in result.detail


def test_send_imessage_invokes_runner():
    calls = []
    result = send_imessage("+1555", "hello", runner=lambda s: calls.append(s) or "")
    assert result.dry_run is False
    assert len(calls) == 1
    assert 'send "hello"' in calls[0]
    assert result.detail == "sent via Messages (iMessage)"
    assert "service type = iMessage" in calls[0]


def test_send_imessage_on_sms_addresses_the_sms_service():
    """A green-bubble contact has no iMessage account to be a participant of.

    Roughly half of a real chat.db is SMS, and every one of those sends used to
    be built against `service type = iMessage`.
    """
    calls = []
    result = send_imessage(
        "+1555", "hello", service="SMS", runner=lambda s: calls.append(s) or ""
    )
    assert "service type = SMS" in calls[0]
    assert "service type = iMessage" not in calls[0]
    assert result.detail == "sent via Messages (SMS)"


def test_unknown_service_falls_back_to_imessage():
    calls = []
    send_imessage("+1555", "hi", service="", runner=lambda s: calls.append(s) or "")
    assert "service type = iMessage" in calls[0]


def test_send_imessage_empty_raises():
    with pytest.raises(SendError):
        send_imessage("+1555", "   ")


def test_build_chat_applescript_targets_chat_by_guid():
    script = build_imessage_chat_applescript("iMessage;+;chat9999", "hi")
    assert 'chat id "iMessage;+;chat9999"' in script
    assert 'send "hi" to targetChat' in script


def test_send_imessage_to_chat_invokes_runner():
    calls = []
    result = send_imessage_to_chat(
        "iMessage;+;chat9999", "hey team", runner=lambda s: calls.append(s) or ""
    )
    assert result.dry_run is False
    assert len(calls) == 1
    assert "chat9999" in calls[0]
    assert result.detail == "sent via Messages"


def test_send_imessage_to_chat_empty_guid_raises():
    with pytest.raises(SendError):
        send_imessage_to_chat("", "hi")


def test_send_slack_dry_run():
    fake = FakeSlackClient({})
    result = send_slack(fake, "C1", "hi", dry_run=True)
    assert result.dry_run is True
    assert fake.posted == []


def test_send_slack_posts():
    fake = FakeSlackClient({"chat.postMessage": {"ok": True, "ts": "123.45"}})
    result = send_slack(fake, "C1", "hi")
    assert result.dry_run is False
    assert fake.posted == [("chat.postMessage", {"channel": "C1", "text": "hi"})]
    assert "123.45" in result.detail


def test_send_slack_empty_raises():
    with pytest.raises(SendError):
        send_slack(FakeSlackClient({}), "C1", "")
