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


# Share of a thread's script that must be kana/hangul before we admit we cannot
# read it. One 漢字-adjacent character in an English sentence should not trigger it.
_UNSUPPORTED_MIN_SHARE = 0.15


def _is_cjk(char: str) -> bool:
    return "CJK" in unicodedata.name(char, "")


def _is_unsupported_script(char: str) -> bool:
    """Japanese kana or Korean hangul — scripts Charla does not model.

    Unicode names these HIRAGANA/KATAKANA/HANGUL, none of which contain "CJK", so
    a kana character counted as *neither* CJK nor Latin and simply vanished from
    the tally. A Japanese sentence was therefore judged on its kanji alone and
    came back 100% "CJK" — i.e. Chinese. Charla would then confidently draft a
    Chinese reply to a Japanese message, with "自动检测 / detected" as its reason.
    """
    name = unicodedata.name(char, "")
    return name.startswith(("HIRAGANA", "KATAKANA", "HANGUL"))


def _script_counts(text: str) -> tuple:
    """Return (cjk_chars, latin_letters, unsupported_chars) in ``text``."""
    cjk = latin = unsupported = 0
    for char in text:
        if char.isascii() and char.isalpha():
            latin += 1
        elif _is_unsupported_script(char):
            unsupported += 1
        elif _is_cjk(char):
            cjk += 1
    return cjk, latin, unsupported


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
        cjk = latin = unsupported = 0
        for message in considered:
            message_cjk, message_latin, message_other = _script_counts(message.text)
            cjk += message_cjk
            latin += message_latin
            unsupported += message_other
        return cjk, latin, unsupported

    theirs = [m for m in recent if not m.is_from_me]
    cjk, latin, unsupported = counts(theirs or recent)
    if cjk + latin + unsupported == 0 and theirs:
        # Their recent messages carry no script at all ("👍", a bare link). Fall
        # back to the whole window rather than conceding — the user's own lines
        # are still a strong signal for which language this thread is in.
        cjk, latin, unsupported = counts(recent)

    total = cjk + latin + unsupported
    if total == 0:
        return None  # emoji/links/numbers only — no signal

    if unsupported / total >= _UNSUPPORTED_MIN_SHARE:
        # Japanese or Korean. Charla models Chinese and English; claiming one of
        # them here would be worse than admitting we don't know, because the model
        # mirrors the conversation perfectly well when we say nothing.
        return None

    share = cjk / (cjk + latin) if cjk + latin else 0.0
    if share >= _CHINESE_MIN_SHARE:
        return CHINESE
    if share >= _MIXED_MIN_SHARE:
        return MIXED
    return ENGLISH


def is_unsupported_script(messages: List[Message]) -> bool:
    """True when the thread is mostly a script Charla does not model (ja/ko)."""
    recent = [m for m in messages if m.text.strip()][-_RECENT_WINDOW:]
    cjk = latin = unsupported = 0
    for message in recent:
        message_cjk, message_latin, message_other = _script_counts(message.text)
        cjk += message_cjk
        latin += message_latin
        unsupported += message_other
    total = cjk + latin + unsupported
    return bool(total) and unsupported / total >= _UNSUPPORTED_MIN_SHARE


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
    if is_unsupported_script(messages):
        # A Japanese or Korean thread. The profile fallback must not fire here:
        # a profile saying 中文 would answer a Japanese message in Chinese, which
        # is the worst of the available options. Let the model mirror instead.
        return None, "跟随对话 / mirror the conversation"
    if profile_language:
        return profile_language, "个人设置兜底 / from your profile"
    return None, "跟随对话 / mirror the conversation"
