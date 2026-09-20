from datetime import date, datetime, timedelta, timezone

import pytest

from lapbar import fitness, streams
from lapbar.providers import strava

TODAY = date(2026, 9, 19)


def act(days_ago, seconds=3600, sport="Ride", **extra):
    day = TODAY - timedelta(days=days_ago)
    return {"start_date_local": f"{day.isoformat()}T08:00:00Z", "moving_time": seconds, "sport_type": sport,
            "type": sport, **extra}


# ---- load per activity

def test_power_load_is_tss_hand_checked():
    # one hour at FTP is 100 by definition; half an hour at 80% of FTP is 0.5 x 0.64 x 100
    assert fitness.load_of(act(0, 3600, weighted_average_watts=250), ftp=250) == (pytest.approx(100), "power")
    assert fitness.load_of(act(0, 1800, weighted_average_watts=200), ftp=250) == (pytest.approx(32), "power")


def test_a_wrong_ftp_cannot_produce_absurd_load():
    load, source = fitness.load_of(act(0, 3600, weighted_average_watts=900), ftp=100)
    assert source == "power" and load == pytest.approx(100 * fitness.MAX_INTENSITY ** 2)


def test_without_an_ftp_power_is_ignored_and_strava_effort_is_used():
    assert fitness.load_of(act(0, 3600, weighted_average_watts=250, suffer_score=90)) == (90.0, "effort")


def test_effort_is_used_as_is_and_only_when_present():
    assert fitness.load_of(act(0, 3600, suffer_score=42)) == (42.0, "effort")
    assert fitness.load_of(act(0, 3600, suffer_score=0))[1] == "time"          # 0 means none recorded


def test_time_fallback_uses_the_sports_typical_rate_so_every_activity_counts():
    assert fitness.load_of(act(0, 3600, "Run")) == (85.0, "time")
    assert fitness.load_of(act(0, 1800, "Walk")) == (12.5, "time")
    assert fitness.load_of(act(0, 3600, "WeightTraining")) == (35.0, "time")
    assert fitness.load_of(act(0, 3600, "SomethingNew")) == (40.0, "time")     # unknown sport falls back to "other"


def test_a_zero_length_activity_has_no_load():
    assert fitness.load_of(act(0, 0)) == (0.0, "none")


# ---- the model

def test_no_usable_activities_gives_none():
    assert fitness.build([], TODAY) is None
    assert fitness.build([act(1, 0)], TODAY) is None


def test_day_one_numbers_match_the_formula_by_hand():
    out = fitness.build([act(1, 3600, suffer_score=84)], TODAY, days=10)
    d0, d1 = out["days"][0], out["days"][1]
    assert d0["date"] == (TODAY - timedelta(days=1)).isoformat()
    assert (d0["fitness"], d0["fatigue"], d0["form"]) == (2.0, 12.0, 0.0)      # 84/42, 84/7, nothing before
    assert d1["form"] == -10.0                                                # yesterday: fitness 2 - fatigue 12
    assert d1["fitness"] == pytest.approx(1.95, abs=0.06) and d1["fatigue"] == pytest.approx(10.29, abs=0.06)   # rounded to 0.1


def test_steady_training_settles_at_the_daily_load_and_form_goes_to_zero():
    acts = [act(n, 3600, suffer_score=60) for n in range(400)]
    now = fitness.build(acts, TODAY)["current"]
    assert now["fitness"] == pytest.approx(60, abs=0.5) and now["fatigue"] == pytest.approx(60, abs=0.5)
    assert abs(now["form"]) < 1


def test_fatigue_reacts_faster_than_fitness():
    base = [act(n, 3600, suffer_score=40) for n in range(60, 200)]
    hard_week = [act(n, 3600, suffer_score=140) for n in range(0, 7)]
    now = fitness.build(base + hard_week, TODAY)["current"]
    assert now["fatigue"] > now["fitness"] + 20               # a hard week: tired
    assert now["form"] < 0 and now["status"] in ("building", "overreaching")


def test_a_rest_week_makes_you_fresher():
    base = [act(n, 3600, suffer_score=80) for n in range(8, 200)]
    now = fitness.build(base, TODAY)["current"]                # last hard day was 8 days ago
    assert now["form"] > 5 and now["status"] in ("fresh", "very_fresh")
    assert now["fitness"] < 80                                # and fitness has started to slip


def test_two_activities_on_one_day_add_up():
    one = fitness.build([act(2, 3600, suffer_score=50), act(2, 1800, suffer_score=30)], TODAY)
    single = fitness.build([act(2, 3600, suffer_score=80)], TODAY)
    assert one["days"] == single["days"]


def test_future_activities_are_ignored():
    assert fitness.build([act(-3, 3600, suffer_score=99)], TODAY) is None


def test_the_series_is_trimmed_but_the_model_still_runs_from_the_first_activity():
    acts = [act(n, 3600, suffer_score=60) for n in range(300)]
    full = fitness.build(acts, TODAY, days=400)
    trimmed = fitness.build(acts, TODAY, days=30)
    assert len(full["days"]) == 300 and len(trimmed["days"]) == 30
    assert trimmed["days"] == full["days"][-30:]              # identical numbers, not restarted from zero


def test_the_first_six_weeks_are_flagged_as_warming_up():
    out = fitness.build([act(n, 3600, suffer_score=60) for n in range(100)], TODAY, days=100)
    flags = [d["warmup"] for d in out["days"]]
    assert flags[:fitness.FITNESS_DAYS] == [True] * fitness.FITNESS_DAYS and flags[fitness.FITNESS_DAYS] is False
    assert out["warming_up"] is False
    assert fitness.build([act(5, 3600, suffer_score=60)], TODAY)["warming_up"] is True


def test_fitness_change_over_four_weeks():
    up = fitness.build([act(n, 3600, suffer_score=60) for n in range(0, 28)] + [act(n, 3600, suffer_score=20) for n in range(28, 90)], TODAY)
    assert up["fitness_change_28d_pct"] > 0
    down = fitness.build([act(n, 3600, suffer_score=90) for n in range(29, 120)], TODAY)
    assert down["fitness_change_28d_pct"] < 0


def test_sources_say_what_the_numbers_are_based_on():
    acts = [act(1, 3600, weighted_average_watts=200), act(2, 3600, suffer_score=50), act(3, 3600, "Run"), act(4, 3600, "Walk")]
    out = fitness.build(acts, TODAY, ftp=250)
    assert out["sources"] == {"power": 1, "effort": 1, "time": 2} and out["ftp"] == 250
    assert fitness.build(acts, TODAY)["ftp"] is None


@pytest.mark.parametrize("form,fit,expected", [
    (25, 50, "very_fresh"), (10, 50, "fresh"), (0, 50, "balanced"), (-15, 50, "building"), (-40, 50, "overreaching"),
    (30, 5, "starting"),                                       # tiny fitness: too little history to say anything
])
def test_status_thresholds(form, fit, expected):
    assert fitness.status_of(form, fit) == expected


# ---- wired into the fetch

def test_fetch_includes_the_fitness_series_and_passes_the_ftp(monkeypatch):
    raw = [{"id": i, "name": f"a{i}", "sport_type": "Ride", "type": "Ride", "distance": 20000, "moving_time": 3600,
            "elapsed_time": 3600, "total_elevation_gain": 0, "weighted_average_watts": 200, "kudos_count": 0,
            "start_date_local": (datetime(2026, 9, 19) - timedelta(days=i)).strftime("%Y-%m-%dT08:00:00Z")} for i in range(1, 30)]
    monkeypatch.setattr(strava, "request_json", lambda url, token=None, **kw: raw)
    monkeypatch.setattr(streams, "get", lambda *a, **k: None)

    class Tokens:
        def access_token(self):
            return "t"

    out = strava.fetch(Tokens(), now=datetime(2026, 9, 19, 12), ftp=250)
    assert out["fitness"]["sources"] == {"power": 29} and out["fitness"]["ftp"] == 250
    assert out["fitness"]["current"]["fitness"] > 0 and len(out["fitness"]["days"]) == 30   # from the first ride through today


def test_fetch_looks_back_far_enough_for_fitness_even_early_in_the_year(monkeypatch):
    seen = []
    monkeypatch.setattr(strava, "request_json", lambda url, token=None, **kw: seen.append(url) or [])

    class Tokens:
        def access_token(self):
            return "t"

    strava.fetch(Tokens(), now=datetime(2026, 1, 10, 12))
    after = int(seen[0].split("after=")[1].split("&")[0])
    looked_back_to = datetime.fromtimestamp(after, timezone.utc).replace(tzinfo=None)
    assert looked_back_to <= datetime(2025, 9, 2)                         # ~130 days before 10 Jan, not just this year


# ---- the chart document and the command that opens it

def test_chart_doc_has_dates_as_days_and_the_right_series_and_panels():
    out = fitness.build([act(n, 3600, suffer_score=60) for n in range(60)], TODAY, days=30)
    doc = fitness.chart_doc(out)
    assert doc["kind"] == "fitness" and doc["x"]["unit"] == "date" and doc["points"] == 30
    xs = doc["x"]["values"]
    assert xs == sorted(xs) and xs[-1] - xs[0] == 29                       # consecutive days
    assert xs[-1] == (TODAY - date(1970, 1, 1)).days
    keys = [s["key"] for s in doc["series"]]
    assert keys == ["fitness", "fatigue", "form", "load"]
    assert {s["panel"] for s in doc["series"] if s["key"] in ("fitness", "fatigue")} == {"trend"}   # one shared axis
    assert all(len(s["values"]) == 30 for s in doc["series"])


def test_the_fitness_command_uses_only_stored_data_and_opens_the_window(monkeypatch, capsys):
    import json
    from lapbar import charts, cli
    out = fitness.build([act(n, 3600, suffer_score=60) for n in range(60)], TODAY)
    cli._write_cache({"fitness": out})
    opened = []
    monkeypatch.setattr(charts, "open_window", lambda path, env=None: opened.append((path, env)) or 1)
    try:
        cli.main(["fitness", "--fg", "#eee", "--bg", "#111"])
    except SystemExit as e:
        assert e.code == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}
    path, env = opened[0]
    doc = json.loads(path.read_text())
    assert doc["kind"] == "fitness" and env == {"LAPBAR_FG": "#eee", "LAPBAR_BG": "#111"}
    import os, stat
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600                   # personal data: owner-only


def test_the_fitness_command_says_so_when_there_is_nothing_to_show(monkeypatch, capsys):
    import json
    from lapbar import cli
    try:
        cli.main(["fitness"])
    except SystemExit as e:
        assert e.code == 1
    assert json.loads(capsys.readouterr().out)["error"] == "no_fitness"


# ---- calibrating effort against power

def ride_with_both(days_ago, watts, effort, seconds=3600):
    return act(days_ago, seconds, weighted_average_watts=watts, suffer_score=effort)


def test_effort_is_rescaled_to_match_power_using_rides_that_have_both():
    # 1 h at 200 W with FTP 250 is 64 TSS; Strava called it 128 effort -> effort should count about half
    rides = [ride_with_both(n, 200, 128) for n in range(1, 8)]
    assert fitness.effort_scale(rides, 250) == pytest.approx(0.5, abs=0.01)
    walk = act(9, 3600, "Walk", suffer_score=40)
    assert fitness.load_of(walk, 250, 0.5) == (20.0, "effort")


def test_no_rescaling_without_an_ftp_or_with_too_few_rides_to_learn_from():
    rides = [ride_with_both(n, 200, 128) for n in range(1, 8)]
    assert fitness.effort_scale(rides, 0) == 1.0
    assert fitness.effort_scale(rides[:fitness.MIN_CALIBRATION - 1], 250) == 1.0
    assert fitness.effort_scale([], 250) == 1.0


def test_the_calibration_is_the_median_so_one_odd_ride_does_not_move_it():
    rides = [ride_with_both(n, 200, 128) for n in range(1, 8)] + [ride_with_both(9, 200, 12)]   # one absurd effort
    assert fitness.effort_scale(rides, 250) == pytest.approx(0.5, abs=0.02)


def test_an_implausible_calibration_is_clamped():
    rides = [ride_with_both(n, 200, 6) for n in range(1, 8)]                 # ratio ~10: something is wrong
    assert fitness.effort_scale(rides, 250) == fitness.SCALE_LIMITS[1]


def test_with_an_ftp_mixed_activities_stay_on_one_scale():
    """The bug this guards against: setting an FTP made rides count half as much as everything else."""
    rides = [ride_with_both(n, 200, 128) for n in range(2, 12)]
    walks = [act(n, 3600, "Walk", suffer_score=64) for n in range(12, 22)]        # 64 effort ~ one such ride's TSS x 2
    without = fitness.build(rides + walks, TODAY, ftp=0)["current"]["fitness"]
    with_ftp = fitness.build(rides + walks, TODAY, ftp=250)
    assert with_ftp["effort_scale"] == pytest.approx(0.5, abs=0.01)
    # rides now count as TSS 64 and walks as effort 64 x 0.5 = 32, so fitness is lower than the effort-only view,
    # but by the same factor for both kinds of activity, not just for the rides
    assert with_ftp["current"]["fitness"] == pytest.approx(without * 0.5, rel=0.05)


def test_the_output_reports_when_it_rescaled_effort():
    rides = [ride_with_both(n, 200, 128) for n in range(1, 10)]
    assert fitness.build(rides, TODAY, ftp=250)["effort_scale"] == pytest.approx(0.5, abs=0.01)
    assert fitness.build(rides, TODAY)["effort_scale"] is None


# ---- FTP estimate (offered by a menu button, never applied on its own)

def hard_rides(*watts, seconds=3600):
    return [act(n + 1, seconds, weighted_average_watts=w) for n, w in enumerate(watts)]


def test_ftp_estimate_is_the_best_weighted_power_of_long_rides_rounded_to_5_watts():
    rides = hard_rides(200, 212, 219, 205, 198, 190)
    assert fitness.estimate_ftp(rides) == {"watts": 220, "rides": 6}


def test_ftp_estimate_needs_a_decent_history():
    assert fitness.estimate_ftp(hard_rides(200, 210, 220, 230)) is None            # four rides: not enough
    assert fitness.estimate_ftp(hard_rides(200, 210, 220, 230, 240)) is not None    # five is


def test_ftp_estimate_ignores_short_rides_rides_without_power_and_other_sports():
    rides = hard_rides(200, 200, 200, 200, 200)
    rides += hard_rides(400, 400, seconds=1200)                                     # 20 min: says little about an hour
    rides += [act(20, 3600), act(21, 3600, "Run", weighted_average_watts=500)]      # no power / not a ride
    rides += [act(22, 3600, "EBikeRide", weighted_average_watts=400)]               # motor assistance
    assert fitness.estimate_ftp(rides) == {"watts": 200, "rides": 5}


def test_the_estimate_is_reported_but_never_used_as_the_ftp():
    rides = hard_rides(200, 212, 219, 205, 198)
    out = fitness.build(rides, TODAY)
    assert out["ftp"] is None and out["ftp_estimate"] == {"watts": 220, "rides": 5}
    assert out["sources"].get("power") is None                                      # still no power-based load
