"""Prompt construction for reply generation.

Encodes the AI behavior rules from the PRD (section 13): produce a one-line
understanding of the conversation plus a few distinct reply candidates that
sound like the user, respecting the chosen intent, tone, and personal style.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Optional

# Intent options (PRD P0): what the reply is trying to do.
INTENTS: Dict[str, str] = {
    "reply": "a natural reply that moves the conversation forward",
    "decline": "a polite, clear decline that keeps the relationship intact",
    "apologize": "a sincere apology that takes responsibility without groveling",
    "follow_up": "a follow-up that nudges for a response or next step",
    "clarify": "a message that asks a clarifying question",
    "negotiate": "a reply that negotiates terms while staying constructive",
    "deescalate": "a reply that lowers tension, clarifies, and offers a next step",
}

# Tone options (PRD P0): how the reply should feel.
TONES: Dict[str, str] = {
    "professional": "concise, clear, and boundaried",
    "friendly": "warm and approachable",
    "humorous": "light and playful without being offensive",
    "serious": "direct and earnest",
    "concise": "as short as possible while staying complete",
    "warm": "kind and empathetic",
    "confident": "self-assured and decisive",
}


@dataclass
class StyleProfile:
    """Optional personal style profile (PRD P1)."""

    formality: Optional[str] = None  # e.g. "casual", "formal"
    directness: Optional[str] = None  # e.g. "direct", "gentle"
    use_emoji: Optional[bool] = None
    concise: Optional[bool] = None
    language: Optional[str] = None  # e.g. "English", "中文", "中英双语"

    def describe(self) -> str:
        parts = []
        if self.formality:
            parts.append(f"formality: {self.formality}")
        if self.directness:
            parts.append(f"directness: {self.directness}")
        if self.use_emoji is not None:
            parts.append("uses emoji" if self.use_emoji else "no emoji")
        if self.concise:
            parts.append("prefers concise messages")
        # ``language`` is deliberately NOT described here. It is resolved per
        # conversation (see language.resolve_language) and passed as an explicit
        # "Reply language" line; emitting it here too would give the model two
        # competing instructions when the profile disagrees with the thread.
        return "; ".join(parts)


def build_system_prompt(num_candidates: int = 3) -> str:
    # A literally valid JSON example, sized to the request. Showing the model a
    # malformed template (e.g. an ellipsis, or doubled braces) costs reliability
    # on providers that have no server-side JSON mode.
    example = json.dumps(
        {
            "understanding": "<one sentence describing what is happening in the conversation>",
            "open_points": [
                "<something they asked or are waiting on that the user has not answered yet>"
            ],
            "candidates": [f"<reply {i}>" for i in range(1, num_candidates + 1)],
        },
        ensure_ascii=False,
    )
    return (
        "You are Charla, a Mac assistant that drafts replies in the "
        "user's own voice for their current iMessage/Slack conversation. The "
        "user will read, edit, and send the reply themselves.\n\n"
        "Rules:\n"
        "- Preserve the user's real intent; never invent facts.\n"
        "- Sound like a real person the user would send, not a template.\n"
        "- Do not over-apologize unless the intent is to apologize.\n"
        "- Professional tone means concise, clear, boundaried.\n"
        "- Humor stays light, never offensive.\n"
        "- For conflict, de-escalate, clarify, and suggest a next step.\n"
        "- Never produce harassment, threats, manipulation, impersonation, or "
        "scam content. For legal/medical/financial topics, be cautious.\n\n"
        "'open_points' is what the user still owes the other side: questions "
        "they were asked but have not answered, decisions being waited on, and "
        "any deadline that was named. Read the whole thread — a question asked "
        "early and never answered still counts. Quote concrete details (dates, "
        "names, numbers) rather than paraphrasing vaguely. Return an empty list "
        "if the user owes them nothing.\n\n"
        "Language rules:\n"
        "- A 'Reply language' line in the user message is authoritative: write "
        "the candidates AND the understanding AND the open_points in it.\n"
        "- 中英混合 means genuine code-switching: mix the two the same natural "
        "way the people in this conversation already do.\n"
        "- If no reply language is given, mirror the conversation, judged by the "
        "most recent messages.\n"
        "- Chinese must sound like a native speaker texting: natural and "
        "colloquial, no translationese (翻译腔), and not overly formal or "
        "official (避免公文腔) unless the tone calls for it.\n"
        "- English must be idiomatic and conversational, not stiff or "
        "textbook-like.\n\n"
        "Return ONLY a JSON object with exactly these keys:\n"
        f"{example}\n"
        f"Provide exactly {num_candidates} candidates that are meaningfully "
        "different from each other."
    )


def build_user_prompt(
    context: str,
    intent: Optional[str] = None,
    tone: Optional[str] = None,
    style: Optional[StyleProfile] = None,
    draft: Optional[str] = None,
    language: Optional[str] = None,
    voice: Optional[str] = None,
) -> str:
    lines = []
    if voice:
        # First, and before the conversation: the model should know whose voice
        # it is writing in before it reads what it is replying to. Verbatim
        # examples of the user's own messages do most of the work here.
        lines.extend([voice, ""])

    lines.extend(["Here is the recent conversation (oldest to newest):", "", context, ""])

    if language:
        lines.append(f"Reply language: {language}")

    if intent:
        desc = INTENTS.get(intent, intent)
        lines.append(f"Intent: {intent} — {desc}")
    else:
        lines.append("Intent: a natural reply that fits the conversation")

    if tone:
        desc = TONES.get(tone, tone)
        lines.append(f"Tone: {tone} — {desc}")

    if style:
        described = style.describe()
        if described:
            lines.append(f"Personal style: {described}")

    if draft:
        lines.append(f'What I roughly want to say: "{draft}"')

    lines.append("")
    lines.append(
        "Write the reply as the user (first person). Return the JSON object now."
    )
    return "\n".join(lines)
