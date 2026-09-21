"""Everything LapBar knows about the Strava API, in one place: which version it targets and every call it makes.

Strava has one public API version, v3 (its OpenAPI/Swagger spec calls itself "Strava API v3", version 3.0.0). It
can still change under a fixed version number, so this module records what LapBar was checked against and
`scripts/check_strava_api.py` compares it with Strava's published spec (monthly, by a GitHub workflow).

LapBar only READS: it never creates, changes or deletes anything on Strava. `CALLS` lists every request;
a test keeps the code, this list and the README in step.
"""
import re

API_VERSION = "v3"
API_SPEC_VERSION = "3.0.0"                # info.version in https://developers.strava.com/swagger/swagger.json
SPEC_URL = "https://developers.strava.com/swagger/swagger.json"
SPEC_CHECKED_ON = "2026-09-20"            # the day this list was last compared with the spec and the changelog
CHANGELOG_URL = "https://developers.strava.com/docs/changelog/"

# Every request goes to BASE, so a change of address is a one-line change here.
BASE = "https://www.strava.com/api/v3"
OAUTH = "https://www.strava.com/oauth"

# Announced in Strava's changelog on 2026-06-01. The changelog does not say when the old address stops working,
# whether the paths stay the same, or whether the OAuth endpoints move: check before switching.
BASE_URL_MIGRATION = {"new_base": "https://api-v3.strava.com", "available_from": "2027-01-04",
                      "source": CHANGELOG_URL + " (2026-06-01)"}

# Response headers read for the request allowance (see ratelimit.py).
RATE_LIMIT_HEADERS = ("X-ReadRateLimit-Usage", "X-ReadRateLimit-Limit", "X-RateLimit-Usage", "X-RateLimit-Limit")

# method: GET/POST are requests LapBar makes; BROWSER is a page LapBar only opens in your browser.
# spec: whether the call is in the Swagger spec (the OAuth pages are documented separately, not in it).
CALLS = [
    {"id": "athlete_activities", "method": "GET", "path": "/athlete/activities", "base": BASE, "spec": True,
     "params": ["after", "before", "per_page", "page"], "scope": "activity:read_all",
     "why": "the activity list: totals, the calendar, the latest activity, fitness",
     "when": "every refresh (one request per 200 activities since 1 January), and once per older year when history is downloaded"},
    {"id": "activity_detail", "method": "GET", "path": "/activities/{id}", "base": BASE, "spec": True,
     "params": ["include_all_efforts"], "scope": "activity:read_all",
     "why": "records (PRs and top-10 places) by name, and one activity that is not in the list",
     "when": "once per activity that has records, and when a chart is opened for an activity that is not stored"},
    {"id": "activity_streams", "method": "GET", "path": "/activities/{id}/streams", "base": BASE, "spec": True,
     "params": ["keys", "key_by_type"], "scope": "activity:read_all",
     "why": "the complete second-by-second data (GPS, altitude, speed, heart rate, power, cadence, ...)",
     "when": "once per activity: the newest ride, the background archive, and charts you open"},
    {"id": "activity_kudos", "method": "GET", "path": "/activities/{id}/kudos", "base": BASE, "spec": True,
     "params": ["per_page"], "scope": "activity:read_all",
     "why": "who gave kudos, for the notification and the popup",
     "when": "for the newest activities when their kudos count changes, and when you open an older ride"},
    {"id": "activity_comments", "method": "GET", "path": "/activities/{id}/comments", "base": BASE, "spec": True,
     "params": ["per_page"], "scope": "activity:read_all",
     "why": "who commented and what they wrote, for the notification and the details window",
     "when": "for the newest activities when their comment count changes, and when you open an older ride's details"},
    {"id": "athlete", "method": "GET", "path": "/athlete", "base": BASE, "spec": True,
     "params": [], "scope": "read",
     "why": "to show which Strava account was signed in, and to know which comments are your own replies",
     "when": "during setup, and once when the first new comment arrives"},
    {"id": "oauth_token", "method": "POST", "path": "/token", "base": OAUTH, "spec": False,
     "params": ["client_id", "client_secret", "code", "grant_type", "refresh_token"], "scope": "-",
     "why": "signing in (authorization_code) and renewing the access token (refresh_token, about every 6 hours)",
     "when": "at sign-in, and when the access token has expired"},
    {"id": "oauth_authorize", "method": "BROWSER", "path": "/authorize", "base": OAUTH, "spec": False,
     "params": ["client_id", "response_type", "redirect_uri", "approval_prompt", "scope"], "scope": "read,activity:read_all",
     "why": "the page where you allow LapBar to read your activities (opened in your browser, not requested by LapBar)",
     "when": "at sign-in"},
]


def url(call_id: str) -> str:
    """The address of a call (with `{id}` still in it for per-activity calls)."""
    call = next(c for c in CALLS if c["id"] == call_id)
    return call["base"] + call["path"]


def call_for(address: str) -> dict | None:
    """The registered call an address belongs to, or None. Query strings are ignored; `{id}` matches one segment."""
    bare = address.split("?", 1)[0]
    for call in CALLS:
        pattern = re.escape(call["base"] + call["path"]).replace(re.escape("{id}"), "[^/]+")
        if re.fullmatch(pattern, bare):
            return call
    return None


def check_spec(spec: dict) -> tuple[list[str], list[str]]:
    """Compare a Swagger spec with what LapBar uses: (problems, notes). Problems mean something may break."""
    problems, notes = [], []
    version = (spec.get("info") or {}).get("version")
    if version != API_SPEC_VERSION:
        problems.append(f"the spec's version is {version!r}, LapBar was checked against {API_SPEC_VERSION!r}")
    if spec.get("host") != "www.strava.com" or spec.get("basePath") != "/api/v3":
        problems.append(f"the spec's address is {spec.get('host')}{spec.get('basePath')}, LapBar uses {BASE}")
    shared = spec.get("parameters") or {}
    for call in CALLS:
        if not call["spec"]:
            continue
        operations = (spec.get("paths") or {}).get(call["path"])
        operation = (operations or {}).get(call["method"].lower())
        if operation is None:
            problems.append(f"{call['method']} {call['path']} is not in the spec any more")
            continue
        if operation.get("deprecated"):
            problems.append(f"{call['method']} {call['path']} is marked deprecated")
        names = set()
        for p in operation.get("parameters", []):
            if "$ref" in p:
                p = shared.get(p["$ref"].rsplit("/", 1)[-1], {})
            names.add(p.get("name"))
        for wanted in call["params"]:
            if wanted not in names:
                problems.append(f"{call['method']} {call['path']}: the parameter {wanted!r} is not in the spec")
    notes.append(f"the spec has {len(spec.get('paths') or {})} paths; LapBar uses "
                 f"{sum(1 for c in CALLS if c['spec'])} of them, all read-only")
    return problems, notes


def summary_lines() -> list[str]:
    lines = [f"Strava API {API_VERSION} (spec {API_SPEC_VERSION}), base {BASE}; checked {SPEC_CHECKED_ON}", ""]
    for c in CALLS:
        lines.append(f"{c['method']:<7} {c['base'] + c['path']}")
        lines.append(f"        {c['why']}; {c['when']}")
    lines += ["", f"Announced: the base URL changes to {BASE_URL_MIGRATION['new_base']}, available from "
                  f"{BASE_URL_MIGRATION['available_from']} ({BASE_URL_MIGRATION['source']})."]
    return lines
