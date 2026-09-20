"""Sport families and per-sport presentation rules (speed vs pace, cadence units)."""

_FAMILIES = {
    "ride": ["Ride", "MountainBikeRide", "GravelRide", "EBikeRide", "EMountainBikeRide",
             "VirtualRide", "Velomobile", "Handcycle"],
    "run": ["Run", "TrailRun", "VirtualRun"],
    "walk": ["Walk", "Hike"],
    "swim": ["Swim"],
    "paddle": ["Canoeing", "Kayaking", "Rowing", "StandUpPaddling", "Surfing", "Kitesurf",
               "Windsurf", "Sail"],
    "winter": ["AlpineSki", "BackcountrySki", "NordicSki", "Snowboard", "Snowshoe", "IceSkate"],
    "skate": ["InlineSkate", "RollerSki", "Skateboard"],
    "gym": ["WeightTraining", "Workout", "Crossfit", "HighIntensityIntervalTraining", "Yoga",
            "Pilates", "Elliptical", "StairStepper"],
}
_FAMILY_OF = {sport: fam for fam, sports in _FAMILIES.items() for sport in sports}

# (unit label, metres per unit) for sports where people think in pace, not speed.
_PACE = {"run": ("km", 1000), "walk": ("km", 1000), "swim": ("100m", 100)}
_PACE_BY_SPORT = {"Rowing": ("500m", 500)}

FOOT_FAMILIES = {"run", "walk"}


def family(sport_type: str | None) -> str:
    """Unknown or new Strava sport types fall back to "other"."""
    return _FAMILY_OF.get(sport_type or "", "other")


def pace(sport_type: str | None, avg_speed_ms: float | None) -> tuple[int, str] | tuple[None, None]:
    """Seconds per pace unit, e.g. (312, "km"), or (None, None) for speed-based sports."""
    unit = _PACE_BY_SPORT.get(sport_type or "") or _PACE.get(family(sport_type))
    if not unit or not avg_speed_ms or avg_speed_ms <= 0:
        return None, None
    return round(unit[1] / avg_speed_ms), unit[0]


def cadence(sport_type: str | None, avg_cadence: float | None) -> tuple[float, str] | tuple[None, None]:
    """Strava stores foot cadence per leg; the Strava app (and watches) show steps/min, i.e. double."""
    if avg_cadence is None:
        return None, None
    if family(sport_type) in FOOT_FAMILIES:
        return round(avg_cadence * 2), "spm"
    return round(avg_cadence), "rpm"
