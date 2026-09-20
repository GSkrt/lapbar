"""Strava provider: returns the shared summary dict (see README)."""
import itertools
import math
from datetime import datetime, timedelta, timezone

from .. import auth, details, fitness, history, kudos, raw, sports, streams
from ..http import HttpError, request_json

ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
PAGE_SIZE = 200
ROUTE_POINTS = 150
LIST_ROUTE_POINTS = 60
HISTORY_PER_REFRESH = 2      # older years downloaded per refresh, so the first sync never eats the request budget
FIRST_YEAR = 2009            # Strava's first year: nothing to find before it
EMPTY_YEARS_STOP = 3         # this many empty years in a row means there is nothing older


def decode_polyline(encoded: str) -> list[list[float]]:
    """Decode a Google-encoded polyline into [[lat, lng], ...]."""
    points: list[list[float]] = []
    index = lat = lng = 0
    while index < len(encoded):
        for axis in (0, 1):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lng += delta
        points.append([round(lat / 1e5, 5), round(lng / 1e5, 5)])
    return points


def _downsample(seq: list, n: int) -> list:
    if len(seq) <= n:
        return seq
    return [seq[round(i * (len(seq) - 1) / (n - 1))] for i in range(n)]


def normalize_route(points: list[list[float]]) -> list[list[float]]:
    """Reduce a GPS track to its bare shape: [[x, y], ...] inside a 0..1 square, y pointing down.

    Aspect ratio is preserved (longitude is scaled by cos(latitude)) and the shape is centred, so
    the output has no coordinates and cannot reveal where the ride happened.
    """
    if len(points) < 2:
        return []
    lats = [p[0] for p in points]
    lngs = [p[1] for p in points]
    k = math.cos(math.radians((min(lats) + max(lats)) / 2))
    xs = [(lng - min(lngs)) * k for lng in lngs]
    ys = [max(lats) - lat for lat in lats]
    width, height = max(xs), max(ys)
    scale = max(width, height)
    if scale == 0:
        return []
    ox, oy = (scale - width) / 2, (scale - height) / 2
    return [[round((x + ox) / scale, 4), round((y + oy) / scale, 4)] for x, y in zip(xs, ys)]


def _local(activity: dict) -> datetime:
    # start_date_local is wall-clock time in the activity's timezone, marked with a stray "Z".
    return datetime.fromisoformat(activity["start_date_local"].replace("Z", ""))


def _totals(activities: list[dict]) -> dict:
    return {
        "distance_km": round(sum(a.get("distance", 0) for a in activities) / 1000, 2),
        "elevation_m": round(sum(a.get("total_elevation_gain", 0) for a in activities)),
        "moving_time_s": sum(a.get("moving_time", 0) for a in activities),
        "count": len(activities),
    }


def _with_by_sport(activities: list[dict]) -> dict:
    """Totals over everything, plus a breakdown per sport family (most time spent first)."""
    groups: dict[str, list[dict]] = {}
    for a in activities:
        groups.setdefault(sports.family(a.get("sport_type") or a.get("type")), []).append(a)
    by_sport = {fam: _totals(acts) for fam, acts in groups.items()}
    ordered = dict(sorted(by_sport.items(), key=lambda kv: kv[1]["moving_time_s"], reverse=True))
    return {**_totals(activities), "by_sport": ordered}


def _days(activities: list[dict]) -> dict:
    """Per calendar day (in the activity's local time) totals, for the widget's calendar."""
    days: dict[str, dict] = {}
    for a in activities:
        day = days.setdefault(_local(a).date().isoformat(), {
            "count": 0, "distance_km": 0.0, "elevation_m": 0.0, "moving_time_s": 0, "families": []})
        day["count"] += 1
        day["elevation_m"] += a.get("total_elevation_gain", 0)
        day["distance_km"] += a.get("distance", 0) / 1000
        day["moving_time_s"] += a.get("moving_time", 0)
        fam = sports.family(a.get("sport_type") or a.get("type"))
        if fam not in day["families"]:
            day["families"].append(fam)
    for day in days.values():
        day["distance_km"] = round(day["distance_km"], 2)
        day["elevation_m"] = round(day["elevation_m"])
    return dict(sorted(days.items()))


def _listed(a: dict) -> dict:
    """Compact per-activity entry for the widget's calendar: same fields as `latest`, minus nulls,
    with a lighter route (no elevation profile: that costs one extra request per activity)."""
    return {k: v for k, v in _latest(a, LIST_ROUTE_POINTS).items() if v is not None}


def _sums(activities: list[dict]) -> dict:
    return {
        "moving_time_s": sum(a.get("moving_time", 0) for a in activities),
        "distance_km": round(sum(a.get("distance", 0) for a in activities) / 1000, 2),
        "effort": round(sum(a.get("suffer_score") or 0 for a in activities)),  # Strava "Relative Effort"
    }


def _load(activities: list[dict], now: datetime) -> dict:
    """This week so far vs last week: in total, and up to the same moment of the week."""
    week_start = datetime(now.year, now.month, now.day) - timedelta(days=now.weekday())
    last_start = week_start - timedelta(days=7)
    same_point = last_start + (now - week_start)
    this_week = [a for a in activities if _local(a) >= week_start]
    last_week = [a for a in activities if last_start <= _local(a) < week_start]
    return {
        "this_week": _sums(this_week),
        "last_week": _sums(last_week),
        "last_week_same_point": _sums([a for a in last_week if _local(a) < same_point]),
        "days_left": 7 - now.weekday(),  # including today
        "has_effort": any(a.get("suffer_score") for a in this_week + last_week),
    }


def _kmh(ms: float | None) -> float | None:
    return round(ms * 3.6, 1) if ms is not None else None


def _rounded(value: float | None) -> int | None:
    return round(value) if value is not None else None


def _latest(a: dict, route_points: int = ROUTE_POINTS) -> dict:
    sport = a.get("sport_type") or a.get("type")
    pace_s, pace_unit = sports.pace(sport, a.get("average_speed"))
    cadence, cadence_unit = sports.cadence(sport, a.get("average_cadence"))
    return {
        "id": a["id"],
        "start": a.get("start_date_local"),
        "sport": sport,
        "family": sports.family(sport),
        "name": a.get("name"),
        "distance_km": round(a.get("distance", 0) / 1000, 2),
        "elevation_m": round(a.get("total_elevation_gain", 0)),
        "moving_time_s": a.get("moving_time", 0),
        "elapsed_time_s": a.get("elapsed_time"),
        "avg_speed_kmh": _kmh(a.get("average_speed")),
        "max_speed_kmh": _kmh(a.get("max_speed")),
        "pace_s": pace_s,          # seconds per pace_unit; null for speed-based sports
        "pace_unit": pace_unit,    # "km", "100m" or "500m"
        "avg_heartrate": _rounded(a.get("average_heartrate")),
        "max_heartrate": _rounded(a.get("max_heartrate")),
        "avg_cadence": cadence,
        "cadence_unit": cadence_unit,  # "rpm" (cycling) or "spm" (running, walking)
        "avg_watts": a.get("average_watts"),
        "max_watts": a.get("max_watts"),
        "weighted_watts": a.get("weighted_average_watts"),
        "kilojoules": a.get("kilojoules"),
        "suffer_score": a.get("suffer_score"),
        "elev_high_m": a.get("elev_high"),
        "elev_low_m": a.get("elev_low"),
        "avg_temp_c": a.get("average_temp"),
        "device": a.get("device_name"),
        "indoor": bool(a.get("trainer")),
        "commute": bool(a.get("commute")),
        "athletes": a.get("athlete_count", 1),  # >1 for group activities
        "kudos": a.get("kudos_count", 0),
        "comments": a.get("comment_count", 0),
        "prs": a.get("pr_count", 0),
        "achievements": a.get("achievement_count", 0),
        "photos": a.get("total_photo_count", a.get("photo_count", 0)),
        "url": f"https://www.strava.com/activities/{a['id']}",
        "route": normalize_route(
            _downsample(decode_polyline((a.get("map") or {}).get("summary_polyline") or ""), route_points)
        ),
    }


def _fetch_since(token: str, since: datetime) -> list[dict]:
    # One day of margin so timezone differences never drop activities from Jan 1.
    after = int((since - timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp())
    activities: list[dict] = []
    page = 1
    while True:
        batch = request_json(
            f"{ACTIVITIES_URL}?after={after}&per_page={PAGE_SIZE}&page={page}", token=token
        )
        activities += batch
        if len(batch) < PAGE_SIZE:
            return activities
        page += 1


def _fetch_year(token: str, year: int) -> list[dict]:
    """Every activity of one calendar year (in the activity's own local time), newest first."""
    # One day of margin on both sides so timezone differences never drop activities near the year's edges.
    after = int((datetime(year, 1, 1) - timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp())
    before = int((datetime(year + 1, 1, 1) + timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp())
    activities: list[dict] = []
    page = 1
    while True:
        batch = request_json(
            f"{ACTIVITIES_URL}?after={after}&before={before}&per_page={PAGE_SIZE}&page={page}", token=token)
        activities += batch
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    mine = [a for a in activities if _local(a).year == year]
    mine.sort(key=_local, reverse=True)
    return mine


def since(activities, limit: str | None):
    """The activities on or after `limit` ("YYYY-MM-DD"); all of them when there is no limit. Order is kept."""
    for a in activities:
        if not limit or str(a.get("start") or "")[:10] >= limit:
            yield a


def sync_history(token: str, this_year: int, max_years: int, budget: int = HISTORY_PER_REFRESH,
                 refresh: bool = False, not_before: str | None = None) -> int:
    """Download the years before `this_year` that are not stored yet, newest first, at most `budget` of them.

    Stops at `max_years` back, at Strava's first year, at the year of `not_before` (the user's limit), or after
    EMPTY_YEARS_STOP empty years in a row. Returns how many years were downloaded;
    `history.summary()["complete"]` says whether anything is left."""
    known = history.index()["years"]
    downloaded, empty_run, complete = 0, 0, True
    floor = max(this_year - 1 - max_years, FIRST_YEAR - 1, int(not_before[:4]) - 1 if not_before else 0)
    for year in range(this_year - 1, floor, -1):
        entry = known.get(str(year))
        if entry is not None and not refresh:
            empty_run = empty_run + 1 if entry.get("count", 0) == 0 else 0
        else:
            if downloaded >= budget:
                complete = False
                break
            raw = _fetch_year(token, year)
            history.save_year(year, [_listed(a) for a in raw], _days(raw),
                              datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            downloaded += 1
            empty_run = empty_run + 1 if not raw else 0
        if empty_run >= EMPTY_YEARS_STOP:
            break
    history.set_complete(complete)
    return downloaded


def fetch(
    token_source: auth.TokenSource | None = None,
    now: datetime | None = None,
    previous: dict | None = None,
    backfill: int = 0,
    optional: bool = True,
    ftp: int = 0,
    history_years: int = 0,
    history_from: str | None = None,
) -> dict:
    """`previous` is the last summary; its elevation profile is reused while the latest ride is unchanged.

    `backfill` is how many older activities' time series to download in the background per call.
    `optional` says whether there is request budget for extras (history downloads, kudos name look-ups);
    what a new kudos or ride needs is fetched regardless."""
    token_source = token_source or auth.default_token_source()
    now = now or datetime.now()
    year_start = datetime(now.year, 1, 1)
    week_start = datetime(now.year, now.month, now.day) - timedelta(days=now.weekday())

    # Fitness needs about 6 weeks of history before today, so look back at least 130 days (more early in the year).
    fetched = _fetch_since(token_source.access_token(),
                           min(year_start, week_start - timedelta(days=7), datetime(now.year, now.month, now.day) - timedelta(days=130)))
    fetched.sort(key=_local, reverse=True)
    year = [a for a in fetched if _local(a) >= year_start]
    week = [a for a in fetched if _local(a) >= week_start]
    month = [a for a in fetched if _local(a) >= datetime(now.year, now.month, 1)]
    today = [a for a in fetched if _local(a) >= datetime(now.year, now.month, now.day)]

    latest = _latest(year[0]) if year else None
    if latest:
        prev = (previous or {}).get("latest") or {}
        if prev.get("id") == latest["id"] and "elevation_profile" in prev:
            latest["elevation_profile"] = prev["elevation_profile"]
        else:
            # One request per new ride; the full series is stored for the charts window too.
            latest["elevation_profile"] = streams.elevation_profile(streams.get(token_source.access_token(), latest))

    if latest and (latest["prs"] or latest["achievements"]):
        # Which records the newest ride has, by name: one request, stored on disk and reused while the counts hold.
        prev = (previous or {}).get("latest") or {}
        if (prev.get("id"), prev.get("prs"), prev.get("achievements")) == (latest["id"], latest["prs"], latest["achievements"]) \
                and "records" in prev:
            latest["records"] = prev["records"]
        else:
            try:
                latest["records"] = details.records(token_source.access_token(), latest)
            except (HttpError, OSError):
                pass              # not shown this time; tried again on the next refresh

    if history_years and optional:
        try:  # older years for the calendar, a couple per refresh until all are stored; failures retry next time
            sync_history(token_source.access_token(), now.year, history_years, not_before=history_from)
        except (HttpError, OSError):
            pass
    days = {**history.merged_days(), **_days(year)}
    if history_from:                      # the user's limit: nothing older is shown or counted
        days = {d: v for d, v in days.items() if d >= history_from}

    listed = [_listed(a) for a in year]  # newest first
    events, kudos_seen, kudoers = kudos.track(
        token_source.access_token(), listed, previous, seed_limit=kudos.SEED_LIMIT if optional else 0)
    total = sum(v.get("count", 0) for v in days.values())
    if backfill and optional and len(raw.known_ids()) < total:
        try:  # archive older activities' full series a few at a time (this year first, then the older years)
            streams.backfill(token_source.access_token(),
                             since(itertools.chain(listed, history.iter_activities()), history_from), backfill)
        except OSError:
            pass
    stored, known = raw.archived_ids(), raw.known_ids()

    return {
        "provider": "strava",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest": latest,
        "today": _with_by_sport(today),
        "week": _with_by_sport(week),
        "month": _with_by_sport(month),
        "year": _with_by_sport(year),
        "days": days,                     # every stored year, for the calendar
        "history": history.summary(days),
        "archive": {"stored": len(stored), "known": len(known), "total": total},   # progress of the full-series archive
        "archived_ids": sorted(stored),                                            # for the calendar's dots
        "load": _load(fetched, now),
        "fitness": fitness.build(fetched, now.date(), ftp=ftp),
        "activities": listed,
        "kudos_seen": kudos_seen,
        "kudoers": kudoers,
        "kudos_events": events,
    }
