"""Nudges to get off the chair, from your own data, quiet when the data (or you) say easy is right.

Opt-in: the setting "Motivational quotes" is Silent until you choose Motivational or Drill sergeant. Local, rule-based, no
service, no AI. The words are data (messages/en.json), so they can be tuned or translated without touching code.

  Motivational   warm and mellow: a walk, an easy spin, a stretch
  Drill sergeant a furious sergeant; the chair, the couch, your excuses and the kudos you are missing are the target,
                 never you or your body

Every nudge has a reason from your activities, and says it:
  idle      3 or more days since your last activity
  falling   your fitness is down 15% or more over two weeks
  comeback  14 or more days off (only ever kind: no guilt)
  rest      your form is very low: an easy day or a rest day
  fresh, praise  good form, or fitness that has been rising
Random pushes only spread a reminder over the day: they need one of the reasons idle or falling, and say it.
They arrive as desktop popups (through the notification service, so do-not-disturb applies), at most one a day for the
data ones, and the popups that ask you to move carry buttons for an excuse.

It stays quiet, with a kind note on the card instead, when it has good reason to:
  excused   you marked today with an excuse ("I'm tired", bad weather, no time, not feeling well, a planned rest day)
  tired     you said "I'm tired" or "not feeling well" today or yesterday
  recovery  your form dipped to -10 or lower in the last two weeks, or last week was clearly your hardest in a while
  pause     you paused it (`lapbar prefs --coach-pause DAYS`)
  also      quiet hours (22:00-08:00), and within an hour of an activity
Not medical advice; it never talks about bodies.
"""
import json
import random
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from . import config, excuses, prefs, system

MODES = ("off", "motivational", "drill")
TONES = ("motivational", "drill")
IDLE_DAYS = 3                 # days without an activity before the first reminder
COMEBACK_DAYS = 14            # days off after which the message is only kind
FALLING_PCT = 15              # fitness down this much in two weeks counts as falling
HARD_FORM = -10               # a form this low in the last two weeks means a hard block: recovery is expected
HARD_WEEK = 1.25              # last week's load this many times the usual: a hard week
RANDOM_CHANCE = 0.12          # per refresh, until the day's cap is reached
MIN_GAP_S = 90 * 60           # between two notifications
AFTER_ACTIVITY_S = 60 * 60    # no nudges within an hour of an activity
RECENT = 24                   # how many messages are remembered, so a line does not repeat soon
ASKS_TO_MOVE = ("idle", "falling", "comeback", "fresh", "random")     # these popups carry excuse buttons
BUTTONS = ("tired", "weather", "time")
POPUP_MS = 20000


def messages() -> dict:
    return json.loads((Path(__file__).resolve().parent / "messages" / "en.json").read_text())


def _state_path():
    return config.state_dir() / "coach.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    config.private_dir(_state_path().parent)
    _state_path().write_text(json.dumps(state))


def facts(summary: dict, now: datetime, marked: dict | None = None) -> dict:
    """What the data says today: days since the last activity, the fitness trend, whether recovery is expected."""
    latest = summary.get("latest") or {}
    start = str(latest.get("start") or "")[:10]
    idle = None
    if start:
        try:
            idle = (now.date() - date.fromisoformat(start)).days
        except ValueError:
            pass
    fit = summary.get("fitness") or {}
    days = fit.get("days") or []
    current = fit.get("current") or {}
    trustworthy = bool(days) and not fit.get("warming_up") and (current.get("fitness") or 0) >= 10
    change14 = None
    if trustworthy and len(days) > 14:
        then = days[-15]["fitness"]
        change14 = round((days[-1]["fitness"] - then) / max(then, 10) * 100)
    recovery = None
    if trustworthy:
        lows = [d["form"] for d in days[-15:-1]]
        loads = [d["load"] for d in days]
        last7, prev7 = sum(loads[-7:]), sum(loads[-14:-7])
        baseline = sum(loads[-42:-14]) / 4 if len(loads) >= 42 else 0
        if lows and min(lows) <= HARD_FORM:
            recovery = f"your form dipped to {round(min(lows))} in the last two weeks"
        elif baseline > 0 and prev7 >= HARD_WEEK * baseline and last7 < prev7:
            recovery = "last week was one of your hardest in a while"
    marked = excuses.load() if marked is None else marked
    key = marked.get(now.date().isoformat())
    return {"idle_days": idle, "status": current.get("status"), "form": current.get("form"),
            "change_28d": fit.get("fitness_change_28d_pct"), "change_14d": change14, "recovery": recovery,
            "excuse_today": excuses.LABELS.get(key), "tired": excuses.recent_low_energy(now.date(), marked)}


def _pick(pool: list[str], recent: list[str], key: str) -> str:
    fresh = [m for m in pool if f"{key}:{m[:24]}" not in recent] or pool
    return random.choice(fresh)


def _reason(f: dict) -> str | None:
    """The data behind a reminder, in one sentence, or None when the data gives no reason to nudge."""
    parts = []
    if f["idle_days"] is not None and f["idle_days"] >= IDLE_DAYS:
        parts.append(f"{f['idle_days']} days since your last activity")
    if f["change_14d"] is not None and f["change_14d"] <= -FALLING_PCT:
        parts.append(f"your fitness is down {abs(f['change_14d'])}% in two weeks")
    return "; ".join(parts).capitalize() if parts else None


def _suggestion(f: dict, tone: str, recent: list[str]) -> dict | None:
    """The one thing worth saying from your data, or None. `notify` says whether it may also be a popup."""
    idle = f["idle_days"]
    drop = -(f["change_14d"] or 0)
    if f["excuse_today"]:
        kind, notify, reason = "excused", False, "You skipped today"
    elif f["tired"]:
        kind, notify, reason = "excused", False, f"You said: {f['tired']}"
    elif f["status"] == "overreaching":
        kind, notify, reason = "rest", True, "Your form is very low"
    elif f["recovery"]:
        kind, notify, reason = "recovery", False, f["recovery"].capitalize()
    elif idle is not None and idle >= COMEBACK_DAYS:
        kind, notify, reason = "comeback", True, f"{idle} days since your last activity"
    elif _reason(f) and (idle or 0) >= IDLE_DAYS:
        kind, notify, reason = "idle", True, _reason(f)
    elif f["change_14d"] is not None and f["change_14d"] <= -FALLING_PCT:
        kind, notify, reason = "falling", True, _reason(f)
    elif f["status"] in ("fresh", "very_fresh") and (idle or 0) >= 1:
        kind, notify, reason = "fresh", True, "Your form is good"
    elif (f["change_28d"] or 0) >= 10 and (idle or 0) <= 2:
        kind, notify, reason = "praise", True, f"Your fitness is up {f['change_28d']}% in four weeks"
    else:
        return None
    excuse = f["excuse_today"] or f["tired"] or ""
    text = _pick(messages()[kind][tone], recent, kind).format(days=idle if idle is not None else 0, drop=drop, excuse=excuse)
    return {"id": kind, "tone": tone, "text": text, "reason": reason, "notify": notify}


def _quiet(now: datetime, hours: str) -> bool:
    try:
        start, end = (datetime.strptime(x, "%H:%M").time() for x in hours.split("-"))
    except ValueError:
        return False
    t = now.time()
    return (start <= t or t < end) if start > end else (start <= t < end)


def notification(kind: str, tone: str, text: str, reason: str | None = None, buttons: bool | None = None) -> dict:
    """A popup: title, body, and (when it asks you to move) the excuse buttons."""
    body = text if not reason else f"{text}\n{reason}."
    event = {"kind": kind, "tone": tone, "title": messages()["titles"][tone], "body": body}
    if kind in ASKS_TO_MOVE if buttons is None else buttons:
        event["actions"] = [{"key": k, "label": excuses.LABELS[k]} for k in BUTTONS]
    return event


def paused(settings: dict, now: datetime) -> bool:
    until = settings.get("coach_pause_until")
    try:
        return bool(until) and now.date() <= date.fromisoformat(until)
    except ValueError:
        return False


def tick(summary: dict, now: datetime | None = None, settings: dict | None = None, chance: float = RANDOM_CHANCE,
         marked: dict | None = None) -> tuple[dict | None, list[dict]]:
    """After a refresh: (the card for the popup, the popups to show now). Both empty when Silent or paused."""
    settings = settings or prefs.load()
    tone = settings["coach_tone"]
    now = now or datetime.now()
    if tone not in TONES or paused(settings, now):
        return None, []
    state = _load_state()
    today = now.date().isoformat()
    if state.get("date") != today:
        state = {**state, "date": today, "data_sent": 0, "random_sent": 0}
    recent = state.get("recent", [])
    f = facts(summary, now, marked)
    card = _suggestion(f, tone, recent)

    events: list[dict] = []
    clock = now.timestamp()
    recent_activity = False
    latest_start = (summary.get("latest") or {}).get("start")
    if latest_start:
        try:
            recent_activity = 0 <= clock - datetime.fromisoformat(latest_start.replace("Z", "")).timestamp() < AFTER_ACTIVITY_S
        except ValueError:
            pass
    may_speak = (not _quiet(now, settings["quiet_hours"]) and clock - state.get("last_at", 0) >= MIN_GAP_S
                 and not recent_activity and not f["recovery"] and not f["excuse_today"] and not f["tired"])
    if may_speak:
        reason = _reason(f)
        comeback_again = card and card["id"] == "comeback" and clock - state.get("comeback_at", 0) < 3 * 86400
        if card and card["notify"] and state["data_sent"] < 1 and not comeback_again:
            events.append(notification(card["id"], card["tone"], card["text"], card["reason"]))
            state["data_sent"] += 1
            if card["id"] == "comeback":
                state["comeback_at"] = clock
        elif reason and (f["idle_days"] or 0) >= 2 and state["random_sent"] < settings["coach_random_per_day"] \
                and random.random() < chance:
            events.append(notification("random", tone, _pick(messages()["random"][tone], recent, "random"), reason))
            state["random_sent"] += 1
    if events:
        state["last_at"] = clock
        state["recent"] = (recent + [f"{events[0]['kind']}:{events[0]['body'][:24]}"])[-RECENT:]
    _save_state(state)
    return card, events


def show(event: dict, day: str, timeout_ms: int = POPUP_MS) -> str | None:
    """Show a popup through the desktop's notification service and wait for it. A button press is recorded as that day's
    excuse. Returns the excuse key that was chosen, or None."""
    command = [system.tool("notify-send"), "-a", "lapbar", "-u", "normal", "-t", str(timeout_ms), event["title"], event["body"]]
    for a in event.get("actions") or []:
        command += ["-A", f"{a['key']}={a['label']}"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout_ms / 1000 + 30)
    except (OSError, subprocess.SubprocessError):
        return None
    chosen = done.stdout.strip()
    if chosen in excuses.LABELS:
        excuses.add(day, chosen)
        return chosen
    return None


def deliver(event: dict, day: str | None = None) -> None:
    """Show a popup without holding anything up: a detached process shows it and records a button press."""
    launcher = Path(__file__).resolve().parent.parent / "bin" / "lapbar"
    payload = json.dumps({"event": event, "day": day or date.today().isoformat()})
    subprocess.Popen([sys.executable, "-I", str(launcher), "coach", "--deliver", payload], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def sample(tone: str, kind: str = "random") -> dict:
    """One example popup, for trying a tone out (no buttons, so trying it records nothing)."""
    if tone not in TONES:
        raise ValueError(f"tone must be one of {', '.join(TONES)}")
    text = random.choice(messages()[kind][tone]).format(days=4, drop=18, excuse="I'm tired")
    return notification(kind, tone, text, "Example only: this is how a nudge with its data reason looks", buttons=False)
