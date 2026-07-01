"""Local-first storage for the personal style profile and feedback log.

Everything is stored under a local config directory (default
``~/.ai_reply_copilot``, overridable via ``AI_REPLY_COPILOT_HOME``). Nothing is
sent anywhere; this is on-device state the user can inspect or delete.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .prompts import StyleProfile

PROFILE_FILENAME = "profile.json"
FEEDBACK_FILENAME = "feedback.jsonl"

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
