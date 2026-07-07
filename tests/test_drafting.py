"""Tests for the surface-agnostic drafting engine."""

from __future__ import annotations

import json

from ai_reply_copilot.drafting import (
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
    assert "中英双语" in user


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
