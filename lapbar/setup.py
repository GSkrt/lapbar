"""Interactive first-run setup: create your own Strava app, enter its credentials, sign in.

Run in a terminal (`lapbar setup`) or from the widget's "Start setup" button, which opens a floating
terminal. Every step explains what to click and where to paste, and nothing secret is ever echoed.
"""
import getpass
import re
import shutil
import sys
import webbrowser
from pathlib import Path

from . import auth, config, vault
from .http import HttpError, request_json

API_SETTINGS_URL = "https://www.strava.com/settings/api"
DOCS_URL = "https://developers.strava.com/docs/getting-started/"
ICON = Path(__file__).resolve().parent.parent / "assets" / "icon.png"
ATHLETE_URL = "https://www.strava.com/api/v3/athlete"
ID_RE = re.compile(r"^\d{3,9}$")
SECRET_RE = re.compile(r"^[0-9a-fA-F]{40}$")
MAX_ATTEMPTS = 3

_tty = sys.stdout.isatty()
bold = (lambda s: f"\033[1m{s}\033[0m") if _tty else (lambda s: s)
dim = (lambda s: f"\033[2m{s}\033[0m") if _tty else (lambda s: s)
warn = (lambda s: f"\033[33m{s}\033[0m") if _tty else (lambda s: s)


class Quit(Exception):
    """The user typed q at a prompt."""


def valid_client_id(text: str) -> bool:
    return bool(ID_RE.match(text))


def valid_client_secret(text: str) -> bool:
    return bool(SECRET_RE.match(text))


def athlete_name() -> str:
    token = auth.default_token_source().access_token()
    athlete = request_json(ATHLETE_URL, token=token)
    return f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip() or "your Strava account"


def _ask(prompt: str, input_fn) -> str:
    answer = input_fn(prompt).strip()
    if answer.lower() in ("q", "quit"):
        raise Quit
    return answer


def _prerequisites(out) -> None:
    out(bold("LapBar setup"))
    out("=============\n")
    out("LapBar reads your activities straight from Strava. For that, Strava makes every app use its")
    out("own API credentials, so you register a small private app under your own account. There is no")
    out("shared key, and your data never passes through anyone else. It takes about two minutes.\n")
    out(bold("You need:"))
    out("  * a Strava account")
    out("  * an active Strava subscription. Strava requires one to create API apps")
    out(f"    ({DOCS_URL})")
    out("  * to use the same account here that owns the app: your app is private to you\n")
    out(dim("Type q at any prompt to quit. You can run this again at any time.\n"))


def _step_create_app(input_fn, out, open_url, open_browser: bool) -> None:
    out(bold("Step 1 of 3 - create your Strava API app"))
    out(f"  1. Open  {API_SETTINGS_URL}")
    out("  2. Fill in the form exactly like this:")
    out("       Application Name ............ LapBar   (anything you like)")
    out("       Category .................... Visualizer")
    out("       Club ........................ leave empty")
    out("       Website ..................... http://localhost")
    out("       Application Description ..... Personal status bar widget")
    out(bold("       Authorization Callback Domain  localhost") + "   <- must be exactly this")
    out("       Icon ........................ Strava asks for an image; any picture works.")
    if ICON.exists():
        out(f"                                     You can use: {ICON}")
    out("  3. Click Create. Strava then shows your Client ID and Client Secret (click 'Show').\n")
    if open_browser:
        answer = _ask("Press Enter to open that page in your browser (or type s to skip): ", input_fn)
        if answer.lower() != "s":
            open_url(API_SETTINGS_URL)
    else:
        _ask("Press Enter when your app is created: ", input_fn)
    out("")


def _step_credentials(input_fn, secret_fn, out, current_id: str = "") -> tuple[str, str]:
    out(bold("Step 2 of 3 - paste your credentials"))
    out("  Client ID     the number near the top of the page")
    out("  Client Secret the long code under it (click 'Show')")
    out(warn("  Take the Client Secret, not the 'Access Token' or 'Refresh Token' further down."))
    out(dim("  You do not need those two: they only have 'read' access, which cannot see your activities."))
    out(dim("  Step 3 asks Strava for the right ones and stores them for you, so there is nothing to copy.\n"))
    out(dim("  The secret goes into your system keyring (never a plain-text file). Typing is hidden.\n"))

    for _ in range(MAX_ATTEMPTS):
        shown = f" [{current_id}]" if current_id else ""
        client_id = _ask(f"Client ID{shown}: ", input_fn) or current_id
        if valid_client_id(client_id):
            break
        out("  That does not look like a Client ID. It is just a number (about 5-6 digits).")
    else:
        raise Quit

    client_secret = _ask_secret(secret_fn, out)
    return client_id, client_secret


def _ask_secret(secret_fn, out) -> str:
    """Ask for the Client Secret. Typing is hidden, so confirm what was received (never the value)."""
    out(dim("  Nothing appears while you type or paste the secret. That is normal: just press Enter."))
    for _ in range(MAX_ATTEMPTS):
        client_secret = secret_fn("Client Secret (hidden): ").strip()
        if client_secret.lower() in ("q", "quit"):
            raise Quit
        if valid_client_secret(client_secret):
            out(f"  Received a Client Secret ({len(client_secret)} characters, hidden).")
            return client_secret
        out("  That does not look like a Client Secret. It is 40 letters and digits (0-9, a-f).")
        if not client_secret:
            out("  (Nothing was received. Paste the secret, then press Enter.)")
    raise Quit


def _store(client_id: str, client_secret: str, out) -> None:
    config.write_env_file(config.user_env_path(), {"STRAVA_CLIENT_ID": client_id})
    where = vault.set("client_secret", client_secret)
    if where == "keyring":
        out("  Saved. The secret is stored in your system keyring.\n")
    else:
        path = config.state_dir() / "secrets.json"
        out(warn("  No system keyring was found, so the secret is stored in a private file that only your"))
        out(warn(f"  user can read: {path}"))
        out(warn("  For better protection, install a keyring (gnome-keyring) and run `lapbar setup --reset`.\n"))


def _sign_in(client_id, client_secret, out, authorize, open_browser) -> str:
    """Returns "ok", "retry" (bad credentials) or "failed"."""
    out(bold("Step 3 of 3 - sign in with Strava"))
    out("  Strava's authorization page opens next. Click " + bold("Authorize") + " and leave every")
    out("  permission ticked: LapBar needs to read your activities.\n")
    try:
        authorize(client_id, client_secret, open_browser=open_browser, out=out)
        return "ok"
    except HttpError as e:
        if e.status in (400, 401, 403):
            out(warn("\n  Strava rejected the Client ID / Client Secret. Check that you copied the Client Secret"))
            out(warn("  (not an Access Token) and the Client ID of the same app.\n"))
            return "retry"
        out(warn(f"\n  Strava error: {e}"))
        return "failed"
    except auth.AuthTimeout as e:
        out(warn(f"\n  {e}"))
        out(f"  Check the settings at {API_SETTINGS_URL}, then run `lapbar setup` again.")
        return "failed"
    except RuntimeError as e:
        out(warn(f"\n  {e}"))
        return "failed"


def run(*, reset: bool = False, open_browser: bool = True, input_fn=input, secret_fn=getpass.getpass,
        out=print, open_url=webbrowser.open, authorize=auth.authorize, verify=athlete_name) -> int:
    try:
        return _run(reset, open_browser, input_fn, secret_fn, out, open_url, authorize, verify)
    except (Quit, EOFError):
        out("\nSetup cancelled. Run `lapbar setup` whenever you want to continue.")
        return 130
    except KeyboardInterrupt:
        out("\nSetup cancelled.")
        return 130


def _run(reset, open_browser, input_fn, secret_fn, out, open_url, authorize, verify) -> int:
    client_id = config.get("STRAVA_CLIENT_ID")
    try:
        client_secret = vault.client_secret()
    except vault.VaultUnavailable:
        client_secret = None
    have_credentials = bool(client_id and client_secret) and not reset

    if have_credentials and auth.has_tokens():
        try:
            name = verify()
        except (HttpError, RuntimeError):
            out(warn("Your saved sign-in no longer works, so let's sign in again.\n"))
        else:
            return _manage(name, client_id, input_fn, secret_fn, out, open_url, authorize, verify, open_browser)

    if not have_credentials:
        _prerequisites(out)
        _step_create_app(input_fn, out, open_url, open_browser)

    for attempt in range(MAX_ATTEMPTS):
        if not have_credentials:
            client_id, client_secret = _step_credentials(input_fn, secret_fn, out, client_id if reset else "")
            _store(client_id, client_secret, out)
        result = _sign_in(client_id, client_secret, out, authorize, open_browser)
        if result == "ok":
            break
        if result == "failed":
            return 1
        have_credentials = False  # bad credentials: enter them again
    else:
        out("Too many attempts. Run `lapbar setup` to try again.")
        return 1

    try:
        out(f"\n{bold('All set!')} Signed in as {bold(verify())}.")
    except (HttpError, RuntimeError):
        out(f"\n{bold('All set!')} (Could not read your name from Strava just now, which is harmless.)")
    out("LapBar will show your latest activity in the bar within a few seconds.")
    out(dim("If the widget is not in your bar yet:  omarchy plugin enable io.github.gskrt.lapbar"))
    out(dim("Your app is private to your Strava account. To remove everything again: lapbar reset"))
    return 0


def _manage(name, client_id, input_fn, secret_fn, out, open_url, authorize, verify, open_browser) -> int:
    """Already set up: keep it, or change something."""
    out(f"LapBar is set up and signed in as {bold(name)}.\n")
    out("  1. Keep everything as it is")
    out("  2. Update the Client Secret (for example after regenerating it on Strava)")
    out("  3. Change the Client ID and Client Secret (a different Strava app)")
    out("  4. Sign in with Strava again")
    out("  5. Remove everything LapBar saved")
    choice = _ask("\nChoose 1-5 [1]: ", input_fn) or "1"
    if choice == "1":
        return 0
    if choice == "2":
        out("")
        secret = _ask_secret(secret_fn, out)
        _store(client_id, secret, out)
        try:
            out(f"The new secret works. Still signed in as {bold(verify())}.")
            return 0
        except (HttpError, RuntimeError):
            out("Strava wants you to sign in again with the new secret.\n")
            return 0 if _sign_in(client_id, secret, out, authorize, open_browser) == "ok" else 1
    if choice == "3":
        return _run(True, open_browser, input_fn, secret_fn, out, open_url, authorize, verify)
    if choice == "4":
        secret = vault.client_secret()
        return 0 if _sign_in(client_id, secret, out, authorize, open_browser) == "ok" else 1
    if choice == "5":
        if _ask("This removes your saved credentials and sign-in. Type yes to confirm: ", input_fn).lower() == "yes":
            out("Removed: " + (", ".join(forget(out)) or "nothing"))
        return 0
    out("Please choose a number from 1 to 5.")
    return 1


def forget(out=print, everything: bool = False) -> list[str]:
    """Remove what lapbar stored: secrets, sign-in, config, cache. The downloaded activities in the data folder
    (raw archive, older years, records) are yours and stay unless `everything` is set."""
    removed = []
    for name in ("client_secret", "tokens"):
        try:
            if vault.get(name) is not None:
                vault.delete(name)
                removed.append(name.replace("_", " "))
        except vault.VaultUnavailable:
            out(warn(f"Could not reach the keyring to remove '{name}'; unlock it and run reset again."))
    for path in (config.state_dir() / "tokens.json", config.state_dir() / "secrets.json",
                 config.state_dir() / "kudos-muted", config.cache_path()):
        if path.exists():
            path.unlink()
            removed.append(path.name)
    series_dir = config.cache_path().parent / "streams"
    if series_dir.is_dir():
        for f in series_dir.glob("*"):
            f.unlink()
        series_dir.rmdir()
        removed.append("downloaded charts")
    if everything and config.data_dir().is_dir():
        shutil.rmtree(config.data_dir())
        removed.append("downloaded activities")
    env = config.user_env_path()
    if env.exists():
        config.write_env_file(env, {}, remove=["STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN"])
        removed.append("client id")
        if not any("=" in line and not line.lstrip().startswith("#") for line in env.read_text().splitlines()):
            env.unlink()  # nothing but comments left
    return removed
