"""Per-activity time series ("streams"): fetched once, stored on disk, never downloaded twice.

Strava returns one sample per second. We resample onto an even grid of distance (the x axis of the
charts is kilometres), average the samples in each cell, and store the result as a small JSON file
per activity under ~/.cache/lapbar/streams/. Activities without distance fall back to elapsed time.
"""
import json
import os

from . import config, raw as raw_archive, sports
from .http import HttpError, request_json

STREAMS_URL = raw_archive.URL      # everything at once, GPS included: the full answer is kept (see raw.py)
# Bump when the stored format or the resampling changes: older files are then downloaded once more.
DATA_VERSION = 2
MAX_POINTS = 1500
# Slowest pace worth drawing (seconds per unit); slower than this means stopped and is left as a gap, so
# standing still does not stretch the axis to 50 min/km.
SLOWEST_PACE = {"km": 1800, "100m": 600, "500m": 1800}
PROFILE_POINTS = 100

# metres per pace unit, matching sports.pace()
_PACE_UNITS = {"km": 1000, "100m": 100, "500m": 500}


def _dir():
    return config.cache_path().parent / "streams"


def path_for(activity_id: int):
    return _dir() / f"{activity_id}.json"


def cached(activity_id: int) -> dict | None:
    """The stored data, or None if there is none or it was written by an older version."""
    try:
        data = json.loads(path_for(activity_id).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if data.get("empty") or data.get("v") == DATA_VERSION else None


def _store(activity_id: int, data: dict) -> None:
    config.private_dir(_dir())
    path = path_for(activity_id)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    tmp.replace(path)


def _cells(x: list[float], n: int) -> list[int]:
    """Cell index of each sample on an even grid of `n` cells over the x range."""
    lo, hi = x[0], x[-1]
    span = (hi - lo) or 1.0
    return [min(n - 1, int((v - lo) / span * n)) for v in x]


def _mean_by_cell(values: list, cells: list[int], n: int, decimals: int) -> list:
    """Average of the samples in each cell.

    A cell that holds no sample at all (you moved further than one cell between two readings) is filled by
    interpolating between its neighbours, so fast stretches are drawn as a line and not a row of dots.
    A cell whose samples all have no value (sensor dropout) stays a gap.
    """
    sums, counts, samples = [0.0] * n, [0] * n, [0] * n
    for v, c in zip(values, cells):
        samples[c] += 1
        if v is not None:
            sums[c] += v
            counts[c] += 1
    out = [s_ / k if k else None for s_, k in zip(sums, counts)]
    filled = [i for i in range(n) if samples[i]]
    for a, b in zip(filled, filled[1:]):                 # neighbouring cells that do have samples
        if b - a > 1 and out[a] is not None and out[b] is not None:
            for i in range(a + 1, b):
                out[i] = out[a] + (out[b] - out[a]) * (i - a) / (b - a)
    return [round(v, decimals) if v is not None else None for v in out]


def build(activity: dict, raw: dict) -> dict:
    """Turn Strava's stream arrays into the chart data. `activity` is a listed/latest activity dict."""
    def data(key):
        entry = raw.get(key)
        return entry.get("data") if isinstance(entry, dict) else None

    distance, time = data("distance"), data("time")
    family = activity.get("family") or sports.family(activity.get("sport"))
    if distance and len(distance) > 1 and distance[-1] > 0:
        x_raw, x_meta = [d / 1000 for d in distance], {"key": "distance", "label": "Distance", "unit": "km"}
    elif time and len(time) > 1:
        x_raw, x_meta = [t / 60 for t in time], {"key": "time", "label": "Time", "unit": "min"}
    else:
        return {"empty": True}

    n = min(len(x_raw), MAX_POINTS)
    cells = _cells(x_raw, n)
    lo, hi = x_raw[0], x_raw[-1]
    step = (hi - lo) / n
    x = [round(lo + step * (i + 0.5), 4) for i in range(n)]  # cell centres

    def mean(key, decimals=1):
        values = data(key)
        return _mean_by_cell(values, cells, n, decimals) if values and len(values) == len(x_raw) else None

    series = []

    def add(key, label, unit, values, decimals, fmt="number"):
        if values and any(v is not None for v in values):
            series.append({"key": key, "label": label, "unit": unit, "decimals": decimals,
                           "format": fmt, "values": values})

    add("altitude", "Elevation", "m", mean("altitude", 1), 0)

    speed = mean("velocity_smooth", 3)  # m/s
    pace_unit = sports.pace(activity.get("sport"), 1.0)[1]
    if speed and pace_unit:
        per, slowest = _PACE_UNITS[pace_unit], SLOWEST_PACE[pace_unit]
        paces = [per / v if v else None for v in speed]
        add("pace", "Pace", f"min/{pace_unit}", [round(p) if p is not None and p <= slowest else None for p in paces], 0, "pace")
    elif speed:
        add("speed", "Speed", "km/h", [round(v * 3.6, 1) if v is not None else None for v in speed], 1)

    add("heartrate", "Heart rate", "bpm", mean("heartrate", 0), 0)
    add("watts", "Power", "W", mean("watts", 0), 0)
    cadence = mean("cadence", 0)
    if cadence:
        foot = family in sports.FOOT_FAMILIES  # Strava stores foot cadence per leg; show steps per minute
        add("cadence", "Cadence", "spm" if foot else "rpm",
            [(round(v * 2) if foot else v) if v is not None else None for v in cadence], 0)
    add("temp", "Temperature", "°C", mean("temp", 0), 0)
    add("grade", "Grade", "%", mean("grade_smooth", 1), 1)

    if not series:
        return {"empty": True}
    return {
        "v": DATA_VERSION, "id": activity["id"], "name": activity.get("name"), "sport": activity.get("sport"), "family": family,
        "start": activity.get("start"), "x": {**x_meta, "values": x},
        "distance_km": round(hi if x_meta["key"] == "distance" else activity.get("distance_km", 0), 2),
        "points": n, "series": series,
    }


def _download(token: str, activity: dict) -> dict:
    """Ask Strava once, keep the complete answer in the raw archive, and return it ({} if there are no streams)."""
    try:
        answer = request_json(STREAMS_URL.format(id=activity["id"]), token=token)
    except HttpError as e:
        if e.status != 404:
            raise
        answer = {}
    answer = answer if isinstance(answer, dict) else {}
    raw_archive.store(activity, answer)
    return answer


def _chart_data(activity: dict, answer: dict) -> dict | None:
    result = build(activity, answer) if answer else {"empty": True}
    _store(activity["id"], result)
    return None if result.get("empty") else result


def get(token: str, activity: dict, refresh: bool = False) -> dict | None:
    """Chart data for an activity: from disk if we have it, otherwise made from the raw archive, otherwise fetched once.

    Returns None when Strava has no streams for it (manual entries); that is remembered too.
    """
    if not refresh:
        hit = cached(activity["id"])
        if hit is not None:
            if hit.get("empty"):
                return None
            if activity.get("name") and hit.get("name") != activity["name"]:      # renamed on Strava since it was stored
                hit["name"] = activity["name"]
                _store(activity["id"], hit)
            return hit
        archived = raw_archive.load(activity)      # the archive has it: rebuild the charts without a request
        if archived is not None:
            return _chart_data(activity, archived)
    return _chart_data(activity, _download(token, activity))


def archive(token: str, activity: dict) -> bool:
    """Make sure the raw archive has this activity (one request if not). True if a download was needed."""
    if activity["id"] in raw_archive.known_ids():
        return False
    _chart_data(activity, _download(token, activity))
    return True


def backfill(token: str, activities, limit: int) -> int:
    """Archive up to `limit` activities that are not stored yet, in the order given (newest first)."""
    done = 0
    have = raw_archive.known_ids()
    for a in activities:
        if done >= limit:
            break
        if a["id"] in have:
            continue
        try:
            archive(token, a)
        except HttpError:
            break  # rate limited or unreachable: try again on the next refresh
        done += 1
    return done


def elevation_profile(data: dict | None) -> list[float]:
    """100 elevation values evenly spaced along the ride, for the popup (derived from the cached data)."""
    if not data:
        return []
    alt = next((s["values"] for s in data["series"] if s["key"] == "altitude"), None)
    if not alt:
        return []
    first = next((v for v in alt if v is not None), None)
    filled, last = [], first
    for v in alt:
        last = v if v is not None else last
        filled.append(last)
    if len(filled) <= PROFILE_POINTS:
        return filled
    return [round(filled[round(i * (len(filled) - 1) / (PROFILE_POINTS - 1))], 1) for i in range(PROFILE_POINTS)]
