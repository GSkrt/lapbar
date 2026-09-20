"""Days you chose to skip, with the reason: "I'm tired", bad weather, no time, not feeling well, a planned rest day.

They are yours, so they live in the data folder (excuses.json), are marked on the popup's calendar, and tell the coach
to leave you alone: a nudge that ignores "I'm tired" would be a bad coach. One excuse per day; choosing the same one again
takes it back.
"""
import json
import os
from datetime import date, datetime

from . import config

LABELS = {"tired": "I'm tired", "weather": "Bad weather", "time": "No time", "unwell": "Not feeling well", "rest": "Planned rest day"}
LOW_ENERGY = ("tired", "unwell")      # these also mean "leave me alone for a couple of days", not just today
QUIET_DAYS = 2                        # how long after "tired" or "unwell" the coach keeps quiet (today counts)


def _file():
    return config.data_dir() / "excuses.json"


def load() -> dict:
    """{"2026-09-20": "tired", ...}"""
    try:
        data = json.loads(_file().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {d: v["key"] for d, v in data.items() if isinstance(v, dict) and v.get("key") in LABELS}


def _save(data: dict) -> None:
    config.private_dir(_file().parent)
    tmp = _file().with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    tmp.replace(_file())


def toggle(day: str, key: str) -> dict:
    """Mark `day` with an excuse, change it, or take it back (the same key again). Returns all excuses."""
    if key not in LABELS:
        raise ValueError(f"excuse must be one of: {', '.join(LABELS)}")
    date.fromisoformat(day)
    current = load()
    data = {d: {"key": k, "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")} for d, k in current.items()}
    if current.get(day) == key:
        del data[day]
    else:
        data[day] = {"key": key, "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
    _save(data)
    return load()


def add(day: str, key: str) -> dict:
    """Mark `day` with an excuse (never removes one: used by the notification buttons)."""
    if load().get(day) == key:
        return load()
    return toggle(day, key)


def clear(day: str) -> dict:
    current = load()
    data = {d: {"key": k, "at": ""} for d, k in current.items() if d != day}
    _save(data)
    return load()


def recent_low_energy(today: date, excuses: dict | None = None) -> str | None:
    """The label of a "tired" or "unwell" excuse from today or the day before, else None."""
    excuses = load() if excuses is None else excuses
    for back in range(QUIET_DAYS):
        key = excuses.get(date.fromordinal(today.toordinal() - back).isoformat())
        if key in LOW_ENERGY:
            return LABELS[key]
    return None
