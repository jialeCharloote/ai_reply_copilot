"""Surface-agnostic drafting engine.

One entry point that every front-end (the Slack app today; a Mac menu-bar app
or browser extension later) calls to turn a target conversation into in-voice
reply candidates. It holds no transport or UI code: the reader client and the
LLM client are injectable, mirroring the UI-agnostic design of
``flow.run_reply_flow``.

The read step and the draft step are separated so a caller can honour the PRD's
P0 sensitive-content gate: load context (local, no cloud call), inspect
``sensitive_categories``, ask the user to continue, and only then draft.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import slack as slack_reader
from .generate import ReplySuggestion, generate_replies
from .llm import get_client
from .models import Message, render_context
from .prompts import StyleProfile
from .safety import scan_sensitive


@dataclass
class SendTarget:
    """Where a chosen reply should be posted."""

    channel: str
    thread_ts: Optional[str] = None


@dataclass
class ConversationContext:
    """Read-only context for a target message, before any cloud call."""

    messages: List[Message]
    sensitive_categories: List[str]
    send_target: SendTarget


@dataclass
class DraftResult:
    """Everything a front-end needs to render drafts and send the choice."""

    understanding: str
    candidates: List[str]
    sensitive_categories: List[str]
    send_target: SendTarget
    context: List[Message] = field(default_factory=list)


def load_context(
    read_client,
    *,
    channel: str,
    message_ts: Optional[str] = None,
    thread_ts: Optional[str] = None,
    limit: int = 20,
) -> ConversationContext:
    """Read the conversation and scan it locally — no LLM call.

    ``thread_ts`` (a real thread) reads the thread; otherwise recent channel
    history is used. The reply is threaded off ``thread_ts`` when present, else
    off ``message_ts`` (so replying to a top-level message starts its thread).
    """
    if thread_ts:
        messages = slack_reader.get_thread_context(
            read_client, channel, thread_ts, limit=limit
        )
    else:
        messages = slack_reader.get_conversation_context(
            read_client, channel, limit=limit
        )
    sensitive = scan_sensitive(render_context(messages)) if messages else []
    reply_thread = thread_ts or message_ts
    return ConversationContext(
        messages=messages,
        sensitive_categories=sensitive,
        send_target=SendTarget(channel=channel, thread_ts=reply_thread),
    )


def draft_from_context(
    messages: List[Message],
    *,
    client,
    style: Optional[StyleProfile] = None,
    intent: Optional[str] = None,
    tone: Optional[str] = None,
    num: int = 3,
) -> ReplySuggestion:
    """Turn already-read context into an understanding + reply candidates."""
    return generate_replies(
        messages,
        client,
        intent=intent,
        tone=tone,
        style=style,
        num_candidates=num,
    )


def draft_replies(
    read_client,
    *,
    channel: str,
    message_ts: Optional[str] = None,
    thread_ts: Optional[str] = None,
    style: Optional[StyleProfile] = None,
    intent: Optional[str] = None,
    tone: Optional[str] = None,
    num: int = 3,
    provider: str = "anthropic",
    model: Optional[str] = None,
    limit: int = 20,
    llm_client=None,
) -> DraftResult:
    """Convenience: read context and draft in one call (skips the gate).

    Front-ends that honour the sensitive-content gate should call
    ``load_context`` then ``draft_from_context`` instead. ``llm_client`` is
    injectable for tests/offline demos.
    """
    context = load_context(
        read_client,
        channel=channel,
        message_ts=message_ts,
        thread_ts=thread_ts,
        limit=limit,
    )
    client = llm_client or get_client(provider=provider, model=model)
    suggestion = draft_from_context(
        context.messages, client=client, style=style, intent=intent, tone=tone, num=num
    )
    return DraftResult(
        understanding=suggestion.understanding,
        candidates=suggestion.candidates,
        sensitive_categories=context.sensitive_categories,
        send_target=context.send_target,
        context=context.messages,
    )
