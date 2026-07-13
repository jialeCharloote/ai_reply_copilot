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
import logging
import os
from collections import OrderedDict
from typing import Dict, List, Optional

from .drafting import draft_from_context, load_context
from .llm import DEFAULT_PROVIDER, get_client
from .models import Message, unanswered_index
from .safety import describe_notice, describe_warning
from .send import send_slack
from .slack import SlackClient, SlackError
from .storage import load_style_profile

logger = logging.getLogger(__name__)

SHORTCUT_CALLBACK = "charla_draft_reply"
CONFIRM_CONTINUE_ACTION = "charla_confirm_continue"
SEND_VIEW_CALLBACK = "charla_send"
CHOICE_BLOCK = "charla_choice"
CHOICE_ACTION = "charla_choice_action"
EDIT_BLOCK = "charla_edit"
EDIT_ACTION = "charla_edit_action"

_TITLE = {"type": "plain_text", "text": "Charla"}
_CANCEL = {"type": "plain_text", "text": "Cancel"}

# Slack's option object caps `text` at 75 chars and `value` at 150. A reply is
# routinely longer than either, so the draft text cannot live in the option:
# the label is a truncated preview, the value is the candidate's index, and the
# full text is rendered in section blocks above (and resolved by index on send).
OPTION_TEXT_LIMIT = 75
OPTION_VALUE_LIMIT = 150
SECTION_TEXT_LIMIT = 3000

# Cap the per-view caches: a modal closed without submitting is never popped.
MAX_OPEN_MODALS = 64


# --- pure helpers (unit-tested) ------------------------------------------------


def dumps_meta(meta: Dict[str, Optional[str]]) -> str:
    return json.dumps(meta)


def loads_meta(raw: str) -> Dict[str, Optional[str]]:
    return json.loads(raw) if raw else {}


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _friendly(exc: Exception, limit: int = 160) -> str:
    """A short, safe error string for the modal.

    ``LLMError`` embeds the provider's raw HTTP response body, which we do not
    want to render verbatim into a Slack workspace. The full detail is logged.
    """
    return _truncate(" ".join(str(exc).split()), limit)


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


def context_blocks(messages: List[Message], language: Optional[str]) -> List[dict]:
    """What Charla read, and what language it will answer in.

    Without this the drafts are a black box: you cannot tell whether it picked up
    the right thread, or how much history it saw, before sending as yourself.
    """
    if not messages:
        return []
    start = unanswered_index(messages)
    lines = []
    for index, message in enumerate(messages):
        if start is not None and index == start:
            lines.append("─── 你上次回复之后 / new since your last reply ───")
        lines.append(message.format_line())
    transcript = _truncate("\n".join(lines), SECTION_TEXT_LIMIT - 100)
    shown = language or "跟随对话 / mirror the conversation"
    return [
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"*Charla read {len(messages)} message(s)* · reply language: *{shown}*",
                }
            ],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"```{transcript}```"}},
        {"type": "divider"},
    ]


def drafts_view(
    meta_json: str,
    understanding: str,
    candidates: List[str],
    *,
    context: Optional[List[Message]] = None,
    open_points: Optional[List[str]] = None,
    language: Optional[str] = None,
) -> dict:
    blocks: List[dict] = []
    blocks.extend(context_blocks(context or [], language))
    if understanding:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    # Truncated like every other section: one over-long model
                    # string here would make Slack reject the whole view.
                    "text": _truncate(f"*Understanding:* {understanding}", SECTION_TEXT_LIMIT),
                },
            }
        )
    if open_points:
        bullets = "\n".join(f"• {point}" for point in open_points)
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _truncate(f"*你还没回应 / Waiting on you:*\n{bullets}", SECTION_TEXT_LIMIT),
                },
            }
        )
    # Full drafts, readable — the radio labels below are only short previews.
    for number, candidate in enumerate(candidates, start=1):
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _truncate(f"*{number}.* {candidate}", SECTION_TEXT_LIMIT),
                },
            }
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
                        "text": {
                            "type": "plain_text",
                            "text": _truncate(f"{number}. {candidate}", OPTION_TEXT_LIMIT),
                        },
                        # The index, not the text — see OPTION_VALUE_LIMIT above.
                        "value": str(number - 1),
                    }
                    for number, candidate in enumerate(candidates, start=1)
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


def selected_text(state_values: dict, candidates: List[str]) -> str:
    """Pull the chosen reply out of a submitted drafts view.

    A non-empty edit field wins; otherwise the selected radio option, whose
    value is an *index* into ``candidates``. Resolving by index is what keeps
    the full draft intact — the option label is a truncated preview, and sending
    that would silently ship a cut-off message.
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
    raw = (selected or {}).get("value", "")
    try:
        return candidates[int(raw)]
    except (ValueError, IndexError):
        return ""


def picked_a_draft(state_values: dict) -> bool:
    """Did the user actually select one of the radio options?"""
    selected = (
        state_values.get(CHOICE_BLOCK, {})
        .get(CHOICE_ACTION, {})
        .get("selected_option")
    )
    return bool((selected or {}).get("value", ""))


def drafts_expired(state_values: dict, candidates: List[str]) -> bool:
    """The user picked a draft, but the text behind it is gone.

    The cache is bounded and never popped when a modal is closed unsubmitted, so
    an older open modal's drafts can be evicted underneath it — and a process
    restart drops them all. Without this the submit handler could only say "Pick
    a draft, or write your own" to a user who *had* picked one, and re-picking
    would never work, because the candidates it resolves against are gone.
    """
    return picked_a_draft(state_values) and not candidates


def reply_thread_ts(meta: Dict[str, Optional[str]]) -> Optional[str]:
    return meta.get("thread_ts") or meta.get("message_ts")


# --- Bolt app (thin glue) ------------------------------------------------------


def resolve_tokens(user_token: Optional[str] = None) -> Dict[str, str]:
    """Validate the token pair up front, before any SDK or network work.

    Charla reads and posts *as you*. If the user token is missing, ``SlackClient``
    would quietly fall back to the bot token and post as the bot — so this must
    fail loudly rather than degrade.
    """
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    if not bot_token:
        raise SlackError("SLACK_BOT_TOKEN is not set (needed to open the modal).")
    user_token = user_token or os.environ.get("SLACK_USER_TOKEN")
    if not user_token:
        raise SlackError(
            "SLACK_USER_TOKEN is not set. Charla reads and posts as you, which "
            "needs your xoxp user token — it will not post as the bot instead."
        )
    return {"bot": bot_token, "user": user_token}


def build_app(*, user_token: Optional[str] = None, llm_factory=None):
    """Construct the Bolt app. ``slack_bolt`` is imported lazily so the rest of
    the package (and the test suite) never require it."""
    tokens = resolve_tokens(user_token)

    from slack_bolt import App

    bot_token, user_token = tokens["bot"], tokens["user"]
    app = App(token=bot_token)

    # Full drafts for the open modal, keyed by view id. They cannot ride in
    # private_metadata (3000-char cap) and must not ride in the option values
    # (150-char cap), so the submit handler resolves the chosen index here.
    # Bounded: a modal the user closes without submitting is never popped, and
    # this process is long-running.
    drafts_by_view: "OrderedDict[str, List[str]]" = OrderedDict()
    # Context that tripped the sensitive gate, awaiting "Draft anyway".
    pending_by_view: "OrderedDict[str, object]" = OrderedDict()

    def _remember(cache: OrderedDict, key: str, value) -> None:
        cache[key] = value
        while len(cache) > MAX_OPEN_MODALS:
            cache.popitem(last=False)

    def user_client() -> SlackClient:
        return SlackClient(token=user_token, token_name="SLACK_USER_TOKEN")

    def llm_client():
        return (llm_factory or get_client)(provider=DEFAULT_PROVIDER)

    def _render_drafts(client, view_id, meta, context) -> None:
        try:
            suggestion = draft_from_context(
                context.messages,
                client=llm_client(),
                style=load_style_profile(),
                source="slack",
                target=meta.get("channel"),
                # The learned voice sends verbatim past messages of yours to the
                # model on every draft. The Slack modal has no flags, so this is
                # the opt-out (`charla voice forget` is the other one).
                use_voice=not os.environ.get("CHARLA_NO_VOICE"),
            )
        except Exception as exc:  # surface generation failures in the modal
            logger.exception("Charla draft failed")
            client.views_update(
                view_id=view_id,
                view=message_view(dumps_meta(meta), f"Couldn't draft: {_friendly(exc)}"),
            )
            return
        _remember(drafts_by_view, view_id, suggestion.candidates)
        client.views_update(
            view_id=view_id,
            view=drafts_view(
                dumps_meta(meta),
                suggestion.understanding,
                suggestion.candidates,
                context=context.messages,
                open_points=suggestion.open_points,
                language=suggestion.language,
            ),
        )

    def _read(meta) -> Optional[object]:
        return load_context(
            user_client(),
            channel=meta["channel"],
            message_ts=meta.get("message_ts"),
            thread_ts=meta.get("thread_ts"),
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

        try:
            context = _read(meta)
        except Exception as exc:
            logger.exception("Charla read failed")
            client.views_update(
                view_id=view_id,
                view=message_view(dumps_meta(meta), f"Couldn't read the conversation: {_friendly(exc)}"),
            )
            return
        if not context.messages:
            client.views_update(view_id=view_id, view=message_view(dumps_meta(meta), "No readable messages here."))
            return
        if context.blocking_categories:
            # Only an actual secret stops you. A sensitive topic (salary, offer,
            # contract) is surfaced in the drafts modal instead of interrupting —
            # those are the professional replies Charla exists for.
            # Stash the scanned context so "Draft anyway" drafts exactly what was
            # gated — a re-read could pull in a new message that never passed it.
            _remember(pending_by_view, view_id, context)
            client.views_update(view_id=view_id, view=confirm_view(dumps_meta(meta), context.blocking_categories))
            return
        _render_drafts(client, view_id, meta, context)

    @app.action(CONFIRM_CONTINUE_ACTION)
    def _confirm(ack, body, client):
        ack()
        view_id = body["view"]["id"]
        meta = loads_meta(body["view"]["private_metadata"])
        context = pending_by_view.pop(view_id, None)
        if context is None:
            # The modal outlived the process. Re-reading here would draft context
            # that never passed the gate, so make the user start over instead.
            client.views_update(
                view_id=view_id,
                view=message_view(dumps_meta(meta), "This draft expired — run the shortcut again."),
            )
            return
        client.views_update(view_id=view_id, view=loading_view(dumps_meta(meta)))
        _render_drafts(client, view_id, meta, context)

    def _notify_send_failed(channel: str, text: str, exc: Exception) -> None:
        """Tell the user their reply did not go out.

        The modal is already closed by the time we know, so the notice goes to
        their own DM. Silence here is the worst outcome: they would believe a
        message went out as them when it did not.
        """
        try:
            client = user_client()
            me = client.auth_user_id()
            if not me:
                return
            client.post_message(
                me,
                f":warning: Charla couldn't send your reply to <#{channel}>: "
                f"{_friendly(exc)}\n\nYour draft, so you don't lose it:\n```{text}```",
            )
        except Exception:  # pragma: no cover - best effort; already logged
            logger.exception("Charla could not deliver the send-failure notice")

    @app.view(SEND_VIEW_CALLBACK)
    def _send(ack, view):
        # Resolve the choice *before* acking, but do not send before acking: a
        # send that times out after Slack accepted the POST would surface as a
        # failure and invite a retry, double-posting the message as the user.
        # There is no idempotency key, so ack first and report failure by DM.
        try:
            meta = loads_meta(view["private_metadata"])
            candidates = drafts_by_view.get(view["id"], [])
            text = selected_text(view["state"]["values"], candidates)
        except Exception:
            logger.exception("Charla could not read the submitted view")
            ack()
            return

        # Only when there is nothing to send: a typed edit still wins, even if the
        # cached candidates behind the radio buttons are long gone.
        if not text and drafts_expired(view["state"]["values"], candidates):
            # They picked a draft we no longer hold. Telling them to pick one is
            # a lie they cannot act on — re-picking resolves against the same
            # empty list. Say what actually happened, the way _confirm does.
            ack(
                response_action="update",
                view=message_view(
                    dumps_meta(meta),
                    "This draft expired — run the shortcut again. (Anything you typed "
                    "in the edit box still sends.)",
                ),
            )
            return

        if not text:
            # Keep the modal open — and keep the candidates, so a retry still works.
            ack(response_action="errors", errors={EDIT_BLOCK: "Pick a draft, or write your own."})
            return

        drafts_by_view.pop(view["id"], None)
        ack()
        try:
            send_slack(user_client(), meta["channel"], text, thread_ts=reply_thread_ts(meta))
        except Exception as exc:
            logger.exception("Charla send failed")
            _notify_send_failed(meta["channel"], text, exc)

    return app


def main() -> None:  # pragma: no cover - runs the live Socket Mode loop
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    logging.basicConfig(level=logging.INFO)
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        raise SlackError(
            "SLACK_APP_TOKEN is not set (the xapp- app-level token for Socket Mode)."
        )
    try:
        app = build_app()
    except SlackError as exc:
        raise SystemExit(f"error: {exc}") from exc
    SocketModeHandler(app, app_token).start()


if __name__ == "__main__":  # pragma: no cover
    main()
