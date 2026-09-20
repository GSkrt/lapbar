"""Ask the user to choose a folder with the desktop's own dialog.

The data window is a Quickshell window, which has no file dialog of its own, so it runs `lapbar pick-folder` and
this opens whichever chooser is installed: zenity (a GTK dialog, part of a normal Omarchy install), then kdialog
or yad. If none is there, the error says what to install.
"""
import os
import shutil
import subprocess
from pathlib import Path

INSTALL_HINT = "No folder chooser found. Install one: omarchy pkg add zenity (or kdialog, or yad)."


class NoChooser(RuntimeError):
    pass


def _command(title: str, start: str) -> list[str] | None:
    start_dir = start if start.endswith("/") else start + "/"
    if shutil.which("zenity"):
        return ["zenity", "--file-selection", "--directory", "--title", title, "--filename", start_dir]
    if shutil.which("kdialog"):
        return ["kdialog", "--getexistingdirectory", start_dir, "--title", title]
    if shutil.which("yad"):
        return ["yad", "--file", "--directory", "--title", title, "--filename", start_dir]
    return None


def choose_folder(start: str | None = None, title: str = "Choose a folder") -> str | None:
    """The chosen folder, or None if the dialog was cancelled. Raises NoChooser if no dialog program exists."""
    start = os.path.expanduser(start or "~")
    while start not in ("/", "") and not Path(start).is_dir():          # open at the nearest folder that exists
        start = str(Path(start).parent)
    command = _command(title, start or "/")
    if command is None:
        raise NoChooser(INSTALL_HINT)
    done = subprocess.run(command, capture_output=True, text=True)
    chosen = done.stdout.strip()
    return chosen.rstrip("/") or "/" if done.returncode == 0 and chosen else None
