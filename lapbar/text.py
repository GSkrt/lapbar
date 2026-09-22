"""Cleaning text that came from someone else's Strava account (a kudos giver's name, a comment, an activity's own
title) before it is ever cached, stored, printed as JSON, or put in a notification: no control characters (so it
cannot manipulate a terminal that later shows it) and a length cap (so it cannot grow without bound). Applied once,
at the point each piece of text first enters LapBar, so every consumer downstream -- the cache, the per-activity
details files, `lapbar fetch --print`, the popup, the desktop notification -- sees only the cleaned version.
"""
import re

MAX_TEXT = 1000


def clean_text(text) -> str:
    """Third-party text as one safe string: no control characters, not endless."""
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", str(text or "")).strip()[:MAX_TEXT]
