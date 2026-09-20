"""LapBar's own preferences: the ones the bar's widget settings cannot carry, because they are also used by the
command line and the data window.

  history_from       earliest day to fetch and show ("YYYY-MM-DD"), or None for all of Strava's history
  export_path        where the DuckDB database goes (None: the default under the data folder)
  export_continuous  append new activities to the database after every refresh

Stored in ~/.config/lapbar/prefs.json (mode 600).
"""
import json
import os
from datetime import date
from pathlib import Path

from . import config

DEFAULTS = {"history_from": None, "export_path": None, "export_continuous": False}


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
        if key == "history_from" and value:
            value = date.fromisoformat(str(value)).isoformat()      # ValueError if it is not a date
            if date.fromisoformat(value) > date.today():
                raise ValueError("The date is in the future")
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
