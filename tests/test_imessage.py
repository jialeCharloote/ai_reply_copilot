"""Tests for the read-only iMessage reader."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ai_reply_copilot.imessage import (
    ChatDatabaseError,
    apple_time_to_datetime,
    decode_attributed_body,
    get_conversation_context,
    list_conversations,
    render_context,
)
from tests.conftest import make_attributed_body


def test_apple_time_nanoseconds():
    # 2001-01-01 + ~700M seconds worth of nanoseconds.
    value = 700_000_000 * 1_000_000_000
    dt = apple_time_to_datetime(value)
    assert dt == datetime.fromtimestamp(700_000_000 + 978307200, tz=timezone.utc)


def test_apple_time_legacy_seconds():
    dt = apple_time_to_datetime(700_000_000)
    assert dt == datetime.fromtimestamp(700_000_000 + 978307200, tz=timezone.utc)


def test_apple_time_none():
    assert apple_time_to_datetime(0) is None
    assert apple_time_to_datetime(None) is None


def test_decode_attributed_body_short():
    blob = make_attributed_body("Hello there")
    assert decode_attributed_body(blob) == "Hello there"


def test_decode_attributed_body_long():
    text = "y" * 200
    blob = make_attributed_body(text)
    assert decode_attributed_body(blob) == text


def test_decode_attributed_body_unicode():
    blob = make_attributed_body("好的，晚上七点见 👍")
    assert decode_attributed_body(blob) == "好的，晚上七点见 👍"


def test_decode_attributed_body_empty():
    assert decode_attributed_body(None) is None
    assert decode_attributed_body(b"") is None
    assert decode_attributed_body(b"no marker here") is None


def test_list_conversations_orders_newest_first(chat_db):
    conversations = list_conversations(db_path=chat_db)
    assert len(conversations) == 2
    # Chat 20 has the most recent message.
    assert conversations[0].chat_id == 20
    assert conversations[1].chat_id == 10
    assert conversations[1].title == "Alex"


def test_list_conversations_uses_identifier_when_no_display_name(chat_db):
    conversations = list_conversations(db_path=chat_db)
    boss = next(c for c in conversations if c.chat_id == 20)
    assert boss.title == "boss@work.com"


def test_list_conversations_limit(chat_db):
    conversations = list_conversations(db_path=chat_db, limit=1)
    assert len(conversations) == 1
    assert conversations[0].chat_id == 20


def test_get_conversation_context_chronological(chat_db):
    messages = get_conversation_context(chat_id=10, db_path=chat_db)
    assert [m.text for m in messages] == [
        "Hey are you free tonight?",
        "Maybe! What did you have in mind?",
        "Dinner at 7?",
    ]
    assert messages[1].is_from_me is True
    assert messages[0].is_from_me is False


def test_get_conversation_context_decodes_attributed_body(chat_db):
    messages = get_conversation_context(chat_id=20, db_path=chat_db)
    assert messages[0].text == "Can you review the deck before EOD?"
    assert messages[1].text == "y" * 0 + "x" * 200


def test_render_context_format(chat_db):
    messages = get_conversation_context(chat_id=10, db_path=chat_db)
    rendered = render_context(messages)
    assert "Me: Maybe! What did you have in mind?" in rendered
    assert "Dinner at 7?" in rendered


def test_missing_db_raises(tmp_path):
    with pytest.raises(ChatDatabaseError):
        list_conversations(db_path=tmp_path / "nope.db")
