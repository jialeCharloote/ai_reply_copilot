"""Read-only Slack reader.

Reads the current Slack conversation (channels and DMs the token can see) via
the Slack Web API and reconstructs context into the shared ``Message`` model,
mirroring the iMessage reader. It never posts anything.

Requires a Slack token in ``SLACK_BOT_TOKEN`` with at least the read scopes
(e.g. ``channels:history``, ``groups:history``, ``im:history``,
``channels:read``, ``users:read``).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .models import Message
from .net import HttpError, request_json

SLACK_API_BASE = "https://slack.com/api"


class SlackError(RuntimeError):
    """Raised when a Slack API call fails or the token is missing.

    ``error`` carries Slack's own error code (``missing_scope``, ``ratelimited``,
    ``channel_not_found``, …) so callers can tell "you lack a scope" apart from
    "that channel does not exist" instead of swallowing both.
    """

    def __init__(self, message: str, *, error: Optional[str] = None):
        super().__init__(message)
        self.error = error


@dataclass
class SlackConversation:
    channel_id: str
    name: str
    is_im: bool
    last_message_at: Optional[datetime]
    last_text: str

    @property
    def title(self) -> str:
        if self.is_im:
            return f"DM: {self.name}" if self.name else f"DM {self.channel_id}"
        return f"#{self.name}" if self.name else self.channel_id


def slack_ts_to_datetime(ts: Optional[str]) -> Optional[datetime]:
    """Convert a Slack ``ts`` string (unix seconds with micros) to datetime."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (ValueError, TypeError):
        return None


_FROM_ENV = object()  # distinct from None, which means "caller had no token"


class SlackClient:
    """Thin Slack Web API wrapper over the standard library."""

    def __init__(self, token=_FROM_ENV, timeout: int = 30, token_name: str = "SLACK_BOT_TOKEN",
                 sleep=time.sleep):
        if token is _FROM_ENV:
            token = os.environ.get(token_name)
        elif token is None:
            # An explicit None used to fall through to SLACK_BOT_TOKEN, which
            # silently posted as the bot when the user token was missing.
            raise SlackError(f"{token_name} is required; refusing to fall back to another token.")
        self.token = token
        self.token_name = token_name
        self.timeout = timeout
        self._sleep = sleep  # injectable so tests exercise backoff without waiting
        self._user_cache: Dict[str, str] = {}
        self._auth_user_id: Optional[str] = None

    def _request(self, method: str, url: str, *, data: Optional[bytes] = None,
                 http_method: str = "GET", idempotent: bool = True) -> dict:
        """One request, with the retry policy from ``net.py``.

        Slack rate-limits hard (conversations.history is ~50 req/min). The old
        code caught ``URLError`` — which ``HTTPError`` subclasses — so a 429 was
        flattened into "could not reach Slack" and the ``Retry-After`` header the
        server had just handed us was discarded.
        """
        if not self.token:
            raise SlackError(f"{self.token_name} is not set.")
        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        try:
            payload = request_json(
                url,
                headers=headers,
                data=data,
                method=http_method,
                timeout=self.timeout,
                sleep=self._sleep,
                idempotent=idempotent,
            )
        except HttpError as exc:
            raise SlackError(f"Slack request failed: {exc}") from exc
        if not payload.get("ok"):
            error = payload.get("error")
            raise SlackError(f"Slack API error on {method}: {error}", error=error)
        return payload

    def call(self, method: str, params: Optional[dict] = None) -> dict:
        url = f"{SLACK_API_BASE}/{method}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        return self._request(method, url)

    def post(self, method: str, payload: dict, *, idempotent: bool = True) -> dict:
        return self._request(
            method,
            f"{SLACK_API_BASE}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            http_method="POST",
            idempotent=idempotent,
        )

    def post_message(
        self, channel: str, text: str, *, thread_ts: Optional[str] = None
    ) -> dict:
        """Post a message. Never retried on an ambiguous failure.

        ``slack_app`` acks the modal before sending precisely so that Slack's own
        3-second retry cannot double-post the user's reply. That care was undone
        one layer down, where the HTTP client happily re-POSTed on a 5xx or a
        dropped connection — the two cases where Slack may already have created
        the message. See ``net.request_json(idempotent=...)``.
        """
        payload = {"channel": channel, "text": escape_markup(text)}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return self.post("chat.postMessage", payload, idempotent=False)

    def auth_user_id(self) -> Optional[str]:
        if self._auth_user_id is None:
            self._auth_user_id = self.call("auth.test").get("user_id")
        return self._auth_user_id

    def user_name(self, user_id: Optional[str]) -> Optional[str]:
        if not user_id:
            return None
        if user_id not in self._user_cache:
            try:
                info = self.call("users.info", {"user": user_id})
                user = info.get("user", {})
                self._user_cache[user_id] = (
                    user.get("profile", {}).get("display_name")
                    or user.get("real_name")
                    or user_id
                )
            except SlackError:
                self._user_cache[user_id] = user_id
        return self._user_cache[user_id]


def read_client(timeout: int = 30) -> SlackClient:
    """Client for reading context.

    Prefers your user token (the Slack app manifest grants the history scopes to
    the user token; the bot only gets ``commands``), and falls back to a bot
    token for the CLI setup documented in the README, which asks for a bot token
    carrying the history scopes.
    """
    if os.environ.get("SLACK_USER_TOKEN"):
        return SlackClient(timeout=timeout, token_name="SLACK_USER_TOKEN")
    return SlackClient(timeout=timeout, token_name="SLACK_BOT_TOKEN")


def posting_client(timeout: int = 30) -> SlackClient:
    """Client for sending. Charla posts *as you*, so this requires the user token
    and will never quietly post as the bot instead."""
    if not os.environ.get("SLACK_USER_TOKEN"):
        raise SlackError(
            "SLACK_USER_TOKEN is not set. Charla sends as you, which needs your "
            "xoxp user token (scope chat:write) — it will not post as the bot instead."
        )
    return SlackClient(timeout=timeout, token_name="SLACK_USER_TOKEN")


class FakeSlackClient(SlackClient):
    """A SlackClient that returns canned API responses (no network).

    ``responses`` maps a method name to a dict, or to a callable taking the
    request params and returning a dict.
    """

    def __init__(self, responses: dict, auth_user_id: str = "U_ME"):
        super().__init__(token="fake-token")
        self._responses = responses
        self._auth_user_id = auth_user_id
        self.posted = []

    def call(self, method: str, params: Optional[dict] = None) -> dict:
        if method not in self._responses:
            raise SlackError(f"Slack API error on {method}: not_configured", error="not_configured")
        handler = self._responses[method]
        data = handler(params or {}) if callable(handler) else handler
        if not data.get("ok", True):
            raise SlackError(
                f"Slack API error on {method}: {data.get('error')}", error=data.get("error")
            )
        return data

    def post(self, method: str, payload: dict, *, idempotent: bool = True) -> dict:
        self.posted.append((method, payload))
        handler = self._responses.get(method, {"ok": True, "ts": "1000.0001"})
        data = handler(payload) if callable(handler) else handler
        if not data.get("ok", True):
            raise SlackError(
                f"Slack API error on {method}: {data.get('error')}", error=data.get("error")
            )
        return data


# Slack's conversations.list does not report each channel's last message, so
# ordering by recency costs one conversations.history call per channel — and that
# method is Tier 3 (~50 requests/minute). Probing every channel in a real
# workspace therefore rate-limits within seconds. The probe count is bounded, and
# what got skipped is reported rather than silently dropped: a quietly truncated
# list means "your most recent conversation" can quietly be the wrong one.
MAX_HISTORY_PROBES = 50
MAX_CHANNEL_PAGES = 5

# Errors that mean "this one channel is not readable" — worth skipping. Anything
# else (auth failure, exhausted rate limit) is about the whole request and must
# not be swallowed channel by channel.
_SKIPPABLE = frozenset(
    {"channel_not_found", "not_in_channel", "missing_scope", "restricted_action",
     "is_archived", "no_permission"}
)


def _iter_channels(client: SlackClient) -> List[dict]:
    """All conversations the token can see, following Slack's cursor pagination.

    The old code asked for 200 with no cursor, so a workspace with more simply
    lost the rest, silently.
    """
    channels: List[dict] = []
    cursor = None
    for _page in range(MAX_CHANNEL_PAGES):
        params = {
            "types": "public_channel,private_channel,im,mpim",
            "exclude_archived": "true",
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        data = client.call("conversations.list", params)
        channels.extend(data.get("channels", []))
        cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
    return channels


def list_conversations(
    client: SlackClient,
    limit: int = 20,
    *,
    max_probes: int = MAX_HISTORY_PROBES,
    report=None,
) -> List[SlackConversation]:
    """List channels/DMs the token can see, most recently active first.

    ``report`` is called with a human-readable note when coverage is incomplete —
    silence there would read as "we looked at everything", which is exactly how a
    truncated list turns into the wrong "most recent conversation".
    """
    channels = _iter_channels(client)
    probed = channels[:max_probes]

    conversations: List[SlackConversation] = []
    unreadable = 0
    for channel in probed:
        channel_id = channel.get("id", "")
        is_im = bool(channel.get("is_im"))
        name = client.user_name(channel.get("user")) or "" if is_im else channel.get("name", "")
        try:
            history = client.call(
                "conversations.history", {"channel": channel_id, "limit": 1}
            )
        except SlackError as exc:
            if exc.error in _SKIPPABLE:
                unreadable += 1
                continue
            raise  # rate limit, bad auth: about the request, not this channel
        latest = (history.get("messages") or [{}])[0]
        conversations.append(
            SlackConversation(
                channel_id=channel_id,
                name=name,
                is_im=is_im,
                last_message_at=slack_ts_to_datetime(latest.get("ts")),
                last_text=(latest.get("text") or "").replace("\n", " "),
            )
        )

    if report:
        skipped = len(channels) - len(probed)
        if skipped:
            # Deliberately not "the most likely": these are simply the first N in
            # whatever order Slack returned, and claiming otherwise would let a
            # partial answer read as a complete one.
            report(
                f"Only checked {len(probed)} of {len(channels)} conversations for recent "
                f"activity ({skipped} unchecked — Slack rate-limits the per-channel "
                "lookup this needs). The most recent may be among the unchecked; pass a "
                "channel ID directly to target one."
            )
        if unreadable:
            report(f"{unreadable} conversation(s) were not readable with this token.")

    conversations.sort(
        key=lambda c: (
            c.last_message_at or datetime.min.replace(tzinfo=timezone.utc),
            c.channel_id,  # stable order when timestamps tie
        ),
        reverse=True,
    )
    return conversations[:limit]


# Slack does not hand you the text people see. It hands you *mrkdwn*: users are
# `<@U024BE7LH>`, channels are `<#C0G9QF9GZ|general>`, links are `<url|label>`,
# and `& < >` arrive HTML-escaped. Feeding that to the model raw meant it saw a
# user ID where a name belongs — and duly wrote "Hi U024BE7LH" into the draft.
_USER_REF = re.compile(r"<@([UW][A-Z0-9]+)(?:\|([^>]*))?>")
_CHANNEL_REF = re.compile(r"<#[A-Z0-9]+(?:\|([^>]*))?>")
_SPECIAL_REF = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")
_LINK_REF = re.compile(r"<((?:https?|mailto):[^|>]+)(?:\|([^>]*))?>")

# Subtypes that are noise in a reply context. "X has joined the channel" is not a
# message anyone is waiting on a reply to, but unanswered_index would happily
# treat it as one.
_IGNORED_SUBTYPES = frozenset(
    {"channel_join", "channel_leave", "channel_topic", "channel_purpose",
     "channel_name", "channel_archive", "channel_unarchive", "group_join",
     "group_leave", "bot_message", "message_changed", "message_deleted",
     "thread_broadcast_join"}
)


def decode_markup(text: str, client: Optional[SlackClient] = None) -> str:
    """Turn Slack's wire format into what a human actually reads on screen."""
    def user(match: re.Match) -> str:
        label = match.group(2)
        if label:
            return f"@{label}"
        name = client.user_name(match.group(1)) if client else None
        return f"@{name or match.group(1)}"

    text = _USER_REF.sub(user, text)
    text = _CHANNEL_REF.sub(lambda m: f"#{m.group(1)}" if m.group(1) else "#channel", text)
    text = _SPECIAL_REF.sub(lambda m: f"@{m.group(1)}", text)
    # A link renders as its label when it has one, else the bare URL.
    text = _LINK_REF.sub(lambda m: m.group(2) or m.group(1), text)
    # Slack escapes exactly these three, and only these three.
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def escape_markup(text: str) -> str:
    """Escape a draft for chat.postMessage.

    Slack parses `& < >` in the text it is sent. An ordinary reply — "keep p99
    <200ms and >99% uptime" — is otherwise read as markup and renders mangled or
    silently loses text. Ampersand first, or the escapes escape each other.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _message_from_raw(
    client: SlackClient, raw: dict, auth_id: Optional[str]
) -> Optional[Message]:
    """Turn one raw Slack message dict into a shared ``Message`` (or None)."""
    if raw.get("subtype") in _IGNORED_SUBTYPES:
        return None
    text = decode_markup((raw.get("text") or "").strip(), client).strip()
    if not text:
        return None
    user_id = raw.get("user")
    return Message(
        text=text,
        is_from_me=bool(auth_id and user_id == auth_id),
        timestamp=slack_ts_to_datetime(raw.get("ts")),
        sender=client.user_name(user_id),
    )


def get_conversation_context(
    client: SlackClient, channel_id: str, limit: int = 20
) -> List[Message]:
    """Return the last ``limit`` messages for a channel in chronological order."""
    data = client.call(
        "conversations.history", {"channel": channel_id, "limit": limit}
    )
    auth_id = client.auth_user_id()
    messages = [
        m
        for m in (_message_from_raw(client, r, auth_id) for r in data.get("messages", []))
        if m is not None
    ]
    messages.reverse()  # history returns newest-first
    return messages


def get_thread_context(
    client: SlackClient, channel_id: str, thread_ts: str, limit: int = 50
) -> List[Message]:
    """Return a thread's messages (parent first) in chronological order."""
    data = client.call(
        "conversations.replies",
        {"channel": channel_id, "ts": thread_ts, "limit": limit},
    )
    auth_id = client.auth_user_id()
    # conversations.replies already returns parent-first chronological order.
    return [
        m
        for m in (_message_from_raw(client, r, auth_id) for r in data.get("messages", []))
        if m is not None
    ]
