"""Per-conversation reply language.

The language of a reply belongs to the *conversation*, not to a global setting:
the same person writes English in a work Slack channel and Chinese to friends on
iMessage. So the language is detected from the recent messages, can be locked per
conversation when the detection is wrong, and the style profile is only a
tiebreaker when there is nothing to go on.

Detection is a local character-class heuristic — no model call, no network.
"""

from __future__ import annotations

import unicodedata
from typing import List, Optional

from .models import Message

CHINESE = "中文"
ENGLISH = "English"
MIXED = "中英混合"

LANGUAGES = (CHINESE, ENGLISH, MIXED)

# Share of CJK characters (vs Latin letters) at which a conversation counts as
# Chinese. Deliberately low: Chinese is information-dense, and a bilingual
# speaker keeps loanwords in English — "这个 API migration 什么时候 deploy?" is only
# ~25% CJK but is plainly a Chinese sentence, and a Chinese reply will keep
# "deploy" in English on its own. Calling that "mixed" would push the model into
# forced, unnatural code-switching.
#
# MIXED is reserved for the genuinely English-dominant sentence carrying a
# Chinese clause ("Let's sync tomorrow, 我这边没问题").
_CHINESE_MIN_SHARE = 0.20
_MIXED_MIN_SHARE = 0.08

# How many of the most recent messages decide the language. Older history may be
# in a different language than the conversation has since settled into.
_RECENT_WINDOW = 6


def _is_cjk(char: str) -> bool:
    return "CJK" in unicodedata.name(char, "")


def _script_counts(text: str) -> tuple:
    """Return (cjk_chars, latin_letters) in ``text``."""
    cjk = latin = 0
    for char in text:
        if char.isascii() and char.isalpha():
            latin += 1
        elif _is_cjk(char):
            cjk += 1
    return cjk, latin


def detect_language(messages: List[Message]) -> Optional[str]:
    """Detect the language of a conversation, or None if there is no signal.

    Weighs the most recent messages only, and ignores the user's own messages
    when the other side has said anything — you reply in *their* language, and
    your own past lines may be from before the conversation switched.
    """
    recent = [m for m in messages if m.text.strip()][-_RECENT_WINDOW:]
    if not recent:
        return None

    def counts(considered) -> tuple:
        cjk = latin = 0
        for message in considered:
            message_cjk, message_latin = _script_counts(message.text)
            cjk += message_cjk
            latin += message_latin
        return cjk, latin

    theirs = [m for m in recent if not m.is_from_me]
    cjk, latin = counts(theirs or recent)
    if cjk + latin == 0 and theirs:
        # Their recent messages carry no script at all ("👍", a bare link). Fall
        # back to the whole window rather than conceding — the user's own lines
        # are still a strong signal for which language this thread is in.
        cjk, latin = counts(recent)

    total = cjk + latin
    if total == 0:
        return None  # emoji/links/numbers only — no signal

    share = cjk / total
    if share >= _CHINESE_MIN_SHARE:
        return CHINESE
    if share >= _MIXED_MIN_SHARE:
        return MIXED
    return ENGLISH


def resolve_language(
    messages: List[Message],
    *,
    explicit: Optional[str] = None,
    locked: Optional[str] = None,
    profile_language: Optional[str] = None,
) -> tuple:
    """Decide the reply language and say why.

    Order: an explicit one-off flag, then a language locked to this conversation,
    then what the conversation itself is in, and only then the style profile —
    which is a *fallback*, not an override. A global "always 中英双语" that
    overrode the conversation would put bilingual replies into an English work
    channel, which is exactly wrong.

    Returns ``(language, reason)``; ``language`` is None when nothing is known,
    in which case the model is left to mirror the conversation on its own.
    """
    if explicit:
        return explicit, "指定 / explicit"
    if locked:
        return locked, "已锁定 / locked to this conversation"
    detected = detect_language(messages)
    if detected:
        return detected, "自动检测 / detected"
    if profile_language:
        return profile_language, "个人设置兜底 / from your profile"
    return None, "跟随对话 / mirror the conversation"
