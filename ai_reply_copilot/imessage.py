"""Read-only iMessage reader.

Reads the local ``chat.db`` SQLite database that macOS uses to store iMessage /
SMS history and reconstructs conversation context. This is intentionally
read-only: it never writes to ``chat.db`` and never sends anything.

Requires Full Disk Access for the process reading the real database
(``~/Library/Messages/chat.db``). For tests you can point ``db_path`` at any
SQLite file with the same schema.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .models import Message, render_context

__all__ = [
    "DEFAULT_CHAT_DB",
    "ChatDatabaseError",
    "Message",
    "Conversation",
    "apple_time_to_datetime",
    "decode_attributed_body",
    "list_conversations",
    "get_conversation_context",
    "get_chat_identifier",
    "get_chat_send_target",
    "ChatTarget",
    "render_context",
]

DEFAULT_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"

# macOS stores message timestamps as (nano)seconds since 2001-01-01 UTC.
_APPLE_EPOCH_OFFSET = 978307200
# Values above this are nanoseconds (modern macOS); below are seconds (legacy).
_NANOSECOND_THRESHOLD = 1e11


class ChatDatabaseError(RuntimeError):
    """Raised when the chat database cannot be opened or read."""


@dataclass
class Conversation:
    chat_id: int
    identifier: str
    display_name: str
    service: str
    last_message_at: Optional[datetime]
    last_text: str

    @property
    def title(self) -> str:
        return self.display_name or self.identifier


def apple_time_to_datetime(value: Optional[int]) -> Optional[datetime]:
    """Convert an Apple ``chat.db`` timestamp to a timezone-aware datetime."""
    if not value:
        return None
    seconds = value / 1e9 if value > _NANOSECOND_THRESHOLD else float(value)
    return datetime.fromtimestamp(seconds + _APPLE_EPOCH_OFFSET, tz=timezone.utc)


def decode_attributed_body(blob: Optional[bytes]) -> Optional[str]:
    """Best-effort extraction of text from a message ``attributedBody`` blob.

    Modern macOS often leaves ``message.text`` NULL and stores the body inside
    ``attributedBody`` as a serialized ``NSAttributedString``. This heuristic
    pulls the readable string out without a full typedstream parser, which is
    good enough for reconstructing conversation context.
    """
    if not blob:
        return None
    try:
        marker = b"NSString"
        idx = blob.find(marker)
        if idx == -1:
            return None
        # Skip past the marker plus the class/version bytes that follow it.
        cursor = idx + len(marker) + 5
        length_byte = blob[cursor]
        if length_byte == 0x81:
            # Length is stored as the next two little-endian bytes.
            length = int.from_bytes(blob[cursor + 1 : cursor + 3], "little")
            start = cursor + 3
        else:
            length = length_byte
            start = cursor + 1
        text = blob[start : start + length]
        decoded = text.decode("utf-8", errors="replace").strip()
        return decoded or None
    except (IndexError, ValueError):
        return None


def _message_text(text: Optional[str], attributed_body: Optional[bytes]) -> str:
    if text and text.strip():
        return text.strip()
    decoded = decode_attributed_body(attributed_body)
    return decoded or ""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    if not db_path.exists():
        raise ChatDatabaseError(
            f"chat.db not found at {db_path}. On macOS this requires Full Disk "
            "Access for the running process."
        )
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError as exc:  # pragma: no cover - env specific
        raise ChatDatabaseError(
            f"Could not open {db_path} read-only: {exc}. Grant Full Disk Access "
            "and try again."
        ) from exc


def list_conversations(
    db_path: Path = DEFAULT_CHAT_DB, limit: int = 20
) -> List[Conversation]:
    """Return the most recently active conversations, newest first."""
    query = """
        SELECT c.ROWID            AS chat_id,
               c.chat_identifier  AS identifier,
               c.display_name     AS display_name,
               c.service_name     AS service,
               m.date             AS date,
               m.text             AS text,
               m.attributedBody   AS attributed_body
        FROM chat c
        JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
        JOIN message m ON m.ROWID = cmj.message_id
        JOIN (
            SELECT cmj.chat_id AS chat_id, MAX(m.date) AS max_date
            FROM chat_message_join cmj
            JOIN message m ON m.ROWID = cmj.message_id
            GROUP BY cmj.chat_id
        ) latest ON latest.chat_id = c.ROWID AND latest.max_date = m.date
        ORDER BY m.date DESC
        LIMIT ?
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(query, (limit,)).fetchall()
    finally:
        conn.close()

    conversations = []
    for row in rows:
        conversations.append(
            Conversation(
                chat_id=row["chat_id"],
                identifier=row["identifier"] or "",
                display_name=row["display_name"] or "",
                service=row["service"] or "",
                last_message_at=apple_time_to_datetime(row["date"]),
                last_text=_message_text(row["text"], row["attributed_body"]),
            )
        )
    return conversations


def get_conversation_context(
    chat_id: int, db_path: Path = DEFAULT_CHAT_DB, limit: int = 20
) -> List[Message]:
    """Return the last ``limit`` messages for a chat in chronological order."""
    query = """
        SELECT m.date           AS date,
               m.text           AS text,
               m.attributedBody AS attributed_body,
               m.is_from_me     AS is_from_me,
               h.id             AS handle
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        WHERE cmj.chat_id = ?
        ORDER BY m.date DESC
        LIMIT ?
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(query, (chat_id, limit)).fetchall()
    finally:
        conn.close()

    messages = []
    for row in rows:
        text = _message_text(row["text"], row["attributed_body"])
        if not text:
            continue
        messages.append(
            Message(
                text=text,
                is_from_me=bool(row["is_from_me"]),
                timestamp=apple_time_to_datetime(row["date"]),
                sender=row["handle"],
            )
        )
    messages.reverse()
    return messages


def get_chat_identifier(
    chat_id: int, db_path: Path = DEFAULT_CHAT_DB
) -> Optional[str]:
    """Return the chat_identifier (phone/email for 1:1 chats) for a chat id."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT chat_identifier FROM chat WHERE ROWID = ?", (chat_id,)
        ).fetchone()
    finally:
        conn.close()
    return row["chat_identifier"] if row else None


@dataclass
class ChatTarget:
    """How to send to a chat: its GUID, human identifier, and whether it's a group."""

    guid: str
    identifier: str
    is_group: bool

    @property
    def recipient(self) -> str:
        """A 1:1 send handle (phone/email), falling back to the GUID."""
        return self.identifier or self.guid


def get_chat_send_target(
    chat_id: int, db_path: Path = DEFAULT_CHAT_DB
) -> Optional[ChatTarget]:
    """Resolve how to send to a chat.

    1:1 chats send to a participant handle (phone/email); group chats must send
    to the existing chat by GUID — ``chat_identifier`` alone is a group GUID
    stub that AppleScript can't address as a participant. ``style`` 43 marks a
    group; the ``;+;`` GUID form is a fallback signal.
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT guid, chat_identifier, style FROM chat WHERE ROWID = ?",
            (chat_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    guid = row["guid"] or ""
    is_group = row["style"] == 43 or ";+;" in guid
    return ChatTarget(guid=guid, identifier=row["chat_identifier"] or "", is_group=is_group)
