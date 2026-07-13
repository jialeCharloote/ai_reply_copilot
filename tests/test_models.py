"""Rendering of the transcript that both the user and the model read."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from ai_reply_copilot.models import Message, render_context, to_local


def _at(moment: datetime) -> Message:
    return Message(text="are you free tonight?", is_from_me=False, timestamp=moment, sender="Alex")


def test_timestamps_render_in_local_time_not_utc(monkeypatch):
    """A message sent at 8pm local must not be stamped with tomorrow's date.

    Readers store UTC. Rendering that UTC verbatim pushed every evening message
    past midnight for any user west of Greenwich — so "tonight" reached the model
    as the next day, in a product whose whole value is time-sensitive replies.
    """
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    try:
        # 2026-07-14 01:00 UTC == 2026-07-13 20:00 CDT — a different calendar day.
        message = _at(datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc))
        line = message.format_line()
        assert "2026-07-13 20:00" in line
        assert "2026-07-14" not in line
    finally:
        os.environ.pop("TZ", None)
        time.tzset()


def test_render_context_uses_local_time(monkeypatch):
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    try:
        rendered = render_context([_at(datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc))])
        assert "[2026-07-13 20:00]" in rendered
    finally:
        os.environ.pop("TZ", None)
        time.tzset()


def test_to_local_preserves_the_instant():
    utc = datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc)
    assert to_local(utc) == utc  # same instant, different wall clock


def test_naive_and_missing_timestamps_survive():
    naive = datetime(2026, 7, 13, 20, 0)
    assert to_local(naive) == naive
    assert to_local(None) is None
    assert "?" in Message(text="hi", is_from_me=True, timestamp=None, sender=None).format_line()
