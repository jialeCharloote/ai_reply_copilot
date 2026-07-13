"""Tests for the shared HTTP layer and its retry policy.

Both the LLM providers and the Slack API rate-limit, and both used to lose a whole
draft on the first 429 — Slack additionally caught ``URLError`` (which
``HTTPError`` subclasses), flattening a 429 into "could not reach Slack" and
throwing away the ``Retry-After`` the server had just supplied.
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from ai_reply_copilot import net
from ai_reply_copilot.llm import AnthropicClient, LLMError
from ai_reply_copilot.net import HttpError, request_json
from ai_reply_copilot.slack import SlackClient, SlackError


def _http_error(code: int, retry_after: str = "") -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError(
        "https://example.com", code, "err", headers, io.BytesIO(b'{"error": "boom"}')
    )


class _Response:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _urlopen_returning(sequence):
    remaining = list(sequence)

    def fake(request, timeout=None):
        item = remaining.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return fake


def _run(sequence, **kwargs):
    """Drive request_json against a scripted sequence; return (result, sleeps)."""
    sleeps: list = []
    with patch.object(net.urllib.request, "urlopen", _urlopen_returning(sequence)):
        result = request_json(
            "https://example.com", headers={}, sleep=sleeps.append, **kwargs
        )
    return result, sleeps


def test_a_rate_limit_is_retried_and_succeeds():
    result, sleeps = _run([_http_error(429), _Response({"ok": True})])
    assert result == {"ok": True}
    assert len(sleeps) == 1


def test_the_servers_retry_after_is_honoured():
    # The server told us how long to wait. Guessing instead is how you get banned.
    _, sleeps = _run([_http_error(429, retry_after="2"), _Response({"ok": True})])
    assert sleeps == [2.0]


def test_backoff_is_exponential_without_a_retry_after():
    _, sleeps = _run([_http_error(503), _Response({"ok": True})])
    assert sleeps == [net.BASE_DELAY]


def test_server_errors_are_retried():
    result, _ = _run([_http_error(500), _http_error(529), _Response({"ok": True})])
    assert result == {"ok": True}


def test_a_client_error_is_not_retried():
    # A 400 or a 401 is wrong and will stay wrong; retrying only wastes time.
    sleeps: list = []
    with patch.object(net.urllib.request, "urlopen", _urlopen_returning([_http_error(400)])):
        with pytest.raises(HttpError) as caught:
            request_json("https://example.com", headers={}, sleep=sleeps.append)
    assert caught.value.status == 400
    assert sleeps == []


def test_retries_are_bounded():
    sequence = [_http_error(429)] * net.MAX_ATTEMPTS
    sleeps: list = []
    with patch.object(net.urllib.request, "urlopen", _urlopen_returning(sequence)):
        with pytest.raises(HttpError):
            request_json("https://example.com", headers={}, sleep=sleeps.append)
    assert len(sleeps) == net.MAX_ATTEMPTS - 1  # no sleep after the last attempt


def test_a_transport_failure_is_retried_then_surfaced():
    boom = urllib.error.URLError("connection reset")
    result, _ = _run([boom, _Response({"ok": True})])
    assert result == {"ok": True}


def test_a_nonsense_retry_after_falls_back_to_backoff():
    _, sleeps = _run([_http_error(429, retry_after="soon"), _Response({"ok": True})])
    assert sleeps == [net.BASE_DELAY]


def test_a_servers_retry_after_is_honoured_past_the_backoff_cap():
    """MAX_DELAY bounds our *guess*; it must not override the server.

    Slack answers a 429 with `Retry-After: 30` routinely. Capping that at 8s meant
    every retry landed back inside the cooldown we had just been told to observe:
    three attempts, three 429s, no progress, and extra load on Slack on the way.
    """
    _, sleeps = _run([_http_error(429, retry_after="30"), _Response({"ok": True})])
    assert sleeps == [30.0]


def test_an_absurd_retry_after_is_still_bounded():
    _, sleeps = _run([_http_error(429, retry_after="9999"), _Response({"ok": True})])
    assert sleeps == [net.MAX_RETRY_AFTER]


# --- non-idempotent requests: a send must never be replayed ----------------------
#
# The retry policy was written for reads and then quietly applied to
# chat.postMessage. A 5xx or a dropped connection on a send is *ambiguous* —
# Slack may have created the message and lost only the response — so retrying
# posts the user's reply twice, as them, in front of their colleagues. There is
# no idempotency key to make it safe.


def _attempts(sequence, **kwargs):
    """Drive request_json and count how many HTTP calls were actually made."""
    calls = []
    inner = _urlopen_returning(sequence)

    def counting(request, timeout=None):
        calls.append(request)
        return inner(request, timeout=timeout)

    with patch.object(net.urllib.request, "urlopen", counting):
        try:
            result = request_json(
                "https://example.com",
                headers={},
                data=b"{}",
                method="POST",
                sleep=lambda _s: None,
                **kwargs,
            )
        except HttpError as exc:
            return None, len(calls), exc
    return result, len(calls), None


def test_a_non_idempotent_post_is_not_replayed_on_a_server_error():
    result, attempts, error = _attempts(
        [_http_error(503), _Response({"ok": True, "ts": "1.0"})], idempotent=False
    )
    assert attempts == 1, "the 503 was retried — the message may already have been sent"
    assert result is None and error is not None


def test_a_non_idempotent_post_is_not_replayed_on_a_dropped_connection():
    # The most dangerous case of all: the request may have been delivered and
    # only the response lost.
    dropped = urllib.error.URLError("connection reset")
    _, attempts, error = _attempts(
        [dropped, _Response({"ok": True, "ts": "1.0"})], idempotent=False
    )
    assert attempts == 1
    assert error is not None


def test_a_non_idempotent_post_still_retries_a_rate_limit():
    # A 429 means the server rejected the call outright, so nothing was created
    # and there is nothing to duplicate.
    result, attempts, error = _attempts(
        [_http_error(429), _Response({"ok": True, "ts": "1.0"})], idempotent=False
    )
    assert attempts == 2
    assert result == {"ok": True, "ts": "1.0"} and error is None


def test_an_idempotent_request_still_retries_everything():
    result, attempts, _ = _attempts(
        [_http_error(503), _Response({"ok": True})], idempotent=True
    )
    assert attempts == 2 and result == {"ok": True}


def test_post_message_does_not_replay_the_users_reply():
    """The end-to-end guarantee, driven through the real SlackClient."""
    client = SlackClient(token="xoxp-fake", sleep=lambda _s: None)
    sent = []

    def counting(request, timeout=None):
        sent.append(request)
        raise urllib.error.HTTPError(
            "https://slack.com", 503, "err", {}, io.BytesIO(b"unavailable")
        )

    with patch.object(net.urllib.request, "urlopen", counting):
        with pytest.raises(SlackError):
            client.post_message("C1", "see you at 7!")
    assert len(sent) == 1, "the reply was POSTed more than once"


# --- the callers -----------------------------------------------------------------


def test_the_llm_survives_a_rate_limit():
    ok = {
        "stop_reason": "end_turn",
        "content": [
            {"type": "text", "text": '{"understanding": "u", "candidates": ["a"]}'}
        ],
    }
    sequence = [_http_error(429, retry_after="1"), _http_error(529), _Response(ok)]
    with patch.object(net.urllib.request, "urlopen", _urlopen_returning(sequence)), \
            patch.object(net.time, "sleep", lambda _s: None):
        out = AnthropicClient(api_key="k").complete("sys", "user")
    assert "candidates" in out


def test_the_llm_still_fails_fast_on_a_bad_request():
    with patch.object(net.urllib.request, "urlopen", _urlopen_returning([_http_error(400)])):
        with pytest.raises(LLMError):
            AnthropicClient(api_key="k").complete("sys", "user")


def test_slack_retries_a_rate_limit_instead_of_calling_it_unreachable():
    # Regression: HTTPError is a subclass of URLError, so a 429 used to surface as
    # "Could not reach Slack" with the Retry-After discarded.
    sleeps: list = []
    sequence = [_http_error(429, retry_after="1"), _Response({"ok": True, "user_id": "U1"})]
    client = SlackClient(token="xoxp-test", sleep=sleeps.append)
    with patch.object(net.urllib.request, "urlopen", _urlopen_returning(sequence)):
        assert client.call("auth.test")["user_id"] == "U1"
    assert sleeps == [1.0]


def test_slack_surfaces_its_own_error_code():
    # "missing_scope" and "channel_not_found" must be distinguishable by callers.
    payload = {"ok": False, "error": "missing_scope"}
    client = SlackClient(token="xoxp-test", sleep=lambda _s: None)
    with patch.object(net.urllib.request, "urlopen", _urlopen_returning([_Response(payload)])):
        with pytest.raises(SlackError) as caught:
            client.call("conversations.history")
    assert caught.value.error == "missing_scope"
