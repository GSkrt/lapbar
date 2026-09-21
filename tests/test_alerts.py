"""Popups for kudos and comments: one per activity, each ending with an orange View on Strava link to its activity."""
import json

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

    class Done:
        stdout = "default\n"
    monkeypatch.setattr(alerts.subprocess, "run", lambda cmd, **kw: shown.append(cmd) or Done())
    monkeypatch.setattr(alerts.subprocess, "Popen", lambda cmd, **kw: opened.append(cmd))
    assert alerts.show("comments", COMMENT) == "default"
    assert opened == [["xdg-open", URL]] and "default=View on Strava" in shown[0] and "-A" in shown[0]
    Done.stdout = "\n"
    opened.clear()
    assert alerts.show("kudos", KUDOS) is None and opened == []


def test_the_alert_command_shows_an_event_and_rejects_rubbish(monkeypatch, capsys):
    got = []
    monkeypatch.setattr(alerts, "show", lambda kind, event: got.append((kind, event)) or "default")
    payload = json.dumps({"kind": "comments", "event": {**COMMENT, "comments": [{"who": "A B.", "text": "hi\x00"}]}})
    with pytest.raises(SystemExit) as e:
        cli.main(["alert", "--deliver", payload])
    assert e.value.code == 0 and got[0][0] == "comments" and got[0][1]["comments"] == [{"who": "A B.", "text": "hi"}]
    with pytest.raises(SystemExit) as e:
        cli.main(["alert", "--deliver", json.dumps({"kind": "kudos", "event": KUDOS})])
    assert e.value.code == 0 and got[1][0] == "kudos"
    for bad in ("not json", json.dumps({"kind": "nope", "event": {}}), json.dumps({"kind": "kudos", "event": {"name": "x"}})):
        with pytest.raises(SystemExit) as e:
            cli.main(["alert", "--deliver", bad])
        assert e.value.code == 1 and "bad_event" in capsys.readouterr().out


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
