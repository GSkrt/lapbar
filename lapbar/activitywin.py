"""The details window of one activity: its records, who gave kudos and the comments (see ../activity/).

Its own Quickshell process, like the chart, data and how-to windows. The window asks `lapbar details <id>` for the
content, so a stored answer costs no request to Strava.
"""
import os
import subprocess
import sys
from pathlib import Path

from . import charts, system

ROOT = Path(__file__).resolve().parent.parent
ACTIVITY_DIR = ROOT / "activity"
LAUNCHER = ROOT / "bin" / "lapbar"
WINDOW_SIZE = (760, 820)


def open_window(activity_id: int, theme: dict | None = None) -> int:
    """Start the details window for an activity and float it. Returns the window process id."""
    env = {**os.environ, **(theme or {}), "LAPBAR_ACTIVITY": str(int(activity_id)), "LAPBAR_BIN": str(LAUNCHER),
           "LAPBAR_ASSETS": str(charts.ASSETS_DIR), "LAPBAR_PYTHON": sys.executable}
    proc = subprocess.Popen([system.tool("quickshell"), "-p", str(ACTIVITY_DIR)], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    charts.float_window(proc.pid, size=WINDOW_SIZE)
    return proc.pid
