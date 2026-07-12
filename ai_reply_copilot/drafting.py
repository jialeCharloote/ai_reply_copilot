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
from .language import resolve_language
from .llm import DEFAULT_PROVIDER, get_client
from .models import Message, render_context
from .prompts import StyleProfile
from .safety import describe_warning, scan_blocking, scan_notice
from .storage import get_conversation_language, load_voice
from .voice import describe_for_prompt


class SensitiveContentError(RuntimeError):
    """Raised when context would reach a cloud model without an explicit OK."""


@dataclass
class SendTarget:
    """Where a chosen reply should be posted."""

    channel: str
    thread_ts: Optional[str] = None


@dataclass
class ConversationContext:
    """Read-only context for a target message, before any cloud call."""

    messages: List[Message]
    # Everything found, for display. ``blocking_categories`` is the subset that
    # must actually stop and ask (an actual secret); the rest are topics worth
    # mentioning but not worth interrupting for.
    sensitive_categories: List[str]
    send_target: SendTarget
    blocking_categories: List[str] = field(default_factory=list)
    notice_categories: List[str] = field(default_factory=list)


@dataclass
class DraftResult:
    """Everything a front-end needs to render drafts and send the choice."""

    understanding: str
    candidates: List[str]
    sensitive_categories: List[str]
    send_target: SendTarget
    context: List[Message] = field(default_factory=list)
    open_points: List[str] = field(default_factory=list)
    language: Optional[str] = None


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
    rendered = render_context(messages) if messages else ""
    blocking = scan_blocking(rendered) if rendered else []
    notices = [c for c in scan_notice(rendered) if c not in blocking] if rendered else []
    reply_thread = thread_ts or message_ts
    return ConversationContext(
        messages=messages,
        sensitive_categories=blocking + notices,
        blocking_categories=blocking,
        notice_categories=notices,
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
    source: str = "slack",
    target: Optional[str] = None,
    use_voice: bool = True,
) -> ReplySuggestion:
    """Turn already-read context into an understanding + reply candidates.

    The reply language is resolved per conversation here, so every front-end gets
    the same behaviour: an English channel is answered in English even if the
    style profile says 中文 (the profile is only a fallback).

    ``use_voice=False`` suppresses the learned voice, whose exemplars are verbatim
    past messages of the user's. That has to be a caller's decision — it used to
    be loaded unconditionally, so the Slack app uploaded them with no way to opt
    out. (``charla voice forget`` is the other off switch.)
    """
    locked = get_conversation_language(source, target) if target else None
    language, _reason = resolve_language(
        messages,
        locked=locked,
        profile_language=style.language if style else None,
    )
    return generate_replies(
        messages,
        client,
        intent=intent,
        tone=tone,
        style=style,
        num_candidates=num,
        language=language,
        voice=describe_for_prompt(load_voice()) if use_voice else None,
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
    provider: str = DEFAULT_PROVIDER,
    model: Optional[str] = None,
    limit: int = 20,
    llm_client=None,
    allow_sensitive: bool = False,
    use_voice: bool = True,
) -> DraftResult:
    """Convenience: read context and draft in one call.

    This still honours the P0 sensitive-content gate — it just cannot *ask*, so
    it refuses by default and the caller must opt in with ``allow_sensitive``.
    A front-end that wants to prompt the user should call ``load_context``,
    inspect ``sensitive_categories``, then call ``draft_from_context``.
    ``llm_client`` is injectable for tests/offline demos.
    """
    context = load_context(
        read_client,
        channel=channel,
        message_ts=message_ts,
        thread_ts=thread_ts,
        limit=limit,
    )
    # Only an actual secret refuses. A sensitive *topic* (salary, offer, contract)
    # is reported on the result and waved through — see safety.py.
    if context.blocking_categories and not allow_sensitive:
        raise SensitiveContentError(
            describe_warning(context.blocking_categories)
            + " Pass allow_sensitive=True to proceed, or gate it via load_context()."
        )
    client = llm_client or get_client(provider=provider, model=model)
    suggestion = draft_from_context(
        context.messages,
        client=client,
        style=style,
        intent=intent,
        tone=tone,
        num=num,
        # Without the target, a language locked to this channel is silently
        # ignored and the thread gets re-detected every time.
        source="slack",
        target=channel,
        use_voice=use_voice,
    )
    return DraftResult(
        understanding=suggestion.understanding,
        candidates=suggestion.candidates,
        open_points=suggestion.open_points,
        language=suggestion.language,
        sensitive_categories=context.sensitive_categories,
        send_target=context.send_target,
        context=context.messages,
    )
