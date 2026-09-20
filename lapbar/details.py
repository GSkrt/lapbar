"""What was achieved on one activity, by name: records and who gave kudos.

Strava's activity list only carries counts ("2 PRs, 5 kudos"). To show *which* records and *who*, two more requests
are needed per activity, so both are made at most once and stored on disk (details/<id>.json under the cache
folder), and only for activities that have something to show:

  records  personal records (PR: 1st, 2nd or 3rd fastest of your own efforts) and top-10 places among everyone
           ("KOM/QOM" for 1st) on segments, plus the best efforts of a run (fastest 5k and so on).
           One request: the detailed activity.
  kudoers  who gave kudos, as Strava names them ("First L."). One request, usually already known from
           kudos.track() for the newest activities.

A stored answer is reused while the counts it was made for still match, because Strava fills in records a little
after an upload and kudos keep arriving.
"""
import json
import os

from . import config, kudos
from .http import request_json

DETAIL_URL = "https://www.strava.com/api/v3/activities/{id}?include_all_efforts=true"
DATA_VERSION = 1
MAX_RECORDS = 40
KIND_ORDER = {"kom": 0, "pr": 1}


def _dir():
    return config.cache_path().parent / "details"


def path_for(activity_id: int):
    return _dir() / f"{activity_id}.json"


def _load(activity_id: int) -> dict:
    try:
        data = json.loads(path_for(activity_id).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if data.get("v") == DATA_VERSION else {}


def _save(activity_id: int, data: dict) -> None:
    config.private_dir(_dir())
    path = path_for(activity_id)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({**data, "v": DATA_VERSION}, f, separators=(",", ":"))
    tmp.replace(path)


def build_records(raw: dict) -> list[dict]:
    """The ranked efforts of a detailed activity, most notable first: KOM places, then PRs."""
    found: dict[tuple, dict] = {}
    for source, key in (("segment", "segment_efforts"), ("best_effort", "best_efforts")):
        for effort in raw.get(key) or []:
            name = (effort.get("segment") or {}).get("name") or effort.get("name") or ""
            base = {"name": name, "seconds": effort.get("elapsed_time"),
                    "distance_m": round(effort.get("distance") or 0), "source": source}
            kom, pr = effort.get("kom_rank"), effort.get("pr_rank")
            if isinstance(kom, int) and 1 <= kom <= 10:
                found[("kom", name, base["seconds"])] = {**base, "kind": "kom", "rank": kom}
            if isinstance(pr, int) and 1 <= pr <= 3:
                found[("pr", name, base["seconds"])] = {**base, "kind": "pr", "rank": pr}
    items = sorted(found.values(), key=lambda i: (KIND_ORDER[i["kind"]], i["rank"], i["name"]))
    return items[:MAX_RECORDS]


def _record_counts(activity: dict) -> list[int]:
    return [activity.get("prs", 0) or 0, activity.get("achievements", 0) or 0]


def records_cached(activity: dict) -> list[dict] | None:
    """The stored records, [] when there is nothing to list, None when a download is needed."""
    counts = _record_counts(activity)
    if not any(counts):
        return []
    hit = _load(activity["id"]).get("records")
    return hit["items"] if hit and hit.get("counts") == counts else None


def records(token: str | None, activity: dict) -> list[dict]:
    hit = records_cached(activity)
    if hit is not None:
        return hit
    raw = request_json(DETAIL_URL.format(id=activity["id"]), token=token)
    items = build_records(raw if isinstance(raw, dict) else {})
    data = _load(activity["id"])
    data["records"] = {"counts": _record_counts(activity), "items": items}
    _save(activity["id"], data)
    return items


def kudoers_cached(activity: dict, known: list[str] | None = None) -> list[str] | None:
    """Names already at hand (the summary's, or stored), [] for no kudos, None when a download is needed."""
    count = activity.get("kudos", 0) or 0
    if count == 0:
        return []
    if known:
        return known
    hit = _load(activity["id"]).get("kudoers")
    return hit["names"] if hit and hit.get("count") == count else None


def kudoers(token: str | None, activity: dict, known: list[str] | None = None) -> list[str]:
    hit = kudoers_cached(activity, known)
    if hit is not None:
        return hit
    names = kudos.kudoers(token, activity["id"])
    data = _load(activity["id"])
    data["kudoers"] = {"count": activity.get("kudos", 0), "names": names}
    _save(activity["id"], data)
    return names


def needs_download(activity: dict, known: list[str] | None = None) -> bool:
    return records_cached(activity) is None or kudoers_cached(activity, known) is None
