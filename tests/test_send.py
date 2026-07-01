"""Tests for the sending path (offline via injected runner / FakeSlackClient)."""

from __future__ import annotations

import pytest

from ai_reply_copilot.send import (
    SendError,
    build_imessage_applescript,
    send_imessage,
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
    assert result.detail == "sent via Messages"


def test_send_imessage_empty_raises():
    with pytest.raises(SendError):
        send_imessage("+1555", "   ")


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
