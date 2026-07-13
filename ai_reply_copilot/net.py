"""One HTTP layer, with the retry policy in a single place.

Both the LLM providers and the Slack API rate-limit, and both were failing a whole
draft on the first 429. Worse, ``slack.py`` caught ``urllib.error.URLError`` —
which ``HTTPError`` subclasses — so a 429 was flattened into "could not reach
Slack" and the ``Retry-After`` header the server had just handed us was thrown
away.

Retries are bounded and only for the statuses where retrying is meaningful:
429 (rate limited) and 5xx (server-side). A 400 or a 401 is retried never — the
request is wrong and will stay wrong.

``sleep`` is injectable so the tests exercise the backoff without waiting.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
# A rate limit is the one status that is safe to retry even for a request that is
# *not* idempotent: a 429 means the server rejected the call, so nothing happened.
RATE_LIMIT_ONLY = frozenset({429})
MAX_ATTEMPTS = 3
BASE_DELAY = 0.5
MAX_DELAY = 8.0
# A ceiling on how long we will honour a server's Retry-After. It is much higher
# than MAX_DELAY because it bounds an *instruction*, not a guess.
MAX_RETRY_AFTER = 60.0


class HttpError(RuntimeError):
    """A request failed. ``status`` is None when it never reached the server."""

    def __init__(self, message: str, *, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _retry_after(headers, attempt: int) -> float:
    """Honour the server's Retry-After when it gives one; else exponential backoff.

    MAX_DELAY bounds our *guess* at a good backoff. It must not bound a delay the
    server explicitly asked for: Slack routinely answers a 429 with
    ``Retry-After: 30``, and sleeping 8s instead meant every retry landed back
    inside the cooldown we had just been told to wait out — three attempts, three
    429s, no progress, and extra load on Slack in the process.
    """
    raw = headers.get("Retry-After") if headers else None
    if raw:
        try:
            return min(float(raw), MAX_RETRY_AFTER)
        except (TypeError, ValueError):
            pass
    return min(BASE_DELAY * (2 ** attempt), MAX_DELAY)


def request_json(
    url: str,
    *,
    headers: dict,
    data: Optional[bytes] = None,
    method: str = "GET",
    timeout: int = 30,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Optional[Callable[[float], None]] = None,
    idempotent: bool = True,
) -> dict:
    """Perform a JSON request, retrying rate limits and server errors.

    ``idempotent=False`` marks a request that *changes something* when it lands —
    ``chat.postMessage`` above all. For those, a 5xx or a dropped connection is
    genuinely ambiguous: Slack may well have created the message and only lost
    the response on the way back. Retrying would post the user's reply a second
    time, as them, in front of their colleagues. So a non-idempotent request
    retries a 429 (which means the server rejected it, so nothing happened) and
    nothing else. Slack offers no idempotency key, so there is no safe retry to
    be had; failing once and telling the truth is the correct behaviour.
    """
    # Resolved at call time, not bound as a default: a default argument captures
    # ``time.sleep`` at import and no test could ever patch it out, so the suite
    # would really sleep through the backoff.
    sleep = sleep or time.sleep
    retry_statuses = RETRY_STATUSES if idempotent else RATE_LIMIT_ONLY
    last: Optional[HttpError] = None

    for attempt in range(max_attempts):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last = HttpError(f"HTTP {exc.code} from {url}: {body}", status=exc.code, body=body)
            if exc.code not in retry_statuses or attempt == max_attempts - 1:
                raise last from exc
            sleep(_retry_after(exc.headers, attempt))
        except urllib.error.URLError as exc:
            # A transport failure (DNS, connection reset, timeout). Worth one more
            # try when the request is safe to repeat — but for a non-idempotent
            # one this is the *most* dangerous case, not the safest: the request
            # may have been delivered and only the response lost.
            last = HttpError(f"Could not reach {url}: {exc.reason}")
            if not idempotent or attempt == max_attempts - 1:
                raise last from exc
            sleep(_retry_after(None, attempt))

    raise last or HttpError(f"Request to {url} failed")  # pragma: no cover - unreachable
