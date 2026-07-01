"""Command-line interface for Charla.

Examples::

    charla list
    charla list --limit 10
    charla show 42
    charla show 42 --limit 30
    charla list --db /path/to/test/chat.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from . import slack as slack_reader
from .flow import run_reply_flow
from .generate import GenerationError, generate_replies
from .imessage import (
    DEFAULT_CHAT_DB,
    ChatDatabaseError,
    get_chat_identifier,
    get_conversation_context,
    list_conversations,
    render_context,
)
from .llm import LLMError, get_client
from .prompts import INTENTS, TONES, StyleProfile
from .send import SendError, send_imessage, send_slack
from .slack import SlackClient, SlackError
from .storage import (
    FEEDBACK_RATINGS,
    load_style_profile,
    profile_path,
    record_feedback,
    save_style_profile,
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
        prog="charla",
        description="Charla — reads your current conversation and drafts replies in your voice.",
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

    p_slack_list = sub.add_parser(
        "slack-list", help="List recently active Slack channels/DMs"
    )
    p_slack_list.add_argument("--limit", type=int, default=20, help="Max channels")

    p_slack_show = sub.add_parser(
        "slack-show", help="Show recent context for a Slack channel/DM"
    )
    p_slack_show.add_argument("channel", help="Slack channel/DM ID (from slack-list)")
    p_slack_show.add_argument("--limit", type=int, default=20, help="Max messages")

    p_suggest = sub.add_parser(
        "suggest", help="Generate reply candidates for a conversation"
    )
    p_suggest.add_argument(
        "target",
        nargs="?",
        default=None,
        help="iMessage chat ROWID (from `list`) or Slack channel ID with "
        "--source slack; defaults to the most recent conversation",
    )
    p_suggest.add_argument(
        "--source",
        choices=["imessage", "slack"],
        default="imessage",
        help="Where to read the conversation from",
    )
    p_suggest.add_argument("--limit", type=int, default=20, help="Max messages")
    p_suggest.add_argument(
        "--intent", choices=sorted(INTENTS), help="What the reply should do"
    )
    p_suggest.add_argument(
        "--tone", choices=sorted(TONES), help="How the reply should feel"
    )
    p_suggest.add_argument("--draft", help="What you roughly want to say")
    p_suggest.add_argument(
        "--provider",
        choices=["openai", "anthropic"],
        default="openai",
        help="LLM provider (needs the matching API key env var)",
    )
    p_suggest.add_argument("--model", help="Override the default model")
    p_suggest.add_argument(
        "--num", type=int, default=3, help="Number of candidates (default 3)"
    )
    p_suggest.add_argument(
        "--ignore-profile",
        action="store_true",
        help="Do not apply the saved personal style profile",
    )
    _add_db_arg(p_suggest)

    p_send = sub.add_parser(
        "send", help="Send a reviewed reply (asks for confirmation first)"
    )
    p_send.add_argument(
        "--source",
        choices=["imessage", "slack"],
        default="imessage",
        help="Where to send",
    )
    p_send.add_argument(
        "--to",
        required=True,
        help="iMessage recipient (phone/email) or Slack channel/DM ID",
    )
    p_send.add_argument("--text", required=True, help="The message text to send")
    p_send.add_argument(
        "--dry-run", action="store_true", help="Preview without sending"
    )
    p_send.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt (use with care)",
    )

    p_reply = sub.add_parser(
        "reply",
        help="End-to-end: read a conversation, suggest, pick/edit, then send",
    )
    p_reply.add_argument(
        "target",
        nargs="?",
        default=None,
        help="iMessage chat ROWID or Slack channel ID (with --source slack); "
        "defaults to the most recent conversation",
    )
    p_reply.add_argument(
        "--source", choices=["imessage", "slack"], default="imessage"
    )
    p_reply.add_argument("--limit", type=int, default=20, help="Max context messages")
    p_reply.add_argument("--intent", choices=sorted(INTENTS))
    p_reply.add_argument("--tone", choices=sorted(TONES))
    p_reply.add_argument("--draft", help="What you roughly want to say")
    p_reply.add_argument("--num", type=int, default=3, help="Number of candidates")
    p_reply.add_argument(
        "--provider", choices=["openai", "anthropic"], default="openai"
    )
    p_reply.add_argument("--model", help="Override the default model")
    p_reply.add_argument("--dry-run", action="store_true", help="Preview without sending")
    p_reply.add_argument("--yes", action="store_true", help="Skip send confirmation")
    p_reply.add_argument(
        "--ignore-profile",
        action="store_true",
        help="Do not apply the saved personal style profile",
    )
    _add_db_arg(p_reply)

    p_profile = sub.add_parser("profile", help="View or set your personal style")
    profile_sub = p_profile.add_subparsers(dest="profile_command", required=True)
    profile_sub.add_parser("show", help="Show the saved style profile")
    p_profile_set = profile_sub.add_parser("set", help="Update the style profile")
    p_profile_set.add_argument("--formality", help="e.g. casual, formal")
    p_profile_set.add_argument("--directness", help="e.g. direct, gentle")
    p_profile_set.add_argument(
        "--emoji", action=argparse.BooleanOptionalAction, help="Use emoji"
    )
    p_profile_set.add_argument(
        "--concise", action=argparse.BooleanOptionalAction, help="Prefer concise"
    )
    p_profile_set.add_argument("--language", help="e.g. English, 中文, 中英双语")

    p_feedback = sub.add_parser("feedback", help="Log feedback about a reply")
    p_feedback.add_argument("rating", choices=list(FEEDBACK_RATINGS))
    p_feedback.add_argument("--text", default="", help="The reply the feedback is about")
    p_feedback.add_argument("--source", default="", help="imessage or slack")
    p_feedback.add_argument("--target", default="", help="chat id / channel")

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


def _cmd_slack_list(args: argparse.Namespace) -> int:
    client = SlackClient()
    conversations = slack_reader.list_conversations(client, limit=args.limit)
    if not conversations:
        print("No Slack conversations found.")
        return 0
    for convo in conversations:
        when = (
            convo.last_message_at.strftime("%Y-%m-%d %H:%M")
            if convo.last_message_at
            else "?"
        )
        preview = convo.last_text
        if len(preview) > 60:
            preview = preview[:57] + "..."
        print(f"{convo.channel_id:>12}  {when}  {convo.title}")
        print(f"              {preview}")
    return 0


def _cmd_slack_show(args: argparse.Namespace) -> int:
    client = SlackClient()
    messages = slack_reader.get_conversation_context(
        client, channel_id=args.channel, limit=args.limit
    )
    if not messages:
        print(f"No messages found for channel {args.channel}.")
        return 0
    print(render_context(messages))
    return 0


def _resolve_target(args: argparse.Namespace) -> Optional[str]:
    """Return the explicit target, or fall back to the most recent conversation."""
    if args.target is not None:
        return args.target
    if args.source == "slack":
        conversations = slack_reader.list_conversations(SlackClient(), limit=1)
        if not conversations:
            print("No Slack conversations found.")
            return None
        convo = conversations[0]
        print(f"Using most recent conversation: {convo.title}")
        return convo.channel_id
    conversations = list_conversations(db_path=args.db, limit=1)
    if not conversations:
        print("No conversations found.")
        return None
    convo = conversations[0]
    print(f"Using most recent conversation: {convo.title}")
    return str(convo.chat_id)


def _cmd_suggest(args: argparse.Namespace) -> int:
    target = _resolve_target(args)
    if target is None:
        return 0
    if args.source == "slack":
        slack_client = SlackClient()
        messages = slack_reader.get_conversation_context(
            slack_client, channel_id=target, limit=args.limit
        )
    else:
        messages = get_conversation_context(
            chat_id=int(target), db_path=args.db, limit=args.limit
        )
    if not messages:
        print(f"No messages found for {args.source} target {target}.")
        return 0

    client = get_client(provider=args.provider, model=args.model)
    style = None if args.ignore_profile else load_style_profile()
    suggestion = generate_replies(
        messages,
        client,
        intent=args.intent,
        tone=args.tone,
        style=style,
        draft=args.draft,
        num_candidates=args.num,
    )

    if suggestion.understanding:
        print(f"Understanding: {suggestion.understanding}\n")
    for index, candidate in enumerate(suggestion.candidates, start=1):
        print(f"{index}. {candidate}")
    return 0


def _confirm(prompt: str) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _cmd_send(args: argparse.Namespace) -> int:
    print(f"About to send via {args.source} to {args.to}:")
    print(f"  {args.text}")
    if not args.dry_run and not args.yes:
        if not _confirm("Send this message? [y/N] "):
            print("Cancelled.")
            return 1

    if args.source == "slack":
        result = send_slack(SlackClient(), args.to, args.text, dry_run=args.dry_run)
    else:
        result = send_imessage(args.to, args.text, dry_run=args.dry_run)

    print(result.detail)
    return 0


def _cmd_reply(args: argparse.Namespace) -> int:
    target = _resolve_target(args)
    if target is None:
        return 0
    if args.source == "slack":
        slack_client = SlackClient()
        messages = slack_reader.get_conversation_context(
            slack_client, channel_id=target, limit=args.limit
        )

        def send_fn(text: str, dry_run: bool):
            return send_slack(slack_client, target, text, dry_run=dry_run)

    else:
        chat_id = int(target)
        messages = get_conversation_context(
            chat_id=chat_id, db_path=args.db, limit=args.limit
        )
        recipient = get_chat_identifier(chat_id, db_path=args.db)
        if not recipient:
            print(f"error: could not resolve a recipient for chat {chat_id}.", file=sys.stderr)
            return 2

        def send_fn(text: str, dry_run: bool):
            return send_imessage(recipient, text, dry_run=dry_run)

    if not messages:
        print(f"No messages found for {args.source} target {target}.")
        return 0

    llm_client = get_client(provider=args.provider, model=args.model)
    style = None if args.ignore_profile else load_style_profile()
    result = run_reply_flow(
        messages,
        llm_client,
        send_fn,
        intent=args.intent,
        tone=args.tone,
        style=style,
        draft=args.draft,
        num=args.num,
        dry_run=args.dry_run,
        auto_yes=args.yes,
    )
    return 0 if result is not None else 1


def _cmd_profile(args: argparse.Namespace) -> int:
    if args.profile_command == "show":
        profile = load_style_profile()
        if profile is None:
            print("No style profile saved yet. Set one with `profile set`.")
            return 0
        described = profile.describe() or "(empty)"
        print(f"Style profile ({profile_path()}):\n  {described}")
        return 0

    # set: merge provided fields onto the existing profile.
    profile = load_style_profile() or StyleProfile()
    if args.formality is not None:
        profile.formality = args.formality
    if args.directness is not None:
        profile.directness = args.directness
    if args.emoji is not None:
        profile.use_emoji = args.emoji
    if args.concise is not None:
        profile.concise = args.concise
    if args.language is not None:
        profile.language = args.language
    path = save_style_profile(profile)
    print(f"Saved style profile to {path}:\n  {profile.describe() or '(empty)'}")
    return 0


def _cmd_feedback(args: argparse.Namespace) -> int:
    path = record_feedback(
        args.rating, text=args.text, source=args.source, target=args.target
    )
    print(f"Recorded '{args.rating}' feedback in {path}.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "show":
            return _cmd_show(args)
        if args.command == "slack-list":
            return _cmd_slack_list(args)
        if args.command == "slack-show":
            return _cmd_slack_show(args)
        if args.command == "suggest":
            return _cmd_suggest(args)
        if args.command == "send":
            return _cmd_send(args)
        if args.command == "reply":
            return _cmd_reply(args)
        if args.command == "profile":
            return _cmd_profile(args)
        if args.command == "feedback":
            return _cmd_feedback(args)
    except (ChatDatabaseError, LLMError, GenerationError, SlackError, SendError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
