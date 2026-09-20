"""Sport-aware output, using payloads shaped like Strava's for sports the author doesn't do."""
from datetime import datetime

import pytest

from lapbar import sports
from lapbar.providers import strava


class FakeTokens:
    def access_token(self):
        return "tok"


def make(sport, date="2026-09-18", **kw):
    base = {"id": abs(hash((sport, date))) % 10**9, "name": sport, "sport_type": sport, "type": sport,
            "start_date_local": f"{date}T07:00:00Z", "distance": 0, "total_elevation_gain": 0,
            "moving_time": 0, "elapsed_time": 0, "map": {"summary_polyline": ""}}
    return {**base, **kw}


def latest_of(monkeypatch, activity):
    monkeypatch.setattr(strava, "request_json",
                        lambda url, token=None, **kw: {} if "/streams" in url else [activity])
    return strava.fetch(FakeTokens(), now=datetime(2026, 9, 19))["latest"]


def test_run_has_pace_and_steps_per_minute(monkeypatch):
    # 10 km in 50:00 -> 3.333 m/s -> 5:00 /km. Strava reports cadence per leg (85) = 170 spm.
    run = make("Run", distance=10000, moving_time=3000, elapsed_time=3100, average_speed=3.3333,
               max_speed=5.0, average_cadence=85.0, average_heartrate=152.4, max_heartrate=178.0,
               total_elevation_gain=64.0, suffer_score=71)
    l = latest_of(monkeypatch, run)
    assert l["family"] == "run"
    assert (l["pace_s"], l["pace_unit"]) == (300, "km")
    assert (l["avg_cadence"], l["cadence_unit"]) == (170, "spm")
    assert (l["avg_heartrate"], l["max_heartrate"]) == (152, 178)
    assert l["avg_speed_kmh"] == 12.0
    assert l["avg_watts"] is None  # no power meter: null, not 0


def test_ride_has_speed_watts_and_rpm_but_no_pace(monkeypatch):
    ride = make("GravelRide", distance=40000, moving_time=5000, average_speed=8.0,
                average_cadence=88.0, average_watts=190.0, weighted_average_watts=205,
                max_watts=640, kilojoules=950.0, average_temp=21)
    l = latest_of(monkeypatch, ride)
    assert l["family"] == "ride"
    assert l["pace_s"] is None and l["pace_unit"] is None
    assert l["avg_speed_kmh"] == 28.8
    assert (l["avg_cadence"], l["cadence_unit"]) == (88, "rpm")
    assert (l["avg_watts"], l["max_watts"], l["weighted_watts"]) == (190.0, 640, 205)
    assert l["kilojoules"] == 950.0 and l["avg_temp_c"] == 21


def test_swim_pace_is_per_100m(monkeypatch):
    # 1500 m in 30:00 -> 0.8333 m/s -> 2:00 /100 m
    swim = make("Swim", distance=1500, moving_time=1800, average_speed=0.8333, trainer=True)
    l = latest_of(monkeypatch, swim)
    assert l["family"] == "swim"
    assert (l["pace_s"], l["pace_unit"]) == (120, "100m")
    assert l["indoor"] is True
    assert l["route"] == [] and l["elevation_profile"] == []


def test_rowing_pace_is_per_500m():
    assert sports.pace("Rowing", 2.5) == (200, "500m")
    assert sports.pace("Kayaking", 2.5) == (None, None)


def test_hike_is_walk_family_with_pace(monkeypatch):
    hike = make("Hike", distance=12000, moving_time=12000, average_speed=1.0, total_elevation_gain=900,
                average_cadence=45.0)
    l = latest_of(monkeypatch, hike)
    assert l["family"] == "walk"
    assert (l["pace_s"], l["pace_unit"]) == (1000, "km")
    assert l["avg_cadence"] == 90 and l["cadence_unit"] == "spm"


def test_gym_session_without_distance_or_speed(monkeypatch):
    lift = make("WeightTraining", moving_time=3600, elapsed_time=4200, average_heartrate=118.0)
    l = latest_of(monkeypatch, lift)
    assert l["family"] == "gym"
    assert l["distance_km"] == 0.0 and l["pace_s"] is None and l["avg_speed_kmh"] is None
    assert l["moving_time_s"] == 3600 and l["avg_heartrate"] == 118


def test_unknown_future_sport_type_does_not_break(monkeypatch):
    l = latest_of(monkeypatch, make("Padel", distance=0, moving_time=5400))
    assert l["sport"] == "Padel" and l["family"] == "other"


def test_missing_optional_fields_are_null_not_errors(monkeypatch):
    l = latest_of(monkeypatch, make("Run", distance=5000, moving_time=1500))
    for key in ("avg_heartrate", "avg_cadence", "avg_watts", "pace_s", "avg_speed_kmh", "device"):
        assert l[key] is None
    assert l["athletes"] == 1 and l["kudos"] == 0


def test_totals_are_broken_down_per_sport_family(monkeypatch):
    acts = [
        make("Run", "2026-09-16", distance=10000, moving_time=3000),
        make("TrailRun", "2026-09-17", distance=15000, moving_time=6000, total_elevation_gain=800),
        make("Ride", "2026-09-18", distance=60000, moving_time=7200),
        make("Swim", "2026-09-15", distance=1000, moving_time=1200),
        make("WeightTraining", "2026-09-14", moving_time=3000),
    ]
    monkeypatch.setattr(strava, "request_json",
                        lambda url, token=None, **kw: {} if "/streams" in url else acts)
    year = strava.fetch(FakeTokens(), now=datetime(2026, 9, 19))["year"]
    assert year["count"] == 5 and year["distance_km"] == 86.0
    by = year["by_sport"]
    assert by["run"] == {"distance_km": 25.0, "elevation_m": 800, "moving_time_s": 9000, "count": 2}
    assert by["ride"]["distance_km"] == 60.0 and by["swim"]["distance_km"] == 1.0
    assert by["gym"]["count"] == 1
    assert list(by) == ["run", "ride", "gym", "swim"]  # most time first


@pytest.mark.parametrize("sport,fam", [("MountainBikeRide", "ride"), ("VirtualRun", "run"),
                                       ("NordicSki", "winter"), ("Yoga", "gym"), ("Velomobile", "ride")])
def test_family_mapping(sport, fam):
    assert sports.family(sport) == fam
