"""Tests for the surface-agnostic drafting engine."""

from __future__ import annotations

import json

import pytest

from ai_reply_copilot.drafting import (
    SensitiveContentError,
    draft_from_context,
    draft_replies,
    load_context,
)
from ai_reply_copilot.llm import FakeClient
from ai_reply_copilot.prompts import StyleProfile
from ai_reply_copilot.slack import FakeSlackClient


def _client(messages, method="conversations.history"):
    return FakeSlackClient(
        {
            method: {"ok": True, "messages": messages},
            "users.info": {"ok": True, "user": {"profile": {"display_name": "Alex"}}},
        }
    )


def test_load_context_channel_flags_sensitive():
    client = _client(
        [{"ts": "1000.0001", "text": "send me the bank account number", "user": "U2"}]
    )
    context = load_context(client, channel="C1", message_ts="1000.0001")
    assert [m.text for m in context.messages] == ["send me the bank account number"]
    assert "financial" in context.sensitive_categories
    # Replying to a top-level message threads the reply off that message.
    assert context.send_target.channel == "C1"
    assert context.send_target.thread_ts == "1000.0001"


def test_load_context_uses_thread_and_reply_target():
    client = _client(
        [
            {"ts": "1.0", "text": "are we on for 3pm?", "user": "U2"},
            {"ts": "2.0", "text": "checking", "user": "U_ME"},
        ],
        method="conversations.replies",
    )
    context = load_context(client, channel="C1", message_ts="2.0", thread_ts="1.0")
    assert [m.text for m in context.messages] == ["are we on for 3pm?", "checking"]
    assert context.sensitive_categories == []
    assert context.send_target.thread_ts == "1.0"


def test_draft_from_context_passes_style_through():
    fake = FakeClient(
        json.dumps({"understanding": "scheduling", "candidates": ["Yes!", "Sure", "On it"]})
    )
    messages = load_context(
        _client([{"ts": "1.0", "text": "3pm still good?", "user": "U2"}]),
        channel="C1",
    ).messages
    style = StyleProfile(formality="casual", use_emoji=False, language="中英双语")
    suggestion = draft_from_context(messages, client=fake, style=style)
    assert suggestion.candidates == ["Yes!", "Sure", "On it"]
    # The style profile reached the model's user prompt.
    _system, user = fake.calls[-1]
    # Style still reaches the prompt, but the language does not ride along on it:
    # it is resolved per conversation and passed as an explicit "Reply language".
    assert "formality: casual" in user
    assert "no emoji" in user


def test_draft_replies_end_to_end_with_fakes():
    client = _client(
        [{"ts": "1.0", "text": "can you jump on a call?", "user": "U2"}],
        method="conversations.replies",
    )
    fake = FakeClient(
        json.dumps({"understanding": "wants a call", "candidates": ["Sure, calling now", "Give me 5", "Can we async?"]})
    )
    result = draft_replies(
        client, channel="C1", message_ts="1.0", thread_ts="1.0", llm_client=fake, num=3
    )
    assert result.understanding == "wants a call"
    assert len(result.candidates) == 3
    assert result.send_target.thread_ts == "1.0"
    assert result.sensitive_categories == []


def test_draft_replies_refuses_sensitive_context_by_default():
    # This convenience path cannot prompt anyone, so it must not quietly upload.
    client = _client([{"ts": "1.0", "text": "my password is hunter2", "user": "U2"}])
    llm = FakeClient(json.dumps({"understanding": "u", "candidates": ["a"]}))
    with pytest.raises(SensitiveContentError, match="credentials"):
        draft_replies(client, channel="C1", llm_client=llm)
    assert llm.calls == []


def test_draft_replies_proceeds_when_explicitly_allowed():
    client = _client([{"ts": "1.0", "text": "my password is hunter2", "user": "U2"}])
    llm = FakeClient(json.dumps({"understanding": "u", "candidates": ["a"]}))
    result = draft_replies(client, channel="C1", llm_client=llm, allow_sensitive=True)
    assert result.candidates == ["a"]
    assert result.sensitive_categories == ["credentials"]


def test_draft_replies_honours_a_language_locked_to_the_channel(tmp_path, monkeypatch):
    # draft_replies used to drop the target, so a channel's locked language was
    # silently ignored and the thread got re-detected on every run.
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    from ai_reply_copilot.storage import set_conversation_language

    client = _client([{"ts": "1.0", "text": "Can you review the deck?", "user": "U2"}])
    llm = FakeClient(json.dumps({"understanding": "u", "open_points": ["p"], "candidates": ["a"]}))

    result = draft_replies(client, channel="C1", llm_client=llm)
    assert "Reply language: English" in llm.calls[-1][1]  # detected
    assert result.open_points == ["p"]

    set_conversation_language("slack", "C1", "中文")
    result = draft_replies(client, channel="C1", llm_client=llm)
    assert "Reply language: 中文" in llm.calls[-1][1]
    assert result.language == "中文"


def test_the_learned_voice_can_be_suppressed_by_the_caller(tmp_path, monkeypatch):
    # draft_from_context used to call load_voice() unconditionally, so the Slack
    # app uploaded the user's verbatim past messages on every draft — including
    # when replying to a different person — with no way to opt out.
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    from ai_reply_copilot.storage import save_voice
    from ai_reply_copilot.voice import learn_voice

    save_voice(learn_voice(["ok 我看下 deck，等下 sync 一下", "sounds good — 我周四之前确认"]))

    messages = load_context(
        _client([{"ts": "1.0", "text": "can you review the deck?", "user": "U2"}]),
        channel="C1",
    ).messages

    on = FakeClient(json.dumps({"understanding": "u", "candidates": ["a"]}))
    draft_from_context(messages, client=on)
    assert "How the user actually writes" in on.calls[-1][1]

    off = FakeClient(json.dumps({"understanding": "u", "candidates": ["a"]}))
    draft_from_context(messages, client=off, use_voice=False)
    assert "How the user actually writes" not in off.calls[-1][1]
