"""The DuckDB export, the preferences and fetch log behind the data window, and the history limit."""
import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from lapbar import cli, config, details, export, fetchlog, history, manage, prefs, raw
from lapbar.providers import strava


try:
    import duckdb
except ImportError:      # the DuckDB-specific tests below skip themselves; the rest run without it
    duckdb = None

needs_duckdb = pytest.mark.skipif(export.availability()["available"] is False, reason="duckdb is not installed")


def act(id_, start, **extra):
    return {"id": id_, "start": start, "sport": "Ride", "family": "ride", "name": f"Ride {id_}", "distance_km": 30.0,
            "moving_time_s": 3600, "elevation_m": 250, "kudos": 2, "prs": 1, "achievements": 2,
            "url": f"https://www.strava.com/activities/{id_}", **extra}


def streams_with_gps(n=1000, lat0=46.05, lon0=14.5):
    return {
        "time": {"data": list(range(n))},
        "distance": {"data": [i * 8.0 for i in range(n)]},
        "latlng": {"data": [[lat0 + i * 1e-5, lon0 + i * 2e-5] for i in range(n)]},
        "altitude": {"data": [300.0 + i * 0.1 for i in range(n)]},
        "velocity_smooth": {"data": [8.0] * n},
        "heartrate": {"data": [120 + i % 40 for i in range(n)]},
        "watts": {"data": [200] * n},
        "moving": {"data": [True] * n},
    }


@pytest.fixture
def data():
    """A small library: two years, three activities, two archived (one indoors without GPS), records and kudos."""
    this_year = [act(3, "2026-09-01T07:00:00Z")]
    older = [act(2, "2025-06-01T07:00:00Z", name="Indoor 'trainer' ride"), act(1, "2025-05-01T07:00:00Z")]
    days = {"2026-09-01": {"count": 1, "distance_km": 30.0, "moving_time_s": 3600, "elevation_m": 250, "families": ["ride"]}}
    history.save_year(2025, older, {"2025-06-01": {"count": 1, "distance_km": 30.0, "moving_time_s": 3600, "elevation_m": 250, "families": ["ride"]},
                                    "2025-05-01": {"count": 1, "distance_km": 30.0, "moving_time_s": 3600, "elevation_m": 250, "families": ["ride"]}}, "x")
    config.private_dir(config.cache_path().parent)
    config.cache_path().write_text(json.dumps({"activities": this_year, "days": {**history.merged_days(), **days},
                                               "kudoers": {"3": ["Ann B.", "Cy D."]}}))
    raw.store(older[1], streams_with_gps())                                  # id 1: with GPS
    raw.store(older[0], {"time": {"data": [0, 1, 2]}, "distance": {"data": [0.0, 5.0, 10.0]}, "watts": {"data": [150, 160, 170]}})   # id 2: indoors
    details._save(1, {"records": {"counts": [1, 2], "items": [
        {"kind": "pr", "rank": 1, "name": "Hill 'A' climb", "seconds": 312, "distance_m": 1200, "source": "segment"}]},
        "kudoers": {"count": 2, "names": ["Eve F.", "Gus H."]}})
    return {"path": None}


def rows(path, sql):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ---- DuckDB missing: the message says what to install

def test_a_missing_duckdb_is_reported_with_the_install_commands(monkeypatch, capsys):
    def missing():
        raise export.DuckdbMissing(export.INSTALL_HINT)
    monkeypatch.setattr(export, "_import", missing)
    assert export.availability() == {"available": False, "version": None, "install": export.INSTALL_COMMANDS, "spatial": False}
    assert "omarchy pkg add python-duckdb" in export.INSTALL_HINT and "sudo" not in export.INSTALL_HINT
    with pytest.raises(SystemExit):
        cli.main(["export", "--path", "/tmp/never.duckdb"])
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "duckdb_missing" and out["install"] == export.INSTALL_COMMANDS and "python-duckdb" in out["message"]


def test_continuous_export_does_nothing_without_duckdb(monkeypatch):
    monkeypatch.setattr(export, "availability", lambda: {"available": False})
    prefs.update(export_continuous=True)
    started = []
    monkeypatch.setattr(export.subprocess, "Popen", lambda *a, **k: started.append(a))
    assert export.spawn_if_enabled() is False and started == []


def test_continuous_export_starts_in_the_background_only_when_switched_on_and_idle(monkeypatch):
    monkeypatch.setattr(export, "availability", lambda: {"available": True})
    started = []
    monkeypatch.setattr(export.subprocess, "Popen", lambda cmd, **k: started.append(cmd))
    assert export.spawn_if_enabled() is False                                  # off by default
    prefs.update(export_continuous=True)
    assert export.spawn_if_enabled() is True and started[0][-1] == "export" and "-I" in started[0]
    export._write_state(running=True, pid=__import__("os").getpid())
    assert export.spawn_if_enabled() is False                                  # one at a time


# ---- the schema is one description used for everything

def test_the_schema_ties_basic_ride_data_to_the_spatial_tables():
    tables = {t["table"]: [c[0] for c in t["columns"]] for t in export.SCHEMA}
    assert {"activities", "routes", "route_cells", "samples", "days", "records", "kudos", "meta"} <= set(tables)
    for spatial in ("routes", "route_cells", "samples", "records", "kudos"):
        assert "activity_id" in tables[spatial]                                # every table joins back to activities.id
    assert {"min_lat", "max_lat", "min_lon", "max_lon", "start_lat", "wkt", "geom"} <= set(tables["routes"])
    assert {"cell_x", "cell_y"} <= set(tables["route_cells"]) and {"lat", "lon"} <= set(tables["samples"])
    assert all(c[2] is not None for t in export.SCHEMA for c in t["columns"]) and export.RELATIONSHIPS
    assert len(export._activity_row({"id": 1})) == len(tables["activities"])
    assert all("GEOMETRY" not in s for s in export.ddl())                      # only added when spatial is available


# ---- building the routes bridge

def test_a_route_is_a_thinned_linestring_with_its_bounds_and_ends():
    lists = export._sample_lists(streams_with_gps(3000))
    route = export._route_row(1, lists)
    assert route[1] == 3000 and route[2] == pytest.approx(2999 * 8.0)
    assert route[7] < route[8] and route[9] < route[10]                       # min_lat < max_lat, min_lon < max_lon
    wkt = route[11]
    assert wkt.startswith("LINESTRING(14.50000 46.05000, ") and wkt.count(",") + 1 <= 401
    assert wkt.endswith(f"{route[6]:.5f} {route[5]:.5f})")                    # the last point is kept


def test_no_gps_means_no_route():
    assert export._route_row(1, export._sample_lists({"time": {"data": [0, 1, 2]}, "watts": {"data": [1, 2, 3]}})) is None


# ---- the real thing

@needs_duckdb
def test_the_export_joins_basic_ride_data_to_the_spatial_data(data, tmp_path):
    path = tmp_path / "out" / "lapbar.duckdb"
    result = export.sync(path)
    assert result["activities"] == 3 and result["activities_added_to_samples"] == 2 and result["sample_rows"] == 1003
    assert rows(path, "SELECT count(*) FROM activities")[0][0] == 3
    assert rows(path, "SELECT name FROM activities WHERE id = 2") == [("Indoor 'trainer' ride",)]      # quoting survives
    assert rows(path, "SELECT id, has_samples, has_route FROM activities ORDER BY id") == [(1, True, True), (2, True, False), (3, False, False)]
    joined = rows(path, "SELECT a.name, r.points, r.wkt LIKE 'LINESTRING%' FROM activities a JOIN routes r ON r.activity_id = a.id")
    assert joined == [("Ride 1", 1000, True)]                                                           # basic data + spatial, joined
    assert rows(path, "SELECT count(*) FROM samples WHERE lat IS NOT NULL")[0][0] == 1000
    assert rows(path, "SELECT min(seconds), sum(seconds) FROM route_cells")[0][1] == 1000
    assert rows(path, "SELECT sum(rides) FROM (SELECT count(DISTINCT activity_id) AS rides FROM route_cells GROUP BY cell_x, cell_y)")[0][0] >= 1
    assert rows(path, "SELECT kind, rank, name, seconds FROM records") == [("pr", 1, "Hill 'A' climb", 312)]
    assert sorted(rows(path, "SELECT giver FROM kudos WHERE activity_id = 1")) == [("Eve F.",), ("Gus H.",)]
    assert rows(path, "SELECT count(*) FROM days")[0][0] == 3
    assert dict(rows(path, "SELECT key, value FROM meta"))["lapbar_schema"] == export.SCHEMA_VERSION
    assert stat.S_IMODE(path.stat().st_mode) == 0o600                                                  # it contains routes


@needs_duckdb
def test_the_route_overlaps_are_a_plain_join(data, tmp_path):
    raw.store(act(4, "2025-07-01T07:00:00Z"), streams_with_gps())                                     # the same road as ride 1
    history.save_year(2025, [act(2, "2025-06-01T07:00:00Z"), act(1, "2025-05-01T07:00:00Z"), act(4, "2025-07-01T07:00:00Z")], {}, "x")
    path = tmp_path / "o.duckdb"
    export.sync(path)
    shared = rows(path, "SELECT a.activity_id, b.activity_id, count(*) FROM route_cells a JOIN route_cells b USING (cell_x, cell_y) "
                        "WHERE a.activity_id < b.activity_id GROUP BY 1, 2")
    assert len(shared) == 1 and shared[0][:2] == (1, 4) and shared[0][2] > 5


@needs_duckdb
def test_running_it_again_adds_only_what_is_new(data, tmp_path):
    path = tmp_path / "again.duckdb"
    export.sync(path)
    again = export.sync(path)
    assert again["activities_added_to_samples"] == 0 and again["sample_rows"] == 1003
    raw.store(act(3, "2026-09-01T07:00:00Z"), streams_with_gps(500, 47.0, 15.0))                      # a new ride was archived
    grown = export.sync(path)
    assert grown["activities_added_to_samples"] == 1 and grown["sample_rows"] == 1503
    assert rows(path, "SELECT has_route FROM activities WHERE id = 3") == [(True,)]


@needs_duckdb
def test_rebuild_starts_a_fresh_file_and_leaves_no_temporary_one(data, tmp_path):
    path = tmp_path / "db" / "rebuild.duckdb"
    export.sync(path)
    export.sync(path, rebuild=True)
    assert rows(path, "SELECT count(*) FROM samples")[0][0] == 1003
    assert sorted(p.name for p in path.parent.iterdir()) == ["rebuild.duckdb"]


@needs_duckdb
def test_the_export_reports_its_progress_and_finish_in_a_state_file(data, tmp_path):
    seen = []
    export.sync(tmp_path / "p.duckdb", progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 2), (2, 2)]
    state = export.state()
    assert state["running"] is False and state["error"] is None and state["sample_rows"] == 1003


@needs_duckdb
def test_a_folder_is_refused_and_a_failure_is_recorded(data, tmp_path):
    with pytest.raises(ValueError, match="is a folder"):
        export.sync(tmp_path)
    assert export.state()["running"] is False


@needs_duckdb
def test_a_database_open_elsewhere_is_reported_clearly(data, tmp_path, monkeypatch):
    def locked(path):
        raise duckdb.IOException("Could not set lock on file")
    monkeypatch.setattr(duckdb, "connect", locked)
    with pytest.raises(export.ExportBusy, match="open in another program"):
        export.sync(tmp_path / "busy.duckdb")
    assert "open in another program" in export.state()["error"]


@needs_duckdb
def test_the_default_path_is_in_the_data_folder_and_a_chosen_path_is_used(data, tmp_path):
    assert prefs.export_path() == config.data_dir() / "lapbar.duckdb"
    prefs.update(export_path=str(tmp_path / "mine.duckdb"))
    assert export.sync()["path"] == str(tmp_path / "mine.duckdb") and (tmp_path / "mine.duckdb").is_file()


# ---- preferences

def test_preferences_default_validate_and_are_private():
    assert prefs.load() == {"history_from": None, "export_path": None, "export_continuous": False, "export_spatial": False,
                              "coach_tone": "off", "coach_random_per_day": 2, "quiet_hours": "22:00-08:00", "coach_pause_until": None}
    assert prefs.update(history_from="2019-04-01")["history_from"] == "2019-04-01"
    assert prefs.update(history_from=None)["history_from"] is None
    with pytest.raises(ValueError):
        prefs.update(history_from="last spring")
    with pytest.raises(ValueError, match="future"):
        prefs.update(history_from=(datetime.now() + timedelta(days=5)).date().isoformat())
    with pytest.raises(KeyError):
        prefs.update(surprise=1)
    assert stat.S_IMODE(prefs._path().stat().st_mode) == 0o600


def test_prefs_command_sets_and_clears_values(capsys):
    def run(*argv):
        try:
            cli.main(["prefs", *argv])
            code = 0
        except SystemExit as e:
            code = e.code or 0
        return code, json.loads(capsys.readouterr().out)
    assert run("--history-from", "2020-01-01", "--continuous", "on", "--export-path", "~/x.duckdb", "--spatial", "on")[1]["prefs"] == {
        "history_from": "2020-01-01", "export_path": "~/x.duckdb", "export_continuous": True, "export_spatial": True,
        "coach_tone": "off", "coach_random_per_day": 2, "quiet_hours": "22:00-08:00", "coach_pause_until": None}
    assert run("--history-from", "none")[1]["prefs"]["history_from"] is None
    code, out = run("--history-from", "nonsense")
    assert code == 1 and out["error"] == "bad_value"


# ---- how fetching went, by day

def test_downloads_and_requests_are_counted_per_day():
    raw.store(act(9, "2025-06-01T07:00:00Z"), streams_with_gps(50))
    raw.store(act(10, "2025-06-01T07:00:00Z"), {})                              # asked, nothing there: still one request
    fetchlog.note_requests(120)
    fetchlog.note_requests(90)                                                  # the highest of the day is kept
    today = fetchlog.last_days(3)[-1]
    assert today["activities"] == 2 and today["requests"] == 120


def test_the_last_days_are_filled_in_oldest_first():
    days = fetchlog.last_days(14)
    assert len(days) == 14 and days[0]["date"] < days[-1]["date"] and all(d["activities"] == 0 for d in days)
    fetchlog.add_download((datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat())
    assert fetchlog.last_days(14)[-4]["activities"] == 1


def test_the_first_look_counts_what_the_archive_already_holds_by_download_day(data):
    (config.data_dir() / "fetch-log.json").unlink(missing_ok=True)
    assert sum(d["activities"] for d in fetchlog.last_days(3)) == 2


# ---- the history limit

def test_since_keeps_the_order_and_drops_older_activities():
    acts = [act(3, "2026-09-01T07:00:00Z"), act(2, "2025-06-01T07:00:00Z"), act(1, "2020-01-01T07:00:00Z")]
    assert [a["id"] for a in strava.since(acts, "2025-06-01")] == [3, 2] and len(list(strava.since(acts, None))) == 3


def test_the_year_walk_stops_at_the_limits_year(monkeypatch):
    years = []
    monkeypatch.setattr(strava, "_fetch_year", lambda token, year: years.append(year) or [])
    strava.sync_history("tok", 2026, 99, budget=99, not_before="2023-05-01")
    assert years == [2025, 2024, 2023]


def test_the_limit_hides_older_days_and_older_activities_are_not_archived(monkeypatch):
    history.save_year(2024, [act(20, "2024-05-01T07:00:00Z")], {"2024-05-01": {"count": 1}}, "x")
    history.save_year(2025, [act(30, "2025-05-01T07:00:00Z")], {"2025-05-01": {"count": 1}}, "x")
    ride = {"id": 10, "start_date_local": "2026-08-01T07:00:00Z", "distance": 30000, "total_elevation_gain": 100,
            "name": "R", "sport_type": "Ride", "moving_time": 3600}
    calls = []
    def fake(url, token=None, **kw):
        if "/streams" in url:
            calls.append(int(url.split("/activities/")[1].split("/")[0]))
            return streams_with_gps(20)
        return [ride] if "athlete/activities" in url else {}
    monkeypatch.setattr(strava, "request_json", fake)
    monkeypatch.setattr(strava.streams, "request_json", fake)

    class Tokens:
        def access_token(self):
            return "tok"
    out = strava.fetch(Tokens(), now=datetime(2026, 9, 19), backfill=5, optional=True, history_from="2025-01-01")
    assert "2024-05-01" not in out["days"] and "2025-05-01" in out["days"]
    assert calls == [10, 30]                                                   # 20 is older than the limit
    assert out["archive"]["total"] == 2


# ---- the window's status

def test_the_window_status_has_everything_it_shows(data):
    status = manage.status()
    assert status["history"]["activities"] == 3 and status["history"]["stored"] == 2
    assert status["history"]["oldest_day"] == "2025-05-01" and status["history"]["limit"] is None
    assert len(status["fetch_days"]) == 14 and "daily_used" in status["budget"]
    e = status["export"]
    assert e["path"].endswith("lapbar.duckdb") and e["continuous"] is False and e["install"] == export.INSTALL_COMMANDS
    assert [t["table"] for t in status["schema"]][:3] == ["activities", "routes", "route_cells"]
    assert status["relationships"] and all({"link", "about"} <= set(r) for r in status["relationships"])
    assert status["examples"] and all("sql" in x for x in status["examples"])
    assert [n["title"] for n in status["notes"]] == ["Indexes are left to you", "Adding an R-tree yourself"]


def test_the_window_starts_its_own_quickshell_process(monkeypatch):
    seen = {}
    class Proc:
        pid = 5
    monkeypatch.setattr(manage.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, env=kw["env"]) or Proc())
    monkeypatch.setattr(manage.charts, "float_window", lambda pid, **kw: None)
    assert manage.open_window({"LAPBAR_FG": "#fff"}) == 5
    assert os.path.basename(seen["cmd"][0]) == "quickshell" and seen["cmd"][1] == "-p" and seen["cmd"][2].endswith("/manage")
    assert seen["env"]["LAPBAR_FG"] == "#fff" and seen["env"]["LAPBAR_BIN"].endswith("bin/lapbar")


def test_reset_removes_preferences_too():
    prefs.update(export_continuous=True)
    from lapbar import setup
    assert "preferences" in setup.forget(out=lambda *_: None) and prefs.load()["export_continuous"] is False



# ---- real geometry (DuckDB's spatial extension) is downloaded only when asked for

class FakeCon:
    """Answers `LOAD spatial` like a DuckDB that does not have the extension, and records what was run."""
    def __init__(self, installable=True):
        self.ran, self.installed, self.installable = [], False, installable

    def execute(self, sql, *a):
        self.ran.append(sql)
        if sql == "INSTALL spatial":
            if not self.installable:
                raise RuntimeError("IO Error: Failed to download extension: no network\nmore")
            self.installed = True
        if sql == "LOAD spatial" and not self.installed:
            raise RuntimeError("extension not found")
        return self

    def fetchall(self):
        return [(0, "activity_id")]


def test_the_spatial_extension_is_never_downloaded_unless_asked():
    con = FakeCon()
    assert export._add_geometry(con, install=False) == (False, None)
    assert "INSTALL spatial" not in con.ran


def test_asking_for_it_installs_it_once_and_adds_the_geometry():
    con = FakeCon()
    assert export._add_geometry(con, install=True) == (True, None)
    assert con.ran[:3] == ["LOAD spatial", "INSTALL spatial", "LOAD spatial"]
    assert any(s.startswith("ALTER TABLE routes ADD COLUMN geom GEOMETRY") for s in con.ran)
    assert any("ST_GeomFromText(wkt)" in s for s in con.ran)


def test_a_failed_download_does_not_fail_the_export_and_says_why():
    added, problem = export._add_geometry(FakeCon(installable=False), install=True)
    assert added is False and "spatial extension" in problem and "wkt column" in problem and "online" in problem


@needs_duckdb
def test_without_the_extension_the_export_still_works_and_reports_it(data, tmp_path, monkeypatch):
    monkeypatch.setattr(export, "_add_geometry", lambda con, install=False: (False, "offline"))
    result = export.sync(tmp_path / "db" / "g.duckdb", spatial=True)
    assert result["spatial"] is False and result["spatial_problem"] == "offline"


@pytest.mark.skipif(not export.spatial_installed(), reason="DuckDB's spatial extension is not installed on this machine")
def test_with_the_extension_routes_get_real_geometry(data, tmp_path):
    path = tmp_path / "db" / "s.duckdb"
    assert export.sync(path)["spatial"] is True
    con = duckdb.connect(str(path))
    try:
        con.execute("LOAD spatial")
        assert con.execute("SELECT count(*) FROM routes WHERE geom IS NOT NULL").fetchone()[0] == 1
        assert con.execute("SELECT ST_GeometryType(geom) FROM routes").fetchone()[0] == "LINESTRING"
        assert con.execute("SELECT ST_Length(geom) > 0 FROM routes").fetchone()[0] is True
    finally:
        con.close()


def haversine_metres(points):
    import math
    total = 0.0
    for (la1, lo1), (la2, lo2) in zip(points, points[1:]):
        p1, p2 = math.radians(la1), math.radians(la2)
        h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
        total += 2 * 6371008.8 * math.asin(math.sqrt(h))
    return total


@pytest.mark.skipif(not export.spatial_installed(), reason="DuckDB's spatial extension is not installed on this machine")
def test_the_documented_length_example_gives_about_the_right_distance(data, tmp_path):
    """DuckDB's spheroid functions want lat/lon while geometries are lon/lat: without the flip the answer is far off."""
    path = tmp_path / "db" / "len.duckdb"
    export.sync(path)
    truth = haversine_metres(streams_with_gps()["latlng"]["data"])
    example = dict(export.EXAMPLES)["Route length in metres (needs the spatial extension)"]
    con = duckdb.connect(str(path))
    try:
        con.execute("LOAD spatial")
        metres = con.execute(example.split("INSTALL spatial; LOAD spatial;\n", 1)[1].replace("LIMIT 10", "LIMIT 1")).fetchone()[1]
        unflipped = con.execute("SELECT ST_Length_Spheroid(geom) FROM routes").fetchone()[0]
    finally:
        con.close()
    assert metres == pytest.approx(truth, rel=0.02)
    assert unflipped != pytest.approx(truth, rel=0.1)                    # the trap the example avoids


def test_the_power_example_uses_time_not_row_counts_because_paused_rides_have_gaps():
    sql = dict(export.EXAMPLES)["Best 20-minute power of each ride"]
    assert "RANGE BETWEEN 1199 PRECEDING" in sql and "ROWS BETWEEN" not in sql.split("\n", 1)[1]


@needs_duckdb
def test_the_power_example_runs_and_a_pause_does_not_stretch_the_window(data, tmp_path):
    path = tmp_path / "db" / "pw.duckdb"
    paused = streams_with_gps(1000)
    paused["time"]["data"] = [i if i < 500 else i + 3000 for i in range(1000)]     # 50 minutes stopped in the middle
    paused["watts"]["data"] = [100] * 500 + [300] * 500
    raw.store(act(1, "2025-05-01T07:00:00Z"), paused)
    export.sync(path)
    sql = dict(export.EXAMPLES)["Best 20-minute power of each ride"]
    best = dict(rows(path, sql.split("\n", 1)[1].replace("LIMIT 10", "")))
    assert best[1] == 300          # the 20-minute window after the pause is all 300 W, and never mixes in the pre-pause 100 W
    assert min(rows(path, "SELECT t FROM samples WHERE activity_id = 1 ORDER BY t"))[0] == 0


@needs_duckdb
def test_samples_are_loaded_in_activity_id_order_so_blocks_stay_clustered(data, tmp_path):
    raw.store(act(0, "2026-12-31T07:00:00Z", name="newest but lowest id"), streams_with_gps(30))
    history.save_year(2025, [act(2, "2025-06-01T07:00:00Z"), act(1, "2025-05-01T07:00:00Z"), act(0, "2026-12-31T07:00:00Z")], {}, "x")
    path = tmp_path / "db" / "order.duckdb"
    export.sync(path)
    ids = [r[0] for r in rows(path, "SELECT activity_id FROM samples ORDER BY rowid")]
    assert ids == sorted(ids)


# ---- the folder chooser and the date picker in the window

def test_the_folder_chooser_uses_zenity_and_returns_the_folder(monkeypatch):
    from lapbar import pick
    seen = {}
    monkeypatch.setattr(pick.shutil, "which", lambda name: "/usr/bin/" + name if name == "zenity" else None)
    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        class Done:
            returncode, stdout = 0, "/home/me/Documents/data/\n"
        return Done()
    monkeypatch.setattr(pick.subprocess, "run", fake_run)
    assert pick.choose_folder("~", "Folder for the DuckDB file") == "/home/me/Documents/data"
    assert os.path.basename(seen["cmd"][0]) == "zenity" and seen["cmd"][1:3] == ["--file-selection", "--directory"]
    assert "Folder for the DuckDB file" in seen["cmd"]


def test_a_cancelled_dialog_is_not_an_error_and_a_missing_start_folder_falls_back(monkeypatch, tmp_path):
    from lapbar import pick
    monkeypatch.setattr(pick.shutil, "which", lambda name: "/usr/bin/zenity" if name == "zenity" else None)
    seen = {}
    def cancelled(cmd, **kw):
        seen["cmd"] = cmd
        class Done:
            returncode, stdout = 1, ""
        return Done()
    monkeypatch.setattr(pick.subprocess, "run", cancelled)
    assert pick.choose_folder(str(tmp_path / "gone" / "deeper")) is None
    assert seen["cmd"][seen["cmd"].index("--filename") + 1] == str(tmp_path) + "/"          # opened at the nearest folder that exists


def test_without_any_chooser_the_error_says_what_to_install(monkeypatch, capsys):
    from lapbar import pick
    monkeypatch.setattr(pick.shutil, "which", lambda name: None)
    with pytest.raises(pick.NoChooser, match="zenity"):
        pick.choose_folder()
    with pytest.raises(SystemExit):
        cli.main(["pick-folder"])
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "no_chooser" and "zenity" in out["message"]


def test_the_pick_folder_command_prints_the_choice_or_cancelled(monkeypatch, capsys):
    from lapbar import pick
    def run(*argv):
        with pytest.raises(SystemExit) as done:
            cli.main(["pick-folder", *argv])
        assert done.value.code == 0
        return json.loads(capsys.readouterr().out)
    monkeypatch.setattr(pick, "choose_folder", lambda start, title: "/data/lapbar")
    assert run("--start", "/data") == {"path": "/data/lapbar"}
    monkeypatch.setattr(pick, "choose_folder", lambda start, title: None)
    assert run() == {"cancelled": True}


def test_the_status_gives_the_picker_its_range_and_the_export_its_folder_and_file_name(data):
    status = manage.status()
    assert status["history"]["first_day"] == "2025-05-01"
    assert status["export"]["dir"] == str(config.data_dir()) and status["export"]["filename"] == "lapbar.duckdb"
    prefs.update(history_from="2026-01-01", export_path="~/elsewhere/mine.duckdb")
    status = manage.status()
    assert status["history"]["first_day"] == "2025-05-01"                 # the earliest day Strava has, whatever the limit
    assert status["export"]["filename"] == "mine.duckdb" and status["export"]["dir"].endswith("/elsewhere")
