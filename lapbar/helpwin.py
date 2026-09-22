"""The how-to window: docs/help.md drawn inside the app (see ../help/), opened from the popup's menu.

Its own Quickshell process, like the chart and data windows. Qt Quick renders the Markdown itself, so the guide needs
no library and no network; its pictures come from docs/screenshots.
"""
import os
import subprocess
from pathlib import Path

from . import charts, system

ROOT = Path(__file__).resolve().parent.parent
HELP_DIR = ROOT / "help"
HELP_FILE = ROOT / "docs" / "help.md"
DOCS_DIR = ROOT / "docs"
WINDOW_SIZE = (980, 820)


def open_window(theme: dict | None = None) -> int:
    """Start the how-to window and float it. Returns the window process id."""
    env = {**os.environ, **(theme or {}), "LAPBAR_HELP_FILE": str(HELP_FILE), "LAPBAR_DOCS": str(DOCS_DIR),
           "LAPBAR_ASSETS": str(charts.ASSETS_DIR)}
    proc = subprocess.Popen([system.tool("quickshell"), "-p", str(HELP_DIR)], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    charts.float_window(proc.pid, size=WINDOW_SIZE)
    return proc.pid
