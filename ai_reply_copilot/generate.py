"""Turn conversation context into reply suggestions via an LLM."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from .models import Message, render_context
from .prompts import StyleProfile, build_system_prompt, build_user_prompt


class GenerationError(RuntimeError):
    """Raised when the model output cannot be parsed into suggestions."""


@dataclass
class ReplySuggestion:
    understanding: str
    candidates: List[str]
    # What the user still owes the other side: unanswered questions, decisions
    # being waited on, named deadlines. Written in the reply language.
    open_points: List[str] = field(default_factory=list)
    language: Optional[str] = None


def _extract_json(raw: str) -> str:
    """Pull the JSON object out of a raw model response.

    Handles plain JSON, ```json fenced blocks, and leading/trailing prose.
    """
    text = raw.strip()
    if text.startswith("```"):
        # Drop the opening fence (``` or ```json) and the closing fence.
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise GenerationError(f"No JSON object found in model output: {raw!r}")
    return text[start : end + 1]


def parse_response(raw: str) -> ReplySuggestion:
    try:
        data = json.loads(_extract_json(raw))
    except json.JSONDecodeError as exc:
        raise GenerationError(f"Invalid JSON from model: {exc}") from exc

    understanding = data.get("understanding")
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise GenerationError(f"Missing 'candidates' list in model output: {data!r}")

    cleaned = [str(c).strip() for c in candidates if str(c).strip()]
    if not cleaned:
        raise GenerationError("Model returned only empty candidates.")

    # open_points is advisory: a model that omits it, or returns the wrong shape,
    # should still yield drafts. Only plain strings are kept — a stray dict would
    # otherwise be rendered to the user as "{'q': 'x'}".
    raw_points = data.get("open_points")
    points = (
        [p.strip() for p in raw_points if isinstance(p, str) and p.strip()]
        if isinstance(raw_points, list)
        else []
    )

    return ReplySuggestion(
        understanding=str(understanding or "").strip(),
        candidates=cleaned,
        open_points=points,
    )


def generate_replies(
    messages: List[Message],
    client,
    *,
    intent: Optional[str] = None,
    tone: Optional[str] = None,
    style: Optional[StyleProfile] = None,
    draft: Optional[str] = None,
    num_candidates: int = 3,
    language: Optional[str] = None,
    voice: Optional[str] = None,
) -> ReplySuggestion:
    """Generate a one-line understanding plus reply candidates for a thread.

    ``language`` is the already-resolved reply language (see
    ``language.resolve_language``). When None, the model mirrors the conversation.
    """
    if not messages:
        raise GenerationError("No conversation context to generate a reply from.")

    system = build_system_prompt(num_candidates=num_candidates)
    user = build_user_prompt(
        context=render_context(messages, mark_unanswered=True),
        intent=intent,
        tone=tone,
        style=style,
        draft=draft,
        language=language,
        voice=voice,
    )
    raw = client.complete(system, user)
    suggestion = parse_response(raw)
    suggestion.language = language
    return suggestion
