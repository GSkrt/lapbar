"""Fitness, fatigue and form, computed on this computer from the activities already fetched.

Strava's API has no "fitness" or "freshness" data (checked against its public spec), so this is the standard
impulse-response ("performance manager") model, the same idea behind TrainingPeaks-style charts:

  fitness  the slow average of daily load, about 42 days: what you have built up
  fatigue  the fast average, about 7 days: what you did lately
  form     yesterday's fitness minus yesterday's fatigue: how fresh you are going into today

Every activity needs one number, its *load*. It is taken from the best data there is, in this order:
  1. power     TSS = hours x (weighted watts / FTP)^2 x 100, when you gave an FTP and the ride has power
  2. effort    Strava's own "Relative Effort" (`suffer_score`), when it recorded one (a heart-rate device)
  3. time      hours x a typical rate for the sport, when nothing else exists, so every activity counts

Power-based TSS and Strava's Relative Effort are on different scales (a ride can be 76 TSS and 164 effort). When
an FTP is set, the effort scale is calibrated against *your own* rides that have both, so rides with power and
activities without it stay comparable. The numbers are still estimates: good for the shape of your training and
for comparing yourself with yourself, not for comparing with anyone else's chart.
"""
from collections import Counter
from datetime import date, datetime, timedelta

from . import sports

FITNESS_DAYS = 42
FATIGUE_DAYS = 7
OUTPUT_DAYS = 120          # how many days of the series are returned (the model runs from your first activity)
MAX_INTENSITY = 1.6        # weighted watts / FTP above this means the FTP is probably wrong; cap it
MIN_CALIBRATION = 5        # rides with both power and effort needed before effort is rescaled to match power
SCALE_LIMITS = (0.2, 2.0)  # a calibration outside this range means something is off; do not trust it further
FTP_MIN_RIDE_S = 40 * 60   # shorter rides say little about what you can hold for an hour
FTP_MIN_RIDES = 5          # power rides of that length needed before an estimate is offered

# Rough load per hour when only the duration is known. Deliberately modest: unknown effort is treated as
# easy-to-moderate, so it never inflates form or fatigue.
RATE_PER_HOUR = {"ride": 55, "run": 85, "walk": 25, "swim": 60, "paddle": 45, "winter": 65,
                 "skate": 55, "gym": 35, "other": 40}

# Form thresholds (lower bound, status id). Borrowed from the usual TSB guidance; treat as a guide.
STATUSES = [(20, "very_fresh"), (5, "fresh"), (-10, "balanced"), (-30, "building"), (float("-inf"), "overreaching")]


def load_of(activity: dict, ftp: int = 0, effort_scale: float = 1.0) -> tuple[float, str]:
    """(load, source) for one raw Strava activity; source is "power", "effort", "time" or "none"."""
    hours = (activity.get("moving_time") or 0) / 3600
    if hours <= 0:
        return 0.0, "none"
    watts = activity.get("weighted_average_watts")
    if ftp and watts and watts > 0:
        return hours * min(watts / ftp, MAX_INTENSITY) ** 2 * 100, "power"
    if activity.get("suffer_score"):
        return float(activity["suffer_score"]) * effort_scale, "effort"
    family = sports.family(activity.get("sport_type") or activity.get("type"))
    return hours * RATE_PER_HOUR.get(family, RATE_PER_HOUR["other"]), "time"


def effort_scale(activities: list[dict], ftp: int) -> float:
    """How to bring Strava's Relative Effort onto the power (TSS) scale, from rides that have both.

    Returns 1.0 (no rescaling) without an FTP, or with too few rides that have both to say anything.
    """
    if not ftp:
        return 1.0
    ratios = []
    for a in activities:
        effort = a.get("suffer_score")
        if effort and effort > 0:
            load, source = load_of(a, ftp)
            if source == "power" and load > 0:
                ratios.append(load / effort)
    if len(ratios) < MIN_CALIBRATION:
        return 1.0
    ratios.sort()
    middle = len(ratios) // 2
    median = ratios[middle] if len(ratios) % 2 else (ratios[middle - 1] + ratios[middle]) / 2
    return min(max(median, SCALE_LIMITS[0]), SCALE_LIMITS[1])


def estimate_ftp(activities: list[dict]) -> dict | None:
    """A ballpark FTP from ride history, or None when there is not enough of it to say anything.

    FTP is roughly the power you can hold for an hour, and a hard ride of that length has a weighted power close
    to it. So: the highest weighted power among rides of 40 minutes or more, rounded to 5 W. It needs several such
    rides with power, so a single lucky or glitched ride cannot decide it. A conservative guide, not a test result.
    """
    watts = [a["weighted_average_watts"] for a in activities
             if (a.get("weighted_average_watts") or 0) > 0
             and (a.get("moving_time") or 0) >= FTP_MIN_RIDE_S
             and sports.family(a.get("sport_type") or a.get("type")) == "ride"
             and (a.get("sport_type") or a.get("type")) != "EBikeRide"]        # motor assistance: not your power
    if len(watts) < FTP_MIN_RIDES:
        return None
    return {"watts": int(round(max(watts) / 5)) * 5, "rides": len(watts)}


def status_of(form: float, fitness: float) -> str:
    if fitness < 10:
        return "starting"           # too little history for the numbers to mean much yet
    return next(name for floor, name in STATUSES if form >= floor)


def build(activities: list[dict], today: date, ftp: int = 0, days: int = OUTPUT_DAYS) -> dict | None:
    """The fitness/fatigue/form series up to `today`, or None if there is nothing to base it on."""
    scale = effort_scale(activities, ftp)
    per_day: dict[date, float] = {}
    sources: Counter = Counter()
    for a in activities:
        try:
            day = datetime.fromisoformat(a["start_date_local"].replace("Z", "")).date()
        except (KeyError, ValueError):
            continue
        if day > today:
            continue
        load, source = load_of(a, ftp, scale)
        if load > 0:
            per_day[day] = per_day.get(day, 0.0) + load
            sources[source] += 1
    if not per_day:
        return None

    start = min(per_day)
    fitness = fatigue = 0.0
    rows, day = [], start
    while day <= today:
        load = per_day.get(day, 0.0)
        form = fitness - fatigue                       # freshness going into the day
        fitness += (load - fitness) / FITNESS_DAYS
        fatigue += (load - fatigue) / FATIGUE_DAYS
        rows.append({"date": day.isoformat(), "load": round(load, 1), "fitness": round(fitness, 1),
                     "fatigue": round(fatigue, 1), "form": round(form, 1),
                     "warmup": (day - start).days < FITNESS_DAYS})
        day += timedelta(days=1)

    now = rows[-1]
    then = rows[-29]["fitness"] if len(rows) > 28 else None          # four weeks ago
    change = round((now["fitness"] - then) / max(then, 10) * 100) if then is not None else None
    return {
        "current": {**now, "status": status_of(now["form"], now["fitness"])},
        "days": rows[-days:],
        "sources": dict(sources),
        "ftp": ftp or None,
        "ftp_estimate": estimate_ftp(activities),        # offered by the menu's "Estimate from my rides"; never applied on its own
        "effort_scale": round(scale, 2) if scale != 1.0 else None,       # set when effort was rescaled to match power
        "warming_up": now["warmup"],
        "fitness_change_28d_pct": change,
    }


EPOCH = date(1970, 1, 1)


def chart_doc(f: dict) -> dict:
    """The fitness series in the chart window's data format: dates on the x axis (as days since 1970)."""
    days = f["days"]
    col = lambda key: [d[key] for d in days]                       # noqa: E731
    return {
        "v": 1, "kind": "fitness", "id": 0, "name": "Fitness, fatigue & form", "sport": None, "start": None,
        "distance_km": 0, "points": len(days),
        "x": {"key": "date", "label": "Date", "unit": "date",
              "values": [(date.fromisoformat(d["date"]) - EPOCH).days for d in days]},
        "series": [
            {"key": "fitness", "label": "Fitness", "unit": "", "decimals": 1, "format": "number",
             "panel": "trend", "values": col("fitness")},
            {"key": "fatigue", "label": "Fatigue", "unit": "", "decimals": 1, "format": "number",
             "panel": "trend", "values": col("fatigue")},
            {"key": "form", "label": "Form", "unit": "", "decimals": 1, "format": "number",
             "draw": "form", "values": col("form")},
            {"key": "load", "label": "Daily load", "unit": "", "decimals": 0, "format": "number",
             "draw": "bars", "values": col("load")},
        ],
        "warming_up": f.get("warming_up", False),
        "sources": f.get("sources", {}),
    }
