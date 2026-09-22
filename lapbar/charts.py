"""Open the chart window for an activity.

The window is its own Quickshell process (see ../charts/), not part of the bar, so a problem in the
charts can never take the bar down. Hyprland tiles new windows by default, so after it opens we float,
size and centre it with hyprctl. Hyprland 0.55+ takes Lua (`hyprctl eval`); older versions take the classic
`hyprctl dispatch` syntax, which is the fallback. If hyprctl is missing the window simply stays where the
compositor put it.
"""
import json
import os
import subprocess
import time
from pathlib import Path

from . import system

CHARTS_DIR = Path(__file__).resolve().parent.parent / "charts"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"      # holds strava/ and logo/
WINDOW_W, WINDOW_H = 1240, 820      # preferred size, shrunk to fit small screens
SCREEN_FRACTION = 0.9
WAIT_FOR_WINDOW = 6.0               # seconds to wait for the window to appear before giving up on floating it


def _hyprctl(*args: str) -> str | None:
    try:
        done = subprocess.run([system.tool("hyprctl"), *args], capture_output=True, text=True, timeout=3)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def _json(*args: str):
    out = _hyprctl(*args)
    try:
        return json.loads(out) if out else None
    except json.JSONDecodeError:
        return None


def _lua(code: str) -> bool:
    """Run Lua in Hyprland; True only if it reported ok (older versions have no `eval`)."""
    out = _hyprctl("eval", code)
    return bool(out) and out.strip().startswith("ok")


def window_geometry(monitor: dict, size: tuple[int, int] | None = None) -> tuple[int, int, int, int]:
    """(width, height, x, y) of a centred window, in Hyprland's logical pixels, for the given monitor."""
    scale = monitor.get("scale") or 1
    logical_w, logical_h = monitor["width"] / scale, monitor["height"] / scale
    wanted_w, wanted_h = size or (WINDOW_W, WINDOW_H)
    width = int(min(wanted_w, logical_w * SCREEN_FRACTION))
    height = int(min(wanted_h, logical_h * SCREEN_FRACTION))
    x = int(monitor.get("x", 0) + (logical_w - width) / 2)
    y = int(monitor.get("y", 0) + (logical_h - height) / 2)
    return width, height, x, y


def _place(pid: int, w: int, h: int, x: int, y: int) -> None:
    sel = f'"pid:{pid}"'
    if _lua(f"hl.dispatch(hl.dsp.window.float({{ action = \"enable\", window = {sel} }}))"):
        _lua(f"hl.dispatch(hl.dsp.window.resize({{ x = {w}, y = {h}, window = {sel} }}))")
        _lua(f"hl.dispatch(hl.dsp.window.move({{ x = {x}, y = {y}, window = {sel} }}))")
        # Omarchy makes every window slightly translucent; a data plot should be fully opaque.
        _lua(f"hl.dispatch(hl.dsp.window.tag({{ tag = \"-default-opacity\", window = {sel} }}))")
        _lua(f"hl.dispatch(hl.dsp.window.set_prop({{ prop = \"opaque\", value = \"true\", window = {sel} }}))")
    else:  # classic dispatcher syntax, for Hyprland versions before Lua config
        target = f"pid:{pid}"
        _hyprctl("--batch", f"dispatch setfloating {target}; dispatch resizewindowpixel exact {w} {h},{target}; "
                            f"dispatch movewindowpixel exact {x} {y},{target}")


def float_window(pid: int, wait: float = WAIT_FOR_WINDOW, sleep=time.sleep, size: tuple[int, int] | None = None) -> bool:
    """Float, size and centre the window owned by `pid`. Returns True if it was found and moved."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        clients = _json("clients", "-j") or []
        if any(c.get("pid") == pid for c in clients):
            break
        sleep(0.15)
    else:
        return False
    monitors = _json("monitors", "-j") or []
    monitor = next((m for m in monitors if m.get("focused")), monitors[0] if monitors else None)
    if not monitor:
        return False
    _place(pid, *window_geometry(monitor, size))
    return True


def open_window(data_path, env: dict | None = None) -> int:
    """Start the chart window on `data_path` and float it. Returns the window process id."""
    child_env = {
        **os.environ, **(env or {}),
        "LAPBAR_CHART_FILE": str(data_path),
        # Quickshell only loads images from inside the folder it runs, so the logo folder is passed as an absolute path.
        "LAPBAR_ASSETS": str(ASSETS_DIR),
    }
    proc = subprocess.Popen(
        [system.tool("quickshell"), "-p", str(CHARTS_DIR)], env=child_env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,  # outlives this command
    )
    float_window(proc.pid)
    return proc.pid
