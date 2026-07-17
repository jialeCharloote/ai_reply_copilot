"""Local-first storage for the personal style profile and feedback log.

Everything is stored under a local config directory (default
``~/.ai_reply_copilot``, overridable via ``AI_REPLY_COPILOT_HOME``). Nothing is
sent anywhere; this is on-device state the user can inspect or delete.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from .prompts import StyleProfile

if TYPE_CHECKING:  # pragma: no cover
    from .voice import VoiceProfile

PROFILE_FILENAME = "profile.json"
FEEDBACK_FILENAME = "feedback.jsonl"
CONVERSATIONS_FILENAME = "conversations.json"
VOICE_FILENAME = "voice.json"
DRAFTS_FILENAME = "drafts.jsonl"

# Feedback ratings mirror the PRD's feedback buttons.
FEEDBACK_RATINGS = ("useful", "too_ai", "wrong_tone", "not_safe", "not_like_me")


def config_dir() -> Path:
    override = os.environ.get("AI_REPLY_COPILOT_HOME")
    return Path(override) if override else Path.home() / ".ai_reply_copilot"


def _ensure_dir() -> Path:
    path = config_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_path() -> Path:
    return config_dir() / PROFILE_FILENAME


def feedback_path() -> Path:
    return config_dir() / FEEDBACK_FILENAME


def load_style_profile() -> Optional[StyleProfile]:
    path = profile_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    known = {
        key: data.get(key)
        for key in ("formality", "directness", "use_emoji", "concise", "language")
    }
    return StyleProfile(**known)


def save_style_profile(profile: StyleProfile) -> Path:
    _ensure_dir()
    path = profile_path()
    payload = {
        "formality": profile.formality,
        "directness": profile.directness,
        "use_emoji": profile.use_emoji,
        "concise": profile.concise,
        "language": profile.language,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def conversations_path() -> Path:
    return config_dir() / CONVERSATIONS_FILENAME


def _conversation_key(source: str, target: str) -> str:
    return f"{source}:{target}"


class ConversationStoreError(RuntimeError):
    """The conversation store exists but could not be read."""


def _load_conversations(*, strict: bool = False) -> dict:
    """Load the per-conversation preferences.

    ``strict`` is for the write path: an unreadable file must NOT be treated as
    an empty one there, because the writer would then overwrite it and destroy
    every other conversation's settings. Readers stay lenient — a broken file
    should degrade to "no preferences", not crash a draft.
    """
    path = conversations_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        if strict:
            raise ConversationStoreError(
                f"{path} is unreadable ({exc}). Refusing to overwrite it and lose "
                "your other saved conversations — fix or delete the file."
            ) from exc
        return {}
    if not isinstance(data, dict):
        if strict:
            raise ConversationStoreError(f"{path} is not a JSON object; refusing to overwrite it.")
        return {}
    return data


def _entry(data: dict, key: str) -> dict:
    """The entry for one conversation, tolerating a non-dict value on disk."""
    entry = data.get(key)
    return entry if isinstance(entry, dict) else {}


def get_conversation_language(source: str, target: str) -> Optional[str]:
    """The language locked to this conversation, if any."""
    data = _load_conversations()
    return _entry(data, _conversation_key(source, target)).get("language")


def set_conversation_language(source: str, target: str, language: Optional[str]) -> Path:
    """Lock (or, with ``language=None``, unlock) the reply language for one
    conversation. Local-first, like everything else under the config dir."""
    _ensure_dir()
    data = _load_conversations(strict=True)
    key = _conversation_key(source, target)
    entry = _entry(data, key)
    if language:
        entry["language"] = language
        data[key] = entry
    else:
        entry.pop("language", None)
        if entry:
            data[key] = entry
        else:
            data.pop(key, None)
    path = conversations_path()
    # Write atomically: a crash mid-write would otherwise truncate the file into
    # exactly the corrupt state the strict load above exists to protect against.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def voice_path() -> Path:
    return config_dir() / VOICE_FILENAME


def load_voice() -> Optional["VoiceProfile"]:
    """The learned voice profile, if the user has run `charla voice learn`.

    Defensive on two axes:

    - **Types.** A hand-edited or truncated ``voice.json`` must not crash a draft.
      A bad field degrades to its default rather than raising deep inside the
      prompt builder on every single reply.
    - **Staleness.** Exemplars are screened at learn time, so a profile written by
      an older, weaker screen would keep uploading its secrets forever. They are
      re-screened here on every load, and a profile from an older schema version
      is ignored outright so the user re-learns under the current rules.
    """
    from .voice import VOICE_SCHEMA_VERSION, VoiceProfile, is_safe_exemplar

    path = voice_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") != VOICE_SCHEMA_VERSION:
        return None  # screened under older rules — make the user re-learn

    defaults = {f.name: f for f in fields(VoiceProfile)}
    values = {}
    for name, spec in defaults.items():
        if name not in data:
            continue
        raw = data[name]
        if name == "exemplars":
            if not isinstance(raw, list):
                continue
            # Re-screen: the current rules, not whatever was in force when saved.
            values[name] = [e for e in raw if isinstance(e, str) and is_safe_exemplar(e)]
        elif name == "sampled" or name == "median_chars":
            if isinstance(raw, int) and not isinstance(raw, bool):
                values[name] = raw
        elif name == "version":
            continue
        elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
            values[name] = float(raw)
    return VoiceProfile(**values)


def save_voice(profile: "VoiceProfile") -> Path:
    from .voice import VOICE_SCHEMA_VERSION

    _ensure_dir()
    path = voice_path()
    payload = asdict(profile)
    payload["version"] = VOICE_SCHEMA_VERSION
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def forget_voice() -> bool:
    """Delete the learned voice. Returns True if there was one."""
    path = voice_path()
    if not path.exists():
        return False
    path.unlink()
    return True


def record_feedback(
    rating: str,
    text: str,
    source: str = "",
    target: str = "",
) -> Path:
    if rating not in FEEDBACK_RATINGS:
        raise ValueError(
            f"Unknown rating {rating!r}; use one of {', '.join(FEEDBACK_RATINGS)}."
        )
    _ensure_dir()
    path = feedback_path()
    entry = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "rating": rating,
        "source": source,
        "target": target,
        "text": text,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def load_feedback() -> List[dict]:
    path = feedback_path()
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


# ── Draft log ────────────────────────────────────────────────────────────────
# Every generated candidate, appended locally so `charla voice eval
# --against-saved` has something real to score. One run of `suggest` yields
# three candidates — a rate over three drafts moves in steps of 0.33, which is
# pure noise — so the eval only becomes meaningful over a pool accumulated
# across runs. Local like everything else here; inspect or delete at will.

# Enough for a stable eval window (the eval reads the last ~50), small enough
# that months of drafting derived from private conversations does not pile up
# on disk forever.
_DRAFTS_KEEP = 500


def drafts_path() -> Path:
    return config_dir() / DRAFTS_FILENAME


def record_drafts(candidates: List[str], source: str = "", target: str = "") -> Optional[Path]:
    """Append generated candidates to the local draft log."""
    texts = [t.strip() for t in candidates if t and t.strip()]
    if not texts:
        return None
    _ensure_dir()
    path = drafts_path()
    stamp = datetime.now(tz=timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as handle:
        for text in texts:
            entry = {"timestamp": stamp, "source": source, "target": target, "text": text}
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) > _DRAFTS_KEEP:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(lines[-_DRAFTS_KEEP:]) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    return path


def load_recent_drafts(limit: int = 50) -> List[str]:
    """The last ``limit`` generated drafts, oldest first. Corrupt lines are
    skipped — a truncated log must not take the eval down with it."""
    path = drafts_path()
    if not path.exists():
        return []
    texts: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = str(entry.get("text", "") or "").strip()
        if text:
            texts.append(text)
    return texts[-limit:] if limit > 0 else []
