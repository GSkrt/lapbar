"""Detect new kudos between two fetches and name who gave them.

State lives in the summary itself (and so in the cache):
  kudos_seen  {activity_id: kudos count we have already reported}
  kudoers     {activity_id: ["First L.", ...]} as of the last look
`seen` only advances for activities we processed successfully, so a failed request retries next time
instead of silently losing the notification.
"""
import html

from . import stravaapi
from .http import HttpError, request_json
from .text import clean_text

KUDOS_URL = stravaapi.url("activity_kudos") + "?per_page=200"
RECENT = 30      # only the newest activities are watched for new kudos (about a month of riding)
SEED_LIMIT = 5   # first-time name lookups per fetch, so the very first run stays cheap
THUMB = "\U000F0513"   # the thumbs-up of the Nerd Font, as in the bar


def kudoers(token: str, activity_id: int) -> list[str]:
    people = request_json(KUDOS_URL.format(id=activity_id), token=token)
    names = []
    for p in people if isinstance(people, list) else []:
        # Cleaned here, at the source, like comments: everything downstream (the cache, details/<id>.json,
        # `lapbar fetch --print`, the notification) then only ever sees a name with no control characters in it.
        name = clean_text(f"{p.get('firstname', '')} {p.get('lastname', '')}".strip())
        names.append(name or "Someone")
    return names


def track(token: str, activities: list[dict], previous: dict | None,
          seed_limit: int = SEED_LIMIT) -> tuple[list[dict], dict, dict]:
    """Returns (events, kudos_seen, kudoers) for the newest activities (newest first)."""
    prev = previous or {}
    first_run = previous is None
    seen_prev = prev.get("kudos_seen")
    listed_prev = {str(a["id"]): a.get("kudos", 0) for a in prev.get("activities", [])}
    if seen_prev is None:  # older cache without tracking: its activity list is the baseline
        seen_prev = dict(listed_prev)
    names_prev = prev.get("kudoers", {})

    events: list[dict] = []
    seen: dict[str, int] = {}
    names: dict[str, list[str]] = {}
    seeded = 0
    blocked = False  # after one failed request (e.g. rate limit) stop asking this round

    for a in activities[:RECENT]:
        key, count = str(a["id"]), a.get("kudos", 0)
        # An activity that was not watched before (the watch widened, or it slid in) starts from the count the last
        # list showed, so only a genuinely new kudo is announced; a brand new activity starts from 0.
        known = count if first_run else seen_prev.get(key, listed_prev.get(key, 0))
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
            events.append({"activity_id": a["id"], "name": a.get("name"), "url": a.get("url"), "count": count - known,
                           "total": count, "from": new})
        else:
            seeded += 1
    return events, seen, names


def notification(event: dict) -> dict:
    """What the popup says: how many new kudos, who gave them and on what. Escaped, because a desktop may read markup."""
    names = event.get("from") or []
    who = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
    count = event["count"]
    title = f"{THUMB}  {count} new kudo{'' if count == 1 else 's'}"
    body = (f"{who} on " if who else "On ") + f"\u201c{event.get('name') or 'your activity'}\u201d \u00b7 {event['total']} total"
    return {"title": title, "body": html.escape(body, quote=False), "url": event.get("url")}
