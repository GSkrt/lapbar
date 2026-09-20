"""Minimal JSON-over-HTTP helper (stdlib only)."""
import json
import urllib.error
import urllib.parse
import urllib.request

from . import ratelimit


class HttpError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


def request_json(url: str, *, form: dict | None = None, token: str | None = None) -> dict | list:
    """GET url, or POST it as a form when `form` is given."""
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            ratelimit.record(resp.headers)
            return json.load(resp)
    except urllib.error.HTTPError as e:
        ratelimit.record(e.headers)          # a 429 carries the counters too
        raise HttpError(e.code, e.read().decode(errors="replace")) from None
