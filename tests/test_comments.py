"""Comments: noticed by their count, named, shown in a popup with a button to Strava, never posted."""
import json
from pathlib import Path

import pytest

from lapbar import cli, comments, config, details, stravaapi
from lapbar.http import HttpError


def c(i, who, text, athlete_id=1000, at=None):
    first, _, last = who.partition(" ")
    return {"id": i, "text": text, "created_at": at or f"2026-09-2{i}T10:00:00Z",
            "athlete": {"id": athlete_id, "firstname": first, "lastname": last}}


def act(i, comments_count, name="Ride"):
    return {"id": i, "name": name, "comments": comments_count, "url": f"https://www.strava.com/activities/{i}"}


@pytest.fixture
def api(monkeypatch):
    """The comments each activity has on Strava, and a log of the requests made."""
    state = {"comments": {}, "calls": [], "me": 7}

    def fake(url, token=None, **kw):
        state["calls"].append(url)
        if url == comments.ATHLETE_URL:
            return {"id": state["me"]}
        activity_id = int(url.split("/activities/")[1].split("/")[0])
        return state["comments"].get(activity_id, [])
    monkeypatch.setattr(comments, "request_json", fake)
    return state


def test_the_call_is_registered_and_only_reads():
    call = next(x for x in stravaapi.CALLS if x["id"] == "activity_comments")
    assert call["method"] == "GET" and call["scope"] == "activity:read_all"
    assert comments.COMMENTS_URL.startswith("https://www.strava.com/api/v3/activities/{id}/comments")


def test_a_new_comment_is_reported_with_who_wrote_it_and_what(api):
    api["comments"][1] = [c(1, "Anna B.", "Nice ride!")]
    previous = {"comments_seen": {"1": 0}, "activities": []}
    events, seen, texts = comments.track("t", [act(1, 1, "Sunday spin")], previous)
    assert events == [{"activity_id": 1, "name": "Sunday spin", "url": "https://www.strava.com/activities/1", "total": 1,
                       "comments": [{"who": "Anna B.", "text": "Nice ride!"}]}]
    assert seen == {"1": 1} and texts["1"][0]["who"] == "Anna B."


def test_only_the_new_comments_are_reported_when_the_old_ones_are_known(api):
    old = comments._one(c(1, "Anna B.", "Nice ride!"))
    api["comments"][1] = [c(1, "Anna B.", "Nice ride!"), c(2, "Marko K.", "Strong legs")]
    previous = {"comments_seen": {"1": 1}, "comment_texts": {"1": [old]}, "activities": []}
    events, seen, _ = comments.track("t", [act(1, 2)], previous)
    assert [x["who"] for x in events[0]["comments"]] == ["Marko K."] and seen == {"1": 2}


def test_the_first_ever_look_reports_nothing_but_learns_the_texts(api):
    api["comments"][1] = [c(1, "Anna B.", "Nice ride!")]
    events, seen, texts = comments.track("t", [act(1, 1)], None)
    assert events == [] and seen == {"1": 1} and len(texts["1"]) == 1


def test_an_older_cache_without_tracking_uses_its_activity_list_as_the_baseline(api):
    events, seen, _ = comments.track("t", [act(1, 2)], {"activities": [act(1, 2)]}, seed_limit=0)
    assert events == [] and seen == {"1": 2} and api["calls"] == []          # nothing new, so no request


def test_no_request_is_made_when_the_count_did_not_change(api):
    events, _, _ = comments.track("t", [act(1, 3)], {"comments_seen": {"1": 3}, "comment_texts": {"1": []}})
    assert events == [] and api["calls"] == []


def test_your_own_reply_is_not_announced_and_your_id_is_looked_up_once(api):
    api["me"] = 7
    api["comments"][1] = [c(1, "Anna B.", "Nice ride!"), c(2, "Gregor S.", "Thanks!", athlete_id=7)]
    previous = {"comments_seen": {"1": 1}, "comment_texts": {"1": [comments._one(c(1, "Anna B.", "Nice ride!"))]},
                "activities": []}
    events, _, _ = comments.track("t", [act(1, 2)], previous)
    assert events == []
    assert api["calls"].count(comments.ATHLETE_URL) == 1
    assert json.loads((config.state_dir() / "athlete.json").read_text()) == {"id": 7}
    comments.own_id("t")
    assert api["calls"].count(comments.ATHLETE_URL) == 1                      # remembered


def test_a_failed_request_holds_the_count_back_so_the_comment_is_reported_next_time(monkeypatch):
    def boom(url, token=None, **kw):
        raise HttpError(429, "slow down")
    monkeypatch.setattr(comments, "request_json", boom)
    events, seen, _ = comments.track("t", [act(1, 1), act(2, 1)], {"comments_seen": {"1": 0, "2": 0}, "activities": []})
    assert events == [] and seen == {"1": 0, "2": 0}


def test_only_the_newest_activities_are_watched(api):
    many = range(1, comments.RECENT + 6)
    listed = [act(i, 1) for i in many]
    previous = {"comments_seen": {str(i): 0 for i in many}, "activities": []}
    api["comments"] = {i: [c(1, "Anna B.", "hi")] for i in many}
    _, seen, _ = comments.track("t", listed, previous)
    assert len(seen) == comments.RECENT


def test_comment_text_is_cleaned_and_capped():
    assert comments.clean_text("hi\x00 there\x1b") == "hi there"
    assert len(comments.clean_text("x" * 5000)) == comments.MAX_TEXT
    assert comments._one({"id": 1, "text": None, "athlete": {}})["who"] == "Someone"


# ---- the popup

def test_the_popup_names_who_wrote_what():
    one = comments.notification({"name": "Sunday spin", "url": "https://www.strava.com/activities/1",
                                 "comments": [{"who": "Anna B.", "text": "Nice ride!"}]})
    assert one == {"title": "Anna B. commented on Sunday spin", "body": "Nice ride!", "url": "https://www.strava.com/activities/1"}
    many = comments.notification({"name": "Sunday spin", "url": None, "comments": [
        {"who": f"P{i} X.", "text": f"t{i}"} for i in range(5)]})
    assert many["title"] == "5 new comments on Sunday spin" and "P0 X.: t0" in many["body"] and "and 2 more" in many["body"]


def test_markup_in_a_comment_is_escaped_so_it_cannot_change_the_popup():
    n = comments.notification({"name": "A <b>ride</b>", "url": None,
                               "comments": [{"who": "<i>Eve</i> X.", "text": "<a href='http://evil'>click</a> & more"}]})
    assert "<" not in n["title"] and "<" not in n["body"] and "&amp;" in n["body"]


def test_a_long_comment_is_shortened_in_the_popup_only():
    n = comments.notification({"name": "R", "url": None, "comments": [{"who": "A B.", "text": "w" * 900}]})
    assert n["body"].endswith("…") and len(n["body"]) < 300


# ---- the details window's data

def test_details_keeps_comments_and_reuses_them_while_the_count_holds(api):
    activity = {"id": 5, "comments": 2, "kudos": 0, "prs": 0, "achievements": 0}
    api["comments"][5] = [c(1, "Anna B.", "one"), c(2, "Marko K.", "two")]
    assert details.comments_cached(activity) is None
    got = details.comments("t", activity)
    assert [x["text"] for x in got] == ["one", "two"]
    api["calls"].clear()
    assert details.comments("t", activity) == got and api["calls"] == []       # stored
    assert details.comments_cached({**activity, "comments": 3}) is None           # a new comment: ask again
    assert details.comments_cached({**activity, "comments": 0}) == []
    assert details.needs_download({"id": 6, "comments": 0}) is False


def test_the_details_command_returns_comments_and_a_description_of_the_activity(monkeypatch, capsys, api):
    activity = {"id": 5, "name": "Spin", "sport": "Ride", "start": "2026-09-20T15:27:53Z", "url": "https://www.strava.com/activities/5",
                "comments": 1, "kudos": 0, "prs": 0, "achievements": 0}
    monkeypatch.setattr(cli, "_read_cache", lambda: {"activities": [activity]})
    monkeypatch.setattr(cli.ratelimit, "check", lambda kind: {})
    monkeypatch.setattr(cli.auth, "default_token_source", lambda: type("T", (), {"access_token": lambda self: "t"})())
    api["comments"][5] = [c(1, "Anna B.", "hello")]
    with pytest.raises(SystemExit) as e:
        cli.main(["details", "5"])
    out = json.loads(capsys.readouterr().out)
    assert e.value.code == 0
    assert out["comments"][0]["who"] == "Anna B." and out["comments"][0]["text"] == "hello"
    assert out["activity"]["name"] == "Spin" and out["activity"]["url"].endswith("/5")


def test_lapbar_cannot_post_comments_and_says_so():
    assert all(call["method"] != "POST" or call["id"] == "oauth_token" for call in stravaapi.CALLS)
    window = (Path(__file__).resolve().parent.parent / "activity" / "shell.qml").read_text()
    assert "can read comments but not write them" in window and "onClicked: win.openStrava()" in window
    assert window.index('"Comment on Strava') > window.index("// ---------- comments") and window.count("onClicked: win.openStrava()") == 2
    assert window.index('"Comment on Strava') < window.index("// ---------- records")           # right below the comments


def test_widening_the_watch_does_not_announce_old_comments_as_new(api):
    previous = {"comments_seen": {"1": 1}, "activities": [act(1, 1), act(2, 4)]}
    events, seen, _ = comments.track("t", [act(1, 1), act(2, 4)], previous, seed_limit=0)
    assert events == [] and seen == {"1": 1, "2": 4} and api["calls"] == []
