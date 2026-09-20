"""Stay inside Strava's request allowance, using the counters Strava itself reports.

Every Strava response carries usage headers (`X-ReadRateLimit-Usage: <15 min>,<today>` and the matching
`-Limit`). We remember the latest of them in a small state file, so every separate `lapbar` command
knows how much of the allowance is used. Strava's daily allowance resets at midnight UTC and the
15-minute one on the quarter hour.

Not every request is equally important, so the allowance is shared out in tiers:

  optional work (history downloads, kudos name look-ups)   only while under 40% used
  automatic refreshes (the timer)                          until 60% used
  manual refreshes (button, menu, opening the popup)       until 90% used
  things you asked for by name (opening a chart)           until 95% used

That leaves room for manual refreshes: the timer alone can never use the last 40% of the day.
"""
import json
import os
import time
from datetime import datetime, timezone

from . import config

OPTIONAL = 0.40
LIMITS = {"auto": 0.60, "manual": 0.90, "action": 0.95}
DEFAULT_LIMITS = (100, 1000)   # what a new Strava app gets for reads: per 15 minutes, per day
QUARTER = 900


class BudgetExhausted(RuntimeError):
    """The allowance for this kind of request is used up for now."""

    def __init__(self, message: str, snapshot: dict):
        super().__init__(message)
        self.snapshot = snapshot


def _file():
    return config.state_dir() / "ratelimit.json"


def _pair(text) -> tuple[int, int] | None:
    try:
        a, b = str(text).split(",")
        return int(a), int(b)
    except (ValueError, AttributeError):
        return None


def record(headers, now: float | None = None) -> None:
    """Remember the usage Strava reported. Never raises: bookkeeping must not break a request."""
    try:
        usage = _pair(headers.get("X-ReadRateLimit-Usage") or headers.get("X-RateLimit-Usage"))
        limit = _pair(headers.get("X-ReadRateLimit-Limit") or headers.get("X-RateLimit-Limit"))
        if not usage:
            return
        state = {"at": now if now is not None else time.time(), "usage": list(usage),
                 "limit": list(limit or DEFAULT_LIMITS)}
        config.private_dir(_file().parent)
        tmp = _file().with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        tmp.replace(_file())
    except Exception:  # noqa: BLE001
        pass


def _midnight_utc(ts: float) -> float:
    d = datetime.fromtimestamp(ts, timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


def snapshot(now: float | None = None) -> dict:
    """Current usage, with the windows that have since reset counted as unused."""
    now = time.time() if now is None else now
    try:
        state = json.loads(_file().read_text())
        at, (uq, ud), (lq, ld) = state["at"], state["usage"], state["limit"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        at, uq, ud, (lq, ld) = None, 0, 0, DEFAULT_LIMITS
    if at is not None:
        if int(at // QUARTER) != int(now // QUARTER):
            uq = 0                                   # a new 15-minute window has begun
        if _midnight_utc(at) != _midnight_utc(now):
            ud = 0                                   # a new (UTC) day has begun
    next_day = _midnight_utc(now) + 86400
    return {
        "quarter_used": uq, "quarter_limit": lq, "daily_used": ud, "daily_limit": ld,
        "fraction": max(uq / lq if lq else 0, ud / ld if ld else 0),
        "resets_at": next_day, "next_window": (int(now // QUARTER) + 1) * QUARTER,
    }


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def allow_optional(snap: dict | None = None) -> bool:
    """Room for extras such as history downloads and kudos name look-ups?"""
    return (snap or snapshot())["fraction"] < OPTIONAL


def backfill_quota(snap: dict, per_refresh: int) -> int:
    """How many older activities to download this refresh: the setting, scaled down as the day's allowance fills up.

    Full speed when nothing has been used, none at the limit for extras; at least one while there is any room."""
    if per_refresh <= 0 or snap["fraction"] >= OPTIONAL:
        return 0
    return max(1, round(per_refresh * (1 - snap["fraction"] / OPTIONAL)))


def check(kind: str, snap: dict | None = None) -> dict:
    """Raise BudgetExhausted unless a request of this kind fits; otherwise return the snapshot.

    kind: "auto" (timer), "manual" (a refresh you asked for), "action" (something you asked for by name).
    """
    snap = snap or snapshot()
    if snap["fraction"] < LIMITS[kind]:
        return snap
    used, limit = snap["daily_used"], snap["daily_limit"]
    if snap["quarter_used"] / max(1, snap["quarter_limit"]) >= LIMITS[kind] and used / max(1, limit) < LIMITS[kind]:
        when = f"the next 15-minute window starts at {_clock(snap['next_window'])}"
        what = f"{snap['quarter_used']} of {snap['quarter_limit']} requests this quarter hour"
    else:
        when = f"the daily allowance resets at {_clock(snap['resets_at'])} (00:00 UTC)"
        what = f"{used} of {limit} requests today"
    if kind == "auto":
        message = (f"Automatic refresh is paused to leave room for manual refreshes ({what}); {when}. "
                   "Refreshing by hand still works.")
    else:
        message = f"Strava's request allowance is nearly used ({what}); {when}."
    raise BudgetExhausted(message, snap)


def summary(snap: dict) -> dict:
    """The part of a snapshot the widget shows."""
    return {**{k: snap[k] for k in ("daily_used", "daily_limit", "quarter_used", "quarter_limit", "resets_at")},
            "auto_paused": snap["fraction"] >= LIMITS["auto"]}
