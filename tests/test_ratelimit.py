import json
import os
import stat
import urllib.request
from datetime import datetime, timezone

import pytest

from lapbar import cli, config, kudos, ratelimit, streams
from lapbar.http import HttpError, request_json
from lapbar.providers import strava

NOON = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc).timestamp()      # a Saturday, mid-day UTC


def use(daily, quarter=0, when=NOON, limits=("100,1000")):
    ratelimit.record({"X-ReadRateLimit-Usage": f"{quarter},{daily}", "X-ReadRateLimit-Limit": limits}, now=when)


# ---- recording and reading Strava's counters

def test_headers_are_parsed_and_stored_privately():
    use(daily=77, quarter=2)
    snap = ratelimit.snapshot(now=NOON)
    assert (snap["daily_used"], snap["daily_limit"], snap["quarter_used"], snap["quarter_limit"]) == (77, 1000, 2, 100)
    assert stat.S_IMODE(os.stat(ratelimit._file()).st_mode) == 0o600


def test_falls_back_to_the_overall_counters_when_the_read_ones_are_missing():
    ratelimit.record({"X-RateLimit-Usage": "5,50", "X-RateLimit-Limit": "200,2000"}, now=NOON)
    snap = ratelimit.snapshot(now=NOON)
    assert (snap["daily_used"], snap["daily_limit"]) == (50, 2000)


def test_a_limit_other_than_the_default_is_respected():
    use(daily=300, limits="200,2000")
    assert ratelimit.snapshot(now=NOON)["fraction"] == pytest.approx(0.15)


def test_garbage_and_missing_headers_never_raise_or_change_anything():
    for headers in ({}, {"X-ReadRateLimit-Usage": "nonsense"}, None, object()):
        ratelimit.record(headers)                              # must not raise
    assert ratelimit.snapshot(now=NOON)["daily_used"] == 0


def test_with_no_information_yet_nothing_is_blocked():
    snap = ratelimit.snapshot(now=NOON)
    assert snap["fraction"] == 0 and (snap["daily_limit"], snap["quarter_limit"]) == (1000, 100)
    assert ratelimit.check("auto", snap) is snap


def test_the_daily_count_resets_at_midnight_utc_and_the_quarter_count_on_the_quarter_hour():
    use(daily=800, quarter=90)
    later_same_quarter = NOON + 300
    assert ratelimit.snapshot(now=later_same_quarter)["quarter_used"] == 90
    next_quarter = NOON + 900
    s = ratelimit.snapshot(now=next_quarter)
    assert s["quarter_used"] == 0 and s["daily_used"] == 800          # only the short window resets
    tomorrow = datetime(2026, 9, 20, 0, 0, 5, tzinfo=timezone.utc).timestamp()
    assert ratelimit.snapshot(now=tomorrow)["daily_used"] == 0


# ---- the tiers: the timer stops early so manual refreshes always have room

@pytest.mark.parametrize("daily,auto,manual,action,optional", [
    (300, True, True, True, True),        # plenty of room: everything runs
    (399, True, True, True, True),
    (400, True, True, True, False),       # 40%: extras stop
    (599, True, True, True, False),
    (600, False, True, True, False),      # 60%: the timer stops, manual still works
    (899, False, True, True, False),
    (900, False, False, True, False),     # 90%: manual stops, a chart you open still downloads
    (949, False, False, True, False),
    (950, False, False, False, False),    # 95%: everything stops
])
def test_each_kind_of_request_has_its_own_ceiling(daily, auto, manual, action, optional):
    use(daily=daily)
    snap = ratelimit.snapshot(now=NOON)

    def allowed(kind):
        try:
            ratelimit.check(kind, snap)
            return True
        except ratelimit.BudgetExhausted:
            return False

    assert (allowed("auto"), allowed("manual"), allowed("action"), ratelimit.allow_optional(snap)) == (auto, manual, action, optional)


def test_the_timer_alone_can_never_use_the_last_40_percent_of_the_day():
    assert ratelimit.LIMITS["auto"] <= 0.60 < ratelimit.LIMITS["manual"]
    assert 1 - ratelimit.LIMITS["auto"] >= 0.40


def test_a_burst_in_the_15_minute_window_also_pauses_the_timer():
    use(daily=50, quarter=70)                                          # 70% of the quarter-hour allowance
    with pytest.raises(ratelimit.BudgetExhausted) as e:
        ratelimit.check("auto", ratelimit.snapshot(now=NOON))
    next_window = datetime.fromtimestamp(NOON + 900).strftime("%H:%M")
    assert "quarter hour" in str(e.value) and next_window in str(e.value)
    ratelimit.check("manual", ratelimit.snapshot(now=NOON))            # ...but a manual refresh is still fine


def test_messages_say_what_is_paused_and_that_manual_refresh_still_works():
    use(daily=650)
    with pytest.raises(ratelimit.BudgetExhausted) as e:
        ratelimit.check("auto", ratelimit.snapshot(now=NOON))
    m = str(e.value)
    assert "650 of 1000" in m and "Refreshing by hand still works" in m and "00:00 UTC" in m
    use(daily=920)
    with pytest.raises(ratelimit.BudgetExhausted) as e:
        ratelimit.check("manual", ratelimit.snapshot(now=NOON))
    assert "920 of 1000" in str(e.value) and "Refreshing by hand" not in str(e.value)


def test_summary_tells_the_widget_whether_auto_refresh_is_paused():
    use(daily=650)
    assert ratelimit.summary(ratelimit.snapshot(now=NOON))["auto_paused"] is True
    use(daily=100)
    assert ratelimit.summary(ratelimit.snapshot(now=NOON))["auto_paused"] is False


# ---- every real response updates the counters, including a 429

class FakeResponse:
    def __init__(self, headers, body=b"[]"):
        self.headers, self._body = headers, body

    def read(self, *a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_successful_response_records_the_counters(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResponse(
        {"X-ReadRateLimit-Usage": "3,120", "X-ReadRateLimit-Limit": "100,1000"}))
    request_json("https://www.strava.com/api/v3/x", token="t")
    assert ratelimit.snapshot()["daily_used"] == 120


def test_an_error_response_records_the_counters_too(monkeypatch):
    import io
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 429, "Too Many", {"X-ReadRateLimit-Usage": "100,900",
                                                            "X-ReadRateLimit-Limit": "100,1000"}, io.BytesIO(b"{}"))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(HttpError):
        request_json("https://www.strava.com/api/v3/x", token="t")
    assert ratelimit.snapshot()["quarter_used"] == 100


# ---- what the budget switches off

def act(i):
    return {"id": i, "name": f"a{i}", "kudos": 2, "sport": "Ride", "family": "ride", "start": "2026-09-1%dT10:00:00Z" % (i % 9),
            "distance_km": 5}


class Tokens:
    def access_token(self):
        return "tok"


def raw_activity(i):
    return {"id": i, "name": f"a{i}", "sport_type": "Ride", "type": "Ride", "distance": 1000, "moving_time": 100,
            "elapsed_time": 100, "total_elevation_gain": 0, "start_date_local": f"2026-09-1{i}T10:00:00Z", "kudos_count": 2}


def test_without_room_history_downloads_and_kudos_name_lookups_are_skipped(monkeypatch):
    from datetime import datetime as dt
    monkeypatch.setattr(strava, "request_json", lambda url, token=None, **kw: [raw_activity(i) for i in (1, 2, 3)])
    downloads, lookups = [], []
    monkeypatch.setattr(streams, "backfill", lambda token, acts, limit: downloads.append(limit) or 0)
    monkeypatch.setattr(kudos, "kudoers", lambda token, i: lookups.append(i) or ["Ana K."])
    monkeypatch.setattr(streams, "get", lambda token, a, refresh=False: None)

    strava.fetch(Tokens(), now=dt(2026, 9, 19), backfill=3, optional=False)
    assert downloads == [] and lookups == []

    strava.fetch(Tokens(), now=dt(2026, 9, 19), backfill=3, optional=True)
    assert downloads == [3] and lookups                                   # extras run when there is room


def test_a_new_kudo_is_still_looked_up_without_room_because_that_is_the_point_of_refreshing(monkeypatch):
    monkeypatch.setattr(kudos, "kudoers", lambda token, i: ["Ana K.", "Bo M."])
    prev = {"kudos_seen": {"1": 1}, "kudoers": {"1": ["Ana K."]}}
    events, _, _ = kudos.track("t", [act(1)], prev, seed_limit=0)
    assert events and events[0]["from"] == ["Bo M."]


# ---- the CLI ties it together

def run(monkeypatch, capsys, argv):
    try:
        cli.main(argv)
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else None


def fake_fetch(seen):
    def go(previous=None, backfill=0, optional=True, ftp=0, **kw):
        seen.update(backfill=backfill, optional=optional)
        return {"provider": "strava", "latest": None}
    return go


def test_the_timer_fetch_is_refused_at_60_percent_but_a_manual_one_goes_through(monkeypatch, capsys):
    monkeypatch.setattr(cli.ratelimit, "snapshot", ratelimit.snapshot)
    use(daily=650, when=__import__("time").time())
    seen = {}
    monkeypatch.setattr(strava, "fetch", fake_fetch(seen))
    code, out = run(monkeypatch, capsys, ["fetch", "--print"])
    assert code == 1 and out["error"] == "budget" and out["budget"]["auto_paused"] is True
    assert "Refreshing by hand still works" in out["message"] and not seen        # Strava was not contacted

    code, out = run(monkeypatch, capsys, ["fetch", "--print", "--manual"])
    assert code == 0 and out["budget"]["daily_used"] == 650 and seen["optional"] is False   # no extras on a manual refresh


def test_a_manual_fetch_is_refused_at_90_percent(monkeypatch, capsys):
    use(daily=920, when=__import__("time").time())
    monkeypatch.setattr(strava, "fetch", fake_fetch({}))
    code, out = run(monkeypatch, capsys, ["fetch", "--print", "--manual"])
    assert code == 1 and out["error"] == "budget" and "920 of 1000" in out["message"]


def test_extras_run_on_the_timer_only_while_there_is_plenty_of_room(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(strava, "fetch", fake_fetch(seen))
    use(daily=100, when=__import__("time").time())
    run(monkeypatch, capsys, ["fetch", "--print"])
    assert seen["optional"] is True and seen["backfill"] == 2       # the setting's 3, scaled down: 10% of the day is used
    use(daily=500, when=__import__("time").time())
    run(monkeypatch, capsys, ["fetch", "--print"])
    assert seen["optional"] is False


def test_stored_charts_open_even_with_the_allowance_used_up(monkeypatch, capsys, keyring):
    from test_cli import prepare
    streams_mod = prepare(monkeypatch, keyring)
    streams_mod._store(5, {"v": streams_mod.DATA_VERSION, "id": 5, "name": "Ride", "x": {"values": [0, 1]}, "series": [
        {"key": "altitude", "values": [1, 2], "label": "Elevation", "unit": "m", "decimals": 0, "format": "number"}], "points": 2})
    use(daily=990, when=__import__("time").time())
    code, out = run(monkeypatch, capsys, ["streams", "5"])
    assert code == 0 and out["series"] == ["altitude"]                     # no download needed, so no budget needed


def test_a_chart_that_needs_downloading_is_refused_at_95_percent(monkeypatch, capsys, keyring):
    from test_cli import prepare
    prepare(monkeypatch, keyring)
    use(daily=960, when=__import__("time").time())
    code, out = run(monkeypatch, capsys, ["streams", "5"])
    assert code == 1 and out["error"] == "budget"
    use(daily=920, when=__import__("time").time())                          # 92%: manual refreshes stop, charts still work
    assert run(monkeypatch, capsys, ["streams", "5"])[0] == 0


def test_background_downloads_slow_down_as_the_day_fills_up():
    q = lambda fraction, setting: ratelimit.backfill_quota({"fraction": fraction}, setting)      # noqa: E731
    assert q(0.0, 12) == 12 and q(0.10, 12) == 9 and q(0.20, 12) == 6 and q(0.30, 12) == 3
    assert q(0.399, 12) == 1                       # at least one while there is any room for extras
    assert q(0.40, 12) == 0 and q(0.90, 12) == 0   # none at the limit for extras
    assert q(0.0, 0) == 0                          # the setting turns it off
