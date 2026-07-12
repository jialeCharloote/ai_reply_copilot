"""Learn how the user actually writes, from the messages they actually sent.

The hand-typed style questionnaire ("formality: casual") is the weakest part of
the product: adjectives do not make a model sound like you, and asking the user
to describe their own voice is asking the wrong person a hard question. Meanwhile
``chat.db`` already holds thousands of messages they really wrote (``is_from_me``).

Two things come out of that, and they are deliberately separated because they
have different privacy costs:

- **Statistics** (`VoiceProfile`) — length, emoji rate, punctuation, capitalisation,
  language mix. Computed locally, never leave the machine as text, and already
  beat the questionnaire.
- **Exemplars** — a handful of verbatim messages. These are what actually make a
  model imitate you, and they are the expensive half: they come from *other*
  conversations and get uploaded with the prompt. So they are opt-in, screened
  through the blocking safety scanner first, and shown to the user before saving.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional

from .language import CHINESE, ENGLISH, detect_language
from .models import Message
from .safety import scan_blocking

# Bump whenever the exemplar screen gets stricter: a profile saved under older,
# weaker rules must not keep uploading its exemplars. storage.load_voice ignores
# a profile whose version does not match, forcing a re-learn.
VOICE_SCHEMA_VERSION = 1

# Messages outside this band say little about voice: a bare "ok" carries no
# style, and a pasted paragraph is not how the user texts.
_MIN_EXEMPLAR_CHARS = 12
_MAX_EXEMPLAR_CHARS = 280

_URL_RE = re.compile(r"https?://\S+")
_EMOJI_RE = re.compile(
    "[" "\U0001f300-\U0001faff" "\U00002600-\U000027bf" "\U0001f1e6-\U0001f1ff" "]"
)


def _has_emoji(text: str) -> bool:
    if _EMOJI_RE.search(text):
        return True
    return any(unicodedata.category(ch) == "So" for ch in text)


@dataclass
class VoiceProfile:
    """What the user's own messages say about how they write."""

    sampled: int = 0
    median_chars: int = 0
    emoji_rate: float = 0.0
    ends_with_period_rate: float = 0.0
    starts_lowercase_rate: float = 0.0
    exclamation_rate: float = 0.0
    question_rate: float = 0.0
    chinese_rate: float = 0.0
    exemplars: List[str] = field(default_factory=list)

    def describe(self) -> str:
        """A prompt-ready description. Empty when nothing was learned."""
        if not self.sampled:
            return ""
        bits = [f"median message length {self.median_chars} characters"]
        bits.append(
            "uses emoji often" if self.emoji_rate > 0.30
            else "uses emoji occasionally" if self.emoji_rate > 0.08
            else "almost never uses emoji"
        )
        bits.append(
            "usually ends sentences with a period"
            if self.ends_with_period_rate > 0.6
            else "usually leaves off the final period"
        )
        if self.starts_lowercase_rate > 0.4:
            bits.append("often starts messages in lowercase")
        if self.exclamation_rate > 0.25:
            bits.append("uses exclamation marks freely")
        if self.question_rate > 0.35:
            bits.append("often asks a question back")
        if 0.15 < self.chinese_rate < 0.85:
            bits.append(
                f"writes in both Chinese and English ({round(self.chinese_rate * 100)}% Chinese)"
            )
        return "; ".join(bits)


def _median(values: List[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _as_text(sample) -> str:
    return (getattr(sample, "text", sample) or "").strip()


def analyze_voice(samples) -> VoiceProfile:
    """Compute local statistics over the user's own messages. No network, no model.

    Accepts plain strings or ``imessage.SentSample`` objects.
    """
    usable = [t for t in (_as_text(s) for s in samples) if t]
    if not usable:
        return VoiceProfile()

    lengths = [len(t) for t in usable]
    chinese = 0
    for text in usable:
        detected = detect_language([Message(text=text, is_from_me=True, timestamp=None, sender=None)])
        if detected == CHINESE:
            chinese += 1

    total = len(usable)
    return VoiceProfile(
        sampled=total,
        median_chars=_median(lengths),
        emoji_rate=sum(_has_emoji(t) for t in usable) / total,
        ends_with_period_rate=sum(t.endswith((".", "。")) for t in usable) / total,
        starts_lowercase_rate=sum(t[:1].islower() for t in usable) / total,
        exclamation_rate=sum(("!" in t or "！" in t) for t in usable) / total,
        question_rate=sum(("?" in t or "？" in t) for t in usable) / total,
        chinese_rate=chinese / total,
    )


# Selecting an exemplar is not a detection problem — it is a *selection* problem,
# and that difference is the whole safety argument here. An exemplar is uploaded
# with every future draft, forever, including when replying to someone else, so a
# missed secret leaks far beyond the conversation it came from. But there are
# thousands of candidate messages and only eight slots, so throwing away anything
# that merely *smells* like a secret costs nothing. These reject the shapes a
# vocabulary-based scanner will always miss: AWS keys, JWTs, PINs, order numbers,
# anything with the entropy of a token rather than of a sentence.
_LONG_DIGIT_RUN = re.compile(r"\d{5,}")
# Hyphens and dots must be inside the character classes: "Tulip-Garden-88" and
# "Blue-Sky-Rain" are exactly the shape of a shared wifi/door code, and leaving
# `-` out let every hyphenated password through.
_TOKENISH = re.compile(r"(?=[\w.\-+/=]*[A-Za-z])(?=[\w.\-+/=]*\d)[\w.\-+/=]{10,}")
_BASE64ISH = re.compile(r"\b[A-Za-z0-9+/]{20,}={0,2}\b")
# A "word" mixing letters, digits AND punctuation looks like a password and not
# like a sentence — "Passw0rd!23" carries no keyword to key off.
_PASSWORDISH = re.compile(r"\S*(?=\S*[A-Za-z])(?=\S*\d)(?=\S*[!@#$%^&*_+=?~-])\S{6,}")
# "Blue-Sky-Rain", "Tulip-Garden-88" — the shape of a shared wifi/door code. Three
# hyphen-joined capitalised words is not a shape English sentences take.
_CODEPHRASE = re.compile(r"\b[A-Z][a-z]{2,}-[A-Z][a-z]{2,}-[A-Za-z0-9]{2,}\b")

# Words that make a message *about* a secret. Deliberately NOT `code` / `key` /
# `account` / `login`: those are four of the commonest words in professional
# English ("review the code", "the key insight", "check my account"), and
# rejecting them silently biased the exemplar pool away from exactly the register
# this product exists to imitate. The narrow list below rarely appears in an
# ordinary sentence, so rejecting on it costs almost nothing.
_SECRET_WORDS = re.compile(
    r"\b(?:password|passwd|pwd|passcode|passphrase|api[_ -]?key|secret[_ -]?key"
    r"|access[_ -]?key|private[_ -]?key|ssh[_ -]?key|otp|2fa|mfa|seed phrase"
    r"|routing|iban|swift code|cvv|ssn)\b",
    re.I,
)
_SECRET_WORDS_CJK = ("密码", "口令", "验证码", "密钥", "秘钥", "私钥", "助记词", "暗号", "卡号")


def _smells_secret(text: str) -> bool:
    """Deliberately over-eager: a false positive costs one message out of
    thousands; a false negative uploads a secret with every future draft."""
    if scan_blocking(text):
        return True
    if _LONG_DIGIT_RUN.search(text) or _TOKENISH.search(text) or _BASE64ISH.search(text):
        return True
    if _PASSWORDISH.search(text) or _CODEPHRASE.search(text) or _SECRET_WORDS.search(text):
        return True
    return any(term in text for term in _SECRET_WORDS_CJK)


def is_safe_exemplar(text: str, context: str = "") -> bool:
    """Whether a message of the user's can be memorialised into every prompt.

    ``context`` is the message it was answering, and it is not optional in
    spirit: the reply to "what's the wifi password?" is a bare word like
    ``sunshinecoast``, which carries no marker of its own. The only thing that
    identifies it as a secret lives in the *other* person's message, so a scanner
    that looks at the user's message alone is structurally blind to it — no amount
    of extra regex closes that gap.

    The honest residual: a diceware passphrase sent with no surrounding context
    ("correct horse battery staple") is, in isolation, indistinguishable from an
    ordinary sentence. Context is the only thing that catches it, which is why it
    is threaded through here rather than papered over with more patterns.
    """
    candidate = (text or "").strip()
    if not (_MIN_EXEMPLAR_CHARS <= len(candidate) <= _MAX_EXEMPLAR_CHARS):
        return False
    if _URL_RE.search(candidate):
        return False  # links say nothing about voice and can carry tokens
    if _smells_secret(candidate):
        return False
    # If they were *asked* for a secret, whatever they answered is one.
    return not (context and _smells_secret(context))


def pick_exemplars(samples, limit: int = 8) -> List[str]:
    """Choose verbatim messages that show the user's voice.

    Accepts plain strings or ``imessage.SentSample`` objects; the latter carry the
    message being replied to, which is what makes screening actually sound.

    Topic-level notices are fine — a message mentioning a salary is not a secret,
    and excluding it would strip out exactly the professional register we want.
    """
    if limit <= 0:
        return []  # "statistics only" must upload nothing, not one message
    seen = set()
    chosen: List[str] = []
    for sample in samples:
        text = getattr(sample, "text", sample) or ""
        context = getattr(sample, "prompt_text", "") or ""
        candidate = text.strip()
        if not is_safe_exemplar(candidate, context):
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        chosen.append(candidate)
        if len(chosen) >= limit:
            break
    return chosen


def learn_voice(samples, *, exemplar_limit: int = 8) -> VoiceProfile:
    """Statistics plus screened exemplars, from the user's own sent messages."""
    profile = analyze_voice(samples)
    if profile.sampled:
        profile.exemplars = pick_exemplars(samples, limit=exemplar_limit)
    return profile


def describe_for_prompt(profile: Optional[VoiceProfile]) -> str:
    """The block that teaches the model to sound like this person.

    Verbatim examples do most of the work — a model imitates real sentences far
    better than it imitates adjectives like "casual".
    """
    if not profile or not profile.sampled:
        return ""
    lines = [f"How the user actually writes (learned from {profile.sampled} of their own messages):"]
    described = profile.describe()
    if described:
        lines.append(f"- {described}")
    if profile.exemplars:
        lines.append("")
        lines.append("Real messages this user has sent — match this voice, do not copy the content:")
        for example in profile.exemplars:
            lines.append(f'- "{example}"')
    return "\n".join(lines)
