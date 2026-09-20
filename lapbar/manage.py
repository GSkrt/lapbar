"""The data window: how much history is stored, what fetching did each day, and the DuckDB export.

`status()` is everything the window shows, as one JSON-able dict (`lapbar manage --status`); `open_window()` starts
the window, its own Quickshell process like the chart window (see ../manage/).
"""
import os
import subprocess
import sys
from pathlib import Path

from . import charts, config, export, fetchlog, history, prefs, ratelimit, raw

MANAGE_DIR = Path(__file__).resolve().parent.parent / "manage"
LAUNCHER = Path(__file__).resolve().parent.parent / "bin" / "lapbar"


def _cache() -> dict:
    return export._cache()


def _dir_bytes(folder: Path) -> int:
    total = 0
    if folder.is_dir():
        for p in folder.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    return total


def _with_today(days: list[dict], snapshot: dict) -> list[dict]:
    """Today's request count is Strava's own current number, which can be ahead of what the log has seen."""
    if days:
        days[-1] = {**days[-1], "requests": max(days[-1]["requests"], snapshot.get("daily_used", 0))}
    return days


def status() -> dict:
    cache = _cache()
    p = prefs.load()
    limit = p["history_from"]
    days = {d: v for d, v in (cache.get("days") or {}).items() if not limit or d >= limit}
    known_total = sum(v.get("count", 0) for v in days.values())
    stored, known = raw.archived_ids(), raw.known_ids()
    raw_bytes = _dir_bytes(raw._dir())
    hist = history.summary()
    db = prefs.export_path()
    return {
        "history": {
            "activities": known_total,
            "oldest_day": min(days) if days else None,
            "newest_day": max(days) if days else None,
            "years_stored": hist["years"],
            "years_complete": hist["complete"],
            "limit": limit,
            "stored": len(stored),
            "no_data": len(known) - len(stored),
            "raw_bytes": raw_bytes,
            "estimated_total_bytes": round(raw_bytes / len(stored) * known_total) if stored else None,
            "data_dir": str(config.data_dir()),
        },
        "fetch_days": _with_today(fetchlog.last_days(14), ratelimit.snapshot()),
        "budget": ratelimit.summary(ratelimit.snapshot()),
        "export": {
            **export.availability(),
            "path": str(db),
            "default_path": prefs.default_export_path(),
            "continuous": bool(p["export_continuous"]),
            "spatial_wanted": bool(p["export_spatial"]),
            "state": export.state(),
            "file_bytes": db.stat().st_size if db.is_file() else None,
        },
        "schema": [{"table": t["table"], "about": t["about"],
                    "columns": [{"name": n, "type": ty, "about": a} for n, ty, a in t["columns"]]} for t in export.SCHEMA],
        "relationships": export.RELATIONSHIPS,
        "examples": [{"title": t, "sql": s} for t, s in export.EXAMPLES],
    }


def open_window(theme: dict | None = None) -> int:
    """Start the data window and float it. Returns the window process id."""
    env = {**os.environ, **(theme or {}), "LAPBAR_BIN": str(LAUNCHER), "LAPBAR_ASSETS": str(charts.ASSETS_DIR),
           "LAPBAR_PYTHON": sys.executable}         # the window runs lapbar with the interpreter that started it
    proc = subprocess.Popen(["quickshell", "-p", str(MANAGE_DIR)], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    charts.float_window(proc.pid)
    return proc.pid
