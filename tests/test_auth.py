import json
import time

import pytest

from lapbar import auth


def test_refresh_rotates_and_persists_refresh_token(tmp_path, monkeypatch, keyring):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    calls = []

    def fake_request(url, form=None, token=None):
        calls.append(form)
        return {"access_token": "acc1", "refresh_token": "ref2", "expires_at": time.time() + 21600}

    monkeypatch.setattr(auth, "request_json", fake_request)
    src = auth.DirectTokenSource("id", "secret", refresh_token="ref1")

    assert src.access_token() == "acc1"
    assert calls[0]["refresh_token"] == "ref1"
    saved = json.loads(keyring.items["tokens"])
    assert saved["refresh_token"] == "ref2"

    # Still valid: no second network call.
    assert src.access_token() == "acc1"
    assert len(calls) == 1


def test_expired_token_refreshes_with_saved_refresh_token(tmp_path, monkeypatch, keyring):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    keyring.items["tokens"] = json.dumps({"access_token": "old", "refresh_token": "saved", "expires_at": 0})
    seen = {}

    def fake_request(url, form=None, token=None):
        seen.update(form)
        return {"access_token": "new", "refresh_token": "saved2", "expires_at": time.time() + 21600}

    monkeypatch.setattr(auth, "request_json", fake_request)
    assert auth.DirectTokenSource("id", "secret", refresh_token="stale-env").access_token() == "new"
    assert seen["refresh_token"] == "saved"  # state file wins over the .env seed


def test_not_authorized_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    try:
        auth.DirectTokenSource("id", "secret").access_token()
    except RuntimeError as e:
        assert "lapbar setup" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_missing_seed_raises_typed_not_authorized(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    import pytest
    with pytest.raises(auth.NotAuthorized):
        auth.DirectTokenSource("id", "secret").access_token()


def test_missing_credentials_raise_typed_not_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("STRAVA_CLIENT_ID", raising=False)
    monkeypatch.delenv("STRAVA_CLIENT_SECRET", raising=False)
    import pytest
    with pytest.raises(auth.NotConfigured):
        auth.default_token_source()


# ---- the browser sign-in step, exercised with a real local callback (no browser, no Strava)

import http.client
import socket
import threading


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def hit_callback(port, query, delay=0.3):
    def go():
        import time
        time.sleep(delay)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/callback?" + query)
        conn.getresponse().read()
    t = threading.Thread(target=go)
    t.start()
    return t


def test_authorize_stores_tokens_after_the_callback(monkeypatch, keyring):
    port = free_port()
    monkeypatch.setattr(auth, "CALLBACK_PORT", port)
    said, opened = [], []
    seen = {}

    def exchange(url, form=None, token=None):
        seen.update(form)
        return {"access_token": "acc", "refresh_token": "ref", "expires_at": 99}

    monkeypatch.setattr(auth, "request_json", exchange)
    monkeypatch.setattr(auth.webbrowser, "open", opened.append)
    t = hit_callback(port, "code=CODE123&scope=read,activity:read_all")
    auth.authorize("12345", "sec", out=said.append)
    t.join()
    assert seen["code"] == "CODE123" and seen["client_secret"] == "sec"
    assert json.loads(keyring.items["tokens"])["refresh_token"] == "ref"
    url = opened[0]
    assert "client_id=12345" in url and f"localhost%3A{port}" in url and "activity%3Aread_all" in url


def test_authorize_no_browser_prints_the_link_instead(monkeypatch, keyring):
    port = free_port()
    monkeypatch.setattr(auth, "CALLBACK_PORT", port)
    monkeypatch.setattr(auth, "request_json", lambda *a, **k: {"access_token": "a", "refresh_token": "r", "expires_at": 1})
    monkeypatch.setattr(auth.webbrowser, "open", lambda url: (_ for _ in ()).throw(AssertionError("opened a browser")))
    said = []
    t = hit_callback(port, "code=X&scope=read,activity:read_all")
    auth.authorize("1", "s", open_browser=False, out=said.append)
    t.join()
    assert any("strava.com/oauth/authorize" in str(line) for line in said)


def test_authorize_insists_on_the_activity_permission(monkeypatch, keyring):
    port = free_port()
    monkeypatch.setattr(auth, "CALLBACK_PORT", port)
    monkeypatch.setattr(auth.webbrowser, "open", lambda url: None)
    t = hit_callback(port, "code=X&scope=read")           # user unticked the activity box
    with pytest.raises(RuntimeError, match="activity:read_all"):
        auth.authorize("1", "s", out=lambda *_: None)
    t.join()
    assert "tokens" not in keyring.items


def test_authorize_times_out_with_the_usual_cause(monkeypatch, keyring):
    monkeypatch.setattr(auth, "CALLBACK_PORT", free_port())
    monkeypatch.setattr(auth.webbrowser, "open", lambda url: None)
    with pytest.raises(auth.AuthTimeout, match="localhost"):
        auth.authorize("1", "s", timeout=0.2, out=lambda *_: None)


def test_authorize_explains_a_busy_port(monkeypatch, keyring):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        monkeypatch.setattr(auth, "CALLBACK_PORT", blocker.getsockname()[1])
        with pytest.raises(RuntimeError, match="already in use"):
            auth.authorize("1", "s", out=lambda *_: None)
