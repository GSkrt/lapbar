"""Popups for things that happen on Strava: new kudos, new comments.

Each popup is about one activity, names who and what, and ends with a "View on Strava" link in Strava's orange
that opens that activity. Two activities with news mean two popups. Clicking the popup does the same (it is the
"default" action, the one Omarchy's notifications run on a click; a desktop that draws notification buttons shows it
as a "View on Strava" button). The text may be markup on some desktops, so everything that came from other people is
escaped.

A popup is shown by a process of its own, because waiting for the click takes a while.
"""
import json
import subprocess
import sys
from pathlib import Path

from . import comments, kudos

ORANGE = "#FC5200"                       # Strava's colour, as its brand guidelines ask for links
LINK_TEXT = "View on Strava"
ACTIVITY_PREFIX = "https://www.strava.com/activities/"
POPUP_MS = 20000
KINDS = {"kudos": kudos.notification, "comments": comments.notification}


def safe_url(url) -> str | None:
    """Only ever link to an activity page on Strava."""
    return url if isinstance(url, str) and url.startswith(ACTIVITY_PREFIX) and url[len(ACTIVITY_PREFIX):].isdigit() else None


def link_markup(url: str) -> str:
    return f'<a href="{url}"><font color="{ORANGE}">{LINK_TEXT}</font></a>'


def compose(kind: str, event: dict) -> dict:
    """The popup for an event: title, body (ending with the link, below the text) and the activity's address."""
    n = KINDS[kind](event)
    url = safe_url(n.get("url"))
    body = n["body"] + ("\n" + link_markup(url) if url else "")
    return {"title": n["title"], "body": body, "url": url,
            "actions": [{"key": "default", "label": LINK_TEXT}] if url else []}


def show(kind: str, event: dict, timeout_ms: int = POPUP_MS) -> str | None:
    """Show the popup through the desktop's notification service and wait for it. Returns the action chosen, or None."""
    n = compose(kind, event)
    command = ["notify-send", "-a", "lapbar", "-u", "normal", "-t", str(timeout_ms), n["title"], n["body"]]
    for action in n["actions"]:
        command += ["-A", f"{action['key']}={action['label']}"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout_ms / 1000 + 30)
    except (OSError, subprocess.SubprocessError):
        return None
    chosen = done.stdout.strip()
    if chosen == "default" and n["url"]:
        try:
            subprocess.Popen(["xdg-open", n["url"]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass
    return chosen or None


def deliver(kind: str, event: dict) -> None:
    """Show a popup without holding anything up: a detached process shows it and opens the page on a click."""
    launcher = Path(__file__).resolve().parent.parent / "bin" / "lapbar"
    payload = json.dumps({"kind": kind, "event": event})
    subprocess.Popen([sys.executable, "-I", str(launcher), "alert", "--deliver", payload],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
