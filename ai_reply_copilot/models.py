"""Shared data models used across channels (iMessage, Slack, ...)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class Message:
    text: str
    is_from_me: bool
    timestamp: Optional[datetime]
    sender: Optional[str]

    def format_line(self) -> str:
        who = "Me" if self.is_from_me else (self.sender or "Them")
        when = self.timestamp.strftime("%Y-%m-%d %H:%M") if self.timestamp else "?"
        return f"[{when}] {who}: {self.text}"


def unanswered_index(messages: List[Message]) -> Optional[int]:
    """Index of the first message that arrived after the user's last reply.

    That slice is what has piled up while the user was away, and is what they
    actually need to see and answer.

    Returns None when the user spoke last (nothing is waiting on them) or there
    are no messages. When the user has not spoken at all in this window, the
    whole window is unanswered, so this returns 0.
    """
    last_from_me = None
    for index, message in enumerate(messages):
        if message.is_from_me:
            last_from_me = index
    if last_from_me is None:
        return 0 if messages else None
    if last_from_me == len(messages) - 1:
        return None  # the user spoke last; nothing is waiting on them
    return last_from_me + 1


def render_context(messages: List[Message], *, mark_unanswered: bool = False) -> str:
    """Render messages as a plain-text transcript ready to feed to an LLM.

    With ``mark_unanswered``, the messages that arrived after the user's last
    reply are marked — the model reliably misses "they asked three things and I
    only answered one" without being shown where the user last spoke.
    """
    start = unanswered_index(messages) if mark_unanswered else None
    lines = []
    for index, message in enumerate(messages):
        if start is not None and index == start:
            lines.append("--- since your last reply (unanswered) ---")
        lines.append(message.format_line())
    return "\n".join(lines)
