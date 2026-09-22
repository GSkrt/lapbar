"""Minimal JSON-over-HTTP helper (stdlib only). The Strava API version, address and every call are in stravaapi.py."""
import json
import urllib.error
import urllib.parse
import urllib.request

from . import ratelimit

# A reply is never buffered past these caps before it is parsed, whatever it claims its own size is (a
# Content-Length header is trusted only to fail fast; the cap is enforced on the bytes actually read). 16 MiB
# covers every call except the activity streams, which can legitimately be large and pass their own `max_bytes`
# (see streams.STREAMS_MAX_BYTES).
#
# A byte cap on its own is not enough: json.loads() decodes numbers into individual Python objects, and how many
# bytes of JSON text that takes has little to do with how much memory the decoded objects need -- a payload of
# many small numbers, packed as densely as possible, decodes into several times its own text size (measured: a
# realistic activity-streams-shaped payload runs about 4-5x, and an adversarial one built to maximise numbers per
# byte does too). A byte cap alone would let a 16 MiB reply grow to tens of MiB decoded, and the streams cap
# existing purely as bytes would let something sized for "a long ride" decode to gigabytes. MAX_NUMBERS bounds
# what actually drives that: how many number tokens json.loads() is allowed to build before it gives up, via
# parse_int/parse_float hooks that raise as soon as the count is exceeded -- confirmed (see tests/test_http.py)
# that this aborts decoding immediately, not after the oversized object is already built, so peak memory tracks
# the limit itself rather than however large or dense the response turns out to be.
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_NUMBERS = 3_000_000           # generous for every call except streams; each one measured near 100 MB peak
ERROR_BODY_MAX_BYTES = 64 * 1024          # Strava's error bodies are a small JSON object; only body[:200] is kept anyway


class HttpError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


class ResponseTooLarge(HttpError):
    """The response (or its own Content-Length header) exceeded the byte cap for this call."""

    def __init__(self, cap: int):
        super().__init__(0, f"response exceeded the {cap}-byte limit")


class TooManyValues(HttpError):
    """The response held more individual numbers than this call allows, so decoding it was abandoned partway
    through rather than let it build an unbounded amount of memory."""

    def __init__(self, cap: int):
        super().__init__(0, f"response held more than {cap} numbers")


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


def _counted_loads(text: bytes, max_numbers: int):
    """json.loads(), but decoding stops the moment more than `max_numbers` individual int/float tokens have been
    seen, before the rest of a very large or very dense response is ever turned into Python objects. Confirmed
    (see tests/test_http.py) that raising from inside parse_int/parse_float aborts json.loads() immediately with
    that exact exception, not a wrapped one, and that peak memory during the abort tracks the count reached, not
    how much of the response was still left unparsed."""
    count = 0

    def as_int(token):
        nonlocal count
        count += 1
        if count > max_numbers:
            raise TooManyValues(max_numbers)
        return int(token)

    def as_float(token):
        nonlocal count
        count += 1
        if count > max_numbers:
            raise TooManyValues(max_numbers)
        return float(token)

    return json.loads(text, parse_int=as_int, parse_float=as_float)


def request_json(url: str, *, form: dict | None = None, token: str | None = None,
                  max_bytes: int = DEFAULT_MAX_BYTES, max_numbers: int = DEFAULT_MAX_NUMBERS) -> dict | list:
    """GET url, or POST it as a form when `form` is given. Never reads more than `max_bytes` of the response, and
    never decodes more than `max_numbers` individual numbers from it."""
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
            return _counted_loads(_read_capped(resp, max_bytes), max_numbers)
    except urllib.error.HTTPError as e:
        ratelimit.record(e.headers)          # a 429 carries the counters too
        raise HttpError(e.code, _read_capped(e, ERROR_BODY_MAX_BYTES).decode(errors="replace")) from None
