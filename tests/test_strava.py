from datetime import datetime

from lapbar.http import HttpError
from lapbar import kudos, streams
from lapbar.providers import strava


class FakeTokens:
    def access_token(self):
        return "tok"


def act(date, dist, elev, name="x", **kw):
    return {"id": hash((date, dist, name)) % 10**9, "start_date_local": f"{date}T07:00:00Z", "distance": dist, "total_elevation_gain": elev,
            "name": name, "sport_type": "Run", "moving_time": 1800, **kw}


def test_summary_week_year_latest(monkeypatch):
    # Saturday 2026-09-19; the week starts Monday 2026-09-14.
    acts = [
        act("2026-01-02", 10000, 100),
        act("2025-12-31", 99999, 999),          # previous year: excluded
        act("2026-09-13", 5000, 50),            # Sunday before the week: year only
        act("2026-09-14", 8000, 80),
        act("2026-09-18", 12000, 120, name="latest", average_watts=210.0),
    ]
    monkeypatch.setattr(strava, "request_json", lambda url, token=None, **kw: acts)
    s = strava.fetch(FakeTokens(), now=datetime(2026, 9, 19, 12))

    assert s["latest"]["name"] == "latest"
    assert s["latest"]["distance_km"] == 12.0
    assert s["latest"]["avg_watts"] == 210.0
    assert {k: s["week"][k] for k in ("distance_km", "elevation_m", "count")} == {
        "distance_km": 20.0, "elevation_m": 200, "count": 2}
    assert {k: s["year"][k] for k in ("distance_km", "elevation_m", "count")} == {
        "distance_km": 35.0, "elevation_m": 350, "count": 4}


def test_no_activities(monkeypatch):
    monkeypatch.setattr(strava, "request_json", lambda url, token=None, **kw: [])
    s = strava.fetch(FakeTokens(), now=datetime(2026, 9, 19))
    assert s["latest"] is None
    assert s["year"]["count"] == 0


def test_pagination(monkeypatch):
    pages = {1: [act("2026-02-01", 1000, 1)] * strava.PAGE_SIZE, 2: [act("2026-02-02", 1000, 1)] * 3}
    monkeypatch.setattr(strava, "request_json",
                        lambda url, token=None, **kw: {} if "/streams" in url
                        else pages[int(url.split("&page=")[1])])
    s = strava.fetch(FakeTokens(), now=datetime(2026, 9, 19))
    assert s["year"]["count"] == strava.PAGE_SIZE + 3


def test_polyline_decode_matches_googles_documented_example():
    assert strava.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [
        [38.5, -120.2], [40.7, -120.95], [43.252, -126.453]]


def test_latest_has_social_fields_route_and_profile(monkeypatch):
    ride = act("2026-09-18", 12000, 120, kudos_count=7, comment_count=2, pr_count=3,
               achievement_count=5, map={"summary_polyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"})
    raw = {"altitude": {"data": [1.0, 2.0, 3.0]}, "distance": {"data": [0.0, 5000.0, 10000.0]}}
    monkeypatch.setattr(strava, "request_json", lambda url, token=None, **kw: [ride])
    monkeypatch.setattr(streams, "request_json", lambda url, token=None, **kw: raw)
    monkeypatch.setattr(kudos, "request_json", lambda url, token=None, **kw: [])
    latest = strava.fetch(FakeTokens(), now=datetime(2026, 9, 19))["latest"]
    assert (latest["kudos"], latest["comments"], latest["prs"], latest["achievements"]) == (7, 2, 3, 5)
    assert len(latest["route"]) == 3
    assert latest["elevation_profile"][0] == 1.0 and latest["elevation_profile"][-1] == 3.0


def test_profile_reused_when_latest_ride_unchanged(monkeypatch):
    ride = act("2026-09-18", 12000, 120)
    urls = []

    def fake(url, token=None, **kw):
        urls.append(url)
        return [ride]

    monkeypatch.setattr(strava, "request_json", fake)
    previous = {"latest": {"id": ride["id"], "elevation_profile": [1, 2, 3]}}
    latest = strava.fetch(FakeTokens(), now=datetime(2026, 9, 19), previous=previous)["latest"]
    assert latest["elevation_profile"] == [1, 2, 3]
    assert not any("/streams" in u for u in urls)


def test_missing_streams_404_gives_empty_profile(monkeypatch):
    ride = act("2026-09-18", 12000, 120)

    monkeypatch.setattr(strava, "request_json", lambda url, token=None, **kw: [ride])
    monkeypatch.setattr(streams, "request_json",
                        lambda url, token=None, **kw: (_ for _ in ()).throw(HttpError(404, "not found")))
    assert strava.fetch(FakeTokens(), now=datetime(2026, 9, 19))["latest"]["elevation_profile"] == []


def test_route_is_normalised_shape_without_coordinates():
    # A 2:1 wide track (in ground distance) at the equator: 0.02 deg lng x 0.01 deg lat.
    pts = [[0.0, 0.0], [0.0, 0.02], [0.01, 0.02], [0.01, 0.0]]
    route = strava.normalize_route(pts)
    assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in route)
    xs, ys = [p[0] for p in route], [p[1] for p in route]
    assert (min(xs), max(xs)) == (0.0, 1.0)          # long axis fills the square
    assert round(max(ys) - min(ys), 3) == 0.5        # aspect ratio 2:1 preserved
    assert min(ys) == 0.25                           # and centred vertically
    assert route[0][1] > route[3][1] or route[0][1] < route[3][1]  # north is up: y grows southward


def test_route_north_is_up():
    route = strava.normalize_route([[46.0, 14.0], [46.1, 14.0]])  # second point is further north
    assert route[1][1] < route[0][1]


def test_degenerate_routes_are_empty():
    assert strava.normalize_route([]) == []
    assert strava.normalize_route([[46.0, 14.0]]) == []
    assert strava.normalize_route([[46.0, 14.0], [46.0, 14.0]]) == []


def test_latest_route_carries_no_raw_coordinates_and_has_strava_url(monkeypatch):
    ride = act("2026-09-18", 12000, 120, map={"summary_polyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"})
    monkeypatch.setattr(strava, "request_json",
                        lambda url, token=None, **kw: {} if "/streams" in url else [ride])
    latest = strava.fetch(FakeTokens(), now=datetime(2026, 9, 19))["latest"]
    assert all(0 <= v <= 1 for point in latest["route"] for v in point)
    assert latest["url"] == f"https://www.strava.com/activities/{ride['id']}"


def test_days_group_activities_by_local_date(monkeypatch):
    acts = [
        act("2026-09-18", 12000, 120, moving_time=3600, sport_type="Ride"),
        act("2026-09-18", 3000, 10, moving_time=1200, sport_type="Run"),
        act("2026-09-18", 4000, 10, moving_time=900, sport_type="Ride"),
        act("2026-09-14", 8000, 80, moving_time=2400, sport_type="Walk"),
    ]
    monkeypatch.setattr(strava, "request_json",
                        lambda url, token=None, **kw: {} if "/streams" in url else acts)
    days = strava.fetch(FakeTokens(), now=datetime(2026, 9, 19))["days"]
    assert list(days) == ["2026-09-14", "2026-09-18"]  # sorted, only active days
    d = days["2026-09-18"]
    assert d["count"] == 3 and d["distance_km"] == 19.0 and d["moving_time_s"] == 5700
    assert d["families"] == ["ride", "run"]            # unique, in order of appearance
    assert days["2026-09-14"]["families"] == ["walk"]


def test_late_evening_activity_counts_on_its_local_day():
    # start_date_local is wall-clock time, so 23:30 local stays on that date even if UTC is the next day.
    a = act("2026-03-01", 5000, 0)
    a["start_date_local"] = "2026-03-01T23:30:00Z"
    assert list(strava._days([a])) == ["2026-03-01"]


def test_no_activities_gives_no_days(monkeypatch):
    monkeypatch.setattr(strava, "request_json", lambda url, token=None, **kw: [])
    assert strava.fetch(FakeTokens(), now=datetime(2026, 9, 19))["days"] == {}


def test_activities_list_is_newest_first_compact_and_complete(monkeypatch):
    poly = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    acts = [act("2026-01-05", 5000, 10, name="old", map={"summary_polyline": poly}),
            act("2026-09-18", 9000, 90, name="new", map={"summary_polyline": poly}, kudos_count=4),
            act("2026-06-01", 7000, 70, name="mid")]
    monkeypatch.setattr(strava, "request_json",
                        lambda url, token=None, **kw: {} if "/streams" in url else acts)
    monkeypatch.setattr(kudos, "request_json", lambda url, token=None, **kw: [])
    out = strava.fetch(FakeTokens(), now=datetime(2026, 9, 19))
    listed = out["activities"]
    assert [a["name"] for a in listed] == ["new", "mid", "old"]
    assert len(listed) == out["year"]["count"]
    assert listed[0]["kudos"] == 4 and listed[0]["url"].endswith(f"/activities/{listed[0]['id']}")
    assert "elevation_profile" not in listed[0]           # only `latest` pays for the extra request
    assert None not in listed[0].values()                  # nulls stripped to keep the cache small
    assert all(len(a["route"]) <= strava.LIST_ROUTE_POINTS for a in listed)
    assert out["latest"]["id"] == listed[0]["id"]


def test_every_active_day_has_matching_listed_activities(monkeypatch):
    acts = [act("2026-09-18", 1000, 1), act("2026-09-18", 2000, 2), act("2026-09-14", 3000, 3)]
    monkeypatch.setattr(strava, "request_json",
                        lambda url, token=None, **kw: {} if "/streams" in url else acts)
    out = strava.fetch(FakeTokens(), now=datetime(2026, 9, 19))
    for day, info in out["days"].items():
        assert sum(1 for a in out["activities"] if a["start"][:10] == day) == info["count"]


def test_events_flow_through_fetch(monkeypatch):
    ride = act("2026-09-18", 12000, 120, kudos_count=3)
    monkeypatch.setattr(strava, "request_json",
                        lambda url, token=None, **kw: {} if "/streams" in url else [ride])
    monkeypatch.setattr(kudos, "request_json",
                        lambda url, token=None, **kw: [{"firstname": "Ana", "lastname": "K."},
                                                       {"firstname": "Bo", "lastname": "M."},
                                                       {"firstname": "Cy", "lastname": "D."}])
    prev = {"activities": [{"id": ride["id"], "kudos": 2}], "kudoers": {str(ride["id"]): ["Ana K.", "Bo M."]}}
    out = strava.fetch(FakeTokens(), now=datetime(2026, 9, 19), previous=prev)
    assert out["kudos_events"][0]["from"] == ["Cy D."]
    assert out["kudos_seen"] == {str(ride["id"]): 3}


def test_load_compares_with_last_week_up_to_the_same_moment(monkeypatch):
    # Now: Wed 2026-09-16 12:00. This week began Mon 14th; last week began Mon 7th.
    acts = [
        act("2026-09-14", 10000, 0, moving_time=3600, suffer_score=40),   # this week
        act("2026-09-15", 5000, 0, moving_time=1800, suffer_score=20),    # this week
        act("2026-09-07", 8000, 0, moving_time=3000, suffer_score=30),    # last Mon: before same point
        act("2026-09-09", 6000, 0, moving_time=2400, suffer_score=25),    # last Wed 07:00: before 12:00
        act("2026-09-11", 20000, 0, moving_time=7200, suffer_score=90),   # last Fri: after same point
    ]
    monkeypatch.setattr(strava, "request_json",
                        lambda url, token=None, **kw: {} if "/streams" in url else acts)
    load = strava.fetch(FakeTokens(), now=datetime(2026, 9, 16, 12))["load"]
    assert load["this_week"] == {"moving_time_s": 5400, "distance_km": 15.0, "effort": 60}
    assert load["last_week"]["moving_time_s"] == 12600
    assert load["last_week_same_point"] == {"moving_time_s": 5400, "distance_km": 14.0, "effort": 55}
    assert load["days_left"] == 5 and load["has_effort"] is True


def test_load_spans_the_new_year(monkeypatch):
    # Thu 2026-01-01 noon. This week began Mon 29 Dec 2025; last week was 22-28 Dec 2025.
    acts = [
        act("2026-01-01", 4000, 0, moving_time=1000),   # this week, new year
        act("2025-12-30", 9000, 0, moving_time=3000),   # this week, but last year
        act("2025-12-24", 6000, 0, moving_time=2400),   # last week
    ]
    monkeypatch.setattr(strava, "request_json",
                        lambda url, token=None, **kw: {} if "/streams" in url else acts)
    out = strava.fetch(FakeTokens(), now=datetime(2026, 1, 1, 12))
    assert out["load"]["this_week"]["moving_time_s"] == 4000      # includes 30 Dec, though it is not in "year"
    assert out["load"]["last_week"]["moving_time_s"] == 2400
    assert out["year"]["count"] == 1                              # calendar-year totals stay in 2026
    assert out["week"]["count"] == 2


def test_today_and_month_totals_include_the_climb(monkeypatch):
    # Saturday 2026-09-19: "today" is the 19th, the month starts on the 1st.
    acts = [
        act("2026-08-31", 7000, 70),            # last month: year only
        act("2026-09-02", 10000, 100),          # month only
        act("2026-09-15", 8000, 80),            # month and week
        act("2026-09-19", 5000, 45),            # today, week and month
        act("2026-09-19", 3000, 30, sport_type="Walk"),
    ]
    monkeypatch.setattr(strava, "request_json", lambda url, token=None, **kw: acts)
    s = strava.fetch(FakeTokens(), now=datetime(2026, 9, 19, 12))
    pick = lambda t: {k: s[t][k] for k in ("distance_km", "elevation_m", "count")}    # noqa: E731
    assert pick("today") == {"distance_km": 8.0, "elevation_m": 75, "count": 2}
    assert pick("week") == {"distance_km": 16.0, "elevation_m": 155, "count": 3}
    assert pick("month") == {"distance_km": 26.0, "elevation_m": 255, "count": 4}
    assert pick("year") == {"distance_km": 33.0, "elevation_m": 325, "count": 5}
    assert s["today"]["by_sport"]["walk"]["elevation_m"] == 30                      # per sport as well


def test_days_carry_their_climb():
    days = strava._days([act("2026-09-18", 1000, 120.4), act("2026-09-18", 1000, 30.4), act("2026-09-14", 1000, 0)])
    assert days["2026-09-18"]["elevation_m"] == 151 and days["2026-09-14"]["elevation_m"] == 0
