"""Response handling for the Anthropic client.

This layer had almost no tests, and it was reading `content[0]["text"]` blind —
so a draft that was merely *cut off* surfaced to the user as "Invalid JSON from
model", blaming the model for a limit we had set, after billing them for it.
"""

from __future__ import annotations

import pytest

from ai_reply_copilot.llm import AnthropicClient, LLMError, _anthropic_text


def _text(body: str) -> dict:
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": body}]}


def test_a_normal_response_returns_its_text():
    assert _anthropic_text(_text("hello"), 4096) == "hello"


def test_a_truncated_reply_names_the_real_cause():
    """The failure that actually happens. Chinese costs ~1 token per character,
    so an understanding + open_points + N candidates ran past 1024 routinely."""
    resp = {"stop_reason": "max_tokens", "content": [{"type": "text", "text": '{"unders'}]}
    with pytest.raises(LLMError) as exc:
        _anthropic_text(resp, 1024)
    message = str(exc.value)
    assert "cut off" in message and "1024" in message
    assert "--num" in message  # tells the user what to actually do about it


def test_a_refusal_is_not_reported_as_a_bug():
    with pytest.raises(LLMError, match="declined"):
        _anthropic_text({"stop_reason": "refusal", "content": []}, 4096)


def test_a_leading_thinking_block_is_skipped():
    # content[0] is not guaranteed to be the text block.
    resp = {
        "stop_reason": "end_turn",
        "content": [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "the answer"},
        ],
    }
    assert _anthropic_text(resp, 4096) == "the answer"


def test_an_empty_content_list_raises_with_the_stop_reason():
    with pytest.raises(LLMError, match="no text content"):
        _anthropic_text({"stop_reason": "end_turn", "content": []}, 4096)


def test_max_tokens_has_headroom_for_the_default_candidates():
    # 1024 was not enough for 3 bilingual candidates plus open_points.
    assert AnthropicClient(api_key="k").max_tokens >= 2048


def test_a_missing_key_is_reported_before_any_network_call(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        AnthropicClient().complete("sys", "user")
