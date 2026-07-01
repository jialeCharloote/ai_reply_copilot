"""Command-line interface for the iMessage reader prototype.

Examples::

    reply-copilot list
    reply-copilot list --limit 10
    reply-copilot show 42
    reply-copilot show 42 --limit 30
    reply-copilot list --db /path/to/test/chat.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .imessage import (
    DEFAULT_CHAT_DB,
    ChatDatabaseError,
    get_conversation_context,
    list_conversations,
    render_context,
)


def _add_db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_CHAT_DB,
        help=f"Path to chat.db (default: {DEFAULT_CHAT_DB})",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reply-copilot",
        description="Read-only iMessage context reader (AI Reply Copilot MVP).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List recently active conversations")
    p_list.add_argument("--limit", type=int, default=20, help="Max conversations")
    _add_db_arg(p_list)

    p_show = sub.add_parser("show", help="Show recent context for a conversation")
    p_show.add_argument("chat_id", type=int, help="Chat ROWID (from `list`)")
    p_show.add_argument("--limit", type=int, default=20, help="Max messages")
    _add_db_arg(p_show)

    return parser


def _cmd_list(args: argparse.Namespace) -> int:
    conversations = list_conversations(db_path=args.db, limit=args.limit)
    if not conversations:
        print("No conversations found.")
        return 0
    for convo in conversations:
        when = (
            convo.last_message_at.strftime("%Y-%m-%d %H:%M")
            if convo.last_message_at
            else "?"
        )
        preview = convo.last_text.replace("\n", " ")
        if len(preview) > 60:
            preview = preview[:57] + "..."
        service = f" ({convo.service})" if convo.service else ""
        print(f"{convo.chat_id:>5}  {when}  {convo.title}{service}")
        print(f"        {preview}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    messages = get_conversation_context(
        chat_id=args.chat_id, db_path=args.db, limit=args.limit
    )
    if not messages:
        print(f"No messages found for chat {args.chat_id}.")
        return 0
    print(render_context(messages))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "show":
            return _cmd_show(args)
    except ChatDatabaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
