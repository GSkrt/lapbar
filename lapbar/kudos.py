"""Detect new kudos between two fetches and name who gave them.

State lives in the summary itself (and so in the cache):
  kudos_seen  {activity_id: kudos count we have already reported}
  kudoers     {activity_id: ["First L.", ...]} as of the last look
`seen` only advances for activities we processed successfully, so a failed request retries next time
instead of silently losing the notification.
"""
from .http import HttpError, request_json

KUDOS_URL = "https://www.strava.com/api/v3/activities/{id}/kudos?per_page=200"
RECENT = 10      # only the newest activities are watched for new kudos
SEED_LIMIT = 5   # first-time name lookups per fetch, so the very first run stays cheap


def kudoers(token: str, activity_id: int) -> list[str]:
    people = request_json(KUDOS_URL.format(id=activity_id), token=token)
    names = []
    for p in people if isinstance(people, list) else []:
        name = f"{p.get('firstname', '')} {p.get('lastname', '')}".strip()
        names.append(name or "Someone")
    return names


def track(token: str, activities: list[dict], previous: dict | None,
          seed_limit: int = SEED_LIMIT) -> tuple[list[dict], dict, dict]:
    """Returns (events, kudos_seen, kudoers) for the newest activities (newest first)."""
    prev = previous or {}
    first_run = previous is None
    seen_prev = prev.get("kudos_seen")
    if seen_prev is None:  # older cache without tracking: its activity list is the baseline
        seen_prev = {str(a["id"]): a.get("kudos", 0) for a in prev.get("activities", [])}
    names_prev = prev.get("kudoers", {})

    events: list[dict] = []
    seen: dict[str, int] = {}
    names: dict[str, list[str]] = {}
    seeded = 0
    blocked = False  # after one failed request (e.g. rate limit) stop asking this round

    for a in activities[:RECENT]:
        key, count = str(a["id"]), a.get("kudos", 0)
        known = count if first_run else seen_prev.get(key, 0)
        stored = names_prev.get(key)
        if stored is not None:
            names[key] = stored
        seen[key] = min(count, known)  # holds back on failure, drops if kudos were withdrawn

        want_events = count > known
        want_names = count > 0 and stored is None and seeded < seed_limit
        if blocked or not (want_events or want_names):
            if not want_events:
                seen[key] = count
            continue
        try:
            current = kudoers(token, a["id"])
        except HttpError:
            blocked = True
            continue
        names[key], seen[key] = current, count
        if want_events:
            new = [n for n in current if n not in stored] if stored is not None else []
            events.append({"activity_id": a["id"], "name": a.get("name"), "count": count - known,
                           "total": count, "from": new})
        else:
            seeded += 1
    return events, seen, names
