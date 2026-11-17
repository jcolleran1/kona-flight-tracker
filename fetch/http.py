"""One small HTTP GET helper for every network call the app makes.

stdlib urllib only. Sends the project's User-Agent, asks for gzip, caps the
response size (512 MB box), and turns every failure into FetchError carrying
the HTTP status and any Retry-After the server sent, so callers can back off
properly instead of guessing.
"""
from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.request

USER_AGENT = "flight-tracker-wall-display/1.0 (personal, noncommercial)"

MAX_BODY_BYTES = 4 * 1024 * 1024


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _retry_after(headers) -> float | None:
    """Retry-After in seconds. Only the delta-seconds form is honoured; an
    HTTP-date would need a trustworthy wall clock, which a Pi without an RTC
    doesn't have right after boot."""
    try:
        value = float((headers.get("Retry-After") or "").strip())
    except (AttributeError, ValueError):
        return None
    return value if 0 <= value <= 86400 else None


def http_get_json(url: str, timeout: float = 10.0):
    """GET `url` and return the decoded JSON body. Raises FetchError."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                raise FetchError(f"response larger than {MAX_BODY_BYTES} bytes")
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                body = gzip.decompress(body)
            return json.loads(body)
    except FetchError:
        raise
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}", status=e.code,
                         retry_after=_retry_after(e.headers)) from e
    except Exception as e:  # URLError, timeout, bad gzip, bad JSON, reset...
        raise FetchError(f"{type(e).__name__}: {e}") from e
