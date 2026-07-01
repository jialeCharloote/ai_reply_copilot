"""Tests for prompt building, response parsing, and reply generation."""

from __future__ import annotations

import json

import pytest

from ai_reply_copilot.generate import (
    GenerationError,
    generate_replies,
    parse_response,
)
from ai_reply_copilot.imessage import Message
from ai_reply_copilot.llm import FakeClient, LLMError, get_client
from ai_reply_copilot.prompts import (
    StyleProfile,
    build_system_prompt,
    build_user_prompt,
)


def _messages():
    return [
        Message(text="Can you send the report today?", is_from_me=False, timestamp=None, sender="boss"),
        Message(text="Working on it", is_from_me=True, timestamp=None, sender=None),
    ]


def _fake_json(understanding="They want the report today.", candidates=None):
    candidates = candidates or ["On it, sending by 3pm.", "Yes—will have it to you EOD.", "Almost done, expect it shortly."]
    return json.dumps({"understanding": understanding, "candidates": candidates})


def test_build_system_prompt_mentions_count():
    prompt = build_system_prompt(num_candidates=3)
    assert "exactly 3 candidates" in prompt
    assert "JSON" in prompt


def test_build_user_prompt_includes_intent_tone_draft():
    prompt = build_user_prompt(
        context="[..] boss: hi",
        intent="decline",
        tone="professional",
        style=StyleProfile(use_emoji=False, language="English"),
        draft="I can't make it",
    )
    assert "decline" in prompt
    assert "professional" in prompt
    assert "no emoji" in prompt
    assert "English" in prompt
    assert "I can't make it" in prompt


def test_build_user_prompt_defaults_without_options():
    prompt = build_user_prompt(context="[..] boss: hi")
    assert "Intent:" in prompt


def test_parse_response_plain_json():
    result = parse_response(_fake_json())
    assert result.understanding == "They want the report today."
    assert len(result.candidates) == 3


def test_parse_response_fenced_json():
    raw = "```json\n" + _fake_json() + "\n```"
    result = parse_response(raw)
    assert len(result.candidates) == 3


def test_parse_response_with_surrounding_prose():
    raw = "Sure! Here you go:\n" + _fake_json() + "\nHope that helps."
    result = parse_response(raw)
    assert result.candidates[0].startswith("On it")


def test_parse_response_strips_empty_candidates():
    raw = json.dumps({"understanding": "x", "candidates": ["Real reply", "", "  "]})
    result = parse_response(raw)
    assert result.candidates == ["Real reply"]


def test_parse_response_no_json_raises():
    with pytest.raises(GenerationError):
        parse_response("I could not help with that.")


def test_parse_response_missing_candidates_raises():
    with pytest.raises(GenerationError):
        parse_response(json.dumps({"understanding": "x"}))


def test_generate_replies_uses_client():
    client = FakeClient(_fake_json())
    result = generate_replies(_messages(), client, intent="reply", tone="concise")
    assert len(result.candidates) == 3
    # The rendered context should have been passed to the model.
    system, user = client.calls[0]
    assert "report today" in user
    assert "concise" in user


def test_generate_replies_empty_context_raises():
    with pytest.raises(GenerationError):
        generate_replies([], FakeClient(_fake_json()))


def test_get_client_unknown_provider():
    with pytest.raises(LLMError):
        get_client("gemini")


def test_openai_client_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = get_client("openai")
    with pytest.raises(LLMError):
        client.complete("sys", "user")
