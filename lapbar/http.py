"""Minimal JSON-over-HTTP helper (stdlib only). The Strava API version, address and every call are in stravaapi.py."""
import json
import urllib.error
import urllib.parse
import urllib.request

from . import ratelimit

# A reply is never buffered past these caps before it is parsed, whatever it claims its own size is (a
# Content-Length header is trusted only to fail fast; the cap is enforced on the bytes actually read). 8 MiB
# covers every call except the activity streams, which can legitimately be large and pass their own `max_bytes`.
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
ERROR_BODY_MAX_BYTES = 64 * 1024          # Strava's error bodies are a small JSON object; only body[:200] is kept anyway


class HttpError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


class ResponseTooLarge(HttpError):
    """The response (or its own Content-Length header) exceeded the byte cap for this call."""

    def __init__(self, cap: int):
        super().__init__(0, f"response exceeded the {cap}-byte limit")


def _read_capped(fp, cap: int) -> bytes:
    """Read at most `cap + 1` bytes from `fp`: enough to know there is more than the cap, never more."""
    chunks: list[bytes] = []
    total = 0
    while total <= cap:
        chunk = fp.read(cap + 1 - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > cap:
        raise ResponseTooLarge(cap)
    return b"".join(chunks)


def request_json(url: str, *, form: dict | None = None, token: str | None = None,
                  max_bytes: int = DEFAULT_MAX_BYTES) -> dict | list:
    """GET url, or POST it as a form when `form` is given. Never reads more than `max_bytes` of the response."""
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            ratelimit.record(resp.headers)
            length = resp.headers.get("Content-Length")
            if length is not None and length.isdigit() and int(length) > max_bytes:
                raise ResponseTooLarge(max_bytes)          # fail fast: no point starting to read
            return json.loads(_read_capped(resp, max_bytes))
    except urllib.error.HTTPError as e:
        ratelimit.record(e.headers)          # a 429 carries the counters too
        raise HttpError(e.code, _read_capped(e, ERROR_BODY_MAX_BYTES).decode(errors="replace")) from None
