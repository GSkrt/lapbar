"""Export everything LapBar has stored into a DuckDB database, for your own analysis (SQL, Python, R, notebooks).

The database is built from the files in the data folder (the raw archive, older years, records and kudos names) and
this year's cache. LapBar itself never reads it back, so it can be opened, queried, copied or deleted freely.

  sync(path)                 create or update: the small tables are rewritten, `samples` gets only the activities
                             that are not in it yet (so running it again after a refresh is quick)
  sync(path, rebuild=True)   build a fresh file next to it and swap it in

DuckDB is optional and is not installed with LapBar. `import duckdb` is done only when needed; if it is missing
the error says what to install (see INSTALL_COMMANDS). `SCHEMA` below is the single description of the tables:
it creates them and it is what the data window shows next to the export settings.
"""
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config, details, history, prefs, raw

INSTALL_COMMANDS = ["omarchy pkg add python-duckdb", "sudo pacman -S python-duckdb"]
INSTALL_HINT = ("DuckDB is not installed. Install its Python package, then try again:\n"
                "    " + INSTALL_COMMANDS[0] + "\n  or\n    " + INSTALL_COMMANDS[1] + "\n"
                "(add the `duckdb` package as well if you want the `duckdb` command line to query the file.)")


class DuckdbMissing(RuntimeError):
    """The duckdb Python package is not installed."""


class ExportBusy(RuntimeError):
    """The database file is open in another program, or another export is running."""


# (column, type, description)
SCHEMA = [
    {"table": "activities", "about": "One row per activity, the same details Strava's activity list has.", "columns": [
        ("id", "BIGINT", "Strava's activity id (primary key)"),
        ("start_local", "TIMESTAMP", "start on the wall clock where it was recorded (no time zone)"),
        ("year", "INTEGER", "calendar year of start_local"),
        ("sport", "VARCHAR", "Strava's sport type: Ride, Run, Swim, Walk, ..."),
        ("family", "VARCHAR", "LapBar's grouping: ride, run, walk, swim, paddle, winter, skate, gym, other"),
        ("name", "VARCHAR", "the activity's title"),
        ("distance_km", "DOUBLE", "distance in kilometres"),
        ("moving_time_s", "INTEGER", "moving time in seconds"),
        ("elapsed_time_s", "INTEGER", "elapsed time in seconds, stops included"),
        ("elevation_m", "INTEGER", "total climb in metres"),
        ("avg_speed_kmh", "DOUBLE", "average speed"),
        ("max_speed_kmh", "DOUBLE", "top speed"),
        ("avg_heartrate", "INTEGER", "average heart rate, bpm"),
        ("max_heartrate", "INTEGER", "highest heart rate, bpm"),
        ("avg_cadence", "DOUBLE", "average cadence (rpm cycling; steps per leg per minute for foot sports)"),
        ("avg_watts", "DOUBLE", "average power, W"),
        ("max_watts", "DOUBLE", "highest power, W"),
        ("weighted_watts", "DOUBLE", "Strava's weighted (normalised) power, W"),
        ("kilojoules", "DOUBLE", "work done, kJ"),
        ("suffer_score", "DOUBLE", "Strava's Relative Effort"),
        ("avg_temp_c", "DOUBLE", "average temperature"),
        ("device", "VARCHAR", "recording device, as Strava names it"),
        ("indoor", "BOOLEAN", "recorded on a trainer"),
        ("commute", "BOOLEAN", "marked as a commute"),
        ("kudos", "INTEGER", "kudos count when last fetched"),
        ("comments", "INTEGER", "comment count"),
        ("prs", "INTEGER", "personal records (1st fastest) count, as Strava counts them"),
        ("achievements", "INTEGER", "achievement count, as Strava counts them"),
        ("url", "VARCHAR", "link to the activity on Strava"),
        ("has_samples", "BOOLEAN", "true when `samples` holds its second-by-second data"),
        ("has_route", "BOOLEAN", "true when `routes` has its GPS line (false indoors and for swims without GPS)"),
    ]},
    {"table": "routes", "about": "The bridge to the spatial data: one row per activity with GPS, joined to activities on the id.", "columns": [
        ("activity_id", "BIGINT", "-> activities.id (primary key)"),
        ("points", "INTEGER", "how many GPS points the activity has"),
        ("length_m", "DOUBLE", "distance covered, metres"),
        ("start_lat", "DOUBLE", "where it started"),
        ("start_lon", "DOUBLE", ""),
        ("end_lat", "DOUBLE", "where it ended"),
        ("end_lon", "DOUBLE", ""),
        ("min_lat", "DOUBLE", "bounding box: south edge"),
        ("max_lat", "DOUBLE", "north edge"),
        ("min_lon", "DOUBLE", "west edge"),
        ("max_lon", "DOUBLE", "east edge"),
        ("wkt", "VARCHAR", "the route as a WKT LINESTRING(lon lat, ...), thinned to at most 400 points; works with ST_GeomFromText"),
        ("geom", "GEOMETRY", "optional: the same line as a geometry (x = longitude, y = latitude, WGS 84 / EPSG:4326), only when DuckDB's spatial extension is installed"),
    ]},
    {"table": "route_cells", "about": "Each route as the map cells (about 100 m) it passes through, so overlaps are a plain join, no extension needed.", "columns": [
        ("activity_id", "BIGINT", "-> routes.activity_id"),
        ("cell_x", "INTEGER", "floor(lon * 1000)"),
        ("cell_y", "INTEGER", "floor(lat * 1000)"),
        ("cell_lat", "DOUBLE", "latitude of the cell's centre"),
        ("cell_lon", "DOUBLE", "longitude of the cell's centre"),
        ("seconds", "INTEGER", "seconds spent in the cell (samples inside it)"),
    ]},
    {"table": "samples", "about": "The complete second-by-second data of every archived activity, GPS included. The big table.", "columns": [
        ("activity_id", "BIGINT", "-> activities.id"),
        ("t", "INTEGER", "seconds since the activity started"),
        ("distance_m", "DOUBLE", "distance so far, metres"),
        ("lat", "DOUBLE", "latitude, degrees, WGS 84 (NULL indoors or without GPS)"),
        ("lon", "DOUBLE", "longitude, degrees, WGS 84"),
        ("altitude_m", "DOUBLE", "altitude, metres"),
        ("speed_ms", "DOUBLE", "smoothed speed, metres per second"),
        ("heartrate", "INTEGER", "bpm"),
        ("cadence", "INTEGER", "rpm (cycling) or steps per leg per minute"),
        ("watts", "INTEGER", "power, W"),
        ("temp_c", "INTEGER", "temperature, degrees C"),
        ("grade_pct", "DOUBLE", "smoothed road grade, percent"),
        ("moving", "BOOLEAN", "false while stopped"),
    ]},
    {"table": "days", "about": "One row per day with activity: what the calendar shows.", "columns": [
        ("date", "DATE", "the day (primary key)"),
        ("activities", "INTEGER", "how many activities that day"),
        ("distance_km", "DOUBLE", "total distance"),
        ("moving_time_s", "INTEGER", "total moving time"),
        ("elevation_m", "INTEGER", "total climb"),
        ("families", "VARCHAR", "sport groups that day, comma separated"),
    ]},
    {"table": "records", "about": "Personal records and top-10 places on segments (and a run's best efforts), by name.", "columns": [
        ("activity_id", "BIGINT", "-> activities.id"),
        ("kind", "VARCHAR", "'pr' (your own 1st, 2nd or 3rd fastest) or 'kom' (top 10 among everyone; rank 1 is the KOM/QOM)"),
        ("rank", "INTEGER", "the place"),
        ("name", "VARCHAR", "the segment's or effort's name"),
        ("seconds", "INTEGER", "time it was achieved in"),
        ("distance_m", "INTEGER", "its length in metres"),
        ("source", "VARCHAR", "'segment' or 'best_effort'"),
    ]},
    {"table": "kudos", "about": "Who gave kudos, as Strava names people (first name and last initial).", "columns": [
        ("activity_id", "BIGINT", "-> activities.id"),
        ("giver", "VARCHAR", "for example 'Jane D.'"),
    ]},
    {"table": "meta", "about": "About this file.", "columns": [
        ("key", "VARCHAR", "exported_at, lapbar_schema, sample_rows (primary key)"),
        ("value", "VARCHAR", ""),
    ]},
]
SCHEMA_VERSION = "2"

RELATIONSHIPS = [
    {"link": "activities.id  <-  routes.activity_id",
     "about": "one summary line per activity with GPS: the bridge from ride data to spatial data"},
    {"link": "routes.activity_id  <-  route_cells.activity_id",
     "about": "the map cells (about 100 m) that line passes through: overlaps are a join on cell_x, cell_y"},
    {"link": "activities.id  <-  samples.activity_id",
     "about": "every second of the activity, with lat and lon on each row"},
    {"link": "activities.id  <-  records.activity_id, kudos.activity_id",
     "about": "records by name, and who gave kudos"},
    {"link": "coordinates",
     "about": "WGS 84 degrees, as Strava gives them (it sends [lat, lon]). Geometries are stored lon/lat (x = lon, y = lat); "
              "DuckDB's *_Spheroid functions expect lat/lon, so wrap geometries in ST_FlipCoordinates for them"},
]

EXAMPLES = [
    ("Kilometres per week", "SELECT date_trunc('week', start_local) AS week, round(sum(distance_km)) AS km\n"
                            "FROM activities WHERE family = 'ride' GROUP BY 1 ORDER BY 1 DESC LIMIT 12;"),
    ("Best 20-minute power of each ride", "SELECT activity_id, round(max(w)) AS best_20min_w FROM (\n"
                                          "  SELECT activity_id, avg(watts) OVER (PARTITION BY activity_id ORDER BY t\n"
                                          "         ROWS BETWEEN 1199 PRECEDING AND CURRENT ROW) AS w\n"
                                          "  FROM samples WHERE watts IS NOT NULL) GROUP BY 1 ORDER BY 2 DESC LIMIT 10;"),
    ("Rides that overlap the most", "SELECT x.name, y.name, count(*) AS shared_cells\n"
                                    "FROM route_cells a JOIN route_cells b USING (cell_x, cell_y)\n"
                                    "JOIN activities x ON x.id = a.activity_id JOIN activities y ON y.id = b.activity_id\n"
                                    "WHERE a.activity_id < b.activity_id GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10;"),
    ("Rides that start near a place", "SELECT a.start_local, a.name, a.distance_km\n"
                                      "FROM routes r JOIN activities a ON a.id = r.activity_id\n"
                                      "WHERE r.start_lat BETWEEN 46.04 AND 46.06 AND r.start_lon BETWEEN 14.49 AND 14.52;"),
    ("Route length in metres (needs the spatial extension)",
     "-- geometries are lon/lat (x, y); the spheroid functions want lat/lon, hence ST_FlipCoordinates\n"
     "INSTALL spatial; LOAD spatial;\n"
     "SELECT a.name, round(ST_Length_Spheroid(ST_FlipCoordinates(r.geom))) AS metres\n"
     "FROM routes r JOIN activities a ON a.id = r.activity_id ORDER BY 2 DESC LIMIT 10;"),
]


def _import():
    try:
        import duckdb
    except ImportError as e:
        raise DuckdbMissing(INSTALL_HINT) from e
    return duckdb


def spatial_installed() -> bool:
    """Is DuckDB's spatial extension already on this computer? (Never downloads anything.)"""
    try:
        duckdb = _import()
        con = duckdb.connect()
    except DuckdbMissing:
        return False
    try:
        con.execute("LOAD spatial")
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        con.close()


def availability() -> dict:
    try:
        duckdb = _import()
    except DuckdbMissing:
        return {"available": False, "version": None, "install": INSTALL_COMMANDS, "spatial": False}
    return {"available": True, "version": duckdb.__version__, "install": INSTALL_COMMANDS, "spatial": spatial_installed()}


def ddl() -> list[str]:
    out = []
    for t in SCHEMA:
        cols = []
        for name, typ, _ in t["columns"]:
            primary = " PRIMARY KEY" if (t["table"], name) in (
                ("activities", "id"), ("days", "date"), ("meta", "key"), ("routes", "activity_id")) else ""
            if typ == "GEOMETRY":
                continue                               # added only when the spatial extension is available
            cols.append(f"{name} {typ}{primary}")
        out.append(f"CREATE TABLE IF NOT EXISTS {t['table']} ({', '.join(cols)})")
    return out


# ------------------------------------------------------------------ progress, shared with the data window

def _state_path():
    return config.data_dir() / "export-state.json"


def state() -> dict:
    try:
        data = json.loads(_state_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"running": False}
    if data.get("running") and not _alive(data.get("pid")):
        data["running"] = False                       # the export process died without finishing
        data.setdefault("error", "The export was interrupted.")
    return data


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True
    return True


def _write_state(**fields) -> None:
    config.private_dir(_state_path().parent)
    tmp = _state_path().with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(fields, f, separators=(",", ":"))
    tmp.replace(_state_path())


# ------------------------------------------------------------------ gathering what is stored

def _cache() -> dict:
    try:
        return json.loads(config.cache_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _all_activities(cache: dict) -> list[dict]:
    seen, out = set(), []
    for a in list(cache.get("activities", [])) + list(history.iter_activities()):
        if a.get("id") not in seen:
            seen.add(a["id"])
            out.append(a)
    return out


def _activity_row(a: dict) -> tuple:
    start = str(a.get("start") or "").replace("Z", "") or None
    return (a["id"], start, int(start[:4]) if start else None, a.get("sport"), a.get("family"), a.get("name"),
            a.get("distance_km"), a.get("moving_time_s"), a.get("elapsed_time_s"), a.get("elevation_m"),
            a.get("avg_speed_kmh"), a.get("max_speed_kmh"), a.get("avg_heartrate"), a.get("max_heartrate"),
            a.get("avg_cadence"), a.get("avg_watts"), a.get("max_watts"), a.get("weighted_watts"), a.get("kilojoules"),
            a.get("suffer_score"), a.get("avg_temp_c"), a.get("device"), bool(a.get("indoor")), bool(a.get("commute")),
            a.get("kudos", 0), a.get("comments", 0), a.get("prs", 0), a.get("achievements", 0), a.get("url"), False, False)


def _detail_files() -> list[dict]:
    out = []
    folder = details._dir()
    if folder.is_dir():
        for path in folder.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                out.append({"id": int(path.stem), **data})
            except (json.JSONDecodeError, ValueError):
                continue
    return out


def _sample_lists(streams: dict) -> list:
    """The parallel columns of `samples` for one activity, all the same length."""
    def data(key):
        entry = streams.get(key)
        return entry.get("data") if isinstance(entry, dict) else None

    n = next((len(v) for v in (data("time"), data("distance"), data("altitude"), data("heartrate")) if v), 0)
    if n == 0:
        return []

    def col(key):
        values = data(key)
        return values if values is not None and len(values) == n else [None] * n

    latlng = data("latlng")
    if latlng is not None and len(latlng) == n:
        lat, lon = [p[0] if p else None for p in latlng], [p[1] if p else None for p in latlng]
    else:
        lat = lon = [None] * n
    t = data("time")
    return [t if t is not None and len(t) == n else list(range(n)), col("distance"), lat, lon, col("altitude"),
            col("velocity_smooth"), col("heartrate"), col("cadence"), col("watts"), col("temp"), col("grade_smooth"),
            col("moving")]


def _lit(v) -> str:
    """A value as an SQL literal (for the small tables; strings are quoted, so a title cannot break the statement)."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return "NULL" if (math.isnan(v) or math.isinf(v)) else repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def _replace(con, table: str, rows: list[tuple], chunk: int = 500) -> None:
    con.execute(f"DELETE FROM {table}")
    for i in range(0, len(rows), chunk):
        values = ", ".join("(" + ", ".join(_lit(v) for v in row) + ")" for row in rows[i:i + chunk])
        con.execute(f"INSERT INTO {table} VALUES {values}")


_SAMPLE_COLUMNS = ("{'a': 'BIGINT', 't': 'INTEGER', 'd': 'DOUBLE', 'la': 'DOUBLE', 'lo': 'DOUBLE', 'al': 'DOUBLE', "
                   "'sp': 'DOUBLE', 'hr': 'INTEGER', 'cd': 'INTEGER', 'w': 'INTEGER', 'tp': 'INTEGER', 'gr': 'DOUBLE', "
                   "'mv': 'BOOLEAN'}")


def _insert_samples(con, activity_id: int, lists: list) -> None:
    """Load one activity's samples through a temporary CSV file: DuckDB reads that about 100x faster than
    it takes rows from Python, which matters at millions of rows. The file is private and deleted at once."""
    cols = [["" if v is None else ("true" if v is True else "false" if v is False else v) for v in c] for c in lists]
    fd, name = tempfile.mkstemp(suffix=".csv", prefix="lapbar-samples-")      # mode 600: it holds GPS positions
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(",".join(map(str, (activity_id, *row))) for row in zip(*cols)))
            f.write("\n")
        con.execute(f"INSERT INTO samples SELECT * FROM read_csv('{name}', header=false, columns={_SAMPLE_COLUMNS})")
    finally:
        os.unlink(name)


def _route_row(activity_id: int, lists: list):
    """The `routes` row for one activity from its sample columns, or None without GPS."""
    dist, lat, lon = lists[1], lists[2], lists[3]
    pts = [(la, lo, d) for la, lo, d in zip(lat, lon, dist) if la is not None and lo is not None]
    if len(pts) < 2:
        return None
    step = max(1, -(-len(pts) // 400))                          # ceil: keep at most 400 points in the line
    thin = pts[::step] + ([pts[-1]] if (len(pts) - 1) % step else [])
    wkt = "LINESTRING(" + ", ".join(f"{lo:.5f} {la:.5f}" for la, lo, _ in thin) + ")"
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    last_d = next((p[2] for p in reversed(pts) if p[2] is not None), None)
    return (activity_id, len(pts), last_d, pts[0][0], pts[0][1], pts[-1][0], pts[-1][1],
            min(lats), max(lats), min(lons), max(lons), wkt)


_CELLS_SQL = (
    "INSERT INTO route_cells SELECT activity_id, cx, cy, (cy + 0.5) / 1000.0, (cx + 0.5) / 1000.0, n FROM ("
    "SELECT activity_id, floor(lon * 1000)::INTEGER AS cx, floor(lat * 1000)::INTEGER AS cy, count(*)::INTEGER AS n "
    "FROM samples WHERE activity_id = ? AND lat IS NOT NULL AND lon IS NOT NULL GROUP BY activity_id, cx, cy)")


# ------------------------------------------------------------------ the export

def _add_geometry(con, install: bool = False) -> tuple[bool, str | None]:
    """Give `routes` a real GEOMETRY column: (added, problem).

    The spatial extension is only downloaded (`INSTALL spatial`, once, from DuckDB's servers) when `install` is
    true, which is the user's explicit choice; otherwise it is used only if it is already there."""
    try:
        con.execute("LOAD spatial")
    except Exception:  # noqa: BLE001 - not installed yet
        if not install:
            return False, None
        try:
            con.execute("INSTALL spatial")
            con.execute("LOAD spatial")
        except Exception as e:  # noqa: BLE001 - offline, blocked, or an unsupported platform: the export still succeeds
            return False, f"Could not download DuckDB's spatial extension ({str(e).splitlines()[0][:120]}). " \
                          "The route lines are still in the wkt column; try again when you are online."
    columns = {row[1] for row in con.execute("PRAGMA table_info('routes')").fetchall()}
    if "geom" not in columns:
        con.execute("ALTER TABLE routes ADD COLUMN geom GEOMETRY")
    con.execute("UPDATE routes SET geom = ST_GeomFromText(wkt) WHERE geom IS NULL")
    return True, None


def sync(path=None, rebuild: bool = False, progress=None, spatial: bool | None = None) -> dict:
    """Create or update the database. Returns {"path", "activities", "new_samples_for", "sample_rows", "bytes"}."""
    duckdb = _import()
    path = Path(os.path.expanduser(str(path))) if path else prefs.export_path()
    if path.is_dir():
        raise ValueError(f"{path} is a folder: give a file name, for example {path / 'lapbar.duckdb'}")
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path.with_name(path.name + ".building") if rebuild else path
    if rebuild:
        for leftover in (target, Path(str(target) + ".wal")):
            leftover.unlink(missing_ok=True)

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_state(running=True, pid=os.getpid(), started=started, done=0, total=0, path=str(path))
    try:
        try:
            con = duckdb.connect(str(target))
        except duckdb.IOException as e:
            raise ExportBusy(f"The database file is open in another program (close it and try again): {e}") from e
        try:
            install = prefs.load()["export_spatial"] if spatial is None else spatial
            result = _sync(con, path, rebuild, started, progress, install)
        finally:
            con.close()
        if rebuild:
            os.replace(target, path)
            Path(str(target) + ".wal").unlink(missing_ok=True)
        os.chmod(path, 0o600)                                # it contains your routes
        result["bytes"] = path.stat().st_size
        _write_state(running=False, started=started, finished=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     error=None, **result)
        return result
    except BaseException as e:                              # noqa: BLE001 - record and re-raise
        _write_state(running=False, started=started, error=str(e), path=str(path))
        raise


def _sync(con, path: Path, rebuild: bool, started: str, progress, install_spatial: bool = False) -> dict:
    for statement in ddl():
        con.execute(statement)
    cache = _cache()
    activities = _all_activities(cache)

    con.execute("BEGIN")
    _replace(con, "activities", [_activity_row(a) for a in activities])
    days = cache.get("days", {})
    _replace(con, "days", [(d, v.get("count"), v.get("distance_km"), v.get("moving_time_s"), v.get("elevation_m"),
                            ",".join(v.get("families", []))) for d, v in sorted(days.items())])
    records, kudos_rows = [], []
    known_names = cache.get("kudoers") or {}
    for entry in _detail_files():
        for r in (entry.get("records") or {}).get("items", []):
            records.append((entry["id"], r["kind"], r["rank"], r["name"], r.get("seconds"), r.get("distance_m"), r.get("source")))
        names = known_names.get(str(entry["id"])) or (entry.get("kudoers") or {}).get("names") or []
        kudos_rows += [(entry["id"], n) for n in names]
    for key, names in known_names.items():                 # names seen by the refresh for activities without a details file
        if not any(row[0] == int(key) for row in kudos_rows):
            kudos_rows += [(int(key), n) for n in names]
    _replace(con, "records", records)
    _replace(con, "kudos", kudos_rows)
    con.execute("COMMIT")

    have = {row[0] for row in con.execute("SELECT DISTINCT activity_id FROM samples").fetchall()}
    by_id = {a["id"]: a for a in activities}
    todo = sorted((i for i in raw.archived_ids() if i not in have and i in by_id),
                  key=lambda i: str(by_id[i].get("start") or ""), reverse=True)
    total = len(todo)
    _write_state(running=True, pid=os.getpid(), started=started, done=0, total=total, path=str(path))
    added_rows = 0
    for n, activity_id in enumerate(todo, 1):
        lists = _sample_lists(raw.load(by_id[activity_id]) or {})
        if lists:
            con.execute("BEGIN")                            # one activity is all or nothing, so a stop leaves no half ride
            _insert_samples(con, activity_id, lists)
            route = _route_row(activity_id, lists)
            if route:
                con.execute("INSERT INTO routes (activity_id, points, length_m, start_lat, start_lon, end_lat, end_lon, "
                            "min_lat, max_lat, min_lon, max_lon, wkt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", list(route))
                con.execute(_CELLS_SQL, [activity_id])
            con.execute("COMMIT")
            added_rows += len(lists[0])
        _write_state(running=True, pid=os.getpid(), started=started, done=n, total=total, path=str(path))
        if progress:
            progress(n, total)

    con.execute("UPDATE activities SET has_samples = id IN (SELECT DISTINCT activity_id FROM samples), "
                "has_route = id IN (SELECT activity_id FROM routes)")
    spatial, spatial_problem = _add_geometry(con, install_spatial)
    sample_rows = con.execute("SELECT count(*) FROM samples").fetchone()[0]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute("DELETE FROM meta")
    con.executemany("INSERT INTO meta VALUES (?, ?)", [("exported_at", now), ("lapbar_schema", SCHEMA_VERSION),
                                                        ("sample_rows", str(sample_rows)), ("spatial", str(spatial).lower())])
    con.execute("CHECKPOINT")
    return {"path": str(path), "activities": len(activities), "activities_added_to_samples": total,
            "sample_rows": sample_rows, "rebuilt": rebuild, "spatial": spatial, "spatial_problem": spatial_problem}


# ------------------------------------------------------------------ continuous export

def spawn_if_enabled() -> bool:
    """After a refresh: start an export in the background when continuous export is on and none is running.

    A separate process, because the first run can take minutes and a refresh must stay quick."""
    if not prefs.load()["export_continuous"] or not availability()["available"] or state().get("running"):
        return False
    launcher = Path(__file__).resolve().parent.parent / "bin" / "lapbar"
    subprocess.Popen([sys.executable, "-I", str(launcher), "export"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return True
