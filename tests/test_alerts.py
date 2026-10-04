"""Popups for kudos and comments: one per activity, each ending with an orange View on Strava link to its activity."""
import io
import json
import os

import pytest

from lapbar import alerts, cli, kudos

URL = "https://www.strava.com/activities/9"
COMMENT = {"name": "Sunday spin", "url": URL, "comments": [{"who": "Anna B.", "text": "Nice ride!"}]}
KUDOS = {"activity_id": 9, "name": "Sunday spin", "url": URL, "count": 2, "total": 5, "from": ["Ana K.", "Bo M."]}


def test_every_popup_ends_with_an_orange_view_on_strava_link_to_its_own_activity():
    for kind, event in (("comments", COMMENT), ("kudos", KUDOS)):
        n = alerts.compose(kind, event)
        last = n["body"].split("\n")[-1]
        assert last == f'<a href="{URL}"><font color="#FC5200">View on Strava</font></a>'      # below the text, in Strava's orange
        assert n["url"] == URL and n["actions"] == [{"key": "default", "label": "View on Strava"}]


def test_the_kudos_popup_names_who_gave_them_and_on_what():
    n = alerts.compose("kudos", KUDOS)
    assert n["title"].endswith("2 new kudos") and n["title"].startswith(kudos.THUMB)
    assert n["body"].startswith("Ana K., Bo M. on “Sunday spin” · 5 total")
    many = alerts.compose("kudos", {**KUDOS, "count": 1, "from": [f"P{i} X." for i in range(6)]})
    assert many["title"].endswith("1 new kudo") and "and 2 more on" in many["body"]


def test_an_activity_without_a_usable_address_gets_no_link_rather_than_a_wrong_one():
    for bad in (None, "https://evil.example/activities/1", "https://www.strava.com/activities/1/../x", "file:///etc/passwd"):
        n = alerts.compose("comments", {**COMMENT, "url": bad})
        assert n["url"] is None and n["actions"] == [] and "<a " not in n["body"]


def test_only_activity_pages_on_strava_are_ever_linked():
    assert alerts.safe_url(URL) == URL
    for bad in ("https://www.strava.com/activities/abc", "http://www.strava.com/activities/1", None, 5):
        assert alerts.safe_url(bad) is None


def test_text_from_other_people_cannot_add_markup_or_links():
    n = alerts.compose("kudos", {**KUDOS, "name": "<a href='http://evil'>x</a>", "from": ["<b>Eve</b> X."]})
    assert "<a href='http://evil'>" not in n["body"] and "&lt;b&gt;Eve" in n["body"]
    assert n["body"].count("<a ") == 1                                                 # only ours


def test_clicking_the_popup_opens_the_activity_and_no_click_opens_nothing(monkeypatch):
    opened, shown = [], []
    answer = ["default"]
    monkeypatch.setattr(alerts.notify, "show", lambda title, body, actions, timeout: shown.append(actions) or answer[0])
    monkeypatch.setattr(alerts.subprocess, "Popen", lambda cmd, **kw: opened.append(cmd))
    assert alerts.show("comments", COMMENT) == "default"
    assert len(opened) == 1 and os.path.basename(opened[0][0]) == "xdg-open" and opened[0][1] == URL
    assert shown[0] == [("default", "View on Strava")]
    answer[0] = None
    opened.clear()
    assert alerts.show("kudos", KUDOS) is None and opened == []


def test_the_popup_text_never_reaches_a_command_line(monkeypatch):
    """Comment text and names go over the session bus, not as arguments to a process everyone can list."""
    started, sent = [], []
    monkeypatch.setattr(alerts.subprocess, "run", lambda cmd, **kw: started.append(cmd))
    monkeypatch.setattr(alerts.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    monkeypatch.setattr(alerts.notify, "show", lambda title, body, actions, timeout: sent.append((title, body)))
    event = {**COMMENT, "comments": [{"who": "Private Person", "text": "a private remark"}]}
    alerts.show("comments", event)
    assert started == [] and "a private remark" in sent[0][1]


def test_no_notification_service_means_no_popup_not_a_crash(monkeypatch):
    def unavailable(*a, **k):
        raise alerts.notify.DBusError("no reachable session bus")
    monkeypatch.setattr(alerts.notify, "show", unavailable)
    assert alerts.show("kudos", KUDOS) is None


def test_the_alert_command_shows_an_event_and_rejects_rubbish(monkeypatch, capsys):
    got = []
    monkeypatch.setattr(alerts, "show", lambda kind, event: got.append((kind, event)) or "default")

    def deliver(payload):
        # The payload comes in on stdin now, not as a --deliver=<JSON> argument: a command-line argument
        # sits in the process table (`ps`, /proc/<pid>/cmdline) for anyone on the machine to read, and this
        # payload can carry another person's name and the text of their comment.
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO(payload))
        with pytest.raises(SystemExit) as e:
            cli.main(["alert", "--deliver"])
        return e.value.code

    payload = json.dumps({"kind": "comments", "event": {**COMMENT, "comments": [{"who": "A B.", "text": "hi\x00"}]}})
    assert deliver(payload) == 0 and got[0][0] == "comments" and got[0][1]["comments"] == [{"who": "A B.", "text": "hi"}]
    assert deliver(json.dumps({"kind": "kudos", "event": KUDOS})) == 0 and got[1][0] == "kudos"
    for bad in ("not json", json.dumps({"kind": "nope", "event": {}}), json.dumps({"kind": "kudos", "event": {"name": "x"}})):
        assert deliver(bad) == 1 and "bad_event" in capsys.readouterr().out


def test_delivering_a_popup_puts_the_payload_on_stdin_not_argv(monkeypatch):
    class FakeStdin:
        def __init__(self):
            self.data = b""

        def write(self, chunk):
            self.data += chunk

        def close(self):
            pass

    started = []

    def fake_popen(cmd, **kw):
        proc = type("Proc", (), {"stdin": FakeStdin()})()
        started.append((cmd, kw, proc))
        return proc

    monkeypatch.setattr(alerts.subprocess, "Popen", fake_popen)
    alerts.deliver("kudos", KUDOS)
    cmd, kw, proc = started[0]
    assert cmd[-1] == "--deliver" and "--deliver" not in cmd[:-1]
    payload = json.loads(proc.stdin.data)
    assert payload["kind"] == "kudos" and payload["event"] == KUDOS and kw["start_new_session"] is True


def fetch_with(monkeypatch, kudos_events, comment_events):
    from lapbar import mute
    delivered, written = [], []
    summary = {"activities": [], "kudos_events": kudos_events, "comment_events": comment_events}
    monkeypatch.setattr(cli.ratelimit, "check", lambda kind: {})
    monkeypatch.setattr(cli.ratelimit, "backfill_quota", lambda snap, n: 0)
    monkeypatch.setattr(cli.ratelimit, "allow_optional", lambda snap: False)
    monkeypatch.setattr(cli.strava, "fetch", lambda **kw: dict(summary))
    monkeypatch.setattr(cli.alerts, "deliver", lambda kind, e: delivered.append((kind, e)))
    monkeypatch.setattr(cli.coach, "tick", lambda s: (None, []))
    monkeypatch.setattr(cli.export, "spawn_if_enabled", lambda: None)
    monkeypatch.setattr(cli, "_write_cache", lambda s: written.append(s))
    monkeypatch.setattr(cli, "_read_cache", lambda: None)
    with pytest.raises(SystemExit):
        cli.main(["fetch"])
    return delivered, written, mute


def test_two_activities_with_news_give_two_popups_each_with_its_own_link(monkeypatch):
    other = {**KUDOS, "activity_id": 10, "url": "https://www.strava.com/activities/10"}
    delivered, written, _ = fetch_with(monkeypatch, [KUDOS, other], [COMMENT])
    assert [(k, e["url"]) for k, e in delivered] == [("kudos", URL), ("kudos", other["url"]), ("comments", URL)]
    assert "kudos_events" not in written[0] and "comment_events" not in written[0]         # never cached, never replayed


def test_a_muted_refresh_delivers_nothing(monkeypatch):
    from lapbar import mute
    mute.set_muted(True)
    delivered, written, _ = fetch_with(monkeypatch, [KUDOS], [COMMENT])
    assert delivered == [] and "comment_events" not in written[0]
