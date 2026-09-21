"""Unit tests for charts/logic.js, run under plain node (skipped if node is not installed)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

LOGIC = Path(__file__).resolve().parent.parent / "charts" / "logic.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def js(expression: str):
    """Evaluate `expression` with logic.js loaded (its `.pragma library` line removed) and return the JSON result."""
    source = LOGIC.read_text().replace(".pragma library", "")
    script = source + f"\nprocess.stdout.write(JSON.stringify({expression}));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_nice_ticks_are_round_numbers_inside_the_range():
    assert js("ticks(0, 36.5, 6)") == [0, 5, 10, 15, 20, 25, 30, 35]
    assert js("ticks(19.3, 21.7, 4)") == [19.5, 20, 20.5, 21, 21.5]
    assert js("ticks(30, 128, 3)") == [40, 60, 80, 100, 120]
    assert js("ticks(0.1, 0.3, 4)") == [0.1, 0.15, 0.2, 0.25, 0.3]      # no 0.30000000000000004


def test_ticks_survive_a_zero_or_tiny_range():
    assert js("niceStep(0, 5)") == 1
    assert js("ticks(5, 5, 3)") == [5]


def test_cursor_snaps_to_the_nearest_sample():
    xs = "[0, 1, 2, 3, 4]"
    assert js(f"indexAt({xs}, 2.4)") == 2 and js(f"indexAt({xs}, 2.6)") == 3
    assert js(f"indexAt({xs}, -5)") == 0 and js(f"indexAt({xs}, 99)") == 4
    assert js("indexAt([], 1)") == -1 and js("indexAt([7], 3)") == 0


def test_visible_range_is_widened_so_lines_reach_the_edges():
    assert js("visibleRange([0,1,2,3,4,5,6], 2.2, 4.4)") == [2, 5]
    assert js("visibleRange([0,1,2,3], 0, 3)") == [0, 3]


def test_stats_ignore_gaps():
    assert js("stats([null, 2, 4, null, 6], 0, 4)") == {"min": 2, "max": 6, "avg": 4, "count": 3}
    assert js("stats([null, null], 0, 1)") is None
    assert js("stats([1, 2, 3, 4], 1, 2)")["avg"] == 2.5                # only the requested slice


def test_zoom_keeps_the_point_under_the_pointer_fixed_and_stays_in_bounds():
    s, e = js("zoom(0, 40, 0, 40, 10, 0.5, 0.1)")                       # zoom in around x=10
    assert (e - s) == pytest.approx(20) and s == pytest.approx(5)       # x=10 stays 25% along the view
    s, e = js("zoom(0, 10, 0, 40, 10, 0.5, 0.1)")
    assert 0 <= s and e <= 40
    assert js("zoom(0, 40, 0, 40, 20, 4, 0.1)") == [0, 40]              # cannot zoom out past the data
    s, e = js("zoom(10, 10.2, 0, 40, 10.1, 0.5, 0.5)")
    assert e - s == pytest.approx(0.5)                                  # minimum span respected


def test_pan_is_clamped_to_the_data():
    assert js("pan(10, 20, 0, 40, -50)") == [0, 10]
    assert js("pan(10, 20, 0, 40, 50)") == [30, 40]
    assert js("pan(10, 20, 0, 40, 3)") == [13, 23]


def test_y_range_has_headroom_and_never_collapses():
    r = js("yRange([10, 20], 0, 1)")
    assert r["min"] < 10 and r["max"] > 20 and r["dataMin"] == 10 and r["dataMax"] == 20
    flat = js("yRange([140, 140, 140], 0, 2)")
    assert flat["max"] > flat["min"]                                    # a flat line still gets an axis
    assert js("yRange([null], 0, 0)") is None


def test_value_formatting():
    hr = {"decimals": 0, "format": "number"}
    assert js(f"fmtValue({json.dumps(hr)}, 140.4)") == "140"
    assert js(f"fmtValue({json.dumps({'decimals': 1})}, 27.84)") == "27.8"
    assert js(f"fmtValue({json.dumps(hr)}, null)") == "–"
    pace = {"format": "pace", "decimals": 0}
    assert js(f"fmtValue({json.dumps(pace)}, 300)") == "5:00" and js(f"fmtValue({json.dumps(pace)}, 365)") == "6:05"
    assert js(f"fmtTick({json.dumps({'decimals': 1})}, 20)") == "20"     # "20", not "20.0"
    assert js(f"fmtTick({json.dumps({'decimals': 1})}, 2.5)") == "2.5"


def test_x_formatting_for_distance_and_time():
    km = {"unit": "km"}
    assert js(f"fmtX({json.dumps(km)}, 5.678)") == "5.68" and js(f"fmtX({json.dumps(km)}, 19.14)") == "19.1"
    mins = {"unit": "min"}
    assert js(f"fmtX({json.dumps(mins)}, 12.5)") == "12:30" and js(f"fmtX({json.dumps(mins)}, 75.25)") == "1:15:15"


def test_palette_colours_follow_the_metric_not_its_position_and_adapt_to_the_theme():
    assert js('seriesColor("altitude", true)') == "#3987e5" and js('seriesColor("altitude", false)') == "#2a78d6"
    assert js('seriesColor("heartrate", true)') == js('seriesColor("heartrate", true)')
    assert js('seriesColor("pace", true)') == js('seriesColor("speed", true)')      # same slot: never both shown
    assert js('isDark("#1f1f28")') is True and js('isDark("#fafafa")') is False
    assert js('isDark("garbage")') is True                                          # safe default


def test_colour_mixing_and_rgba():
    assert js('mix("#ffffff", "#000000", 0.5)') == "#808080"
    assert js('mix("#ff0000", "#ff0000", 0.3)') == "#ff0000"
    assert js('rgba("#ff8000", 0.1)') == "rgba(255,128,0,0.1)"
    assert js('parseColor("#abc")') == [170, 187, 204] and js('parseColor("nope")') is None


def test_every_metric_the_data_can_contain_has_its_own_palette_slot():
    slots = js("SLOT")
    activity = {k: v for k, v in slots.items() if k not in ("fitness", "fatigue", "form", "load")}
    assert set(activity) == {"altitude", "speed", "pace", "heartrate", "watts", "cadence", "temp", "grade"}
    assert len({v for k, v in activity.items() if k != "pace"}) == 7                # distinct colours per activity chart


def test_the_fitness_chart_keeps_fitness_and_fatigue_apart():
    slots = js("SLOT")
    assert slots["fitness"] != slots["fatigue"]

def days(y, m, d):
    from datetime import date
    return (date(y, m, d) - date(1970, 1, 1)).days


def test_dates_are_written_from_days_since_1970():
    assert js("fmtDate(0, true)") == "Thu 1 Jan 1970"
    assert js(f"fmtDate({days(2026, 9, 19)}, false)") == "19 Sep"
    assert js(f"fmtDate({days(2026, 9, 19)}, true)") == "Sat 19 Sep 2026"
    assert js(f'fmtX({{"unit": "date"}}, {days(2026, 1, 5)})') == "5 Jan"


def test_short_date_axes_tick_every_day_or_two():
    d0 = days(2026, 9, 1)
    assert [t["x"] for t in js(f"dateTicks({d0}, {d0 + 6})")] == list(range(d0, d0 + 7))
    two = [t["x"] for t in js(f"dateTicks({d0}, {d0 + 15})")]
    assert all(b - a == 2 for a, b in zip(two, two[1:]))


def test_weekly_date_axes_tick_on_mondays():
    from datetime import date, timedelta
    d0 = days(2026, 8, 1)
    ticks = js(f"dateTicks({d0}, {d0 + 50})")
    assert ticks and all((date(1970, 1, 1) + timedelta(days=t["x"])).weekday() == 0 for t in ticks)
    assert all(b["x"] - a["x"] == 7 for a, b in zip(ticks, ticks[1:]))


def test_long_date_axes_tick_on_month_starts_with_month_names():
    ticks = js(f"dateTicks({days(2026, 5, 23)}, {days(2026, 9, 19)})")           # the popup's 120 days
    assert [t["label"] for t in ticks] == ["Jun", "Jul", "Aug", "Sep"]
    assert all(t["x"] == days(2026, m, 1) for t, m in zip(ticks, (6, 7, 8, 9)))


def test_a_new_year_is_labelled_with_the_year():
    ticks = js(f"dateTicks({days(2025, 11, 10)}, {days(2026, 3, 20)})")
    assert [t["label"] for t in ticks] == ["Dec", "2026", "Feb", "Mar"]


def test_year_long_axes_use_two_month_steps():
    ticks = js(f"dateTicks({days(2025, 6, 1)}, {days(2026, 6, 1)})")
    assert [t["label"] for t in ticks] == ["Jun", "Aug", "Oct", "Dec", "Feb", "Apr", "Jun"]
    assert all(t["x"] == days(y, m, 1) for t, (y, m) in zip(ticks, [(2025, 6), (2025, 8), (2025, 10), (2025, 12), (2026, 2), (2026, 4), (2026, 6)]))


# ---- level of detail: only actual samples are ever drawn

def test_zoomed_in_every_actual_sample_is_drawn():
    out = js("lod([0, 1, 2, 3, 4], [5, 6, 7, 8, 9], 0, 4, 0, 4, 100)")
    assert out["raw"] is True and out["segments"] == [[0, 5, 1, 6, 2, 7, 3, 8, 4, 9]]


def test_zoomed_out_a_long_series_is_reduced_to_a_few_samples_per_pixel_column():
    out = js("(function() { var xs = [], v = []; for (var i = 0; i < 100000; i++) { xs.push(i); v.push(Math.sin(i / 50) * 100); }"
             " return lod(xs, v, 0, 99999, 0, 99999, 500); })()")
    assert out["raw"] is False
    assert out["points"] <= 4 * 500 + 4 and out["points"] < 100000 / 40          # bounded by the width, not the ride


def test_every_drawn_point_is_a_real_sample_never_an_average():
    out = js("(function() { var xs = [], v = []; for (var i = 0; i < 5000; i++) { xs.push(i * 0.5); v.push((i * 37) % 101); }"
             " var r = lod(xs, v, 0, 4999, 0, 2499.5, 200); var bad = 0, seg = r.segments[0];"
             " for (var k = 0; k < seg.length; k += 2) { var i = Math.round(seg[k] / 0.5); if (xs[i] !== seg[k] || v[i] !== seg[k + 1]) bad++; }"
             " return { bad: bad, points: r.points }; })()")
    assert out["bad"] == 0 and out["points"] > 0


def test_a_one_sample_spike_survives_the_reduction():
    out = js("(function() { var xs = [], v = []; for (var i = 0; i < 50000; i++) { xs.push(i); v.push(i === 31337 ? 999 : 10); }"
             " var r = lod(xs, v, 0, 49999, 0, 49999, 300); var seen = false, low = 0;"
             " r.segments.forEach(function(s) { for (var k = 0; k < s.length; k += 2) { if (s[k] === 31337 && s[k + 1] === 999) seen = true; } });"
             " return { seen: seen, ext: extent([r]) }; })()")
    assert out["seen"] is True and out["ext"] == {"min": 10, "max": 999}         # every-nth-sample drawing would lose it


def test_the_first_and_last_sample_are_always_there_and_x_only_increases():
    out = js("(function() { var xs = [], v = []; for (var i = 0; i < 20000; i++) { xs.push(i); v.push(i % 13); }"
             " var r = lod(xs, v, 0, 19999, 0, 19999, 250), s = r.segments[0], up = true;"
             " for (var k = 2; k < s.length; k += 2) if (s[k] < s[k - 2]) up = false;"
             " return { first: s[0], last: s[s.length - 2], up: up }; })()")
    assert out == {"first": 0, "last": 19999, "up": True}


def test_gaps_in_the_data_split_the_line_in_both_modes():
    raw = js("(function() { var v = typed([1, 2, null, null, 5, 6]); return lod([0, 1, 2, 3, 4, 5], v, 0, 5, 0, 5, 100, gapCounts(v)); })()")
    assert raw["segments"] == [[0, 1, 1, 2], [4, 5, 5, 6]]
    out = js("(function() { var xs = [], w = []; for (var i = 0; i < 9000; i++) { xs.push(i); w.push(i >= 4000 && i < 5000 ? null : i % 5); }"
             " var v = typed(w); var r = lod(xs, v, 0, 8999, 0, 8999, 100, gapCounts(v)); var ends = r.segments.map(function(s) { return [s[0], s[s.length - 2]]; });"
             " return { n: r.segments.length, ends: ends }; })()")
    assert out["n"] == 2 and out["ends"][0][1] == 3999 and out["ends"][1][0] == 5000


def test_a_flat_or_empty_range_does_not_break_it():
    assert js("lod([0, 1, 2], [4, 4, 4], 0, 2, 0, 2, 50)")["segments"] == [[0, 4, 1, 4, 2, 4]]
    assert js("lod([], [], 0, -1, 0, 1, 50)")["segments"] == []
    assert js("extent([lod([0, 1], [null, null], 0, 1, 0, 1, 50)])") is None


def test_gaps_split_a_long_reduced_line_and_the_extremes_are_exact():
    out = js("(function() { var xs = [], w = []; for (var i = 0; i < 30000; i++) { xs.push(i * 0.37); w.push(i % 4001 === 17 ? null : Math.sin(i / 90) * 50 + (i % 11)); }"
             " var v = typed(w), g = gapCounts(v);"
             " var fast = lod(xs, v, 0, 29999, 0, 11099, 400, g);"
             " return { gaps: g !== null, nf: fast.segments.length, e: extent([fast]), s: (function() { var lo = 1e9, hi = -1e9;"
             " for (var i = 0; i < 30000; i++) if (w[i] !== null) { lo = Math.min(lo, w[i]); hi = Math.max(hi, w[i]); } return [lo, hi]; })() }; })()")
    assert out["gaps"] is True and out["nf"] == 9                       # 8 dropouts split the line into 9 runs
    assert out["e"]["min"] == pytest.approx(out["s"][0]) and out["e"]["max"] == pytest.approx(out["s"][1])   # the extremes are exact


def test_no_gaps_means_no_gap_counts_and_typed_arrays_mark_missing_values_as_nan():
    assert js("gapCounts(typed([1, 2, 3]))") is None
    assert js("(function() { var t = typed([1, null, 3]); return [t[0], t[1] !== t[1], t[2]]; })()") == [1, True, 3]
    assert js("Array.from(gapCounts(typed([1, null, null, 4])))") == [0, 0, 1, 2, 2]
