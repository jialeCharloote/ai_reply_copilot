"""Tests for the read-only Slack reader."""

from __future__ import annotations

import pytest

from ai_reply_copilot.slack import (
    FakeSlackClient,
    SlackClient,
    SlackError,
    get_conversation_context,
    list_conversations,
    slack_ts_to_datetime,
)

_HISTORY = {
    "C1": [
        {"ts": "1000.0001", "text": "Deploy tonight?", "user": "U2"},
        {"ts": "1000.0002", "text": "Yes, after review", "user": "U_ME"},
    ],
    "D1": [
        {"ts": "2000.0001", "text": "lunch?", "user": "U2"},
    ],
}

_USERS = {"U2": "Alex", "U_ME": "Charlotte"}


def _history_handler(params):
    channel = params["channel"]
    limit = int(params.get("limit", 20))
    # Slack returns messages newest-first.
    newest_first = list(reversed(_HISTORY.get(channel, [])))
    return {"ok": True, "messages": newest_first[:limit]}


def _users_info_handler(params):
    uid = params["user"]
    return {"ok": True, "user": {"profile": {"display_name": _USERS.get(uid, uid)}}}


@pytest.fixture
def fake_slack():
    return FakeSlackClient(
        {
            "conversations.list": {
                "ok": True,
                "channels": [
                    {"id": "C1", "name": "general", "is_im": False},
                    {"id": "D1", "is_im": True, "user": "U2"},
                ],
            },
            "conversations.history": _history_handler,
            "users.info": _users_info_handler,
        }
    )


def test_slack_ts_to_datetime():
    dt = slack_ts_to_datetime("1000.0001")
    assert dt is not None
    assert dt.year == 1970
    assert slack_ts_to_datetime(None) is None
    assert slack_ts_to_datetime("nope") is None


def test_list_conversations_sorted_and_titled(fake_slack):
    conversations = list_conversations(fake_slack)
    assert len(conversations) == 2
    # D1 has the most recent message (ts 2000 > 1000).
    assert conversations[0].channel_id == "D1"
    assert conversations[0].is_im is True
    assert conversations[0].title == "DM: Alex"
    assert conversations[1].title == "#general"


def test_list_conversations_respects_limit(fake_slack):
    assert len(list_conversations(fake_slack, limit=1)) == 1


def test_get_conversation_context_chronological_and_from_me(fake_slack):
    messages = get_conversation_context(fake_slack, "C1")
    assert [m.text for m in messages] == ["Deploy tonight?", "Yes, after review"]
    assert messages[0].is_from_me is False
    assert messages[0].sender == "Alex"
    assert messages[1].is_from_me is True


def test_get_conversation_context_skips_empty(fake_slack):
    fake_slack._responses["conversations.history"] = lambda p: {
        "ok": True,
        "messages": [{"ts": "1.0", "text": "", "user": "U2"}, {"ts": "2.0", "text": "hi", "user": "U2"}],
    }
    messages = get_conversation_context(fake_slack, "C1")
    assert [m.text for m in messages] == ["hi"]


def test_client_requires_token(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    client = SlackClient()
    with pytest.raises(SlackError):
        client.call("auth.test")


def test_api_error_raises(fake_slack):
    fake_slack._responses["conversations.history"] = {"ok": False, "error": "channel_not_found"}
    with pytest.raises(SlackError):
        get_conversation_context(fake_slack, "C1")
