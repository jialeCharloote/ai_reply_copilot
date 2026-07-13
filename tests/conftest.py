"""Shared test fixtures: build a minimal in-file chat.db."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_SCHEMA = """
CREATE TABLE handle (
    ROWID INTEGER PRIMARY KEY,
    id TEXT,
    service TEXT
);
CREATE TABLE chat (
    ROWID INTEGER PRIMARY KEY,
    guid TEXT,
    chat_identifier TEXT,
    display_name TEXT,
    service_name TEXT,
    style INTEGER
);
CREATE TABLE message (
    ROWID INTEGER PRIMARY KEY,
    guid TEXT,
    text TEXT,
    attributedBody BLOB,
    handle_id INTEGER,
    service TEXT,
    date INTEGER,
    is_from_me INTEGER
);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
"""


def make_attributed_body(text: str) -> bytes:
    """Build a fake attributedBody blob matching the decoder's expectations."""
    body = text.encode("utf-8")
    prefix = b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84"
    marker = b"NSString"
    skip = b"\x01\x94\x84\x01+"  # 5 bytes the decoder skips
    if len(body) < 128:
        length = bytes([len(body)])
    else:
        length = b"\x81" + len(body).to_bytes(2, "little")
    return prefix + marker + skip + length + body + b"\x86"


@pytest.fixture
def chat_db(tmp_path) -> Path:
    """A temporary chat.db with two conversations of sample messages."""
    db_path = tmp_path / "chat.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)

    conn.executemany(
        "INSERT INTO handle (ROWID, id, service) VALUES (?, ?, ?)",
        [(1, "+15551234567", "iMessage"), (2, "boss@work.com", "iMessage")],
    )
    conn.executemany(
        "INSERT INTO chat (ROWID, guid, chat_identifier, display_name, "
        "service_name, style) VALUES (?, ?, ?, ?, ?, ?)",
        [
            # style 45 = 1:1, style 43 = group. Chats 30 and 40 have no messages,
            # so they stay invisible to the join-based readers/tests while still
            # exercising get_chat_send_target.
            (10, "iMessage;-;+15551234567", "+15551234567", "Alex", "iMessage", 45),
            (20, "iMessage;-;boss@work.com", "boss@work.com", "", "iMessage", 45),
            (30, "iMessage;+;chat9999", "chat9999", "Launch Team", "iMessage", 43),
            # A green-bubble 1:1. About half of a real chat.db looks like this,
            # and the fixture had none — which is why SMS sends stayed broken.
            (40, "SMS;-;+15557654321", "+15557654321", "Sam", "SMS", 45),
        ],
    )

    # Apple nanosecond timestamps (increasing = newer).
    base = 700_000_000 * 1_000_000_000
    long_text = "x" * 200  # exercises the 0x81 two-byte length path
    messages = [
        # (ROWID, text, attributedBody, handle_id, date, is_from_me)
        (1, "Hey are you free tonight?", None, 1, base + 1, 0),
        (2, None, make_attributed_body("Maybe! What did you have in mind?"), None, base + 2, 1),
        (3, "Dinner at 7?", None, 1, base + 3, 0),
        (4, "Can you review the deck before EOD?", None, 2, base + 100, 0),
        (5, None, make_attributed_body(long_text), 2, base + 101, 0),
    ]
    conn.executemany(
        "INSERT INTO message (ROWID, text, attributedBody, handle_id, date, is_from_me) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        messages,
    )
    conn.executemany(
        "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
        [(10, 1), (10, 2), (10, 3), (20, 4), (20, 5)],
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def group_chat_db(tmp_path) -> Path:
    """A temporary chat.db whose only conversation is a group chat."""
    db_path = tmp_path / "group.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO handle (ROWID, id, service) VALUES (?, ?, ?)",
        [(1, "+15551234567", "iMessage"), (2, "+15559998888", "iMessage")],
    )
    conn.execute(
        "INSERT INTO chat (ROWID, guid, chat_identifier, display_name, "
        "service_name, style) VALUES (?, ?, ?, ?, ?, ?)",
        (30, "iMessage;+;chat9999", "chat9999", "Launch Team", "iMessage", 43),
    )
    base = 700_000_000 * 1_000_000_000
    conn.executemany(
        "INSERT INTO message (ROWID, text, attributedBody, handle_id, date, is_from_me) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "Ship the launch post tonight?", None, 1, base + 1, 0),
            (2, "give me 10 min", None, None, base + 2, 1),
        ],
    )
    conn.executemany(
        "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
        [(30, 1), (30, 2)],
    )
    conn.commit()
    conn.close()
    return db_path
