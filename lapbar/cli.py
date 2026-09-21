"""Fetch from all enabled providers and write cache.json for the widget."""
import argparse
import itertools
import json
import os
import sys
from datetime import date, datetime, timedelta

from . import activitywin, alerts, auth, charts, coach, comments, config, details, excuses, export, fitness, helpwin, history, manage, mute, pick, prefs, ratelimit, raw, setup, streams, stravaapi, vault
from .http import HttpError, request_json
from .providers import strava


def _write_cache(summary: dict) -> None:
    path = config.cache_path()
    config.private_dir(path.parent)
    tmp = path.with_suffix(".tmp")
    # Owner-only: the cache holds the names of people who gave you kudos.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(summary, f, separators=(",", ":"))
    tmp.replace(path)  # atomic, so the widget never reads a half-written file


def _read_cache() -> dict | None:
    try:
        return json.loads(config.cache_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def cmd_auth(_args) -> int:
    client_id = config.get("STRAVA_CLIENT_ID")
    try:
        client_secret = vault.client_secret()
    except vault.VaultUnavailable as e:
        print(f"lapbar: {e}", file=sys.stderr)
        return 1
    if not (client_id and client_secret):
        print(f"lapbar: {auth.NOT_CONFIGURED_MESSAGE}", file=sys.stderr)
        return 1
    try:
        auth.authorize(client_id, client_secret)
    except (RuntimeError, HttpError, OSError) as e:
        print(f"lapbar: {e}", file=sys.stderr)
        return 1
    return 0


def _classify(exc: Exception) -> tuple[str, str]:
    """(machine-readable code, human message) for a failed fetch."""
    if isinstance(exc, auth.NotConfigured):
        return "not_configured", str(exc)
    if isinstance(exc, ratelimit.BudgetExhausted):
        return "budget", str(exc)
    if isinstance(exc, vault.VaultUnavailable):
        return "vault_unavailable", f"Cannot reach your keyring ({exc}). Unlock it (log in again) and refresh."
    if isinstance(exc, auth.NotAuthorized):
        return "not_authorized", str(exc)
    if isinstance(exc, HttpError):
        if exc.status == 429:
            return "rate_limited", "Strava rate limit reached, will retry later"
        if exc.status == 401:
            return "not_authorized", "Strava rejected the login. Run `lapbar auth` again."
        return "http_error", str(exc)
    return "network", f"Could not reach Strava ({exc})"


def cmd_fetch(args) -> int:
    kind = "manual" if args.manual else "auto"
    try:
        snap = ratelimit.check(kind)
        # Extras (history downloads, kudos name look-ups) only run on the timer and only with plenty of room.
        summary = strava.fetch(previous=_read_cache(), backfill=ratelimit.backfill_quota(snap, args.backfill), ftp=args.ftp,
                               history_years=args.history_years, history_from=prefs.load()["history_from"],
                               optional=(kind == "auto" and ratelimit.allow_optional(snap)))
    except (auth.NotConfigured, auth.NotAuthorized, vault.VaultUnavailable, ratelimit.BudgetExhausted,
            HttpError, OSError) as e:
        # Keep the previous cache so a failure never blanks what was already shown.
        code, message = _classify(e)
        print(f"lapbar: {message}", file=sys.stderr)
        if args.print:
            payload = {"error": code, "message": message}
            if isinstance(e, ratelimit.BudgetExhausted):
                payload["budget"] = ratelimit.summary(e.snapshot)
            print(json.dumps(payload))
        return 1
    # Events are delivered once, on stdout; keeping them out of the cache means they can never replay.
    try:                                # nudges: a card for the popup; the popups themselves are shown here, with excuse buttons
        summary["coach"], events = coach.tick(summary)
        for event in events:
            coach.deliver(event)
    except Exception:  # noqa: BLE001 - a nudge must never break a refresh
        summary["coach"] = None
    # Kudos and comment events are delivered once, as popups (one per activity), and never cached or replayed.
    news = [("kudos", e) for e in summary.pop("kudos_events", None) or []] + \
           [("comments", e) for e in summary.pop("comment_events", None) or []]
    try:
        if not mute.is_muted():
            for kind, event in news:
                alerts.deliver(kind, event)   # each ends with a View on Strava link to its own activity
    except Exception:  # noqa: BLE001 - an alert must never break a refresh
        pass
    _write_cache(summary)
    try:
        export.spawn_if_enabled()      # continuous DuckDB export, in its own process so the refresh stays quick
    except OSError:
        pass
    if args.print:
        summary["muted"] = mute.is_muted()
        summary["budget"] = ratelimit.summary(ratelimit.snapshot())
        print(json.dumps(summary, indent=2 if sys.stdout.isatty() else None))
    return 0


def _activity_for(activity_id: int, token: str) -> dict:
    """The listed form of an activity: from the cache when we have it, else from Strava."""
    for a in (_read_cache() or {}).get("activities", []):
        if a.get("id") == activity_id:
            return a
    older = history.find(activity_id)
    if older is not None:
        return older
    return strava._listed(request_json(stravaapi.url("activity_detail").format(id=activity_id), token=token))


def _series_data(args):
    """(data or None, error dict or None) for `streams` and `charts`."""
    try:
        if args.refresh or streams.cached(args.activity) is None:
            ratelimit.check("action")          # stored data costs nothing, so only a download is checked
        token = auth.default_token_source().access_token()
        data = streams.get(token, _activity_for(args.activity, token), refresh=args.refresh)
    except (auth.NotConfigured, auth.NotAuthorized, vault.VaultUnavailable, ratelimit.BudgetExhausted,
            HttpError, OSError) as e:
        code, message = _classify(e)
        return None, {"error": code, "message": message}
    if data is None:
        return None, {"error": "no_streams", "message": "Strava has no time series for this activity (for example a manual entry)."}
    return data, None


def cmd_streams(args) -> int:
    data, error = _series_data(args)
    print(json.dumps(error or {"path": str(streams.path_for(args.activity)), "points": data["points"],
                               "series": [s["key"] for s in data["series"]]}))
    return 1 if error else 0


def cmd_details(args) -> int:
    """The records, kudos names and comments of one activity, for the details window: from what is stored, else downloaded."""
    cache = _read_cache() or {}
    activity = next((a for a in cache.get("activities", []) if a.get("id") == args.activity), None) \
        or history.find(args.activity)
    if activity is None:
        print(json.dumps({"error": "unknown_activity", "message": "That activity is not in the stored list."}))
        return 1
    known = (cache.get("kudoers") or {}).get(str(activity["id"]))
    known_comments = (cache.get("comment_texts") or {}).get(str(activity["id"]))
    try:
        token = None
        if details.needs_download(activity, known, known_comments):
            ratelimit.check("action")          # stored answers cost nothing, so only a download is checked
            token = auth.default_token_source().access_token()
        out = {"id": activity["id"], "records": details.records(token, activity),
               "kudoers": details.kudoers(token, activity, known),
               "comments": [{k: c.get(k) for k in ("who", "text", "at")}
                            for c in details.comments(token, activity, known_comments)],
               "activity": {k: activity.get(k) for k in ("name", "sport", "start", "url", "kudos", "comments", "prs", "achievements")}}
    except (auth.NotConfigured, auth.NotAuthorized, vault.VaultUnavailable, ratelimit.BudgetExhausted,
            HttpError, OSError) as e:
        code, message = _classify(e)
        print(json.dumps({"error": code, "message": message}))
        return 1
    print(json.dumps(out))
    return 0


def cmd_alert(args) -> int:
    """Show one popup for an event (used by a refresh, in a process of its own so it can wait for a click)."""
    try:
        payload = json.loads(args.deliver)
        kind, event = payload["kind"], payload["event"]
        if kind not in alerts.KINDS or not isinstance(event, dict):
            raise ValueError("unknown alert")
        if kind == "comments":
            event["comments"] = [{"who": comments.clean_text(c.get("who")) or "Someone", "text": comments.clean_text(c.get("text"))}
                                 for c in event["comments"] if isinstance(c, dict)]
            if not event["comments"]:
                return 0
        elif not isinstance(event.get("count"), int) or not isinstance(event.get("total"), int):
            raise ValueError("kudos event without counts")
        event["name"] = comments.clean_text(event.get("name"))
        event["from"] = [comments.clean_text(n) for n in event.get("from") or [] if isinstance(n, str)]
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        print(json.dumps({"error": "bad_event", "message": str(e)}))
        return 1
    print(json.dumps({"chosen": alerts.show(kind, event)}))
    return 0


def cmd_activity(args) -> int:
    """Open the details window for an activity: records, kudos and comments."""
    theme = {f"LAPBAR_{k}": v for k, v in (("FG", args.fg), ("BG", args.bg), ("ACCENT", args.accent), ("FONT", args.font)) if v}
    activitywin.open_window(args.activity, theme)
    print(json.dumps({"ok": True}))
    return 0


def cmd_history(args) -> int:
    """Older years for the calendar: read what is stored (no request), or download it with --sync."""
    if args.sync:
        try:
            ratelimit.check("action")
            token = auth.default_token_source().access_token()
            done = strava.sync_history(token, datetime.now().year, args.years, budget=999, refresh=args.refresh,
                                       not_before=prefs.load()["history_from"])
        except (auth.NotConfigured, auth.NotAuthorized, vault.VaultUnavailable, ratelimit.BudgetExhausted,
                HttpError, OSError) as e:
            code, message = _classify(e)
            print(json.dumps({"error": code, "message": message}))
            return 1
        print(json.dumps({"downloaded_years": done, **history.summary()}))
        return 0
    if args.year is None:
        print(json.dumps(history.summary()))
        return 0
    if args.year == datetime.now().year:
        activities = (_read_cache() or {}).get("activities", [])
    else:
        activities = history.year_activities(args.year)
    print(json.dumps({"year": args.year, "activities": activities}))
    return 0


def cmd_archive(args) -> int:
    """Download the full time series (with GPS) of activities that are not stored yet, now: `--limit` of them."""
    cache = _read_cache() or {}
    activities = strava.since(itertools.chain(cache.get("activities", []), history.iter_activities()),
                              prefs.load()["history_from"])
    try:
        ratelimit.check("action")
        token = auth.default_token_source().access_token()
        done = streams.backfill(token, activities, args.limit)
    except (auth.NotConfigured, auth.NotAuthorized, vault.VaultUnavailable, ratelimit.BudgetExhausted,
            HttpError, OSError) as e:
        code, message = _classify(e)
        print(json.dumps({"error": code, "message": message}))
        return 1
    print(json.dumps({"downloaded": done, "stored": len(raw.archived_ids()), "known": len(raw.known_ids())}))
    return 0


def cmd_manage(args) -> int:
    """The data window (default), or its status as JSON."""
    if args.status:
        print(json.dumps(manage.status()))
        return 0
    theme = {f"LAPBAR_{k}": v for k, v in (("FG", args.fg), ("BG", args.bg), ("ACCENT", args.accent), ("FONT", args.font)) if v}
    manage.open_window(theme)
    print(json.dumps({"ok": True}))
    return 0


def cmd_coach(args) -> int:
    """The coach: what the popup shows (JSON), `--test` to see and send a sample of a tone."""
    if args.deliver:                                       # started by coach.deliver(): show one popup and record a button press
        payload = json.loads(args.deliver)
        coach.show(payload["event"], payload["day"])
        return 0
    settings = prefs.load()
    if args.test:
        tone = args.tone or (settings["coach_tone"] if settings["coach_tone"] in coach.TONES else "motivational")
        try:
            event = coach.sample(tone, args.kind)
        except (ValueError, KeyError) as e:
            print(json.dumps({"error": "bad_value", "message": str(e)}))
            return 1
        coach.show(event, date.today().isoformat(), timeout_ms=8000)
        print(json.dumps(event))
        return 0
    summary = _read_cache() or {}
    now = datetime.now()
    print(json.dumps({"tone": settings["coach_tone"], "card": summary.get("coach"), "paused": coach.paused(settings, now),
                      "paused_until": settings["coach_pause_until"], "quiet_hours": settings["quiet_hours"],
                      "random_per_day": settings["coach_random_per_day"], "excuses": excuses.load(),
                      "excuse_labels": excuses.LABELS}))
    return 0


def cmd_excuse(args) -> int:
    """Mark a day with an excuse ("I'm tired"), or take it back; prints all excuses (JSON)."""
    day = args.date or date.today().isoformat()
    try:
        if args.key == "clear":
            marked = excuses.clear(day)
        else:
            marked = excuses.toggle(day, args.key)
    except ValueError as e:
        print(json.dumps({"error": "bad_value", "message": str(e)}))
        return 1
    print(json.dumps({"excuses": marked, "day": day}))
    return 0


def cmd_howto(args) -> int:
    """Open the how-to window (the guide from docs/help.md)."""
    theme = {f"LAPBAR_{k}": v for k, v in (("FG", args.fg), ("BG", args.bg), ("ACCENT", args.accent), ("FONT", args.font)) if v}
    helpwin.open_window(theme)
    print(json.dumps({"ok": True}))
    return 0


def cmd_prefs(args) -> int:
    changes = {}
    if args.history_from is not None:
        changes["history_from"] = None if args.history_from.lower() in ("none", "") else args.history_from
    if args.export_path is not None:
        changes["export_path"] = args.export_path
    if args.continuous is not None:
        changes["export_continuous"] = args.continuous == "on"
    if args.spatial is not None:
        changes["export_spatial"] = args.spatial == "on"
    if args.coach_tone is not None:
        changes["coach_tone"] = args.coach_tone
    if args.coach_random is not None:
        changes["coach_random_per_day"] = args.coach_random
    if args.quiet_hours is not None:
        changes["quiet_hours"] = args.quiet_hours
    if args.coach_pause is not None:
        changes["coach_pause_until"] = (date.today() + timedelta(days=args.coach_pause)).isoformat() if args.coach_pause > 0 else None
    try:
        current = prefs.update(**changes) if changes else prefs.load()
    except ValueError as e:
        print(json.dumps({"error": "bad_value", "message": f"That is not a valid date (use YYYY-MM-DD): {e}"}))
        return 1
    print(json.dumps({"prefs": current}))
    return 0


def cmd_api(args) -> int:
    """Which Strava API version LapBar targets and every call it makes."""
    if args.json:
        print(json.dumps({"api": stravaapi.API_VERSION, "spec": stravaapi.API_SPEC_VERSION, "base": stravaapi.BASE,
                          "checked": stravaapi.SPEC_CHECKED_ON, "migration": stravaapi.BASE_URL_MIGRATION,
                          "rate_limit_headers": stravaapi.RATE_LIMIT_HEADERS, "calls": stravaapi.CALLS}))
    else:
        print("\n".join(stravaapi.summary_lines()))
    return 0


def cmd_pick_folder(args) -> int:
    """Open the desktop's folder chooser: {"path": ...}, {"cancelled": true} or an error."""
    try:
        chosen = pick.choose_folder(args.start, args.title)
    except pick.NoChooser as e:
        print(json.dumps({"error": "no_chooser", "message": str(e)}))
        return 1
    print(json.dumps({"path": chosen} if chosen else {"cancelled": True}))
    return 0


def cmd_export(args) -> int:
    """Create or update the DuckDB database from everything stored."""
    try:
        result = export.sync(args.path, rebuild=args.rebuild, spatial=True if args.spatial else None)
    except export.DuckdbMissing as e:
        print(json.dumps({"error": "duckdb_missing", "message": str(e), "install": export.INSTALL_COMMANDS}))
        return 1
    except (export.ExportBusy, ValueError, OSError) as e:
        print(json.dumps({"error": "export_failed", "message": str(e)}))
        return 1
    print(json.dumps(result))
    return 0


def cmd_charts(args) -> int:
    data, error = _series_data(args)
    if error:
        print(json.dumps(error))
        return 1
    theme = {f"LAPBAR_{k}": v for k, v in (("FG", args.fg), ("BG", args.bg), ("ACCENT", args.accent), ("FONT", args.font)) if v}
    activity = next((a for a in (_read_cache() or {}).get("activities", []) if a.get("id") == args.activity), None) \
        or history.find(args.activity) \
        or {"id": args.activity, "start": data.get("start"), "name": data.get("name"),
            "sport": data.get("sport"), "family": data.get("family")}
    # The window gets every recorded sample and thins them out itself as you zoom; the overview is the fallback.
    charts.open_window(streams.detail(activity) or streams.path_for(args.activity), theme)
    print(json.dumps({"ok": True}))
    return 0


def cmd_fitness(args) -> int:
    """Open the fitness chart from what was already fetched (no request to Strava)."""
    data = (_read_cache() or {}).get("fitness")
    if not data or not data.get("days"):
        print(json.dumps({"error": "no_fitness", "message": "Not enough activity history yet to work out fitness. Refresh first."}))
        return 1
    path = config.cache_path().parent / "fitness-chart.json"
    config.private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(fitness.chart_doc(data), f, separators=(",", ":"))
    theme = {f"LAPBAR_{k}": v for k, v in (("FG", args.fg), ("BG", args.bg), ("ACCENT", args.accent), ("FONT", args.font)) if v}
    charts.open_window(path, theme)
    print(json.dumps({"ok": True}))
    return 0


def cmd_setup(args) -> int:
    return setup.run(reset=args.reset, open_browser=not args.no_browser)


def cmd_reset(args) -> int:
    if not args.yes:
        print(f"Your downloaded activities in {config.data_dir()} are kept; add --all to remove them too.")
        answer = input("Remove LapBar's saved credentials, sign-in and cache from this computer? [y/N] ")
        if answer.strip().lower() != "y":
            print("Nothing removed.")
            return 1
    removed = setup.forget(everything=args.all)
    print("Removed: " + (", ".join(removed) if removed else "nothing (already clean)"))
    return 0


def cmd_status(_args) -> int:
    client_id = bool(config.get("STRAVA_CLIENT_ID"))
    try:
        secret = bool(vault.client_secret())
        problem = None
    except vault.VaultUnavailable as e:
        secret, problem = False, str(e)
    print(json.dumps({
        "configured": client_id and secret,
        "signed_in": auth.has_tokens(),
        "keyring_available": vault.keyring_available(),
        "problem": problem,
    }))
    return 0


def cmd_mute(args) -> int:
    if args.state == "toggle":
        mute.set_muted(not mute.is_muted())
    elif args.state in ("on", "off"):
        mute.set_muted(args.state == "on")
    print(json.dumps({"muted": mute.is_muted()}))
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lapbar")
    sub = parser.add_subparsers(dest="command")
    streams_p = sub.add_parser("streams", help="download (once) and store an activity's time series")
    streams_p.add_argument("activity", type=int)
    streams_p.add_argument("--refresh", action="store_true", help="download again even if stored")
    streams_p.set_defaults(func=cmd_streams)
    details_p = sub.add_parser("details", help="an activity's records (PRs, KOMs), who gave kudos and its comments (JSON)")
    details_p.add_argument("activity", type=int)
    details_p.set_defaults(func=cmd_details)
    history_p = sub.add_parser("history", help="older years for the calendar: show what is stored, or --sync to download")
    history_p.add_argument("year", type=int, nargs="?", help="print that year's stored activities (JSON)")
    history_p.add_argument("--sync", action="store_true", help="download the years that are not stored yet")
    history_p.add_argument("--refresh", action="store_true", help="with --sync: download stored years again too")
    history_p.add_argument("--years", type=int, default=99, metavar="N", help="with --sync: how many years back (default: all)")
    history_p.set_defaults(func=cmd_history)
    archive_p = sub.add_parser("archive", help="download the full time series (with GPS) of activities not stored yet")
    archive_p.add_argument("--limit", type=int, default=50, metavar="N", help="how many to download now (default 50)")
    archive_p.set_defaults(func=cmd_archive)
    manage_p = sub.add_parser("manage", help="open the data window: history, fetching by day, DuckDB export")
    manage_p.add_argument("--status", action="store_true", help="print what the window shows, as JSON")
    for flag in ("--fg", "--bg", "--accent", "--font"):
        manage_p.add_argument(flag, default=None)
    manage_p.set_defaults(func=cmd_manage)
    coach_p = sub.add_parser("coach", help="nudges to get off the chair: show the current one, or --test a tone")
    coach_p.add_argument("--test", action="store_true", help="send a sample notification now")
    coach_p.add_argument("--tone", choices=("motivational", "drill"), help="with --test: which tone (default: yours)")
    coach_p.add_argument("--kind", default="random", choices=("random", "idle", "comeback", "rest", "fresh", "praise"))
    coach_p.add_argument("--deliver", metavar="JSON", help=argparse.SUPPRESS)
    coach_p.set_defaults(func=cmd_coach)
    excuse_p = sub.add_parser("excuse", help="mark a day you skip, with the reason (tired, weather, time, unwell, rest), or clear it")
    excuse_p.add_argument("key", choices=(*("tired", "weather", "time", "unwell", "rest"), "clear"))
    excuse_p.add_argument("--date", metavar="YYYY-MM-DD", help="the day (default: today)")
    excuse_p.set_defaults(func=cmd_excuse)
    activity_p = sub.add_parser("activity", help="open the details window of an activity: records, kudos and comments")
    activity_p.add_argument("activity", type=int)
    for flag in ("--fg", "--bg", "--accent", "--font"):
        activity_p.add_argument(flag, default=None)
    activity_p.set_defaults(func=cmd_activity)
    alert_p = sub.add_parser("alert", help="show a kudos or comment popup (used by a refresh)")
    alert_p.add_argument("--deliver", metavar="JSON", required=True, help='{"kind": "kudos"|"comments", "event": {...}}')
    alert_p.set_defaults(func=cmd_alert)
    howto_p = sub.add_parser("howto", help="open the how-to window: the guide, inside the app")
    for flag in ("--fg", "--bg", "--accent", "--font"):
        howto_p.add_argument(flag, default=None)
    howto_p.set_defaults(func=cmd_howto)
    prefs_p = sub.add_parser("prefs", help="show or change LapBar's preferences (JSON)")
    prefs_p.add_argument("--history-from", metavar="YYYY-MM-DD|none", help="earliest day to fetch and show")
    prefs_p.add_argument("--export-path", metavar="FILE", help="where the DuckDB database goes")
    prefs_p.add_argument("--continuous", choices=("on", "off"), help="update the database after every refresh")
    prefs_p.add_argument("--spatial", choices=("on", "off"), help="add real geometry (downloads DuckDB's spatial extension once)")
    prefs_p.add_argument("--coach-tone", choices=("off", "motivational", "drill"), help="motivational quotes: off (silent), motivational or drill")
    prefs_p.add_argument("--coach-random", type=int, metavar="N", help="random pushes per day (0 to 12)")
    prefs_p.add_argument("--coach-pause", type=int, metavar="DAYS", help="no nudges for this many days (illness, holiday, a planned break); 0 ends it")
    prefs_p.add_argument("--quiet-hours", metavar="22:00-08:00", help="no coach notifications between these times")
    prefs_p.set_defaults(func=cmd_prefs)
    api_p = sub.add_parser("api", help="the Strava API version LapBar targets and every call it makes")
    api_p.add_argument("--json", action="store_true")
    api_p.set_defaults(func=cmd_api)
    pick_p = sub.add_parser("pick-folder", help="open the desktop's folder chooser and print the choice (JSON)")
    pick_p.add_argument("--start", default=None, metavar="DIR", help="folder to open at")
    pick_p.add_argument("--title", default="Choose a folder")
    pick_p.set_defaults(func=cmd_pick_folder)
    export_p = sub.add_parser("export", help="export everything stored into a DuckDB database")
    export_p.add_argument("--path", metavar="FILE", help="database file (default: the saved path)")
    export_p.add_argument("--rebuild", action="store_true", help="build a fresh file instead of updating")
    export_p.add_argument("--spatial", action="store_true", help="download DuckDB's spatial extension if needed and add real geometry")
    export_p.set_defaults(func=cmd_export)
    charts_p = sub.add_parser("charts", help="open the chart window for an activity")
    charts_p.add_argument("activity", type=int)
    charts_p.add_argument("--refresh", action="store_true", help="download the data again first")
    for opt in ("fg", "bg", "accent", "font"):
        charts_p.add_argument(f"--{opt}", default="", help="theme value passed on to the window")
    charts_p.set_defaults(func=cmd_charts)
    fitness_p = sub.add_parser("fitness", help="open the fitness, fatigue and form chart (from stored data)")
    for opt in ("fg", "bg", "accent", "font"):
        fitness_p.add_argument(f"--{opt}", default="", help="theme value passed on to the window")
    fitness_p.set_defaults(func=cmd_fitness)
    setup_p = sub.add_parser("setup", help="guided first-run setup: create your Strava app, sign in")
    setup_p.add_argument("--reset", action="store_true", help="enter credentials again")
    setup_p.add_argument("--no-browser", action="store_true", help="print links instead of opening a browser")
    setup_p.set_defaults(func=cmd_setup)
    reset_p = sub.add_parser("reset", help="remove all saved credentials, sign-in and cache")
    reset_p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    reset_p.add_argument("--all", action="store_true", help="also delete the downloaded activities (the raw archive)")
    reset_p.set_defaults(func=cmd_reset)
    sub.add_parser("status", help="show whether LapBar is set up (JSON)").set_defaults(func=cmd_status)
    mute_p = sub.add_parser("mute", help="mute or unmute kudos and comment notifications")
    mute_p.add_argument("state", nargs="?", choices=["on", "off", "toggle", "status"], default="status")
    mute_p.set_defaults(func=cmd_mute)
    sub.add_parser("auth", help="one-time Strava login").set_defaults(func=cmd_auth)
    fetch = sub.add_parser("fetch", help="fetch data and update the cache (default)")
    fetch.add_argument("--print", action="store_true", help="also print the summary")
    fetch.add_argument("--ftp", type=int, default=0, metavar="WATTS",
                       help="your cycling FTP; enables power-based load for rides with a power meter")
    fetch.add_argument("--history-years", type=int, default=0, metavar="N",
                       help="also keep N earlier years for the calendar (downloaded a couple per refresh)")
    fetch.add_argument("--manual", action="store_true",
                       help="a refresh you asked for: allowed further into the daily request allowance than the timer")
    fetch.add_argument("--backfill", type=int, default=3, metavar="N",
                       help="download the time series of up to N older activities per refresh (default 3, 0 = off)")
    fetch.set_defaults(func=cmd_fetch)
    args = parser.parse_args(argv)
    if not args.command:
        args = parser.parse_args(["fetch"])
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
