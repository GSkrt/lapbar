import json
import os
import stat

import pytest

from lapbar import auth, config, vault


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_secrets_go_to_the_keyring_and_nowhere_else(keyring, tmp_path):
    assert vault.set("client_secret", "s3cret") == "keyring"
    assert keyring.items["client_secret"] == "s3cret"
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert "s3cret" not in f.read_text(), f"secret leaked into {f}"


def test_without_a_keyring_the_fallback_file_is_private(keyring):
    keyring.broken = "Cannot autolaunch D-Bus without X11 $DISPLAY"
    assert vault.set("client_secret", "s3cret") == "file"
    path = config.state_dir() / "secrets.json"
    assert mode(path) == 0o600
    assert mode(path.parent) == 0o700
    assert vault.get("client_secret") == "s3cret"


def test_file_is_never_world_readable_even_transiently(keyring, monkeypatch):
    keyring.broken = "no bus"
    seen = []
    real_replace = type(config.state_dir()).replace

    def spy(self, target):
        seen.append(mode(self))  # the temp file, just before it becomes secrets.json
        return real_replace(self, target)

    monkeypatch.setattr(type(config.state_dir()), "replace", spy)
    vault.set("tokens", "{}")
    assert seen and all(m == 0o600 for m in seen)


def test_keyring_write_removes_older_file_copy(keyring):
    keyring.broken = "locked"
    vault.set("client_secret", "old")
    keyring.broken = None
    vault.set("client_secret", "new")
    assert not (config.state_dir() / "secrets.json").exists()
    assert vault.get("client_secret") == "new"


def test_locked_keyring_is_reported_not_mistaken_for_unset(keyring):
    keyring.broken = "Object does not exist at path /org/freedesktop/secrets/collection/login"
    with pytest.raises(vault.VaultUnavailable):
        vault.get("client_secret")


def test_unset_item_is_none_not_an_error(keyring):
    assert vault.get("client_secret") is None


def test_client_secret_prefers_the_environment(keyring, monkeypatch):
    vault.set("client_secret", "from-keyring")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "from-env")
    assert vault.client_secret() == "from-env"


def test_legacy_plaintext_is_migrated_into_the_keyring_and_deleted(keyring):
    env = config.user_env_path()
    config.write_env_file(env, {"STRAVA_CLIENT_ID": "12345", "STRAVA_CLIENT_SECRET": "old-secret"})
    tokens = config.state_dir() / "tokens.json"
    config.private_dir(tokens.parent)
    tokens.write_text(json.dumps({"access_token": "a", "refresh_token": "r", "expires_at": 1}))

    assert sorted(vault.migrate_legacy()) == ["client_secret", "tokens"]

    assert keyring.items["client_secret"] == "old-secret"
    assert json.loads(keyring.items["tokens"])["refresh_token"] == "r"
    assert not tokens.exists()
    text = env.read_text()
    assert "STRAVA_CLIENT_ID=12345" in text            # the non-secret id stays
    assert "old-secret" not in text and "STRAVA_CLIENT_SECRET" not in text


def test_migration_waits_while_the_keyring_is_locked(keyring):
    config.write_env_file(config.user_env_path(), {"STRAVA_CLIENT_SECRET": "old-secret"})
    keyring.broken = "locked"
    assert vault.migrate_legacy() == []
    assert "old-secret" in config.user_env_path().read_text()   # untouched, and still usable
    keyring.broken = None
    assert vault.migrate_legacy() == ["client_secret"]


def test_migration_never_overwrites_a_newer_keyring_value(keyring):
    keyring.items["client_secret"] = "newer"
    config.write_env_file(config.user_env_path(), {"STRAVA_CLIENT_SECRET": "older"})
    vault.migrate_legacy()
    assert keyring.items["client_secret"] == "newer"


def test_tokens_roundtrip_through_auth_uses_the_keyring(keyring, monkeypatch):
    auth._save_tokens({"access_token": "a", "refresh_token": "r", "expires_at": 9})
    assert json.loads(keyring.items["tokens"])["refresh_token"] == "r"
    assert auth.has_tokens() is True


def test_env_file_keeps_comments_and_other_keys_and_is_private():
    path = config.user_env_path()
    config.private_dir(path.parent)
    path.write_text("# my notes\nOTHER=1\nSTRAVA_CLIENT_ID=old\n")
    config.write_env_file(path, {"STRAVA_CLIENT_ID": "999"})
    assert path.read_text().splitlines() == ["# my notes", "OTHER=1", "STRAVA_CLIENT_ID=999"]
    assert mode(path) == 0o600


def test_cache_is_owner_only(keyring, monkeypatch):
    from lapbar import cli
    cli._write_cache({"provider": "strava"})
    assert mode(config.cache_path()) == 0o600
    assert mode(config.cache_path().parent) == 0o700


def test_tests_cannot_reach_the_real_keyring():
    """Regression: an early test run once overwrote a developer's real sign-in tokens."""
    import subprocess
    with pytest.raises(AssertionError, match="real secret-tool"):
        subprocess.run(["secret-tool", "lookup", "service", "lapbar", "key", "tokens"])


def test_migration_does_not_delete_the_original_if_the_copy_cannot_be_confirmed(keyring, monkeypatch):
    config.write_env_file(config.user_env_path(), {"STRAVA_CLIENT_SECRET": "old-secret"})
    real_call = keyring.__call__

    def swallow_writes(args, stdin=None):
        if args[0] == "store":                     # the keyring says nothing and stores nothing
            keyring.calls.append(list(args))
            import subprocess as sp
            return sp.CompletedProcess(args, 0, "", "")
        return real_call(args, stdin)

    monkeypatch.setattr(vault, "_run", swallow_writes)
    assert vault.migrate_legacy() == []
    assert "old-secret" in config.user_env_path().read_text()     # still there


def test_no_keyring_switch_makes_the_real_runner_refuse_before_spawning_anything(monkeypatch):
    from conftest import REAL_RUN
    monkeypatch.setenv("LAPBAR_NO_KEYRING", "1")
    with pytest.raises(FileNotFoundError):
        REAL_RUN(["lookup", "service", "lapbar", "key", "tokens"])


def test_no_keyring_switch_means_secrets_go_to_the_private_file(monkeypatch):
    def refuse(args, stdin=None):
        raise FileNotFoundError("keyring disabled")
    monkeypatch.setattr(vault, "_run", refuse)
    assert vault.set("client_secret", "s3cret") == "file"
    assert vault.get("client_secret") == "s3cret"
