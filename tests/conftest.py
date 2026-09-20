import subprocess
import urllib.request

import pytest

from lapbar import streams, vault
from lapbar.http import HttpError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests must mock request_json; anything that reaches the real network is a bug in the test."""
    def blocked(*args, **kwargs):
        raise AssertionError("test tried to use the real network")
    monkeypatch.setattr(urllib.request, "urlopen", blocked)


class FakeKeyring:
    """In-memory stand-in for `secret-tool`, so tests never touch the real keyring."""

    def __init__(self):
        self.items: dict[str, str] = {}
        self.broken: str | None = None    # set to a message to simulate a locked/unreachable keyring
        self.calls: list[list[str]] = []

    def __call__(self, args, stdin=None):
        self.calls.append(list(args))
        if self.broken:
            return subprocess.CompletedProcess(args, 1, "", self.broken)
        verb, key = args[0], args[-1]
        if verb == "store":
            self.items[key] = stdin
            return subprocess.CompletedProcess(args, 0, "", "")
        if verb == "lookup":
            if key in self.items:
                return subprocess.CompletedProcess(args, 0, self.items[key], "")
            return subprocess.CompletedProcess(args, 1, "", "")
        if verb == "clear":
            self.items.pop(key, None)
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected secret-tool call: {args}")


REAL_RUN = vault._run


@pytest.fixture(autouse=True)
def no_streams_by_default(monkeypatch):
    """Activities have no time series unless a test provides some (and never hit the network)."""
    def none(url, token=None, **kw):
        raise HttpError(404, "no streams")
    monkeypatch.setattr(streams, "request_json", none)


@pytest.fixture(autouse=True)
def keyring(monkeypatch, tmp_path):
    fake = FakeKeyring()
    monkeypatch.setattr(vault, "_run", fake)
    # Belt and braces: even code that bypasses vault._run must not be able to call the real secret-tool.
    real_subprocess_run = subprocess.run

    def guarded(cmd, *a, **kw):
        if cmd and "secret-tool" in str(cmd[0]):
            raise AssertionError("a test tried to run the real secret-tool (would touch the real keyring)")
        return real_subprocess_run(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", guarded)
    # Never read or write the developer's real config, state, or cache either.
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.chdir(tmp_path)
    for name in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return fake
