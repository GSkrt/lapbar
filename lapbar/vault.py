"""Where secrets live: the system keyring when there is one, otherwise a private file.

Two items are stored: "client_secret" (your Strava app's secret) and "tokens" (JSON with the access and
refresh tokens). Nothing secret is ever written to the cache, to logs or to the plain-text config file
(which only holds the non-secret Client ID).

Order of lookup: the keyring, then the private file, then legacy plain-text files from older versions
(which are migrated into the keyring the first time it is reachable and then deleted).
"""
import json
import os
import subprocess

from . import config, system

SERVICE = "lapbar"
TIMEOUT = 8  # seconds; a locked keyring can wait for an unlock prompt


class VaultUnavailable(RuntimeError):
    """The keyring could not be reached (locked, no session bus, secret-tool missing)."""


def _run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    if os.environ.get("LAPBAR_NO_KEYRING"):  # opt out: use the private file instead
        raise FileNotFoundError("keyring disabled by LAPBAR_NO_KEYRING")
    # secret-tool handles the Strava client secret and the sign-in tokens, so it is resolved to Omarchy's own
    # copy rather than trusting whatever a $PATH search would find first (see system.py).
    return subprocess.run([system.tool("secret-tool"), *args], input=stdin, capture_output=True, text=True,
                          timeout=TIMEOUT)


class Keyring:
    def _call(self, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
        try:
            return _run(args, stdin)
        except FileNotFoundError:
            raise VaultUnavailable("secret-tool is not installed (package libsecret)") from None
        except subprocess.TimeoutExpired:
            raise VaultUnavailable("the keyring did not answer in time; it may be locked") from None

    def get(self, name: str) -> str | None:
        done = self._call(["lookup", "service", SERVICE, "key", name])
        if done.returncode == 0:
            return done.stdout or None
        if done.stderr.strip():  # a plain "not found" is silent; anything said means a real problem
            raise VaultUnavailable(done.stderr.strip())
        return None

    def set(self, name: str, value: str) -> None:
        done = self._call(["store", "--label", f"lapbar {name}", "service", SERVICE, "key", name], value)
        if done.returncode != 0:
            raise VaultUnavailable(done.stderr.strip() or "could not write to the keyring")

    def delete(self, name: str) -> None:
        self._call(["clear", "service", SERVICE, "key", name])


class PrivateFile:
    """Fallback when there is no keyring: one JSON file that only the current user can read."""

    @property
    def path(self):
        return config.state_dir() / "secrets.json"

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        config.private_dir(self.path.parent)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # 0600 from the first byte
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        tmp.replace(self.path)

    def get(self, name: str) -> str | None:
        return self._load().get(name) or None

    def set(self, name: str, value: str) -> None:
        data = self._load()
        data[name] = value
        self._save(data)

    def delete(self, name: str) -> None:
        data = self._load()
        if data.pop(name, None) is not None:
            if data:
                self._save(data)
            else:
                self.path.unlink(missing_ok=True)


def _legacy(name: str) -> str | None:
    """Plain-text locations used by earlier versions (read-only fallback)."""
    if name == "tokens":
        try:
            return (config.state_dir() / "tokens.json").read_text() or None
        except FileNotFoundError:
            return None
    if name == "client_secret":
        return config.get("STRAVA_CLIENT_SECRET") or None
    return None


def get(name: str) -> str | None:
    problem = None
    try:
        value = Keyring().get(name)
        if value:
            return value
    except VaultUnavailable as e:
        problem = e
    value = PrivateFile().get(name) or _legacy(name)
    if value:
        return value
    if problem:
        raise problem
    return None


def set(name: str, value: str) -> str:  # noqa: A001 - mirrors get/delete
    """Store a secret; returns where it went: "keyring" or "file"."""
    try:
        Keyring().set(name, value)
        PrivateFile().delete(name)
        return "keyring"
    except VaultUnavailable:
        PrivateFile().set(name, value)
        return "file"


def delete(name: str) -> None:
    try:
        Keyring().delete(name)
    except VaultUnavailable:
        pass
    PrivateFile().delete(name)


def keyring_available() -> bool:
    try:
        Keyring().get("probe")
        return True
    except VaultUnavailable:
        return False


def client_secret() -> str | None:
    return os.environ.get("STRAVA_CLIENT_SECRET") or get("client_secret")


def migrate_legacy() -> list[str]:
    """Move plain-text secrets from older versions into the keyring, then delete them.

    Only runs when the keyring is reachable, so a locked keyring never causes secrets to be
    parked somewhere weaker.
    """
    tokens_file = config.state_dir() / "tokens.json"
    env_file = config.user_env_path()
    has_old_secret = "STRAVA_CLIENT_SECRET" in config.read_env_file(env_file)
    if not (tokens_file.exists() or has_old_secret) or not keyring_available():
        return []
    moved = []
    if tokens_file.exists():
        if _copy_verified("tokens", tokens_file.read_text()):
            tokens_file.unlink()
            moved.append("tokens")
    if has_old_secret:
        secret = config.read_env_file(env_file)["STRAVA_CLIENT_SECRET"]
        if not secret or _copy_verified("client_secret", secret):
            config.write_env_file(env_file, {}, remove=["STRAVA_CLIENT_SECRET"])
            moved.append("client_secret")
    return moved


def _copy_verified(name: str, value: str) -> bool:
    """Put `value` in the keyring unless it already holds something, and confirm by reading back.

    Returns True only when the keyring holds a value for `name`, so the caller may then delete the
    plain-text original. An existing keyring value is never overwritten.
    """
    ring = Keyring()
    if ring.get(name) is None:
        ring.set(name, value)
    return ring.get(name) is not None
