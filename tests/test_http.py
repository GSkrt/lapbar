"""request_json never buffers more than its byte cap, on a claimed size or on the actual bytes, before parsing."""
import io
import urllib.error

import pytest

from lapbar import http


class FakeResponse:
    """Stands in for what `urllib.request.urlopen` returns: a readable, a context manager, and `.headers`."""

    def __init__(self, data: bytes, headers=None):
        self._body = io.BytesIO(data)
        self.headers = headers or {}

    def read(self, amt=-1):
        return self._body.read(amt)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHTTPError(urllib.error.HTTPError):
    """A real HTTPError, but with a body we control and no real socket underneath."""

    def __init__(self, code: int, body: bytes, headers=None):
        self.code = code
        self.headers = headers or {}
        self._body = io.BytesIO(body)
        Exception.__init__(self, f"HTTP {code}")

    def read(self, amt=-1):
        return self._body.read(amt)


# ---- _read_capped: the building block, tested directly (no network at all)

def test_read_capped_returns_everything_up_to_the_cap():
    assert http._read_capped(io.BytesIO(b"hello"), 10) == b"hello"
    assert http._read_capped(io.BytesIO(b"hello"), 5) == b"hello"          # exactly at the cap: fine


def test_read_capped_raises_instead_of_ever_buffering_past_the_cap():
    huge = b"x" * (5 * 1024 * 1024)
    with pytest.raises(http.ResponseTooLarge):
        http._read_capped(io.BytesIO(huge), 1024)


def test_read_capped_never_reads_more_than_cap_plus_one_byte():
    class Counting(io.BytesIO):
        max_requested = 0

        def read(self, amt=-1):
            Counting.max_requested = max(Counting.max_requested, amt if amt is not None and amt >= 0 else 0)
            return super().read(amt)

    fp = Counting(b"y" * 1000)
    with pytest.raises(http.ResponseTooLarge):
        http._read_capped(fp, 100)
    assert Counting.max_requested <= 101                                    # cap + 1, never the whole 1000


def test_read_capped_handles_short_reads_from_the_underlying_stream():
    class Stingy:
        """Never returns more than 3 bytes per call, like a slow or chunked connection."""
        def __init__(self, data):
            self.data = data

        def read(self, amt=-1):
            chunk, self.data = self.data[:3], self.data[3:]
            return chunk

    assert http._read_capped(Stingy(b"hello world"), 100) == b"hello world"


# ---- request_json: the full path, with a fake urlopen (the autouse fixture blocks the real one)

def test_a_normal_response_is_parsed_as_json(monkeypatch):
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda req, timeout: FakeResponse(b'{"ok": true}'))
    assert http.request_json("https://example/x") == {"ok": True}


def test_a_response_over_the_cap_is_rejected_by_its_content_length_without_reading_the_body(monkeypatch):
    read_calls = []

    class Loud(FakeResponse):
        def read(self, amt=-1):
            read_calls.append(amt)
            return super().read(amt)

    monkeypatch.setattr(http.urllib.request, "urlopen",
                        lambda req, timeout: Loud(b"{}", headers={"Content-Length": "999999999"}))
    with pytest.raises(http.ResponseTooLarge):
        http.request_json("https://example/x", max_bytes=1024)
    assert read_calls == []                                                 # rejected before any byte was read


def test_a_response_over_the_cap_with_no_content_length_is_still_rejected(monkeypatch):
    body = b'{"a": "' + b"z" * 10000 + b'"}'
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda req, timeout: FakeResponse(body))
    with pytest.raises(http.ResponseTooLarge):
        http.request_json("https://example/x", max_bytes=100)


def test_a_lying_content_length_does_not_help_an_oversized_body_through(monkeypatch):
    body = b'{"a": "' + b"z" * 10000 + b'"}'
    monkeypatch.setattr(http.urllib.request, "urlopen",
                        lambda req, timeout: FakeResponse(body, headers={"Content-Length": "5"}))
    with pytest.raises(http.ResponseTooLarge):
        http.request_json("https://example/x", max_bytes=100)


def test_a_call_can_raise_its_own_cap_for_a_legitimately_large_reply(monkeypatch):
    body = ('{"values": [' + ",".join("1" for _ in range(5000)) + "]}").encode()
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda req, timeout: FakeResponse(body))
    assert len(http.request_json("https://example/x", max_bytes=len(body) + 10)["values"]) == 5000


def test_streams_asks_for_a_larger_cap_than_everything_else():
    from lapbar import streams
    assert streams.STREAMS_MAX_BYTES > http.DEFAULT_MAX_BYTES


# ---- error bodies get their own, smaller cap

def test_an_oversized_error_body_is_capped_too_not_just_the_message_slice(monkeypatch):
    monkeypatch.setattr(http.urllib.request, "urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(FakeHTTPError(500, b"e" * (2 * 1024 * 1024))))
    with pytest.raises(http.ResponseTooLarge):
        http.request_json("https://example/x")


def test_a_normal_sized_error_body_still_comes_through_as_an_httperror(monkeypatch):
    monkeypatch.setattr(http.urllib.request, "urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(FakeHTTPError(404, b'{"message": "Not Found"}')))
    with pytest.raises(http.HttpError) as exc:
        http.request_json("https://example/x")
    assert exc.value.status == 404 and "Not Found" in str(exc.value)
