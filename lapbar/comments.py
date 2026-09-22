"""Detect new comments between two fetches and say who wrote them (alerts.py shows the popup).

State lives in the summary itself (and so in the cache), like the kudos:
  comments_seen   {activity_id: comment count we have already reported}
  comment_texts   {activity_id: [{"id", "who", "text", "at"}, ...]} as of the last look
`seen` only advances for activities we processed successfully, so a failed request retries next time.

Comments you wrote yourself (a reply on Strava) are not announced: your Strava id is looked up once and kept.
LapBar cannot answer a comment: Strava's API only lists them (there is no call to post one), so the popup links to
the activity on Strava, where the reply is written.
"""
import html
import json
from pathlib import Path

from . import config, stravaapi
from .http import HttpError, request_json
from .text import MAX_TEXT, clean_text  # noqa: F401 -- re-exported: cli.py and older tests import them from here

COMMENTS_URL = stravaapi.url("activity_comments") + "?per_page=200"
ATHLETE_URL = stravaapi.url("athlete")
RECENT = 30          # only the newest activities are watched for new comments (about a month of riding)
SEED_LIMIT = 5       # first-time text look-ups per fetch, so the very first run stays cheap
POPUP_LINES = 3      # comments shown in one popup
POPUP_CHARS = 240


def _one(c: dict) -> dict:
    athlete = c.get("athlete") or {}
    who = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()
    return {"id": c.get("id"), "who": clean_text(who) or "Someone", "text": clean_text(c.get("text")),
            "at": c.get("created_at"), "athlete_id": athlete.get("id")}


def fetch(token: str, activity_id: int) -> list[dict]:
    """The comments on one activity, oldest first (one request)."""
    raw = request_json(COMMENTS_URL.format(id=activity_id), token=token)
    found = [_one(c) for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []
    return sorted(found, key=lambda c: c.get("at") or "")


def _athlete_file() -> Path:
    return config.state_dir() / "athlete.json"


def own_id(token: str) -> int | None:
    """The signed-in athlete's Strava id: kept after the first look-up (one request, once)."""
    try:
        return json.loads(_athlete_file().read_text()).get("id")
    except (OSError, json.JSONDecodeError):
        pass
    me = request_json(ATHLETE_URL, token=token)
    athlete_id = me.get("id") if isinstance(me, dict) else None
    if athlete_id:
        _athlete_file().parent.mkdir(parents=True, exist_ok=True)
        _athlete_file().write_text(json.dumps({"id": athlete_id}))
    return athlete_id


def track(token: str, activities: list[dict], previous: dict | None,
          seed_limit: int = SEED_LIMIT) -> tuple[list[dict], dict, dict]:
    """Returns (events, comments_seen, comment_texts) for the newest activities (newest first)."""
    prev = previous or {}
    first_run = previous is None
    seen_prev = prev.get("comments_seen")
    listed_prev = {str(a["id"]): a.get("comments", 0) for a in prev.get("activities", [])}
    if seen_prev is None:  # older cache without tracking: its activity list is the baseline
        seen_prev = dict(listed_prev)
    texts_prev = prev.get("comment_texts", {})

    events: list[dict] = []
    seen: dict[str, int] = {}
    texts: dict[str, list[dict]] = {}
    seeded = 0
    blocked = False  # after one failed request (e.g. rate limit) stop asking this round

    for a in activities[:RECENT]:
        key, count = str(a["id"]), a.get("comments", 0)
        # An activity that was not watched before starts from the count the last list showed (see kudos.track).
        known = count if first_run else seen_prev.get(key, listed_prev.get(key, 0))
        stored = texts_prev.get(key)
        if stored is not None:
            texts[key] = stored
        seen[key] = min(count, known)  # holds back on failure, drops if comments were deleted

        want_events = count > known
        want_texts = count > 0 and stored is None and seeded < seed_limit
        if blocked or not (want_events or want_texts):
            if not want_events:
                seen[key] = count
            continue
        try:
            current = fetch(token, a["id"])
            if want_events:
                mine = own_id(token)
        except HttpError:
            blocked = True
            continue
        texts[key], seen[key] = current, count
        if want_events:
            if stored is not None:
                old = {c["id"] for c in stored}
                new = [c for c in current if c["id"] not in old]
            else:
                new = current[-(count - known):]
            new = [c for c in new if not (mine and c.get("athlete_id") == mine)]
            if new:
                events.append({"activity_id": a["id"], "name": a.get("name"), "url": a.get("url"), "total": count,
                               "comments": [{"who": c["who"], "text": c["text"]} for c in new]})
        else:
            seeded += 1
    return events, seen, texts


def notification(event: dict) -> dict:
    """What the popup says: who wrote what. Escaped, because a desktop may read the text as markup."""
    new = event["comments"]
    name = event.get("name") or "your activity"
    title = f"{new[0]['who']} commented on {name}" if len(new) == 1 else f"{len(new)} new comments on {name}"
    lines = []
    for c in new[:POPUP_LINES]:
        text = c["text"] if len(c["text"]) <= POPUP_CHARS else c["text"][:POPUP_CHARS].rstrip() + "…"
        lines.append(text if len(new) == 1 else f"{c['who']}: {text}")
    if len(new) > POPUP_LINES:
        lines.append(f"and {len(new) - POPUP_LINES} more")
    return {"title": html.escape(title, quote=False), "body": html.escape("\n".join(lines), quote=False),
            "url": event.get("url")}
