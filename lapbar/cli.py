"""Fetch from all enabled providers and write cache.json for the widget."""
import argparse
import json
import os
import sys

from . import auth, charts, config, fitness, mute, ratelimit, setup, streams, vault
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
        summary = strava.fetch(previous=_read_cache(), backfill=args.backfill, ftp=args.ftp,
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
    _write_cache({k: v for k, v in summary.items() if k != "kudos_events"})
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
    return strava._listed(request_json(f"https://www.strava.com/api/v3/activities/{activity_id}", token=token))


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


def cmd_charts(args) -> int:
    data, error = _series_data(args)
    if error:
        print(json.dumps(error))
        return 1
    theme = {f"LAPBAR_{k}": v for k, v in (("FG", args.fg), ("BG", args.bg), ("ACCENT", args.accent), ("FONT", args.font)) if v}
    charts.open_window(streams.path_for(args.activity), theme)
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
        answer = input("Remove LapBar's saved credentials, sign-in and cache from this computer? [y/N] ")
        if answer.strip().lower() != "y":
            print("Nothing removed.")
            return 1
    removed = setup.forget()
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
    reset_p.set_defaults(func=cmd_reset)
    sub.add_parser("status", help="show whether LapBar is set up (JSON)").set_defaults(func=cmd_status)
    mute_p = sub.add_parser("mute", help="mute or unmute kudos notifications")
    mute_p.add_argument("state", nargs="?", choices=["on", "off", "toggle", "status"], default="status")
    mute_p.set_defaults(func=cmd_mute)
    sub.add_parser("auth", help="one-time Strava login").set_defaults(func=cmd_auth)
    fetch = sub.add_parser("fetch", help="fetch data and update the cache (default)")
    fetch.add_argument("--print", action="store_true", help="also print the summary")
    fetch.add_argument("--ftp", type=int, default=0, metavar="WATTS",
                       help="your cycling FTP; enables power-based load for rides with a power meter")
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
