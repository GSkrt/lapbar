"""How fetching went, day by day: the activities archived and the requests Strava counted.

A small file in the data folder (fetch-log.json), one entry per UTC day (the day Strava's allowance resets on):
  {"days": {"2026-09-20": {"activities": 39, "requests": 172}}}
`activities` counts full time series downloaded that day; `requests` is the highest daily usage Strava reported
that day. Only the last KEEP days are kept. Bookkeeping never raises.
"""
import gzip
import json
import os
from datetime import datetime, timedelta, timezone

from . import config

KEEP = 400


def _file():
    return config.data_dir() / "fetch-log.json"


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _load() -> dict:
    try:
        return json.loads(_file().read_text()).get("days", {})
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        return {}


def _save(days: dict) -> None:
    config.private_dir(_file().parent)
    keep = dict(sorted(days.items())[-KEEP:])
    tmp = _file().with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"days": keep}, f, separators=(",", ":"))
    tmp.replace(_file())


def add_download(day: str | None = None) -> None:
    try:
        days = _load()
        entry = days.setdefault(day or _today(), {"activities": 0, "requests": 0})
        entry["activities"] += 1
        _save(days)
    except Exception:  # noqa: BLE001
        pass


def note_requests(used: int, day: str | None = None) -> None:
    try:
        days = _load()
        entry = days.setdefault(day or _today(), {"activities": 0, "requests": 0})
        entry["requests"] = max(entry["requests"], int(used))
        _save(days)
    except Exception:  # noqa: BLE001
        pass


def _seed_from_archive() -> dict:
    """The first time: count what the raw archive already holds by the day it was downloaded."""
    from . import raw
    days: dict = {}
    for path in (raw._dir().glob("*/*.json.gz") if raw._dir().is_dir() else []):
        try:
            fetched = json.loads(gzip.decompress(path.read_bytes()))["fetched"][:10]
        except Exception:  # noqa: BLE001
            continue
        days.setdefault(fetched, {"activities": 0, "requests": 0})["activities"] += 1
    return days


def last_days(count: int = 14) -> list[dict]:
    """The last `count` days, oldest first, zeros filled in: [{"date", "activities", "requests"}]."""
    days = _load()
    if not days and not _file().exists():
        days = _seed_from_archive()
        if days:
            try:
                _save(days)
            except Exception:  # noqa: BLE001
                pass
    today = datetime.now(timezone.utc).date()
    out = []
    for i in range(count - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        entry = days.get(day, {})
        out.append({"date": day, "activities": entry.get("activities", 0), "requests": entry.get("requests", 0)})
    return out
