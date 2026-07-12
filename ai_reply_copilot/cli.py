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
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__
from . import slack as slack_reader
from .flow import run_reply_flow
from .generate import GenerationError, generate_replies
from .imessage import (
    DEFAULT_CHAT_DB,
    ChatDatabaseError,
    get_chat_send_target,
    get_conversation_context,
    list_conversations,
    render_context,
    sample_sent_messages,
)
from .language import LANGUAGES, resolve_language
from .llm import DEFAULT_PROVIDER, LLMError, get_client
from .models import unanswered_index
from .prompts import INTENTS, TONES, StyleProfile
from .safety import describe_notice, describe_warning, scan_blocking, scan_notice
from .send import SendError, send_imessage, send_imessage_to_chat, send_slack
from .slack import SlackError, posting_client, read_client
from .storage import (
    FEEDBACK_RATINGS,
    ConversationStoreError,
    forget_voice,
    get_conversation_language,
    load_style_profile,
    load_voice,
    profile_path,
    record_feedback,
    save_style_profile,
    save_voice,
    set_conversation_language,
    voice_path,
)
from .voice import describe_for_prompt, learn_voice


# Distinct from the generic error exit (2) so a front-end can tell "you must
# confirm before this leaves the machine" apart from "something broke".
EXIT_SENSITIVE = 3


def _add_db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_CHAT_DB,
        help=f"Path to chat.db (default: {DEFAULT_CHAT_DB})",
    )


def _add_language_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--language",
        choices=list(LANGUAGES),
        help="Reply language for this run. By default Charla follows the "
        "conversation (English channel -> English reply, 中文对话 -> 中文回复).",
    )
    parser.add_argument(
        "--remember-language",
        action="store_true",
        help="Lock the resolved language to this conversation for future runs",
    )
    parser.add_argument(
        "--no-context",
        action="store_true",
        help="Do not print the messages Charla read",
    )


def _add_allow_sensitive_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--allow-sensitive",
        action="store_true",
        help="Send the context to the cloud model even if it looks sensitive "
        "(otherwise Charla asks first, and refuses when it cannot ask)",
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
        default=DEFAULT_PROVIDER,
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
    p_suggest.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON (for front-ends like the menu-bar app)",
    )
    _add_language_args(p_suggest)
    _add_allow_sensitive_arg(p_suggest)
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
        "--provider", choices=["openai", "anthropic"], default=DEFAULT_PROVIDER
    )
    p_reply.add_argument("--model", help="Override the default model")
    p_reply.add_argument("--dry-run", action="store_true", help="Preview without sending")
    p_reply.add_argument("--yes", action="store_true", help="Skip send confirmation")
    p_reply.add_argument(
        "--ignore-profile",
        action="store_true",
        help="Do not apply the saved personal style profile",
    )
    _add_language_args(p_reply)
    _add_allow_sensitive_arg(p_reply)
    _add_db_arg(p_reply)

    p_voice = sub.add_parser(
        "voice",
        help="Learn how you actually write, from your own sent messages",
    )
    voice_sub = p_voice.add_subparsers(dest="voice_command", required=True)
    p_voice_learn = voice_sub.add_parser(
        "learn",
        help="Read your own past messages from chat.db and learn your voice",
    )
    p_voice_learn.add_argument(
        "--sample", type=int, default=400, help="How many of your messages to read"
    )
    p_voice_learn.add_argument(
        "--examples",
        type=int,
        default=8,
        help="How many verbatim messages to keep as voice examples (0 = stats only, "
        "nothing of yours is ever uploaded)",
    )
    p_voice_learn.add_argument(
        "--yes", action="store_true", help="Save without confirming"
    )
    _add_db_arg(p_voice_learn)
    voice_sub.add_parser("show", help="Show the learned voice (and what would be uploaded)")
    voice_sub.add_parser("forget", help="Delete the learned voice")

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
    client = read_client()
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
    client = read_client()
    messages = slack_reader.get_conversation_context(
        client, channel_id=args.channel, limit=args.limit
    )
    if not messages:
        print(f"No messages found for channel {args.channel}.")
        return 0
    print(render_context(messages))
    return 0


def _note(message: str) -> None:
    """Human-readable chatter. Always stderr: stdout may be a JSON payload that a
    front-end (the macOS shell) parses, and prose there corrupts it."""
    print(message, file=sys.stderr)


def _resolve_target(args: argparse.Namespace) -> Optional[str]:
    """Return the explicit target, or fall back to the most recent conversation."""
    if args.target is not None:
        return args.target
    if args.source == "slack":
        conversations = slack_reader.list_conversations(read_client(), limit=1)
        if not conversations:
            _note("No Slack conversations found.")
            return None
        convo = conversations[0]
        _note(f"Using most recent conversation: {convo.title}")
        return convo.channel_id
    conversations = list_conversations(db_path=args.db, limit=1)
    if not conversations:
        _note("No conversations found.")
        return None
    convo = conversations[0]
    _note(f"Using most recent conversation: {convo.title}")
    return str(convo.chat_id)


def _sensitive_gate(args: argparse.Namespace, messages) -> Tuple[bool, List[str]]:
    """Ask before any conversation context leaves the machine.

    This runs on *every* path that reaches a cloud model. In particular it is not
    waived by ``--dry-run`` (which only suppresses *sending*, not the upload) nor
    by ``--yes`` (a send flag, not a privacy decision). The user's own ``--draft``
    is scanned too — it is put in the prompt just like the conversation is.

    Only an *actual* secret stops the run (see safety.scan_blocking). A merely
    sensitive *topic* — a salary, an offer, a contract — is reported and waved
    through: those are the professional replies Charla exists for, and a gate
    that fires on every one of them just teaches the user to click past it.

    Returns ``(ok_to_proceed, categories)``. Callers must report *these*
    categories rather than rescanning, or the reported set drifts from the
    gated one (the draft would be dropped).
    """
    scanned = render_context(messages)
    if getattr(args, "draft", None):
        scanned = f"{scanned}\n{args.draft}"

    blocking = scan_blocking(scanned)
    notices = [c for c in scan_notice(scanned) if c not in blocking]
    categories = blocking + notices

    if notices:
        _note(describe_notice(notices))  # informative, never interrupts
    if not blocking:
        return True, categories

    if getattr(args, "allow_sensitive", False):
        _note(describe_warning(blocking) + " (allowed via --allow-sensitive)")
        return True, categories

    _note(describe_warning(blocking))
    if getattr(args, "json", False):
        # A machine is on the other end: there is nobody to ask, and prompting
        # would corrupt the JSON on stdout. Refuse rather than upload silently.
        # EXIT_SENSITIVE lets a front-end offer its own "draft anyway" instead of
        # treating this as a generic failure.
        _note("Refusing to send it to a cloud model. Re-run with --allow-sensitive to continue.")
        return False, categories
    # _confirm returns False on EOF, so a piped/unattended run also refuses.
    return _confirm("Continue anyway? [y/N] "), categories


def _resolve_language(args: argparse.Namespace, target: str, messages) -> Tuple[Optional[str], str]:
    """Pick the reply language for *this* conversation, and optionally lock it.

    The style profile is only a fallback here — see language.resolve_language.
    """
    # --ignore-profile must ignore *all* of the profile, language included:
    # the fallback language is part of it and used to leak into the prompt.
    profile = None if getattr(args, "ignore_profile", False) else load_style_profile()
    language, reason = resolve_language(
        messages,
        explicit=getattr(args, "language", None),
        locked=get_conversation_language(args.source, str(target)),
        profile_language=profile.language if profile else None,
    )
    if getattr(args, "remember_language", False):
        if not language:
            _note("Nothing to remember: no language could be determined.")
        else:
            set_conversation_language(args.source, str(target), language)
            _note(f"Locked this conversation to {language}.")
            reason = "已锁定 / locked to this conversation"
    return language, reason


def _format_context_panel(messages, language: Optional[str], reason: str) -> str:
    """Show what Charla actually read, so the drafts are not a black box.

    PRD §10 promises the user can see the context the AI read; without this you
    cannot tell whether it picked the right thread or is answering stale history.
    """
    lines = ["─" * 60, f"Charla read {len(messages)} message(s):", ""]
    start = unanswered_index(messages)
    for index, message in enumerate(messages):
        if start is not None and index == start:
            lines.append("  ── 以下是你上次回复之后的新消息 / new since your last reply ──")
        lines.append(f"  {message.format_line()}")
    lines.append("")
    shown = language or "跟随对话 / mirror the conversation"
    lines.append(f"Reply language: {shown}  ({reason})")
    lines.append("─" * 60)
    return "\n".join(lines)


def _format_understanding(suggestion) -> str:
    lines = []
    if suggestion.understanding:
        lines.append(f"Understanding: {suggestion.understanding}")
    if suggestion.open_points:
        lines.append("")
        lines.append("你还没回应 / Waiting on you:")
        for point in suggestion.open_points:
            lines.append(f"  • {point}")
    lines.append("")
    return "\n".join(lines)


def _cmd_suggest(args: argparse.Namespace) -> int:
    target = _resolve_target(args)
    if target is None:
        return 0
    if args.source == "slack":
        slack_client = read_client()
        messages = slack_reader.get_conversation_context(
            slack_client, channel_id=target, limit=args.limit
        )
    else:
        messages = get_conversation_context(
            chat_id=int(target), db_path=args.db, limit=args.limit
        )
    if not messages:
        _note(f"No messages found for {args.source} target {target}.")
        return 0

    ok, categories = _sensitive_gate(args, messages)
    if not ok:
        return EXIT_SENSITIVE

    language, reason = _resolve_language(args, target, messages)
    client = get_client(provider=args.provider, model=args.model)
    style, voice = _load_style_and_voice(args)
    suggestion = generate_replies(
        messages,
        client,
        intent=args.intent,
        tone=args.tone,
        style=style,
        draft=args.draft,
        num_candidates=args.num,
        language=language,
        voice=voice,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "source": args.source,
                    "target": str(target),
                    "understanding": suggestion.understanding,
                    "open_points": suggestion.open_points,
                    "candidates": suggestion.candidates,
                    "language": language,
                    "sensitive": categories,
                },
                ensure_ascii=False,
            )
        )
        return 0

    if not args.no_context:
        print(_format_context_panel(messages, language, reason))
    print(_format_understanding(suggestion))
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
        # A dry run makes no network call, so it must not demand a token.
        client = None if args.dry_run else posting_client()
        result = send_slack(client, args.to, args.text, dry_run=args.dry_run)
    else:
        result = send_imessage(args.to, args.text, dry_run=args.dry_run)

    print(result.detail)
    return 0


def _cmd_reply(args: argparse.Namespace) -> int:
    target = _resolve_target(args)
    if target is None:
        return 0
    if args.source == "slack":
        slack_client = read_client()
        messages = slack_reader.get_conversation_context(
            slack_client, channel_id=target, limit=args.limit
        )
        # Resolve the posting token up front. Deferring it into send_fn would
        # only fail *after* the LLM was billed and the user picked a draft.
        send_client = None if args.dry_run else posting_client()

        def send_fn(text: str, dry_run: bool):
            # Read with whichever token has the scopes; always post as you.
            return send_slack(send_client, target, text, dry_run=dry_run)

    else:
        chat_id = int(target)
        messages = get_conversation_context(
            chat_id=chat_id, db_path=args.db, limit=args.limit
        )
        chat = get_chat_send_target(chat_id, db_path=args.db)
        if chat is None or not (chat.guid or chat.identifier):
            print(f"error: could not resolve a recipient for chat {chat_id}.", file=sys.stderr)
            return 2

        if chat.is_group:
            # Groups must be addressed by GUID; the chat_identifier is a stub.
            def send_fn(text: str, dry_run: bool):
                return send_imessage_to_chat(chat.guid, text, dry_run=dry_run)
        else:
            def send_fn(text: str, dry_run: bool):
                return send_imessage(chat.recipient, text, dry_run=dry_run)

    if not messages:
        _note(f"No messages found for {args.source} target {target}.")
        return 0

    # Note this gates even under --dry-run/--yes: those govern *sending*, but the
    # context is uploaded to draft regardless, so the privacy call comes first.
    ok, _categories = _sensitive_gate(args, messages)
    if not ok:
        print("Cancelled.")
        return EXIT_SENSITIVE

    language, reason = _resolve_language(args, target, messages)
    if not args.no_context:
        print(_format_context_panel(messages, language, reason))

    llm_client = get_client(provider=args.provider, model=args.model)
    style, voice = _load_style_and_voice(args)
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
        language=language,
        voice=voice,
    )
    return 0 if result is not None else 1


def _load_style_and_voice(args: argparse.Namespace):
    """Style and learned voice, both suppressed by --ignore-profile."""
    if getattr(args, "ignore_profile", False):
        return None, None
    return load_style_profile(), describe_for_prompt(load_voice())


def _render_voice(profile) -> str:
    lines = [f"Learned from {profile.sampled} of your own messages:"]
    described = profile.describe()
    if described:
        lines.append(f"  {described}")
    if profile.exemplars:
        lines.append("")
        lines.append(
            f"  {len(profile.exemplars)} of your messages are kept as voice examples. "
            "These are\n  included in the prompt on every draft, so they DO go to the "
            "cloud model —\n  including when you reply to someone else:"
        )
        for example in profile.exemplars:
            lines.append(f"    · {example}")
    else:
        lines.append("")
        lines.append("  No verbatim examples kept — statistics only, nothing of yours is uploaded.")
    return "\n".join(lines)


def _cmd_voice(args: argparse.Namespace) -> int:
    if args.voice_command == "show":
        profile = load_voice()
        if profile is None or not profile.sampled:
            print("No voice learned yet. Run `charla voice learn`.")
            return 0
        print(f"Voice ({voice_path()}):")
        print(_render_voice(profile))
        return 0

    if args.voice_command == "forget":
        print("Forgot the learned voice." if forget_voice() else "Nothing to forget.")
        return 0

    # learn
    texts = sample_sent_messages(db_path=args.db, limit=args.sample)
    if not texts:
        print("Found none of your own messages in chat.db — nothing to learn from.")
        return 1

    profile = learn_voice(texts, exemplar_limit=max(0, args.examples))
    print(_render_voice(profile))
    print()
    print(
        "Statistics stay on this machine. Voice examples are part of the prompt, so\n"
        "they are uploaded with every draft — messages containing secrets were already\n"
        "screened out. Use `--examples 0` for statistics only."
    )
    if not args.yes and not _confirm("\nSave this voice? [y/N] "):
        print("Not saved.")
        return 1
    path = save_voice(profile)
    print(f"Saved to {path}. Drafts will now sound like you. (`charla voice forget` undoes it.)")
    return 0


def _describe_profile(profile: StyleProfile) -> str:
    """One renderer for `profile show` and `profile set`, so they can't drift.

    ``language`` is shown separately and labelled a *fallback*: it no longer
    overrides the conversation, so an English work channel still gets an English
    reply even when this says 中文.
    """
    lines = [f"  {profile.describe() or '(no style set)'}"]
    if profile.language:
        lines.append(f"  fallback language: {profile.language}")
        lines.append(
            "    (only used when a conversation gives no language signal — "
            "Charla otherwise follows the conversation.\n"
            "     To pin one conversation: --language X --remember-language)"
        )
    return "\n".join(lines)


def _cmd_profile(args: argparse.Namespace) -> int:
    if args.profile_command == "show":
        profile = load_style_profile()
        if profile is None:
            print("No style profile saved yet. Set one with `profile set`.")
            return 0
        print(f"Style profile ({profile_path()}):")
        print(_describe_profile(profile))
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
    print(f"Saved style profile to {path}:")
    print(_describe_profile(profile))
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
        if args.command == "voice":
            return _cmd_voice(args)
        if args.command == "profile":
            return _cmd_profile(args)
        if args.command == "feedback":
            return _cmd_feedback(args)
    except (
        ChatDatabaseError,
        LLMError,
        GenerationError,
        SlackError,
        SendError,
        ConversationStoreError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
