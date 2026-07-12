"""Tests for the end-to-end reply flow orchestration."""

from __future__ import annotations

import json

from ai_reply_copilot.flow import run_reply_flow
from ai_reply_copilot.llm import FakeClient
from ai_reply_copilot.models import Message
from ai_reply_copilot.send import SendResult


def _messages():
    return [Message(text="Dinner at 7?", is_from_me=False, timestamp=None, sender="Alex")]


def _llm():
    return FakeClient(
        json.dumps(
            {
                "understanding": "Alex proposes dinner at 7.",
                "candidates": ["Sounds good!", "Can we do 8?", "Rain check?"],
            }
        )
    )


def _scripted_prompt(answers):
    it = iter(answers)
    return lambda _msg: next(it)


def _recording_send():
    sent = []

    def send_fn(text, dry_run):
        result = SendResult("imessage", "+1555", text, dry_run, "ok")
        sent.append(result)
        return result

    return send_fn, sent


def test_flow_pick_and_send():
    send_fn, sent = _recording_send()
    prompt = _scripted_prompt(["1", "y"])
    out = []
    result = run_reply_flow(
        _messages(), _llm(), send_fn, prompt=prompt, output=out.append
    )
    assert result is not None
    assert sent[0].text == "Sounds good!"
    assert any("Understanding" in line for line in out)


def test_flow_edit_then_send():
    send_fn, sent = _recording_send()
    prompt = _scripted_prompt(["e2", "Let's do 8:30", "y"])
    result = run_reply_flow(
        _messages(), _llm(), send_fn, prompt=prompt, output=lambda _s: None
    )
    assert result is not None
    assert sent[0].text == "Let's do 8:30"


def test_flow_edit_empty_keeps_candidate():
    send_fn, sent = _recording_send()
    prompt = _scripted_prompt(["e3", "", "y"])
    run_reply_flow(_messages(), _llm(), send_fn, prompt=prompt, output=lambda _s: None)
    assert sent[0].text == "Rain check?"


def test_flow_cancel_at_pick():
    send_fn, sent = _recording_send()
    prompt = _scripted_prompt(["q"])
    result = run_reply_flow(
        _messages(), _llm(), send_fn, prompt=prompt, output=lambda _s: None
    )
    assert result is None
    assert sent == []


def test_flow_cancel_at_confirm():
    send_fn, sent = _recording_send()
    prompt = _scripted_prompt(["1", "n"])
    result = run_reply_flow(
        _messages(), _llm(), send_fn, prompt=prompt, output=lambda _s: None
    )
    assert result is None
    assert sent == []


def test_flow_invalid_choice():
    send_fn, sent = _recording_send()
    prompt = _scripted_prompt(["9"])
    result = run_reply_flow(
        _messages(), _llm(), send_fn, prompt=prompt, output=lambda _s: None
    )
    assert result is None
    assert sent == []


def test_flow_dry_run_skips_confirm():
    send_fn, sent = _recording_send()
    prompt = _scripted_prompt(["1"])  # no confirmation needed in dry-run
    result = run_reply_flow(
        _messages(), _llm(), send_fn, dry_run=True, prompt=prompt, output=lambda _s: None
    )
    assert result is not None
    assert sent[0].dry_run is True


def test_editing_prefills_the_draft_instead_of_making_you_retype_it():
    # The single most-used step of the core loop: "Edit the reply:" used to be an
    # empty line, so tweaking one word meant retyping 300 characters — which is
    # the reason to abandon the flow rather than fix it.
    send_fn, sent = _recording_send()
    seen = {}

    def fake_edit(prompt_text, initial):
        seen["initial"] = initial          # the candidate is handed to the editor
        return initial.replace("8", "9")   # …and the user tweaks it in place

    run_reply_flow(
        _messages(), _llm(), send_fn,
        prompt=_scripted_prompt(["e2", "y"]),
        output=lambda _s: None,
        edit=fake_edit,
    )
    assert seen["initial"] == "Can we do 8?"   # prefilled, not blank
    assert sent[0].text == "Can we do 9?"


def test_exit_cancels_instead_of_being_read_as_an_edit():
    # `choice.startswith("e")` swallowed "exit", which then failed to parse as an
    # index and reported "Invalid choice" instead of quitting.
    send_fn, sent = _recording_send()
    said = []
    result = run_reply_flow(
        _messages(), _llm(), send_fn,
        prompt=_scripted_prompt(["exit"]),
        output=said.append,
    )
    assert result is None
    assert sent == []
    assert "Cancelled." in said
    assert "Invalid choice." not in said
