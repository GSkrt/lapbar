"""The complete, unmodified time series of every activity, kept on this computer.

Strava's second-by-second streams (time, distance, GPS position, altitude, speed, heart rate, power, cadence,
temperature, grade, moving) cost one request per activity. The charts only need a shrunken copy, but throwing the
rest away would mean downloading everything again the day you want the raw data (a power curve, GPS overlays of
your rides, a database). So the full answer is kept, compressed:

  <data dir>/raw/<year>/<activity id>.json.gz   the streams as Strava sent them, with a small header
  <data dir>/raw/<year>/<activity id>.empty     Strava has none for it (a manual entry): remembered, never asked again

The data dir is ~/.local/share/lapbar by default (see config.data_dir). It is yours: nothing here is a cache.
"""
import gzip
import json
import os
from datetime import datetime, timezone

from . import config, fetchlog, stravaapi

DATA_VERSION = 1
KEYS = "time,distance,latlng,altitude,velocity_smooth,heartrate,cadence,watts,temp,grade_smooth,moving"
URL = stravaapi.url("activity_streams") + "?keys=" + KEYS + "&key_by_type=true"


def _dir():
    return config.data_dir() / "raw"


def _year(activity: dict) -> str:
    return str(activity.get("start") or "")[:4] or "unknown"


def path_for(activity: dict):
    return _dir() / _year(activity) / f"{activity['id']}.json.gz"


def _empty_path(activity: dict):
    return _dir() / _year(activity) / f"{activity['id']}.empty"


def _write(path, payload: bytes) -> None:
    config.private_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(payload)
    tmp.replace(path)


def store(activity: dict, streams: dict) -> None:
    """Keep what Strava sent. An empty answer is remembered too, so it is not requested again."""
    if not streams:
        _write(_empty_path(activity), b"")
        fetchlog.add_download()
        return
    doc = {"v": DATA_VERSION, "id": activity["id"], "sport": activity.get("sport"), "start": activity.get("start"),
           "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "streams": streams}
    _write(path_for(activity), gzip.compress(json.dumps(doc, separators=(",", ":")).encode(), 6))
    _empty_path(activity).unlink(missing_ok=True)
    fetchlog.add_download()


def load(activity: dict) -> dict | None:
    """The stored streams, {} if Strava has none for this activity, None if it has not been downloaded."""
    try:
        doc = json.loads(gzip.decompress(path_for(activity).read_bytes()))
    except (FileNotFoundError, OSError, json.JSONDecodeError, EOFError):
        return {} if _empty_path(activity).exists() else None
    return doc.get("streams", {}) if doc.get("v") == DATA_VERSION else None


def _ids(suffix: str) -> set[int]:
    out: set[int] = set()
    try:
        years = list(_dir().iterdir())
    except FileNotFoundError:
        return out
    for year in years:
        if year.is_dir():
            out.update(int(p.name[: -len(suffix)]) for p in year.iterdir()
                       if p.name.endswith(suffix) and p.name[: -len(suffix)].isdigit())
    return out


def archived_ids() -> set[int]:
    """Activities whose complete time series is stored here."""
    return _ids(".json.gz")


def known_ids() -> set[int]:
    """Activities already asked about: stored, or known to have nothing."""
    return _ids(".json.gz") | _ids(".empty")
