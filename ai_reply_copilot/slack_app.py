"""Slack front-end for Charla.

A message-action shortcut ("Draft reply (Charla)") reads the surrounding
conversation, drafts 3 in-voice candidates, and — after you pick or edit one —
posts the reply **as you** (via a Slack user token). It runs over Socket Mode,
so no public URL is needed; ideal for local dogfooding.

The Bolt handlers are thin glue over ``drafting`` and ``send``; all logic that
is worth testing lives in the pure helpers below (view builders + state
parsing), mirroring the injectable design of ``flow.run_reply_flow``. This is
the only module that depends on ``slack_bolt``; nothing else imports it.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .drafting import draft_from_context, load_context
from .llm import get_client
from .safety import describe_warning
from .send import send_slack
from .slack import SlackClient
from .storage import load_style_profile

SHORTCUT_CALLBACK = "charla_draft_reply"
CONFIRM_CONTINUE_ACTION = "charla_confirm_continue"
SEND_VIEW_CALLBACK = "charla_send"
CHOICE_BLOCK = "charla_choice"
CHOICE_ACTION = "charla_choice_action"
EDIT_BLOCK = "charla_edit"
EDIT_ACTION = "charla_edit_action"

_TITLE = {"type": "plain_text", "text": "Charla"}
_CANCEL = {"type": "plain_text", "text": "Cancel"}


# --- pure helpers (unit-tested) ------------------------------------------------


def dumps_meta(meta: Dict[str, Optional[str]]) -> str:
    return json.dumps(meta)


def loads_meta(raw: str) -> Dict[str, Optional[str]]:
    return json.loads(raw) if raw else {}


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _base_view(meta_json: str, blocks: List[dict], *, submit: Optional[str] = None,
               callback_id: Optional[str] = None) -> dict:
    view: dict = {
        "type": "modal",
        "private_metadata": meta_json,
        "title": _TITLE,
        "close": _CANCEL,
        "blocks": blocks,
    }
    if submit:
        view["submit"] = {"type": "plain_text", "text": submit}
    if callback_id:
        view["callback_id"] = callback_id
    return view


def loading_view(meta_json: str) -> dict:
    return _base_view(
        meta_json,
        [{"type": "section", "text": {"type": "mrkdwn", "text": "_Reading the conversation and drafting…_"}}],
    )


def message_view(meta_json: str, text: str) -> dict:
    return _base_view(
        meta_json,
        [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
    )


def confirm_view(meta_json: str, categories: List[str]) -> dict:
    """First-step gate shown when the context trips the sensitive scanner."""
    return _base_view(
        meta_json,
        [
            {"type": "section", "text": {"type": "mrkdwn", "text": describe_warning(categories)}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": CONFIRM_CONTINUE_ACTION,
                        "style": "primary",
                        "text": {"type": "plain_text", "text": "Draft anyway"},
                    }
                ],
            },
        ],
    )


def drafts_view(meta_json: str, understanding: str, candidates: List[str]) -> dict:
    blocks: List[dict] = []
    if understanding:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Understanding:* {understanding}"}}
        )
    blocks.append(
        {
            "type": "input",
            "block_id": CHOICE_BLOCK,
            "label": {"type": "plain_text", "text": "Pick a draft"},
            "element": {
                "type": "radio_buttons",
                "action_id": CHOICE_ACTION,
                "options": [
                    {
                        "text": {"type": "plain_text", "text": _truncate(c, 150)},
                        "value": _truncate(c, 2000),
                    }
                    for c in candidates
                ],
            },
        }
    )
    blocks.append(
        {
            "type": "input",
            "optional": True,
            "block_id": EDIT_BLOCK,
            "label": {"type": "plain_text", "text": "Edit / write your own (optional)"},
            "element": {"type": "plain_text_input", "action_id": EDIT_ACTION, "multiline": True},
        }
    )
    return _base_view(meta_json, blocks, submit="Send as me", callback_id=SEND_VIEW_CALLBACK)


def selected_text(state_values: dict) -> str:
    """Pull the chosen reply out of a submitted drafts view.

    A non-empty edit field wins; otherwise the selected radio option.
    """
    edit = (
        state_values.get(EDIT_BLOCK, {})
        .get(EDIT_ACTION, {})
        .get("value")
    )
    if edit and edit.strip():
        return edit.strip()
    selected = (
        state_values.get(CHOICE_BLOCK, {})
        .get(CHOICE_ACTION, {})
        .get("selected_option")
    )
    return (selected or {}).get("value", "")


def reply_thread_ts(meta: Dict[str, Optional[str]]) -> Optional[str]:
    return meta.get("thread_ts") or meta.get("message_ts")


# --- Bolt app (thin glue) ------------------------------------------------------


def build_app(*, user_token: Optional[str] = None, llm_factory=None):
    """Construct the Bolt app. ``slack_bolt`` is imported lazily so the rest of
    the package (and the test suite) never require it."""
    from slack_bolt import App

    app = App(token=os.environ["SLACK_BOT_TOKEN"])
    user_token = user_token or os.environ.get("SLACK_USER_TOKEN")

    def user_client() -> SlackClient:
        return SlackClient(token=user_token)

    def llm_client():
        return (llm_factory or get_client)(provider="anthropic")

    def _render_drafts(client, view_id: str, meta: Dict[str, Optional[str]]) -> None:
        context = load_context(
            user_client(),
            channel=meta["channel"],
            message_ts=meta.get("message_ts"),
            thread_ts=meta.get("thread_ts"),
        )
        if not context.messages:
            client.views_update(view_id=view_id, view=message_view(dumps_meta(meta), "No readable messages here."))
            return
        try:
            suggestion = draft_from_context(context.messages, client=llm_client(), style=load_style_profile())
        except Exception as exc:  # surface generation failures in the modal
            client.views_update(view_id=view_id, view=message_view(dumps_meta(meta), f"Couldn't draft: {exc}"))
            return
        client.views_update(
            view_id=view_id,
            view=drafts_view(dumps_meta(meta), suggestion.understanding, suggestion.candidates),
        )

    @app.shortcut(SHORTCUT_CALLBACK)
    def _shortcut(ack, shortcut, client):
        ack()
        msg = shortcut["message"]
        meta = {
            "channel": shortcut["channel"]["id"],
            "message_ts": msg["ts"],
            "thread_ts": msg.get("thread_ts"),  # real thread only; None otherwise
        }
        # Open a placeholder within the 3s trigger window, then update it.
        opened = client.views_open(trigger_id=shortcut["trigger_id"], view=loading_view(dumps_meta(meta)))
        view_id = opened["view"]["id"]

        context = load_context(user_client(), channel=meta["channel"], thread_ts=meta["thread_ts"])
        if context.sensitive_categories:
            client.views_update(view_id=view_id, view=confirm_view(dumps_meta(meta), context.sensitive_categories))
            return
        _render_drafts(client, view_id, meta)

    @app.action(CONFIRM_CONTINUE_ACTION)
    def _confirm(ack, body, client):
        ack()
        view_id = body["view"]["id"]
        meta = loads_meta(body["view"]["private_metadata"])
        client.views_update(view_id=view_id, view=loading_view(dumps_meta(meta)))
        _render_drafts(client, view_id, meta)

    @app.view(SEND_VIEW_CALLBACK)
    def _send(ack, view, logger):
        ack()
        meta = loads_meta(view["private_metadata"])
        text = selected_text(view["state"]["values"])
        if not text:
            return
        try:
            send_slack(user_client(), meta["channel"], text, thread_ts=reply_thread_ts(meta))
        except Exception as exc:  # pragma: no cover - network dependent
            logger.error("Charla send failed: %s", exc)

    return app


def main() -> None:  # pragma: no cover - runs the live Socket Mode loop
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = build_app()
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":  # pragma: no cover
    main()
