"""Minimal LLM client layer using only the standard library.

Providers implement a single ``complete(system, user) -> str`` method. The
``FakeClient`` lets tests and offline demos run without network or API keys.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional


class LLMError(RuntimeError):
    """Raised when an LLM request cannot be made or fails."""


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        detail = exc.read().decode("utf-8", errors="replace")
        raise LLMError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network dependent
        raise LLMError(f"Could not reach {url}: {exc.reason}") from exc


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
        model: str = "gpt-4o-mini",
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
        model: str = "claude-3-5-sonnet-latest",
        max_tokens: int = 1024,
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
        try:
            return resp["content"][0]["text"]
        except (KeyError, IndexError) as exc:  # pragma: no cover
            raise LLMError(f"Unexpected Anthropic response: {resp}") from exc


def get_client(provider: str = "openai", model: Optional[str] = None):
    provider = provider.lower()
    if provider == "openai":
        return OpenAIClient(model=model or "gpt-4o-mini")
    if provider == "anthropic":
        return AnthropicClient(model=model or "claude-3-5-sonnet-latest")
    raise LLMError(f"Unknown provider: {provider!r} (use 'openai' or 'anthropic').")
