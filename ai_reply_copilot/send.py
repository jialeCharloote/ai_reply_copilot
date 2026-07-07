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


def build_imessage_applescript(recipient: str, text: str) -> str:
    recipient = _escape_applescript(recipient)
    text = _escape_applescript(text)
    return (
        'tell application "Messages"\n'
        "    set targetService to 1st account whose service type = iMessage\n"
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


def send_imessage(
    recipient: str,
    text: str,
    *,
    dry_run: bool = False,
    runner: Optional[Callable[[str], str]] = None,
) -> SendResult:
    if not text.strip():
        raise SendError("Refusing to send an empty message.")
    script = build_imessage_applescript(recipient, text)
    if dry_run:
        return SendResult("imessage", recipient, text, True, "dry-run (not sent)")
    (runner or _default_osascript_runner)(script)
    return SendResult("imessage", recipient, text, False, "sent via Messages")


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
