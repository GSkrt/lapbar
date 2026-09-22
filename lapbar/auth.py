"""Strava authentication.

A TokenSource hands out a valid access token. `DirectTokenSource` talks to Strava
with the user's own client id/secret (bring-your-own-app mode). A Worker-backed
source can be added later without touching the provider.
"""
import hmac
import json
import re
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Protocol

from . import config, stravaapi, vault
from .http import request_json

TOKEN_URL = stravaapi.url("oauth_token")
AUTHORIZE_URL = stravaapi.url("oauth_authorize")
REQUIRED_SCOPE = "activity:read_all"
CALLBACK_PORT = 8734


NOT_CONFIGURED_MESSAGE = "LapBar is not set up yet. Run `lapbar setup` (or use the button in the widget)."


class NotConfigured(RuntimeError):
    """No client id/secret available."""


class NotAuthorized(RuntimeError):
    """No usable refresh token: the one-time `lapbar auth` login has not been done."""


class TokenSource(Protocol):
    def access_token(self) -> str: ...


def _load_tokens() -> dict:
    try:
        return json.loads(vault.get("tokens") or "{}")
    except json.JSONDecodeError:
        return {}


def _save_tokens(resp: dict) -> dict:
    # Strava may rotate the refresh token on every refresh; always keep the newest.
    tokens = {
        "access_token": resp["access_token"],
        "refresh_token": resp["refresh_token"],
        "expires_at": resp["expires_at"],
    }
    vault.set("tokens", json.dumps(tokens))
    return tokens


class DirectTokenSource:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str = ""):
        self.client_id = client_id
        self.client_secret = client_secret
        self.seed_refresh_token = refresh_token

    def access_token(self) -> str:
        tokens = _load_tokens()
        if tokens.get("access_token") and tokens.get("expires_at", 0) > time.time() + 60:
            return tokens["access_token"]
        refresh = tokens.get("refresh_token") or self.seed_refresh_token
        if not refresh:
            raise NotAuthorized("Not signed in to Strava yet. Run `lapbar setup`.")
        resp = request_json(TOKEN_URL, form={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        })
        return _save_tokens(resp)["access_token"]


def default_token_source() -> TokenSource:
    client_id = config.get("STRAVA_CLIENT_ID")
    if not client_id:
        raise NotConfigured(NOT_CONFIGURED_MESSAGE)
    vault.migrate_legacy()
    client_secret = vault.client_secret()
    if not client_secret:
        raise NotConfigured(NOT_CONFIGURED_MESSAGE)
    return DirectTokenSource(client_id, client_secret, config.get("STRAVA_REFRESH_TOKEN"))


class AuthTimeout(RuntimeError):
    """The browser login never came back to the local callback."""


def _new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize(client_id: str, client_secret: str, *, open_browser: bool = True,
              timeout: float = 300, out=print) -> None:
    """One-time browser login: catch the redirect on localhost and store the tokens.

    The callback server is on localhost, so anything on the machine (or a web page that guesses the port) can send
    it a request while a sign-in is open. A random `state`, generated fresh for this one flow and sent with the
    authorize request, means only a callback that came from that exact authorize page is accepted: a forged
    callback carrying someone else's authorization code cannot be used to bind LapBar to the wrong Strava account.
    """
    redirect_uri = f"http://localhost:{CALLBACK_PORT}/callback"
    state = _new_state()
    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            if not hmac.compare_digest(params.get("state", ""), state):
                self.send_error(400, "Invalid or missing state")
                return                      # not our flow: ignored, not stored; keep waiting for the real one
            result.update(params)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"LapBar is authorized. You can close this tab.")

        def log_message(self, *args):
            pass

    try:
        server = HTTPServer(("127.0.0.1", CALLBACK_PORT), Handler)
    except OSError as e:
        raise RuntimeError(
            f"Port {CALLBACK_PORT} is already in use, probably by another LapBar sign-in that is still "
            "running. Close it and try again.") from e
    server.timeout = 1
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "approval_prompt": "force",
        "scope": f"read,{REQUIRED_SCOPE}",
        "state": state,
    })
    if open_browser:
        out("Opening your browser. If nothing happens, open this address yourself:")
    else:
        out("Open this address in your browser:")
    out(f"\n  {url}\n")
    if open_browser:
        webbrowser.open(url)
    deadline = time.monotonic() + timeout
    try:
        while "code" not in result and "error" not in result:
            if time.monotonic() > deadline:
                raise AuthTimeout(
                    "Nothing came back from Strava. The most common cause is an Authorization Callback "
                    "Domain that is not exactly 'localhost' in your Strava app settings.")
            server.handle_request()
    finally:
        server.server_close()

    if "error" in result:
        raise RuntimeError(f"Strava authorization failed: {result['error']}")
    if REQUIRED_SCOPE not in granted_scopes(result.get("scope", "")):
        raise RuntimeError(f"Missing '{REQUIRED_SCOPE}' permission. Re-run and leave every box ticked.")
    resp = request_json(TOKEN_URL, form={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": result["code"],
        "grant_type": "authorization_code",
    })
    _save_tokens(resp)
    out("Signed in.")


def granted_scopes(text: str) -> set[str]:
    """The scopes in a `scope` value: Strava has sent them comma-separated, and its changelog (2026-04-23) describes
    the token response's list as space-delimited, so both are accepted."""
    return {s for s in re.split(r"[,\s]+", text or "") if s}


def has_tokens() -> bool:
    try:
        return bool(_load_tokens().get("refresh_token"))
    except vault.VaultUnavailable:
        return False
