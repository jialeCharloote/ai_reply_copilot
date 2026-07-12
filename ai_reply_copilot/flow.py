"""End-to-end reply flow: understand -> suggest -> pick/edit -> review -> send.

The orchestration is UI-agnostic: ``prompt`` and ``output`` are injectable so
the loop can be driven by the CLI or by tests without real stdin/stdout. The
actual send is delegated to ``send_fn`` so this module stays channel-agnostic.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from .generate import generate_replies
from .models import Message
from .prompts import StyleProfile
from .send import SendResult


def _parse_index(raw: str, count: int) -> Optional[int]:
    try:
        value = int(raw)
    except ValueError:
        return None
    if 1 <= value <= count:
        return value - 1
    return None


def run_reply_flow(
    messages: List[Message],
    llm_client,
    send_fn: Callable[[str, bool], SendResult],
    *,
    intent: Optional[str] = None,
    tone: Optional[str] = None,
    style: Optional[StyleProfile] = None,
    draft: Optional[str] = None,
    num: int = 3,
    dry_run: bool = False,
    auto_yes: bool = False,
    language: Optional[str] = None,
    voice: Optional[str] = None,
    prompt: Optional[Callable[[str], str]] = None,
    output: Optional[Callable[[str], None]] = None,
) -> Optional[SendResult]:
    """Run the interactive reply loop; return the SendResult or None if cancelled."""
    # Resolve at call time so callers/tests can monkeypatch builtins.
    prompt = prompt or input
    output = output or print

    suggestion = generate_replies(
        messages,
        llm_client,
        intent=intent,
        tone=tone,
        style=style,
        draft=draft,
        num_candidates=num,
        language=language,
        voice=voice,
    )

    if suggestion.understanding:
        output(f"Understanding: {suggestion.understanding}")
    if suggestion.open_points:
        output("\n你还没回应 / Waiting on you:")
        for point in suggestion.open_points:
            output(f"  • {point}")
    output("")
    for index, candidate in enumerate(suggestion.candidates, start=1):
        output(f"{index}. {candidate}")

    choice = prompt(
        "\nPick a number to send, e<n> to edit, or q to cancel: "
    ).strip().lower()
    if choice in ("", "q", "quit"):
        output("Cancelled.")
        return None

    if choice.startswith("e"):
        idx = _parse_index(choice[1:], len(suggestion.candidates))
        if idx is None:
            output("Invalid choice.")
            return None
        edited = prompt("Edit the reply: ").strip()
        text = edited or suggestion.candidates[idx]
    else:
        idx = _parse_index(choice, len(suggestion.candidates))
        if idx is None:
            output("Invalid choice.")
            return None
        text = suggestion.candidates[idx]

    output(f"\nReply:\n  {text}")
    if not dry_run and not auto_yes:
        confirm = prompt("Send this? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            output("Cancelled.")
            return None

    result = send_fn(text, dry_run)
    output(result.detail)
    return result
