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
MAX_ATTEMPTS = 3
BASE_DELAY = 0.5
MAX_DELAY = 8.0


class HttpError(RuntimeError):
    """A request failed. ``status`` is None when it never reached the server."""

    def __init__(self, message: str, *, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _retry_after(headers, attempt: int) -> float:
    """Honour the server's Retry-After when it gives one; else exponential backoff."""
    raw = headers.get("Retry-After") if headers else None
    if raw:
        try:
            return min(float(raw), MAX_DELAY)
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
) -> dict:
    """Perform a JSON request, retrying rate limits and server errors."""
    # Resolved at call time, not bound as a default: a default argument captures
    # ``time.sleep`` at import and no test could ever patch it out, so the suite
    # would really sleep through the backoff.
    sleep = sleep or time.sleep
    last: Optional[HttpError] = None

    for attempt in range(max_attempts):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last = HttpError(f"HTTP {exc.code} from {url}: {body}", status=exc.code, body=body)
            if exc.code not in RETRY_STATUSES or attempt == max_attempts - 1:
                raise last from exc
            sleep(_retry_after(exc.headers, attempt))
        except urllib.error.URLError as exc:
            # A transport failure (DNS, connection reset, timeout). Worth one more
            # try; a genuinely unreachable host will simply fail again.
            last = HttpError(f"Could not reach {url}: {exc.reason}")
            if attempt == max_attempts - 1:
                raise last from exc
            sleep(_retry_after(None, attempt))

    raise last or HttpError(f"Request to {url} failed")  # pragma: no cover - unreachable
