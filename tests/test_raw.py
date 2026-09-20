"""The raw archive: the complete streams (GPS included) are kept once, and everything else is made from them."""
import gzip
import json
from datetime import datetime

import pytest

from lapbar import auth, cli, config, history, raw, ratelimit, setup, streams
from lapbar.http import HttpError
from lapbar.providers import strava

STREAMS = {
    "time": {"data": [0, 1, 2, 3]},
    "distance": {"data": [0.0, 10.0, 20.0, 30.0]},
    "latlng": {"data": [[46.05, 14.5], [46.0501, 14.5002], [46.0502, 14.5004], [46.0503, 14.5006]]},
    "altitude": {"data": [300.0, 301.0, 302.0, 303.0]},
    "heartrate": {"data": [100, 110, 120, 130]},
    "watts": {"data": [150, 160, 170, 180]},
    "moving": {"data": [True, True, True, True]},
}


def act(id_=1, start="2025-06-01T07:00:00Z", **extra):
    return {"id": id_, "start": start, "sport": "Ride", "family": "ride", "name": "R", "distance_km": 0.03, **extra}


class Api:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.urls = answer, error, []

    def __call__(self, url, token=None, **kw):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def api(monkeypatch):
    fake = Api(STREAMS)
    monkeypatch.setattr(streams, "request_json", fake)
    return fake


# ---- the archive itself

def test_the_streams_are_kept_complete_and_compressed_in_a_private_folder_per_year():
    raw.store(act(7), STREAMS)
    path = raw.path_for(act(7))
    assert path.parent.name == "2025" and path.name == "7.json.gz"
    assert json.loads(gzip.decompress(path.read_bytes()))["streams"]["latlng"]["data"][0] == [46.05, 14.5]   # GPS is kept
    assert raw.load(act(7)) == STREAMS
    assert oct(path.stat().st_mode & 0o777) == "0o600" and oct(path.parent.stat().st_mode & 0o777) == "0o700"


def test_nothing_from_strava_is_remembered_as_empty_and_not_asked_again():
    assert raw.load(act(8)) is None
    raw.store(act(8), {})
    assert raw.load(act(8)) == {} and raw.archived_ids() == set() and raw.known_ids() == {8}


def test_the_archive_lists_what_it_holds():
    raw.store(act(1), STREAMS)
    raw.store(act(2, start="2024-01-02T07:00:00Z"), STREAMS)
    raw.store(act(3), {})
    assert raw.archived_ids() == {1, 2} and raw.known_ids() == {1, 2, 3}


def test_a_damaged_file_counts_as_not_downloaded(tmp_path):
    raw.store(act(4), STREAMS)
    raw.path_for(act(4)).write_bytes(b"not gzip")
    assert raw.load(act(4)) is None


# ---- downloading through the streams module

def test_a_download_asks_for_gps_and_everything_else_and_keeps_the_full_answer(api):
    data = streams.get("tok", act())
    assert "latlng" in api.urls[0] and "heartrate" in api.urls[0] and "moving" in api.urls[0]
    assert raw.load(act()) == STREAMS
    assert data["points"] > 0 and {s["key"] for s in data["series"]} >= {"altitude", "heartrate", "watts"}


def test_charts_are_rebuilt_from_the_archive_without_a_request(api):
    streams.get("tok", act())
    streams.path_for(1).unlink()                       # the chart-ready copy is only a cache
    api.urls.clear()
    assert streams.get("tok", act())["points"] > 0
    assert api.urls == []


def test_refresh_downloads_again(api):
    streams.get("tok", act())
    streams.get("tok", act(), refresh=True)
    assert len(api.urls) == 2


def test_an_activity_without_streams_is_remembered(monkeypatch):
    fake = Api(error=HttpError(404, "no streams"))
    monkeypatch.setattr(streams, "request_json", fake)
    assert streams.get("tok", act()) is None and streams.get("tok", act()) is None
    assert len(fake.urls) == 1 and raw.load(act()) == {}


def test_other_errors_are_not_swallowed(monkeypatch):
    monkeypatch.setattr(streams, "request_json", Api(error=HttpError(429, "slow down")))
    with pytest.raises(HttpError):
        streams.get("tok", act())
    assert raw.load(act()) is None


def test_archive_downloads_only_what_is_missing(api):
    assert streams.archive("tok", act()) is True and streams.archive("tok", act()) is False
    assert len(api.urls) == 1


def test_an_activity_whose_charts_exist_but_not_the_archive_is_archived_by_backfill(api):
    streams._store(1, {"v": streams.DATA_VERSION, "id": 1, "x": {"values": [0]}, "series": [], "points": 0})   # old chart-ready copy
    assert streams.get("tok", act()) is not None and api.urls == []          # opening a chart needs no request...
    assert streams.backfill("tok", [act()], 5) == 1 and raw.load(act()) == STREAMS   # ...but the archive still gets filled in


def test_backfill_goes_in_order_skips_stored_and_stops_at_the_limit(api):
    raw.store(act(2), STREAMS)
    acts = [act(1), act(2), act(3), act(4), act(5)]
    assert streams.backfill("tok", acts, 2) == 2
    assert raw.archived_ids() == {1, 2, 3}             # 1 and 3 downloaded, 2 skipped, 4 and 5 left for later


def test_backfill_stops_quietly_when_rate_limited(monkeypatch):
    monkeypatch.setattr(streams, "request_json", Api(error=HttpError(429, "slow down")))
    assert streams.backfill("tok", [act(1), act(2)], 5) == 0


# ---- the refresh, the summary and the commands

class Tokens:
    def access_token(self):
        return "tok"


def listed_ride(id_, date):
    return {"id": id_, "start_date_local": f"{date}T07:00:00Z", "distance": 30000, "total_elevation_gain": 100,
            "name": "R", "sport_type": "Ride", "moving_time": 3600}


def test_a_refresh_archives_this_year_first_then_older_years_and_reports_progress(monkeypatch):
    history.save_year(2024, [act(20, "2024-05-01T07:00:00Z"), act(21, "2024-04-01T07:00:00Z")],
                      {"2024-05-01": {"count": 1}, "2024-04-01": {"count": 1}}, "x")
    calls = []
    def fake(url, token=None, **kw):
        if "/streams" in url:
            calls.append(int(url.split("/activities/")[1].split("/")[0]))
            return STREAMS
        return [listed_ride(10, "2026-08-01"), listed_ride(11, "2026-07-01")] if "athlete/activities" in url else {}
    monkeypatch.setattr(strava, "request_json", fake)
    monkeypatch.setattr(streams, "request_json", fake)
    out = strava.fetch(Tokens(), now=datetime(2026, 9, 19), backfill=3, optional=True)
    # the newest ride first (the popup needs it), then the quota of 3: this year's next, then the older years
    assert calls == [10, 11, 20, 21]
    assert out["archive"] == {"stored": 4, "known": 4, "total": 4} and out["archived_ids"] == [10, 11, 20, 21]


def test_no_background_downloads_without_room_for_extras(monkeypatch):
    calls = []
    def fake(url, token=None, **kw):
        calls.append(url)
        return [listed_ride(10, "2026-08-01")] if "athlete/activities" in url else STREAMS
    monkeypatch.setattr(strava, "request_json", fake)
    monkeypatch.setattr(streams, "request_json", fake)
    history.save_year(2024, [act(20, "2024-05-01T07:00:00Z")], {}, "x")
    strava.fetch(Tokens(), now=datetime(2026, 9, 19), backfill=3, optional=False)
    assert raw.archived_ids() == {10}          # the newest ride's series is always fetched (the popup needs it); nothing else


def test_archive_command_downloads_now_and_respects_the_budget(monkeypatch, capsys):
    monkeypatch.setattr(streams, "request_json", Api(STREAMS))
    monkeypatch.setattr(auth, "default_token_source", lambda: Tokens())
    monkeypatch.setattr(ratelimit, "check", lambda kind, snap=None: None)
    history.save_year(2024, [act(20, "2024-05-01T07:00:00Z"), act(21, "2024-04-01T07:00:00Z")], {}, "x")
    try:
        cli.main(["archive", "--limit", "1"])
    except SystemExit:
        pass
    assert json.loads(capsys.readouterr().out)["downloaded"] == 1 and raw.archived_ids() == {20}
    def refuse(kind, snap=None):
        raise ratelimit.BudgetExhausted("used up", {})
    monkeypatch.setattr(ratelimit, "check", refuse)
    with pytest.raises(SystemExit):
        cli.main(["archive"])
    assert json.loads(capsys.readouterr().out)["error"] == "budget"


# ---- where it lives

def test_the_archive_lives_in_the_data_folder_not_the_cache(tmp_path):
    raw.store(act(), STREAMS)
    assert config.data_dir() in raw.path_for(act()).parents and config.cache_path().parent not in raw.path_for(act()).parents


def test_the_data_folder_can_be_moved_with_an_environment_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("LAPBAR_DATA_DIR", str(tmp_path / "elsewhere"))
    raw.store(act(), STREAMS)
    assert (tmp_path / "elsewhere" / "raw" / "2025" / "1.json.gz").is_file()


def test_older_years_and_records_kept_in_the_cache_by_an_earlier_version_are_moved_over():
    old = config.cache_path().parent / "history"
    old.mkdir(parents=True)
    (old / "index.json").write_text(json.dumps({"v": 1, "years": {"2019": {"count": 1, "days": {"2019-01-01": {"count": 1}}}}, "complete": True}))
    assert history.index()["years"]["2019"]["count"] == 1
    assert not old.exists() and (config.data_dir() / "history" / "index.json").is_file()


def test_reset_keeps_the_downloaded_activities_unless_asked(monkeypatch):
    raw.store(act(), STREAMS)
    setup.forget(out=lambda *_: None)
    assert raw.load(act()) == STREAMS
    assert "downloaded activities" in setup.forget(out=lambda *_: None, everything=True)
    assert raw.load(act()) is None
