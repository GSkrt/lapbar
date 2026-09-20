import json
import urllib.error

from lapbar import auth, cli
from lapbar.http import HttpError
from lapbar.providers import strava


def run_fetch(monkeypatch, capsys, exc, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(strava, "fetch", lambda previous=None, backfill=0, optional=True, ftp=0, **kw: (_ for _ in ()).throw(exc))
    try:
        cli.main(["fetch", "--print"])
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_errors_are_machine_readable(monkeypatch, capsys, tmp_path):
    cases = [
        (auth.NotConfigured("x"), "not_configured"),
        (auth.NotAuthorized("x"), "not_authorized"),
        (HttpError(429, "slow down"), "rate_limited"),
        (HttpError(401, "nope"), "not_authorized"),
        (HttpError(500, "boom"), "http_error"),
        (urllib.error.URLError("dns"), "network"),
    ]
    for exc, expected in cases:
        code, payload = run_fetch(monkeypatch, capsys, exc, tmp_path)
        assert code == 1 and payload["error"] == expected and payload["message"]


def test_success_prints_summary_and_writes_cache(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(strava, "fetch", lambda previous=None, backfill=0, optional=True, ftp=0, **kw: {"provider": "strava", "latest": None})
    try:
        cli.main(["fetch", "--print"])
    except SystemExit as e:
        assert e.code == 0
    assert json.loads(capsys.readouterr().out)["provider"] == "strava"
    assert json.loads((tmp_path / "lapbar" / "cache.json").read_text())["provider"] == "strava"


def test_mute_switch_persists_and_toggles(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    def run(*argv):
        try:
            cli.main(list(argv))
        except SystemExit as e:
            assert e.code == 0
        return json.loads(capsys.readouterr().out)["muted"]

    assert run("mute") is False
    assert run("mute", "on") is True
    assert run("mute", "status") is True          # survives across invocations
    assert run("mute", "toggle") is False
    assert run("mute", "toggle") is True
    assert run("mute", "off") is False


def test_events_are_printed_once_but_never_cached(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    summary = {"provider": "strava", "latest": None, "kudos_events": [{"activity_id": 1, "count": 2}]}
    monkeypatch.setattr(strava, "fetch", lambda previous=None, backfill=0, optional=True, ftp=0, **kw: dict(summary))
    try:
        cli.main(["fetch", "--print"])
    except SystemExit:
        pass
    printed = json.loads(capsys.readouterr().out)
    assert printed["kudos_events"] and printed["muted"] is False
    cached = json.loads((tmp_path / "lapbar" / "cache.json").read_text())
    assert "kudos_events" not in cached and "muted" not in cached


# ---- streams / charts commands

def fake_run(monkeypatch, capsys, argv):
    try:
        cli.main(argv)
    except SystemExit as e:
        code = e.code
    return code, json.loads(capsys.readouterr().out)


def prepare(monkeypatch, keyring):
    from lapbar import streams
    keyring.items.update(client_secret="s" * 40, tokens=json.dumps({"access_token": "a", "refresh_token": "r", "expires_at": 9e12}))
    from lapbar import config
    config.write_env_file(config.user_env_path(), {"STRAVA_CLIENT_ID": "12345"})
    cli._write_cache({"activities": [{"id": 5, "name": "Ride", "sport": "Ride", "family": "ride", "start": "2026-09-18T18:00:00Z"}]})
    raw = {"time": {"data": list(range(60))}, "distance": {"data": [i * 10.0 for i in range(60)]},
           "altitude": {"data": [100.0] * 60}}
    monkeypatch.setattr(streams, "request_json", lambda url, token=None, **kw: raw)
    return streams


def test_streams_command_downloads_once_and_reports_the_path(monkeypatch, capsys, keyring):
    streams = prepare(monkeypatch, keyring)
    code, out = fake_run(monkeypatch, capsys, ["streams", "5"])
    assert code == 0 and out["path"].endswith("streams/5.json") and out["series"] == ["altitude"]
    monkeypatch.setattr(streams, "request_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("downloaded twice")))
    assert fake_run(monkeypatch, capsys, ["streams", "5"])[0] == 0        # served from disk


def test_streams_command_explains_when_strava_has_none(monkeypatch, capsys, keyring):
    streams = prepare(monkeypatch, keyring)
    monkeypatch.setattr(streams, "request_json", lambda *a, **k: (_ for _ in ()).throw(HttpError(404, "none")))
    code, out = fake_run(monkeypatch, capsys, ["streams", "5"])
    assert code == 1 and out["error"] == "no_streams"


def test_charts_command_opens_the_window_with_the_theme(monkeypatch, capsys, keyring):
    prepare(monkeypatch, keyring)
    opened = []
    monkeypatch.setattr(charts_module(), "open_window", lambda path, env=None: opened.append((str(path), env)) or 1)
    code, out = fake_run(monkeypatch, capsys, ["charts", "5", "--fg", "#eee", "--bg", "#111", "--font", "JetBrains"])
    assert code == 0 and out == {"ok": True}
    path, env = opened[0]
    assert path.endswith("streams/5.json")
    assert env == {"LAPBAR_FG": "#eee", "LAPBAR_BG": "#111", "LAPBAR_FONT": "JetBrains"}


def charts_module():
    from lapbar import charts
    return charts
