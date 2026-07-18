"""End-to-end reply flow: understand -> suggest -> pick/edit -> review -> send.

The orchestration is UI-agnostic: ``prompt`` and ``output`` are injectable so
the loop can be driven by the CLI or by tests without real stdin/stdout. The
actual send is delegated to ``send_fn`` so this module stays channel-agnostic.
"""

from __future__ import annotations

import sys
from typing import Callable, List, Optional

from .generate import generate_replies
from .models import Message
from .prompts import StyleProfile
from .send import SendResult
from .voice_eval import off_voice_hints


def _edit_line(prompt_text: str, initial: str) -> str:
    """Read a line that starts out containing ``initial``, editable in place.

    Falls back to plain input when there is no terminal to edit in (a pipe, a
    test) or when readline is missing — degrade, never crash.
    """
    try:
        import readline
    except ImportError:  # pragma: no cover - readline ships with CPython on macOS
        readline = None

    if readline is None or not sys.stdin.isatty():
        return input(prompt_text)

    def _hook() -> None:
        readline.insert_text(initial)
        readline.redisplay()

    readline.set_startup_hook(_hook)
    try:
        return input(prompt_text)
    finally:
        readline.set_startup_hook()  # never leak the hook into a later prompt


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
    voice_profile=None,
    prompt: Optional[Callable[[str], str]] = None,
    output: Optional[Callable[[str], None]] = None,
    edit: Optional[Callable[[str, str], str]] = None,
) -> Optional[SendResult]:
    """Run the interactive reply loop; return the SendResult or None if cancelled."""
    # Resolve at call time so callers/tests can monkeypatch builtins.
    if edit is None:
        # In-place editing needs a real terminal. A caller that injected its own
        # `prompt` (the tests, or any non-terminal front-end) drives the edit
        # through that instead of reaching for stdin behind its back.
        edit = _edit_line if prompt is None else (lambda text, _initial: prompt(text))
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
        # Flag a candidate that severely breaks the learned voice *before* the
        # user picks it — the whole point of learning a voice is lost if the
        # off-voice draft is chosen because nothing said so.
        for hint in off_voice_hints(voice_profile, candidate):
            output(f"   ⚠ {hint}")

    choice = prompt(
        "\nPick a number to send, e<n> to edit, or q to cancel: "
    ).strip().lower()
    if choice in ("", "q", "quit", "exit"):
        output("Cancelled.")
        return None

    # "e2" edits candidate 2. Checking the prefix alone also swallowed "exit",
    # which then failed to parse as an index and reported "Invalid choice".
    if len(choice) > 1 and choice[0] == "e" and choice[1:].isdigit():
        idx = _parse_index(choice[1:], len(suggestion.candidates))
        if idx is None:
            output("Invalid choice.")
            return None
        # Prefilled, so tweaking a draft is a tweak. Retyping a 300-character
        # reply from scratch is the single most-used step of the core loop, and
        # it was the reason to abandon the flow rather than fix a word.
        edited = edit("Edit: ", suggestion.candidates[idx]).strip()
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
