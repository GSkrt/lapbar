"""LapBar's own preferences: the ones the bar's widget settings cannot carry, because they are also used by the
command line and the data window.

  history_from       earliest day to fetch and show ("YYYY-MM-DD"), or None for all of Strava's history
  export_path        where the DuckDB database goes (None: the default under the data folder)
  export_continuous  append new activities to the database after every refresh
  export_spatial     download DuckDB's spatial extension (once) and add real geometry to the database
  coach_tone         "Motivational quotes": off (silent), motivational or drill (see coach.py); off by default
  coach_random_per_day  at most this many random pushes a day (0 to 12)
  quiet_hours        no notifications between these times, "HH:MM-HH:MM" (the coach only)
  coach_pause_until  the coach says nothing until this day (illness, injury, holiday, a planned break), or None

Stored in ~/.config/lapbar/prefs.json (mode 600).
"""
import json
import os
from datetime import date, datetime
from pathlib import Path

from . import config

DEFAULTS = {"history_from": None, "export_path": None, "export_continuous": False, "export_spatial": False,
            "coach_tone": "off", "coach_random_per_day": 2, "quiet_hours": "22:00-08:00", "coach_pause_until": None}


def _path():
    return config.user_env_path().parent / "prefs.json"


def load() -> dict:
    try:
        data = json.loads(_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}


def _save(data: dict) -> None:
    config.private_dir(_path().parent)
    tmp = _path().with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(_path())


def update(**changes) -> dict:
    """Change some preferences; raises ValueError for a bad date. Returns all of them."""
    data = load()
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise KeyError(key)
        if key == "coach_pause_until" and value:
            value = date.fromisoformat(str(value)).isoformat()
        if key == "history_from" and value:
            value = date.fromisoformat(str(value)).isoformat()      # ValueError if it is not a date
            if date.fromisoformat(value) > date.today():
                raise ValueError("The date is in the future")
        if key == "coach_tone" and value not in ("off", "motivational", "drill"):
            raise ValueError("tone must be off, motivational or drill")
        if key == "coach_random_per_day":
            value = int(value)
            if not 0 <= value <= 12:
                raise ValueError("random pushes per day must be 0 to 12")
        if key == "quiet_hours":
            try:
                for part in str(value).split("-"):
                    datetime.strptime(part, "%H:%M")
                if len(str(value).split("-")) != 2:
                    raise ValueError
            except ValueError:
                raise ValueError("quiet hours look like 22:00-08:00") from None
        if key == "export_path":
            value = str(value).strip() or None
        data[key] = value if value != "" else None
    _save(data)
    return data


def default_export_path() -> str:
    return str(config.data_dir() / "lapbar.duckdb")


def export_path() -> Path:
    chosen = load()["export_path"] or default_export_path()
    return Path(os.path.expanduser(chosen))
