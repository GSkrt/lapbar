"""Older years of activities, for the calendar.

The refresh only looks at this year (and a little before it). Earlier years are downloaded once, one request per
200 activities of that year, and stored under the cache folder:

  history/index.json   {"years": {"2024": {"fetched": ..., "count": 88, "days": {date: totals}}}, "complete": bool}
                       small: the per-day totals of every stored year, which is all the calendar needs to draw itself
  history/<year>.json  the year's activities in the same compact form the popup uses for this year's, read only
                       when you page the calendar to that year or open a ride from it

A stored year is not downloaded again (edits or deletions on Strava do not reach it) unless you ask:
`lapbar history --sync --refresh`.
"""
import json
import os

from . import config

DATA_VERSION = 1


def _dir():
    return config.cache_path().parent / "history"


def _write(path, data) -> None:
    config.private_dir(path.parent)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    tmp.replace(path)


def _read(path):
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("v") == DATA_VERSION else None


def index() -> dict:
    return _read(_dir() / "index.json") or {"v": DATA_VERSION, "years": {}, "complete": False}


def save_year(year: int, activities: list[dict], days: dict, fetched: str) -> None:
    idx = index()
    idx["years"][str(year)] = {"fetched": fetched, "count": len(activities), "days": days}
    _write(_dir() / f"{year}.json", {"v": DATA_VERSION, "year": year, "activities": activities})
    _write(_dir() / "index.json", idx)


def set_complete(complete: bool) -> None:
    idx = index()
    if idx.get("complete") != complete:
        idx["complete"] = complete
        _write(_dir() / "index.json", idx)


def year_activities(year: int) -> list[dict]:
    data = _read(_dir() / f"{year}.json")
    return data["activities"] if data else []


def find(activity_id: int) -> dict | None:
    """An activity from any stored year (newest year first), or None."""
    for year in sorted((int(y) for y, v in index()["years"].items() if v.get("count")), reverse=True):
        for a in year_activities(year):
            if a.get("id") == activity_id:
                return a
    return None


def merged_days() -> dict:
    """The per-day totals of every stored year in one map (dates are unique across years)."""
    out: dict = {}
    for entry in index()["years"].values():
        out.update(entry.get("days", {}))
    return dict(sorted(out.items()))


def summary(days: dict | None = None) -> dict:
    """What the widget needs to know about the stored years."""
    idx = index()
    days = merged_days() if days is None else days
    return {"years": sorted((int(y) for y, v in idx["years"].items() if v.get("count")), reverse=True),
            "from": next(iter(days), None), "complete": bool(idx.get("complete"))}
