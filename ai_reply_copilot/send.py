"""Sending path for reviewed replies.

iMessage is sent by driving ``Messages.app`` via AppleScript (needs Automation
permission). Slack is sent via ``chat.postMessage``. Sending is a deliberate,
reviewed action: callers should confirm before calling, and ``dry_run`` lets
you preview without sending.

Only plain text is sent — no threaded replies, tapbacks, edits, or other
operations that would require private APIs or disabling SIP (per the PRD).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

from .imessage import IMESSAGE_SERVICE, normalize_service


class SendError(RuntimeError):
    """Raised when a message cannot be sent."""


@dataclass
class SendResult:
    channel: str  # "imessage" or "slack"
    target: str
    text: str
    dry_run: bool
    detail: str = ""


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_imessage_applescript(
    recipient: str, text: str, service: str = IMESSAGE_SERVICE
) -> str:
    """Build the 1:1 send script for ``service`` ("iMessage" or "SMS").

    The service is not cosmetic. An SMS-only contact has no iMessage account, so
    ``1st account whose service type = iMessage`` either picks the wrong account
    or raises — which is why green-bubble chats could not be replied to at all.
    """
    service = normalize_service(service)
    recipient = _escape_applescript(recipient)
    text = _escape_applescript(text)
    # `service` is an AppleScript enum (iMessage / SMS), so it is interpolated
    # unquoted — and only ever from normalize_service, never from user input.
    return (
        'tell application "Messages"\n'
        f"    set targetService to 1st account whose service type = {service}\n"
        f'    set targetBuddy to participant "{recipient}" of targetService\n'
        f'    send "{text}" to targetBuddy\n'
        "end tell"
    )


def _default_osascript_runner(script: str) -> str:
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - macOS only
        raise SendError("osascript not found; iMessage sending needs macOS.") from exc
    if proc.returncode != 0:
        raise SendError(proc.stderr.strip() or "osascript failed")
    return proc.stdout.strip()


def build_imessage_chat_applescript(chat_guid: str, text: str) -> str:
    """Send to an existing chat by GUID — required for group chats, where the
    ``chat_identifier`` is a group stub with no addressable participant."""
    chat_guid = _escape_applescript(chat_guid)
    text = _escape_applescript(text)
    return (
        'tell application "Messages"\n'
        f'    set targetChat to chat id "{chat_guid}"\n'
        f'    send "{text}" to targetChat\n'
        "end tell"
    )


def send_imessage(
    recipient: str,
    text: str,
    *,
    service: str = IMESSAGE_SERVICE,
    dry_run: bool = False,
    runner: Optional[Callable[[str], str]] = None,
) -> SendResult:
    if not text.strip():
        raise SendError("Refusing to send an empty message.")
    service = normalize_service(service)
    script = build_imessage_applescript(recipient, text, service)
    label = "SMS" if service == "SMS" else "iMessage"
    if dry_run:
        return SendResult("imessage", recipient, text, True, f"dry-run (not sent, {label})")
    (runner or _default_osascript_runner)(script)
    return SendResult("imessage", recipient, text, False, f"sent via Messages ({label})")


def send_imessage_to_chat(
    chat_guid: str,
    text: str,
    *,
    dry_run: bool = False,
    runner: Optional[Callable[[str], str]] = None,
) -> SendResult:
    """Send to an existing chat (1:1 or group) by its GUID."""
    if not text.strip():
        raise SendError("Refusing to send an empty message.")
    if not chat_guid:
        raise SendError("No chat GUID to send to.")
    script = build_imessage_chat_applescript(chat_guid, text)
    if dry_run:
        return SendResult("imessage", chat_guid, text, True, "dry-run (not sent)")
    (runner or _default_osascript_runner)(script)
    return SendResult("imessage", chat_guid, text, False, "sent via Messages")


def send_slack(
    client,
    channel: str,
    text: str,
    *,
    thread_ts: Optional[str] = None,
    dry_run: bool = False,
) -> SendResult:
    if not text.strip():
        raise SendError("Refusing to send an empty message.")
    if dry_run:
        return SendResult("slack", channel, text, True, "dry-run (not sent)")
    resp = client.post_message(channel, text, thread_ts=thread_ts)
    return SendResult("slack", channel, text, False, f"sent (ts={resp.get('ts')})")
