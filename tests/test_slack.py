"""Tests for the read-only Slack reader."""

from __future__ import annotations

import pytest

from ai_reply_copilot.send import send_slack
from ai_reply_copilot.slack import (
    FakeSlackClient,
    SlackClient,
    SlackError,
    get_conversation_context,
    get_thread_context,
    list_conversations,
    posting_client,
    read_client,
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


def test_get_thread_context_parent_first(fake_slack):
    # conversations.replies returns parent-first chronological order.
    fake_slack._responses["conversations.replies"] = lambda p: {
        "ok": True,
        "messages": [
            {"ts": "1.0", "text": "Can you review the deck?", "user": "U2"},
            {"ts": "2.0", "text": "on it", "user": "U_ME"},
        ],
    }
    messages = get_thread_context(fake_slack, "C1", "1.0")
    assert [m.text for m in messages] == ["Can you review the deck?", "on it"]
    assert messages[0].is_from_me is False
    assert messages[0].sender == "Alex"
    assert messages[1].is_from_me is True


def test_send_slack_threads_reply(fake_slack):
    fake_slack._responses["chat.postMessage"] = {"ok": True, "ts": "9.9"}
    result = send_slack(fake_slack, "C1", "sounds good", thread_ts="1.0")
    assert result.detail == "sent (ts=9.9)"
    method, payload = fake_slack.posted[-1]
    assert method == "chat.postMessage"
    assert payload["thread_ts"] == "1.0"


def test_send_slack_without_thread_omits_thread_ts(fake_slack):
    fake_slack._responses["chat.postMessage"] = {"ok": True, "ts": "9.9"}
    send_slack(fake_slack, "C1", "hi")
    _, payload = fake_slack.posted[-1]
    assert "thread_ts" not in payload


# --- "post as you", never as the bot -------------------------------------------


def test_posting_client_refuses_to_fall_back_to_the_bot_token(monkeypatch):
    # Regression: posting used to silently degrade to SLACK_BOT_TOKEN when the
    # user token was missing, so replies went out as the bot, not as you.
    monkeypatch.delenv("SLACK_USER_TOKEN", raising=False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-bot")
    with pytest.raises(SlackError, match="SLACK_USER_TOKEN"):
        posting_client()


def test_posting_client_uses_the_user_token(monkeypatch):
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-me")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-bot")
    assert posting_client().token == "xoxp-me"


def test_explicit_none_token_does_not_silently_use_the_bot_token(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-bot")
    with pytest.raises(SlackError, match="refusing to fall back"):
        SlackClient(token=None, token_name="SLACK_USER_TOKEN")


def test_read_client_prefers_the_user_token(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-bot")
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-me")
    assert read_client().token == "xoxp-me"
    monkeypatch.delenv("SLACK_USER_TOKEN")
    assert read_client().token == "xoxb-bot"


# --- listing conversations must not lie about coverage --------------------------


def _many_channels(count, *, history_error=None):
    channels = [{"id": f"C{i}", "name": f"chan{i}", "is_im": False} for i in range(count)]

    def history(params):
        if history_error and params["channel"] == "C1":
            return {"ok": False, "error": history_error}
        return {"ok": True, "messages": [{"ts": "1000.0", "text": "hi", "user": "U2"}]}

    return FakeSlackClient({
        "conversations.list": {"ok": True, "channels": channels},
        "conversations.history": history,
        "users.info": {"ok": True, "user": {"profile": {"display_name": "Alex"}}},
    })


def test_the_probe_count_is_bounded_and_the_shortfall_is_reported():
    # conversations.history is Tier 3 (~50/min) and there is one call per channel,
    # so probing a real workspace rate-limits within seconds. Bounding it is fine;
    # doing so *silently* is not — the user would read a truncated list as complete.
    from ai_reply_copilot import slack as slack_module

    client = _many_channels(120)
    notes = []
    slack_module.list_conversations(client, limit=20, max_probes=10, report=notes.append)
    assert any("120" in n and "10" in n for n in notes)


def test_no_report_when_coverage_is_complete():
    notes = []
    list_conversations(_many_channels(3), report=notes.append)
    assert notes == []


def test_an_unreadable_channel_is_skipped_and_counted():
    notes = []
    conversations = list_conversations(
        _many_channels(3, history_error="not_in_channel"), report=notes.append
    )
    assert [c.channel_id for c in conversations] == ["C2", "C0"]  # C1 skipped
    assert any("not readable" in n for n in notes)


def test_a_rate_limit_is_not_swallowed_channel_by_channel():
    # THE bug: every per-channel failure was `continue`d, so a 429 silently
    # dropped channels and "your most recent conversation" could quietly be the
    # wrong one. A rate limit is about the request, not about that one channel.
    with pytest.raises(SlackError):
        list_conversations(_many_channels(3, history_error="ratelimited"))


def test_channel_pagination_follows_the_cursor():
    # The old code asked for 200 with no cursor, so a bigger workspace silently
    # lost everything past the first page.
    pages = iter([
        {"ok": True, "channels": [{"id": "C0", "name": "a", "is_im": False}],
         "response_metadata": {"next_cursor": "abc"}},
        {"ok": True, "channels": [{"id": "C1", "name": "b", "is_im": False}],
         "response_metadata": {"next_cursor": ""}},
    ])
    client = FakeSlackClient({
        "conversations.list": lambda params: next(pages),
        "conversations.history": lambda p: {
            "ok": True, "messages": [{"ts": "1000.0", "text": "hi", "user": "U2"}]
        },
    })
    assert {c.channel_id for c in list_conversations(client)} == {"C0", "C1"}
