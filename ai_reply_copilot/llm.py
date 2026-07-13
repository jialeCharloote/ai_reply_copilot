"""Minimal LLM client layer using only the standard library.

Providers implement a single ``complete(system, user) -> str`` method. The
``FakeClient`` lets tests and offline demos run without network or API keys.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from .net import HttpError, request_json


class LLMError(RuntimeError):
    """Raised when an LLM request cannot be made or fails."""


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 60, **kwargs) -> dict:
    """POST JSON, retrying rate limits and server errors (see ``net.py``).

    A single 429 or a transient 529 overload used to lose the whole draft, along
    with the context that had just been read.
    """
    try:
        return request_json(
            url,
            headers=headers,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            timeout=timeout,
            **kwargs,
        )
    except HttpError as exc:
        raise LLMError(str(exc)) from exc


def _anthropic_text(resp: dict, max_tokens: int) -> str:
    """Pull the text out of a Messages response, naming the real failure.

    Every non-answer used to surface as ``Unexpected Anthropic response: {...}``
    with the raw body attached — so a truncated draft, a refusal, and a genuine
    bug were indistinguishable, and the one that actually happens (truncation)
    was reported as the one that never does.
    """
    stop = resp.get("stop_reason")
    if stop == "max_tokens":
        raise LLMError(
            f"The model's reply was cut off at max_tokens ({max_tokens}). Try a "
            "smaller --num, a shorter --limit, or raise max_tokens."
        )
    if stop == "refusal":
        raise LLMError("The model declined to draft a reply for this conversation.")

    # Not necessarily content[0]: a thinking block would come first if thinking is
    # ever enabled, and a refusal comes back with no content at all.
    for block in resp.get("content") or []:
        if block.get("type") == "text" and block.get("text"):
            return block["text"]
    raise LLMError(f"Anthropic returned no text content (stop_reason={stop!r}).")


class FakeClient:
    """Returns a canned response. Useful for tests and offline demos."""

    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


class OpenAIClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        # Tone and bilingual nuance are the product; a mini-tier model is the
        # wrong default for the one thing Charla differentiates on.
        model: str = "gpt-4o",
        base_url: str = "https://api.openai.com/v1",
        temperature: float = 0.8,
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature

    def complete(self, system: str, user: str) -> str:
        if not self.api_key:
            raise LLMError("OPENAI_API_KEY is not set.")
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = _post_json(f"{self.base_url}/chat/completions", headers, payload)
        try:
            return resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:  # pragma: no cover
            raise LLMError(f"Unexpected OpenAI response: {resp}") from exc


class AnthropicClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        # Default to the current-generation Opus for the best bilingual/tone
        # quality. Use ``claude-sonnet-5`` via --model for cheaper/faster drafts.
        model: str = "claude-opus-4-8",
        # Headroom for an understanding + open_points + N candidates. 1024 was not
        # enough: Chinese costs roughly a token per character, so a bilingual
        # thread at --num 5 ran off the end, the JSON came back cut mid-string,
        # and the user was told their model returned invalid JSON. It had not —
        # we had simply stopped it mid-sentence, and then billed them for it.
        max_tokens: int = 4096,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str) -> str:
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set.")
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        resp = _post_json("https://api.anthropic.com/v1/messages", headers, payload)
        return _anthropic_text(resp, self.max_tokens)


DEFAULT_PROVIDER = "anthropic"


def get_client(provider: str = DEFAULT_PROVIDER, model: Optional[str] = None):
    provider = provider.lower()
    if provider == "openai":
        return OpenAIClient(model=model or "gpt-4o")
    if provider == "anthropic":
        return AnthropicClient(model=model or "claude-opus-4-8")
    raise LLMError(f"Unknown provider: {provider!r} (use 'openai' or 'anthropic').")
