"""Older years for the calendar: downloaded once, stored, merged into the calendar's day totals."""
import json
import re
from datetime import datetime

import pytest

from lapbar import auth, cli, history, ratelimit
from lapbar.http import HttpError
from lapbar.providers import strava

NOW = datetime(2026, 9, 19, 12)


class Tokens:
    def access_token(self):
        return "tok"


def ride(date, dist=10000, elev=100, **extra):
    return {"id": abs(hash((date, dist))) % 10**9, "start_date_local": f"{date}T07:00:00Z", "distance": dist,
            "total_elevation_gain": elev, "name": "R", "sport_type": "Ride", "moving_time": 1800, **extra}


class FakeStrava:
    """A tiny activity list served by year, honouring `after`, `before` and paging like the real endpoint."""

    def __init__(self, acts):
        self.acts, self.calls = acts, []

    def __call__(self, url, token=None, **kw):
        self.calls.append(url)
        q = dict(re.findall(r"[?&](\w+)=(\d+)", url))
        after, before = int(q.get("after", 0)), int(q.get("before", 10**12))
        page, per = int(q.get("page", 1)), int(q.get("per_page", 30))
        def ts(a):
            return datetime.fromisoformat(a["start_date_local"].replace("Z", "")).replace(tzinfo=__import__("datetime").timezone.utc).timestamp()
        rows = [a for a in sorted(self.acts, key=ts) if after < ts(a) < before]
        return rows[(page - 1) * per: page * per]


@pytest.fixture
def api(monkeypatch):
    fake = FakeStrava([
        ride("2025-06-01"), ride("2025-06-02", 20000, 50), ride("2025-12-31"),
        ride("2024-03-10"),
        ride("2023-07-04"),
        ride("2026-01-02"),
    ])
    monkeypatch.setattr(strava, "request_json", fake)
    return fake


# ---- downloading older years

def test_a_year_is_downloaded_once_and_stored(api):
    assert strava.sync_history("tok", 2026, 5, budget=1) == 1
    assert [a["start"][:10] for a in history.year_activities(2025)] == ["2025-12-31", "2025-06-02", "2025-06-01"]
    assert history.index()["years"]["2025"]["count"] == 3
    assert strava.sync_history("tok", 2026, 5, budget=1) == 1            # the next call carries on with 2024, not 2025 again
    assert sorted(history.index()["years"]) == ["2024", "2025"]


def test_only_a_couple_of_years_per_refresh_and_the_rest_later(api):
    assert strava.sync_history("tok", 2026, 5, budget=2) == 2
    assert history.summary()["complete"] is False
    assert strava.sync_history("tok", 2026, 5, budget=2) == 2            # 2023 and 2022 (empty)
    while not history.summary()["complete"]:
        strava.sync_history("tok", 2026, 5, budget=2)
    assert history.summary()["years"] == [2025, 2024, 2023]


def test_it_stops_after_three_empty_years_in_a_row(api):
    strava.sync_history("tok", 2026, 99, budget=99)
    stored = sorted(int(y) for y in history.index()["years"])
    assert stored == [2020, 2021, 2022, 2023, 2024, 2025]                # 2023 has data, then 2022, 2021, 2020 are empty
    assert history.summary()["complete"] is True
    calls = len(api.calls)
    strava.sync_history("tok", 2026, 99, budget=99)                      # nothing left to fetch
    assert len(api.calls) == calls


def test_the_number_of_years_is_respected(api):
    strava.sync_history("tok", 2026, 1, budget=99)
    assert sorted(history.index()["years"]) == ["2025"] and history.summary()["complete"] is True


def test_refresh_downloads_stored_years_again(api):
    strava.sync_history("tok", 2026, 2, budget=99)
    api.calls.clear()
    assert strava.sync_history("tok", 2026, 2, budget=99) == 0 and api.calls == []
    assert strava.sync_history("tok", 2026, 2, budget=99, refresh=True) == 2


def test_a_busy_year_is_read_page_by_page(monkeypatch):
    monkeypatch.setattr(strava, "PAGE_SIZE", 2)
    fake = FakeStrava([ride(f"2025-05-0{d}") for d in range(1, 6)])
    monkeypatch.setattr(strava, "request_json", fake)
    strava.sync_history("tok", 2026, 1, budget=1)
    assert len(history.year_activities(2025)) == 5 and len(fake.calls) == 3


def test_late_new_year_activities_stay_in_their_own_local_year(monkeypatch):
    fake = FakeStrava([ride("2024-12-31"), ride("2025-01-01")])
    monkeypatch.setattr(strava, "request_json", fake)
    strava.sync_history("tok", 2026, 2, budget=99)
    assert [a["start"][:10] for a in history.year_activities(2025)] == ["2025-01-01"]
    assert [a["start"][:10] for a in history.year_activities(2024)] == ["2024-12-31"]


# ---- what the popup gets

def test_stored_years_are_merged_into_the_calendar_and_summarised(api):
    strava.sync_history("tok", 2026, 5, budget=99)
    out = strava.fetch(Tokens(), now=NOW, history_years=0)               # 0: no downloading, but stored years still show
    assert "2023-07-04" in out["days"] and "2025-06-02" in out["days"] and "2026-01-02" in out["days"]
    assert list(out["days"]) == sorted(out["days"])
    assert out["days"]["2025-06-02"]["elevation_m"] == 50
    assert out["history"] == {"years": [2025, 2024, 2023], "from": "2023-07-04", "complete": True}


def test_a_refresh_downloads_older_years_only_when_there_is_room_for_extras(api):
    strava.fetch(Tokens(), now=NOW, history_years=5, optional=False)
    assert history.index()["years"] == {}
    strava.fetch(Tokens(), now=NOW, history_years=5, optional=True)
    assert sorted(history.index()["years"]) == ["2024", "2025"]          # a couple of years per refresh


def test_a_failed_history_download_does_not_break_the_refresh(monkeypatch):
    real = FakeStrava([ride("2026-01-02")])
    def flaky(url, token=None, **kw):
        if "before=" in url:
            raise HttpError(429, "slow down")
        return real(url, token)
    monkeypatch.setattr(strava, "request_json", flaky)
    out = strava.fetch(Tokens(), now=NOW, history_years=5)
    assert "2026-01-02" in out["days"] and out["history"]["years"] == []


# ---- `lapbar history`

def run(capsys, *argv):
    try:
        cli.main(["history", *argv])
        code = 0
    except SystemExit as e:
        code = e.code or 0
    return code, json.loads(capsys.readouterr().out)


def test_history_command_prints_a_stored_year_without_any_request(api, capsys):
    strava.sync_history("tok", 2026, 5, budget=99)
    api.calls.clear()
    code, out = run(capsys, "2025")
    assert code == 0 and out["year"] == 2025 and len(out["activities"]) == 3 and api.calls == []
    code, out = run(capsys)
    assert out["years"] == [2025, 2024, 2023]


def test_history_sync_downloads_everything_and_respects_the_budget(api, monkeypatch, capsys):
    monkeypatch.setattr(ratelimit, "check", lambda kind, snap=None: None)
    monkeypatch.setattr(auth, "default_token_source", lambda: Tokens())
    code, out = run(capsys, "--sync")
    assert code == 0 and out["years"] == [2025, 2024, 2023] and out["complete"] is True
    def refuse(kind, snap=None):
        raise ratelimit.BudgetExhausted("used up", {})
    monkeypatch.setattr(ratelimit, "check", refuse)
    code, out = run(capsys, "--sync", "--refresh")
    assert code == 1 and out["error"] == "budget"


def test_an_older_activity_can_be_found_by_id(api):
    strava.sync_history("tok", 2026, 5, budget=99)
    target = history.year_activities(2024)[0]
    assert history.find(target["id"])["id"] == target["id"] and history.find(1) is None


def test_stored_files_are_private(api):
    strava.sync_history("tok", 2026, 1, budget=1)
    assert oct((history._dir() / "2025.json").stat().st_mode & 0o777) == "0o600"
    assert oct(history._dir().stat().st_mode & 0o777) == "0o700"
