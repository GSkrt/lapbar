import json
import os
import stat

import pytest

from lapbar import config, streams
from lapbar.http import HttpError

RIDE = {"id": 1, "name": "Evening Ride", "sport": "Ride", "family": "ride", "start": "2026-09-18T18:56:50Z",
        "distance_km": 10.0}
RUN = {**RIDE, "id": 2, "sport": "Run", "family": "run", "name": "Morning Run"}


def raw_ride(seconds=3600):
    """One sample per second, 10 m/s -> 36 km/h, climbing 0.01 m/s, steady 150 bpm."""
    t = list(range(seconds))
    return {
        "time": {"data": t},
        "distance": {"data": [10.0 * i for i in t]},
        "altitude": {"data": [100 + 0.01 * i for i in t]},
        "velocity_smooth": {"data": [10.0] * seconds},
        "heartrate": {"data": [150] * seconds},
        "watts": {"data": [200] * seconds},
        "cadence": {"data": [90] * seconds},
        "temp": {"data": [21] * seconds},
        "grade_smooth": {"data": [1.5] * seconds},
    }


def keys(data):
    return [s["key"] for s in data["series"]]


def test_x_axis_is_kilometres_on_an_even_grid():
    d = streams.build(RIDE, raw_ride())
    assert d["x"]["unit"] == "km" and d["x"]["key"] == "distance"
    xs = d["x"]["values"]
    assert len(xs) == streams.MAX_POINTS == d["points"]        # 3600 samples reduced to the cap
    assert xs == sorted(xs) and 0 <= xs[0] < 0.03 and 35.9 < xs[-1] < 36.0
    steps = {round(b - a, 3) for a, b in zip(xs, xs[1:])}
    assert len(steps) == 1                                     # evenly spaced, so hover maps to an index
    assert d["distance_km"] == 35.99                           # last sample: 3599 s at 10 m/s


def test_every_available_series_is_present_with_units_and_all_align_with_x():
    d = streams.build(RIDE, raw_ride())
    assert keys(d) == ["altitude", "speed", "heartrate", "watts", "cadence", "temp", "grade"]
    units = {s["key"]: s["unit"] for s in d["series"]}
    assert units == {"altitude": "m", "speed": "km/h", "heartrate": "bpm", "watts": "W",
                     "cadence": "rpm", "temp": "°C", "grade": "%"}
    assert all(len(s["values"]) == d["points"] for s in d["series"])
    speed = next(s for s in d["series"] if s["key"] == "speed")
    assert set(speed["values"]) == {36.0}                       # 10 m/s in km/h


def test_series_the_activity_lacks_are_left_out():
    raw = {k: v for k, v in raw_ride(600).items() if k in ("time", "distance", "altitude")}
    assert keys(streams.build(RIDE, raw)) == ["altitude"]


def test_runners_get_pace_not_speed_and_steps_per_minute():
    # 10 m/s would be 1:40 /km; use a runner's 3.333 m/s = 5:00 /km
    raw = raw_ride(1200)
    raw["velocity_smooth"] = {"data": [3.3333] * 1200}
    raw["cadence"] = {"data": [85] * 1200}
    d = streams.build(RUN, raw)
    assert "speed" not in keys(d) and "pace" in keys(d)
    pace = next(s for s in d["series"] if s["key"] == "pace")
    assert pace["unit"] == "min/km" and pace["format"] == "pace" and set(pace["values"]) == {300}
    cad = next(s for s in d["series"] if s["key"] == "cadence")
    assert cad["unit"] == "spm" and set(cad["values"]) == {170}  # per-leg 85 doubled


def test_stops_are_gaps_in_pace_not_absurd_values():
    raw = raw_ride(300)
    raw["velocity_smooth"] = {"data": [0.0] * 150 + [3.3333] * 150}     # stood still, then ran 5:00/km
    pace = next(s for s in streams.build(RUN, raw)["series"] if s["key"] == "pace")["values"]
    assert pace[0] is None and pace[-1] == 300


def test_a_series_with_no_data_at_all_is_left_out_rather_than_drawn_empty():
    raw = raw_ride(300)
    raw["velocity_smooth"] = {"data": [0.0] * 300}
    assert "pace" not in keys(streams.build(RUN, raw))


def test_fast_stretches_are_interpolated_not_left_as_dots():
    # 20 samples, but the last ones jump far (a car ride): most distance cells hold no sample at all.
    distance = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 100]   # metres, then a leap
    raw = {"time": {"data": list(range(20))}, "distance": {"data": [float(d) for d in distance]},
           "altitude": {"data": [float(i) for i in range(20)]}}
    alt = next(s for s in streams.build(RIDE, raw)["series"] if s["key"] == "altitude")["values"]
    assert None not in alt                                   # the empty cells were bridged
    assert alt == sorted(alt) and alt[-1] > alt[len(alt) // 2]


def test_sensor_dropouts_are_still_gaps_after_interpolation():
    raw = raw_ride(200)
    raw["heartrate"] = {"data": [150] * 60 + [None] * 80 + [150] * 60}
    hr = next(s for s in streams.build(RIDE, raw)["series"] if s["key"] == "heartrate")["values"]
    assert None in hr[len(hr) // 2 - 3:len(hr) // 2 + 3]


def test_pace_slower_than_the_cap_is_a_gap_so_stops_do_not_stretch_the_axis():
    raw = raw_ride(300)
    raw["velocity_smooth"] = {"data": [0.4] * 150 + [1.5] * 150}      # 0.4 m/s = 41:40 /km, then 11:07 /km
    pace = next(s for s in streams.build(RUN, raw)["series"] if s["key"] == "pace")["values"]
    assert pace[0] is None and pace[-1] == 667
    assert max(v for v in pace if v is not None) <= streams.SLOWEST_PACE["km"]


def test_missing_samples_stay_gaps_and_are_not_averaged_in_as_zero():
    raw = raw_ride(100)
    raw["heartrate"] = {"data": [None] * 50 + [150] * 50}
    hr = next(s for s in streams.build(RIDE, raw)["series"] if s["key"] == "heartrate")["values"]
    assert hr[0] is None and hr[-1] == 150 and 0 not in hr


def test_falls_back_to_time_when_there_is_no_distance():
    raw = raw_ride(600)
    del raw["distance"]
    d = streams.build(RIDE, raw)
    assert d["x"]["unit"] == "min" and d["x"]["key"] == "time"
    assert 9.9 < d["x"]["values"][-1] < 10.0


def test_nothing_usable_is_recorded_as_empty():
    assert streams.build(RIDE, {}) == {"empty": True}
    assert streams.build(RIDE, {"time": {"data": [0, 1]}}) == {"empty": True}   # x but no series


def test_downloaded_once_then_served_from_disk(monkeypatch):
    calls = []
    monkeypatch.setattr(streams, "request_json", lambda url, token=None, **kw: calls.append(url) or raw_ride(600))
    first = streams.get("tok", RIDE)
    second = streams.get("tok", RIDE)
    assert len(calls) == 1 and first == second
    assert streams.path_for(1).exists()


def test_refresh_downloads_again(monkeypatch):
    calls = []
    monkeypatch.setattr(streams, "request_json", lambda url, token=None, **kw: calls.append(url) or raw_ride(600))
    streams.get("tok", RIDE)
    streams.get("tok", RIDE, refresh=True)
    assert len(calls) == 2


def test_an_activity_with_no_streams_is_remembered_not_retried(monkeypatch):
    calls = []

    def none(url, token=None, **kw):
        calls.append(url)
        raise HttpError(404, "no streams")

    monkeypatch.setattr(streams, "request_json", none)
    assert streams.get("tok", RIDE) is None
    assert streams.get("tok", RIDE) is None
    assert len(calls) == 1


def test_other_errors_are_not_remembered_and_propagate(monkeypatch):
    def limited(url, token=None, **kw):
        raise HttpError(429, "slow down")

    monkeypatch.setattr(streams, "request_json", limited)
    with pytest.raises(HttpError):
        streams.get("tok", RIDE)
    assert not streams.path_for(1).exists()          # so it is tried again later


def test_stored_files_are_private():
    streams._store(7, {"x": 1})
    assert stat.S_IMODE(os.stat(streams.path_for(7)).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(streams.path_for(7).parent).st_mode) == 0o700


def test_backfill_downloads_newest_first_up_to_the_limit_and_skips_what_it_has(monkeypatch):
    got = []
    monkeypatch.setattr(streams, "request_json",
                        lambda url, token=None, **kw: got.append(int(url.split("/activities/")[1].split("/")[0])) or raw_ride(300))
    acts = [{**RIDE, "id": i} for i in (10, 9, 8, 7, 6)]
    streams.get("tok", acts[1])                       # 9 is already on disk
    got.clear()
    assert streams.backfill("tok", acts, limit=2) == 2
    assert got == [10, 8]                             # newest first, skipped 9
    assert streams.backfill("tok", acts, limit=10) == 2   # 7 and 6 remain
    assert streams.backfill("tok", acts, limit=10) == 0   # nothing left


def test_backfill_stops_at_the_first_failure_so_it_never_hammers_a_rate_limit(monkeypatch):
    calls = []

    def limited(url, token=None, **kw):
        calls.append(url)
        raise HttpError(429, "slow down")

    monkeypatch.setattr(streams, "request_json", limited)
    assert streams.backfill("tok", [{**RIDE, "id": i} for i in (3, 2, 1)], limit=5) == 0
    assert len(calls) == 1


def test_elevation_profile_for_the_popup_is_derived_from_the_stored_series():
    d = streams.build(RIDE, raw_ride(3600))
    prof = streams.elevation_profile(d)
    assert len(prof) == streams.PROFILE_POINTS
    assert prof == sorted(prof) and prof[0] < 101 and prof[-1] > 135
    assert streams.elevation_profile(None) == [] and streams.elevation_profile({"series": []}) == []


def test_json_written_for_the_chart_window_is_plain_and_complete():
    streams.get("tok", RIDE) if False else streams._store(1, streams.build(RIDE, raw_ride(300)))
    doc = json.loads(streams.path_for(1).read_text())
    assert {"id", "name", "x", "series", "points", "distance_km"} <= set(doc)


def test_files_from_an_older_format_are_downloaded_again_once(monkeypatch):
    streams._store(1, {"id": 1, "name": "old", "x": {"values": [0, 1]}, "series": [], "points": 2})   # no version
    calls = []
    monkeypatch.setattr(streams, "request_json", lambda url, token=None, **kw: calls.append(url) or raw_ride(300))
    data = streams.get("tok", RIDE)
    assert len(calls) == 1 and data["v"] == streams.DATA_VERSION
    streams.get("tok", RIDE)
    assert len(calls) == 1                                    # current version: served from disk again


def test_empty_markers_stay_valid_across_versions(monkeypatch):
    streams._store(1, {"empty": True})
    monkeypatch.setattr(streams, "request_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("retried")))
    assert streams.get("tok", RIDE) is None


def test_backfill_redoes_stale_files_too(monkeypatch):
    streams._store(5, {"id": 5, "series": [], "x": {"values": []}, "points": 0})
    got = []
    monkeypatch.setattr(streams, "request_json", lambda url, token=None, **kw: got.append(url) or raw_ride(300))
    assert streams.backfill("tok", [{**RIDE, "id": 5}], limit=3) == 1 and len(got) == 1


# ---- the window's detail file: the actual samples, never averages

def noisy_ride(seconds=4000):
    """A distinctive value on every second, so any averaging shows."""
    raw = raw_ride(seconds)
    raw["heartrate"] = {"data": [100 + (i * 7) % 61 for i in range(seconds)]}
    raw["watts"] = {"data": [(i * 13) % 400 for i in range(seconds)]}
    return raw


def test_the_detail_build_keeps_every_recorded_sample_unaveraged():
    raw = noisy_ride()
    d = streams.build(RIDE, raw, full=True)
    assert d["points"] == 4000 == len(d["x"]["values"])
    hr = next(s for s in d["series"] if s["key"] == "heartrate")["values"]
    assert hr == raw["heartrate"]["data"]                                        # sample for sample
    watts = next(s for s in d["series"] if s["key"] == "watts")["values"]
    assert watts == raw["watts"]["data"] and max(watts) == 399                   # the peaks are still there
    assert d["x"]["values"][:3] == [0.0, 0.01, 0.02] and d["distance_km"] == 39.99


def test_the_overview_is_unchanged_and_small():
    d = streams.build(RIDE, noisy_ride())
    assert d["points"] == streams.MAX_POINTS == 1500


def test_an_extremely_long_ride_keeps_every_kth_real_sample_and_the_finish(monkeypatch):
    monkeypatch.setattr(streams, "FULL_MAX_POINTS", 1000)
    raw = noisy_ride(3500)
    d = streams.build(RIDE, raw, full=True)
    hr = next(s for s in d["series"] if s["key"] == "heartrate")["values"]
    assert len(hr) <= 1001 and hr[0] == raw["heartrate"]["data"][0] and hr[-1] == raw["heartrate"]["data"][-1]
    assert set(hr) <= set(raw["heartrate"]["data"])                              # only values that were recorded
    assert d["x"]["values"][-1] == round(3499 * 10 / 1000, 4)


def test_detail_is_made_from_the_archive_without_a_request_and_only_the_latest_are_kept(monkeypatch):
    from lapbar import raw as archive
    monkeypatch.setattr(streams, "request_json", lambda *a, **k: pytest.fail("no request expected"))
    for i in range(1, 8):
        act = {**RIDE, "id": i}
        archive.store(act, noisy_ride(300))
        path = streams.detail(act)
        assert path == streams.detail_path(i) and json.loads(path.read_text())["points"] == 300
        os.utime(path, (i, i))                                                    # older ids look older
    names = sorted(p.name for p in streams._dir().glob("*.full.json"))
    assert len(names) == streams.DETAIL_KEEP and "7.full.json" in names and "1.full.json" not in names
    assert streams.detail({**RIDE, "id": 99}) is None                             # not archived: nothing to make it from
