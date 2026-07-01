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
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .models import Message

SLACK_API_BASE = "https://slack.com/api"


class SlackError(RuntimeError):
    """Raised when a Slack API call fails or the token is missing."""


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


class SlackClient:
    """Thin Slack Web API wrapper over the standard library."""

    def __init__(self, token: Optional[str] = None, timeout: int = 30):
        self.token = token or os.environ.get("SLACK_BOT_TOKEN")
        self.timeout = timeout
        self._user_cache: Dict[str, str] = {}
        self._auth_user_id: Optional[str] = None

    def call(self, method: str, params: Optional[dict] = None) -> dict:
        if not self.token:
            raise SlackError("SLACK_BOT_TOKEN is not set.")
        url = f"{SLACK_API_BASE}/{method}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.token}"}, method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:  # pragma: no cover - network dependent
            raise SlackError(f"Could not reach Slack: {exc}") from exc
        if not data.get("ok"):
            raise SlackError(f"Slack API error on {method}: {data.get('error')}")
        return data

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


class FakeSlackClient(SlackClient):
    """A SlackClient that returns canned API responses (no network).

    ``responses`` maps a method name to a dict, or to a callable taking the
    request params and returning a dict.
    """

    def __init__(self, responses: dict, auth_user_id: str = "U_ME"):
        super().__init__(token="fake-token")
        self._responses = responses
        self._auth_user_id = auth_user_id

    def call(self, method: str, params: Optional[dict] = None) -> dict:
        if method not in self._responses:
            raise SlackError(f"Slack API error on {method}: not_configured")
        handler = self._responses[method]
        data = handler(params or {}) if callable(handler) else handler
        if not data.get("ok", True):
            raise SlackError(f"Slack API error on {method}: {data.get('error')}")
        return data


def list_conversations(
    client: SlackClient, limit: int = 20
) -> List[SlackConversation]:
    """List channels/DMs the token can see, most recently active first."""
    data = client.call(
        "conversations.list",
        {
            "types": "public_channel,private_channel,im,mpim",
            "exclude_archived": "true",
            "limit": 200,
        },
    )
    conversations: List[SlackConversation] = []
    for channel in data.get("channels", []):
        channel_id = channel.get("id", "")
        is_im = bool(channel.get("is_im"))
        if is_im:
            name = client.user_name(channel.get("user")) or ""
        else:
            name = channel.get("name", "")
        try:
            history = client.call(
                "conversations.history", {"channel": channel_id, "limit": 1}
            )
        except SlackError:
            continue
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

    conversations.sort(
        key=lambda c: c.last_message_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return conversations[:limit]


def get_conversation_context(
    client: SlackClient, channel_id: str, limit: int = 20
) -> List[Message]:
    """Return the last ``limit`` messages for a channel in chronological order."""
    data = client.call(
        "conversations.history", {"channel": channel_id, "limit": limit}
    )
    auth_id = client.auth_user_id()
    messages: List[Message] = []
    for raw in data.get("messages", []):
        text = (raw.get("text") or "").strip()
        if not text:
            continue
        user_id = raw.get("user")
        messages.append(
            Message(
                text=text,
                is_from_me=bool(auth_id and user_id == auth_id),
                timestamp=slack_ts_to_datetime(raw.get("ts")),
                sender=client.user_name(user_id),
            )
        )
    messages.reverse()
    return messages
