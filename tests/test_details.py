"""Records (PRs, KOMs) and kudos names of one activity, by name."""
import json
from datetime import datetime

import pytest

from lapbar import auth, cli, details, ratelimit
from lapbar.http import HttpError
from lapbar.providers import strava

RAW = {
    "segment_efforts": [
        {"name": "Hill climb", "elapsed_time": 312, "distance": 1200.4, "pr_rank": 1, "kom_rank": None, "segment": {"name": "Hill climb"}},
        {"name": "Sprint", "elapsed_time": 45, "distance": 300, "pr_rank": None, "kom_rank": 3, "segment": {"name": "Sprint"}},
        {"name": "Both", "elapsed_time": 100, "distance": 500, "pr_rank": 2, "kom_rank": 1, "segment": {"name": "Both"}},
        {"name": "Plain", "elapsed_time": 90, "pr_rank": None, "kom_rank": None},
        {"name": "Slow", "elapsed_time": 80, "pr_rank": 4, "kom_rank": 11},           # outside PR 1-3 and the top 10
    ],
    "best_efforts": [{"name": "5k", "elapsed_time": 1500, "distance": 5000, "pr_rank": 1}],
}


def activity(**extra):
    return {"id": 42, "prs": 3, "achievements": 5, "kudos": 2, **extra}


# ---- building the list

def test_records_are_the_ranked_efforts_koms_first_then_prs():
    got = [(i["kind"], i["rank"], i["name"]) for i in details.build_records(RAW)]
    assert got == [("kom", 1, "Both"), ("kom", 3, "Sprint"), ("pr", 1, "5k"), ("pr", 1, "Hill climb"), ("pr", 2, "Both")]


def test_a_record_carries_the_time_it_was_achieved_in():
    hill = next(i for i in details.build_records(RAW) if i["name"] == "Hill climb")
    assert hill == {"name": "Hill climb", "seconds": 312, "distance_m": 1200, "source": "segment", "kind": "pr", "rank": 1}
    five_k = next(i for i in details.build_records(RAW) if i["name"] == "5k")
    assert five_k["source"] == "best_effort" and five_k["seconds"] == 1500


def test_nothing_ranked_gives_an_empty_list_and_odd_payloads_do_not_crash():
    assert details.build_records({}) == [] and details.build_records({"segment_efforts": None}) == []


# ---- storing

def test_records_are_downloaded_once_and_reused(monkeypatch):
    calls = []
    monkeypatch.setattr(details, "request_json", lambda url, token=None, **kw: calls.append(url) or RAW)
    first = details.records("tok", activity())
    assert details.records("tok", activity()) == first and len(calls) == 1
    assert "include_all_efforts=true" in calls[0] and "/activities/42" in calls[0]
    assert oct(details.path_for(42).stat().st_mode & 0o777) == "0o600"


def test_records_are_downloaded_again_when_the_counts_change(monkeypatch):
    calls = []
    monkeypatch.setattr(details, "request_json", lambda url, token=None, **kw: calls.append(url) or RAW)
    details.records("tok", activity())
    details.records("tok", activity(prs=4))            # Strava added a record after the upload was processed
    assert len(calls) == 2


def test_an_activity_without_records_costs_nothing(monkeypatch):
    monkeypatch.setattr(details, "request_json", lambda *a, **k: pytest.fail("no request expected"))
    assert details.records(None, activity(prs=0, achievements=0)) == []


def test_kudos_names_come_from_the_summary_then_the_disk_then_strava(monkeypatch):
    calls = []
    monkeypatch.setattr(details.kudos, "kudoers", lambda token, id: calls.append(id) or ["Ann B.", "Cy D."])
    assert details.kudoers(None, activity(), known=["Known K."]) == ["Known K."] and calls == []
    assert details.kudoers("tok", activity()) == ["Ann B.", "Cy D."] and calls == [42]
    assert details.kudoers("tok", activity()) == ["Ann B.", "Cy D."] and calls == [42]      # stored
    details.kudoers("tok", activity(kudos=3))                                                # one more kudo: look again
    assert calls == [42, 42]
    assert details.kudoers(None, activity(kudos=0)) == []


# ---- the latest ride, during a fetch

class Tokens:
    def access_token(self):
        return "tok"


def ride(**extra):
    return {"id": 7, "start_date_local": "2026-09-18T07:00:00Z", "distance": 30000, "total_elevation_gain": 100,
            "name": "Loop", "sport_type": "Ride", "moving_time": 3600, **extra}


def fetch_with(monkeypatch, acts, detail_calls, previous=None):
    def fake(url, token=None, **kw):
        if "/activities/7" in url and "include_all_efforts" in url:
            detail_calls.append(url)
            return RAW
        return {} if "/streams" in url or "/kudos" in url else acts
    monkeypatch.setattr(strava, "request_json", fake)
    monkeypatch.setattr(details, "request_json", fake)
    return strava.fetch(Tokens(), now=datetime(2026, 9, 19, 12), previous=previous)


def test_the_latest_ride_gets_its_records_when_it_has_any(monkeypatch):
    calls = []
    out = fetch_with(monkeypatch, [ride(pr_count=2, achievement_count=4)], calls)
    assert [r["name"] for r in out["latest"]["records"]][:2] == ["Both", "Sprint"] and len(calls) == 1


def test_a_ride_without_records_asks_for_nothing(monkeypatch):
    calls = []
    out = fetch_with(monkeypatch, [ride()], calls)
    assert "records" not in out["latest"] and calls == []


def test_records_are_reused_while_the_ride_and_its_counts_are_unchanged(monkeypatch):
    calls = []
    acts = [ride(pr_count=2, achievement_count=4)]
    first = fetch_with(monkeypatch, acts, calls)
    again = fetch_with(monkeypatch, acts, calls, previous=first)
    assert again["latest"]["records"] == first["latest"]["records"] and len(calls) == 1


def test_a_failed_records_request_does_not_break_the_refresh(monkeypatch):
    def fake(url, token=None, **kw):
        if "include_all_efforts" in url:
            raise HttpError(429, "slow down")
        return {} if "/streams" in url or "/kudos" in url else [ride(pr_count=1, achievement_count=1)]
    monkeypatch.setattr(strava, "request_json", fake)
    monkeypatch.setattr(details, "request_json", fake)
    out = strava.fetch(Tokens(), now=datetime(2026, 9, 19, 12))
    assert out["latest"]["name"] == "Loop" and "records" not in out["latest"]


# ---- `lapbar details <id>`

def write_cache(tmp_path, monkeypatch, activities, kudoers=None):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    (tmp_path / "lapbar").mkdir()
    (tmp_path / "lapbar" / "cache.json").write_text(json.dumps({"activities": activities, "kudoers": kudoers or {}}))


def run(capsys, *argv):
    try:
        cli.main(["details", *argv])
        code = 0
    except SystemExit as e:
        code = e.code or 0
    return code, json.loads(capsys.readouterr().out)


def test_details_command_returns_records_and_names_and_downloads_only_what_is_missing(monkeypatch, capsys, tmp_path):
    write_cache(tmp_path, monkeypatch, [activity()], kudoers={"42": ["Known K."]})
    checks, calls = [], []
    monkeypatch.setattr(ratelimit, "check", lambda kind, snap=None: checks.append(kind))
    monkeypatch.setattr(auth, "default_token_source", lambda: Tokens())
    monkeypatch.setattr(details, "request_json", lambda url, token=None, **kw: calls.append(url) or RAW)
    code, out = run(capsys, "42")
    assert code == 0 and out["kudoers"] == ["Known K."] and out["records"][0]["name"] == "Both"
    assert checks == ["action"] and len(calls) == 1               # the names were known, so only the records cost a request
    checks.clear()
    code, out = run(capsys, "42")                                   # everything stored: no budget check, no request
    assert code == 0 and checks == [] and len(calls) == 1


def test_details_of_an_unknown_activity_is_a_clean_error(monkeypatch, capsys, tmp_path):
    write_cache(tmp_path, monkeypatch, [activity()])
    code, out = run(capsys, "999")
    assert code == 1 and out["error"] == "unknown_activity"


def test_details_respects_the_request_budget(monkeypatch, capsys, tmp_path):
    write_cache(tmp_path, monkeypatch, [activity()])
    def refuse(kind, snap=None):
        raise ratelimit.BudgetExhausted("used up", {})
    monkeypatch.setattr(ratelimit, "check", refuse)
    code, out = run(capsys, "42")
    assert code == 1 and out["error"] == "budget"
