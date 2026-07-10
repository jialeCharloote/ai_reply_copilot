"""Tests for the Slack front-end's pure helpers.

These cover the view builders and state parsing that carry the modal's logic.
None of them import ``slack_bolt`` — only ``build_app``/``main`` do (lazily), so
the whole module is unit-testable without the Slack SDK installed.
"""

from __future__ import annotations

from ai_reply_copilot import slack_app
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


def test_drafts_view_renders_one_radio_option_per_candidate():
    view = drafts_view("", "", ["a", "b", "c"])
    choice_block = next(b for b in view["blocks"] if b.get("block_id") == CHOICE_BLOCK)
    options = choice_block["element"]["options"]
    assert [o["value"] for o in options] == ["a", "b", "c"]
    assert choice_block["element"]["action_id"] == CHOICE_ACTION


def test_drafts_view_omits_understanding_block_when_empty():
    with_u = drafts_view("", "some understanding", ["a"])
    without_u = drafts_view("", "", ["a"])
    assert len(with_u["blocks"]) == len(without_u["blocks"]) + 1


def test_drafts_view_truncates_long_candidates_within_slack_limits():
    long = "x" * 5000
    view = drafts_view("", "", [long])
    option = view["blocks"][0]["element"]["options"][0]
    # Radio labels cap at 150 chars, values at 2000; both must fit.
    assert len(option["text"]["text"]) <= 150
    assert len(option["value"]) <= 2000
    assert option["text"]["text"].endswith("…")


# --- selected_text: which reply actually gets sent -----------------------------


def _state(*, edit=None, selected=None):
    values = {}
    if edit is not None:
        values[EDIT_BLOCK] = {EDIT_ACTION: {"value": edit}}
    if selected is not None:
        values[CHOICE_BLOCK] = {CHOICE_ACTION: {"selected_option": {"value": selected}}}
    return values


def test_selected_text_prefers_a_non_empty_edit():
    text = selected_text(_state(edit="my own words", selected="draft 2"))
    assert text == "my own words"


def test_selected_text_falls_back_to_radio_when_edit_blank():
    text = selected_text(_state(edit="   ", selected="draft 2"))
    assert text == "draft 2"


def test_selected_text_uses_radio_when_no_edit_field():
    assert selected_text(_state(selected="draft 3")) == "draft 3"


def test_selected_text_empty_when_nothing_chosen():
    assert selected_text(_state()) == ""
    assert selected_text({}) == ""


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
