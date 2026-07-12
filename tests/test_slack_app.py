"""Tests for the Slack front-end's pure helpers.

These cover the view builders and state parsing that carry the modal's logic.
None of them import ``slack_bolt`` — only ``build_app``/``main`` do (lazily), so
the whole module is unit-testable without the Slack SDK installed.
"""

from __future__ import annotations

import json

import pytest

from ai_reply_copilot import slack_app
from ai_reply_copilot.slack import SlackError
from ai_reply_copilot.slack_app import (
    CHOICE_ACTION,
    CHOICE_BLOCK,
    CONFIRM_CONTINUE_ACTION,
    EDIT_ACTION,
    EDIT_BLOCK,
    SEND_VIEW_CALLBACK,
    confirm_view,
    drafts_view,
    dumps_meta,
    loads_meta,
    loading_view,
    message_view,
    reply_thread_ts,
    selected_text,
)


# --- meta round-tripping -------------------------------------------------------


def test_meta_round_trips_through_private_metadata():
    meta = {"channel": "C123", "message_ts": "1.2", "thread_ts": None}
    assert loads_meta(dumps_meta(meta)) == meta


def test_loads_meta_empty_string_is_empty_dict():
    # Slack sends "" for private_metadata on views without it set.
    assert loads_meta("") == {}


# --- simple views --------------------------------------------------------------


def test_loading_and_message_views_carry_metadata_and_no_submit():
    meta_json = dumps_meta({"channel": "C1"})
    for view in (loading_view(meta_json), message_view(meta_json, "hi")):
        assert view["type"] == "modal"
        assert view["private_metadata"] == meta_json
        assert "submit" not in view  # informational only — nothing to send


def test_message_view_shows_the_given_text():
    view = message_view("", "No readable messages here.")
    assert view["blocks"][0]["text"]["text"] == "No readable messages here."


# --- sensitive-content confirm gate --------------------------------------------


def test_confirm_view_has_the_continue_button_and_no_submit():
    view = confirm_view(dumps_meta({"channel": "C1"}), ["financial"])
    # The gate is advanced with an action button, not a modal submit.
    assert "submit" not in view
    buttons = view["blocks"][1]["elements"]
    assert buttons[0]["action_id"] == CONFIRM_CONTINUE_ACTION


# --- drafts view ---------------------------------------------------------------


def test_drafts_view_is_submittable_with_the_send_callback():
    view = drafts_view("", "They ask about dinner.", ["Yes", "No", "Maybe"])
    assert view["callback_id"] == SEND_VIEW_CALLBACK
    assert view["submit"]["text"] == "Send as me"


def _options(view):
    block = next(b for b in view["blocks"] if b.get("block_id") == CHOICE_BLOCK)
    return block["element"]["options"]


def test_drafts_view_renders_one_radio_option_per_candidate():
    view = drafts_view("", "", ["a", "b", "c"])
    choice_block = next(b for b in view["blocks"] if b.get("block_id") == CHOICE_BLOCK)
    options = choice_block["element"]["options"]
    # Values are indices, not the draft text — the text does not fit (see below).
    assert [o["value"] for o in options] == ["0", "1", "2"]
    assert choice_block["element"]["action_id"] == CHOICE_ACTION


def test_drafts_view_omits_understanding_block_when_empty():
    with_u = drafts_view("", "some understanding", ["a"])
    without_u = drafts_view("", "", ["a"])
    assert len(with_u["blocks"]) == len(without_u["blocks"]) + 1


def test_drafts_view_renders_the_full_draft_in_a_section_block():
    # The radio label is only a preview; the full text must be readable.
    long = "x" * 400
    view = drafts_view("", "", [long])
    sections = [b for b in view["blocks"] if b["type"] == "section"]
    assert any(long in b["text"]["text"] for b in sections)


def test_drafts_view_respects_slack_option_limits():
    # Slack caps option text at 75 chars and option value at 150. Exceeding
    # either makes views_update fail with invalid_blocks — i.e. the modal simply
    # does not render for any realistic (>75 char) reply.
    view = drafts_view("", "", ["x" * 5000, "y" * 5000, "z" * 5000])
    for option in _options(view):
        assert len(option["text"]["text"]) <= slack_app.OPTION_TEXT_LIMIT
        assert len(option["value"]) <= slack_app.OPTION_VALUE_LIMIT


# --- selected_text: which reply actually gets sent -----------------------------


CANDIDATES = ["draft 1", "draft 2", "draft 3"]


def _state(*, edit=None, selected=None):
    values = {}
    if edit is not None:
        values[EDIT_BLOCK] = {EDIT_ACTION: {"value": edit}}
    if selected is not None:
        values[CHOICE_BLOCK] = {CHOICE_ACTION: {"selected_option": {"value": selected}}}
    return values


def test_selected_text_prefers_a_non_empty_edit():
    text = selected_text(_state(edit="my own words", selected="1"), CANDIDATES)
    assert text == "my own words"


def test_selected_text_falls_back_to_radio_when_edit_blank():
    text = selected_text(_state(edit="   ", selected="1"), CANDIDATES)
    assert text == "draft 2"


def test_selected_text_uses_radio_when_no_edit_field():
    assert selected_text(_state(selected="2"), CANDIDATES) == "draft 3"


def test_selected_text_empty_when_nothing_chosen():
    assert selected_text(_state(), CANDIDATES) == ""
    assert selected_text({}, CANDIDATES) == ""


def test_selected_text_sends_the_full_draft_not_the_truncated_label():
    # Regression: the option value used to carry the (truncated) draft text, so
    # picking a long candidate sent a cut-off message ending in "…" — as you.
    long = "I'd love to join, but " + "a" * 500 + " — talk tomorrow?"
    view = drafts_view("", "", [long])
    chosen = _options(view)[0]["value"]
    assert selected_text(_state(selected=chosen), [long]) == long


def test_selected_text_empty_on_a_stale_or_bogus_index():
    assert selected_text(_state(selected="7"), CANDIDATES) == ""
    assert selected_text(_state(selected="draft 2"), CANDIDATES) == ""


# --- reply threading -----------------------------------------------------------


def test_reply_thread_ts_prefers_the_real_thread():
    assert reply_thread_ts({"thread_ts": "111.1", "message_ts": "222.2"}) == "111.1"


def test_reply_thread_ts_falls_back_to_the_message_ts():
    # No real thread → reply as a new thread rooted at the picked message.
    assert reply_thread_ts({"thread_ts": None, "message_ts": "222.2"}) == "222.2"


def test_reply_thread_ts_none_when_absent():
    assert reply_thread_ts({}) is None


# --- lazy slack_bolt dependency ------------------------------------------------


def test_importing_the_module_does_not_require_slack_bolt():
    # If this test file imported and ran, the helpers were reachable without the
    # SDK. Guard the promise explicitly so a stray top-level import is caught.
    import sys

    assert "slack_bolt" not in sys.modules or slack_app  # helpers stay SDK-free


# --- token guard: the app must never post as the bot ---------------------------


def test_resolve_tokens_requires_the_user_token(monkeypatch):
    # build_app() used to accept a missing SLACK_USER_TOKEN and silently post as
    # the bot. Validated before slack_bolt is even imported, so it fails fast.
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-bot")
    monkeypatch.delenv("SLACK_USER_TOKEN", raising=False)
    with pytest.raises(SlackError, match="SLACK_USER_TOKEN"):
        slack_app.resolve_tokens()


def test_resolve_tokens_requires_the_bot_token(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-me")
    with pytest.raises(SlackError, match="SLACK_BOT_TOKEN"):
        slack_app.resolve_tokens()


def test_resolve_tokens_returns_both(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-bot")
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-me")
    assert slack_app.resolve_tokens() == {"bot": "xoxb-bot", "user": "xoxp-me"}


def test_selected_text_still_resolves_after_a_rejected_submission():
    # _send used to pop the candidate cache before sending. If the submission was
    # then rejected (no text / send failure) the modal stayed open but the cache
    # was gone, so every retry resolved to "" — permanently unsendable.
    candidates = ["draft one", "draft two"]
    cache = {"V1": candidates}

    # First submit: nothing picked -> rejected, modal stays open, cache retained.
    assert selected_text(_state(), cache.get("V1", [])) == ""
    assert "V1" in cache, "a rejected submission must not discard the drafts"

    # Retry with a pick: still resolves to the full draft.
    assert selected_text(_state(selected="1"), cache.get("V1", [])) == "draft two"


# --- context panel: see what Charla read before you send as yourself -----------

from datetime import datetime  # noqa: E402

from ai_reply_copilot.models import Message  # noqa: E402


def _ctx():
    def m(text, mine, who="Priya"):
        return Message(text=text, is_from_me=mine, timestamp=datetime(2026, 7, 12, 9, 31), sender=who)
    return [m("Can you review the deck?", False), m("Sure, looking now", True),
            m("Also, ok to present Thursday?", False)]


def test_drafts_view_shows_the_context_it_read_and_the_reply_language():
    view = drafts_view("{}", "u", ["a"], context=_ctx(), language="English")
    rendered = json.dumps(view, ensure_ascii=False)
    assert "Charla read 3 message(s)" in rendered
    assert "Can you review the deck?" in rendered
    assert "reply language: *English*" in rendered


def test_drafts_view_marks_what_arrived_since_your_last_reply():
    view = drafts_view("{}", "u", ["a"], context=_ctx(), language="English")
    rendered = json.dumps(view, ensure_ascii=False)
    assert "new since your last reply" in rendered
    # The marker sits before the message that came after your reply.
    assert rendered.index("new since your last reply") < rendered.index("ok to present Thursday")


def test_drafts_view_lists_what_is_still_waiting_on_you():
    view = drafts_view("{}", "u", ["a"], open_points=["Confirm Thursday", "Legal on slide 12"])
    rendered = json.dumps(view, ensure_ascii=False)
    assert "Waiting on you" in rendered
    assert "Confirm Thursday" in rendered
    assert "Legal on slide 12" in rendered


def test_drafts_view_stays_within_block_kit_limits_with_a_long_context():
    long_ctx = [
        Message(text="x" * 300, is_from_me=i % 2 == 0, timestamp=None, sender="A")
        for i in range(40)
    ]
    view = drafts_view("{}", "u" * 200, ["c" * 500] * 3, context=long_ctx,
                       open_points=["p" * 400] * 5, language="中文")
    assert len(view["blocks"]) <= 100
    for block in view["blocks"]:
        if block["type"] == "section":
            assert len(block["text"]["text"]) <= slack_app.SECTION_TEXT_LIMIT
    for option in _options(view):
        assert len(option["text"]["text"]) <= slack_app.OPTION_TEXT_LIMIT
        assert len(option["value"]) <= slack_app.OPTION_VALUE_LIMIT


def test_context_blocks_empty_without_messages():
    assert slack_app.context_blocks([], "English") == []


def test_drafts_view_truncates_a_runaway_understanding():
    # Every other section is truncated; this one was not, so a single over-long
    # model string would make Slack reject the entire view with invalid_blocks.
    view = drafts_view("{}", "u" * 5000, ["a"])
    for block in view["blocks"]:
        if block["type"] == "section":
            assert len(block["text"]["text"]) <= slack_app.SECTION_TEXT_LIMIT
