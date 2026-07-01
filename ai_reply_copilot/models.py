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


def render_context(messages: List[Message]) -> str:
    """Render messages as a plain-text transcript ready to feed to an LLM."""
    return "\n".join(message.format_line() for message in messages)
