"""The in-app how-to: docs/help.md drawn by help/shell.qml, opened from the popup's menu."""
import os
import re
from pathlib import Path

import pytest

from lapbar import charts, cli, helpwin

ROOT = Path(__file__).resolve().parent.parent
GUIDE = (ROOT / "docs" / "help.md").read_text()
PANEL = (ROOT / "Panel.qml").read_text()


def test_the_guide_covers_the_bar_button_the_popup_and_every_menu_entry():
    for text in ("**Kudos and PRs**", "middle-click refreshes now", "right-click mutes", "Refresh every", "FTP...", "Manage data...",
                 "Update credentials or sign in...", "Open Strava API settings", "About LapBar", "Reset account..."):
        assert text in GUIDE, text
    for entry in ("Manage data", "How to use", "Update credentials or sign in", "Open Strava API settings", "About LapBar", "Reset account"):
        assert entry in PANEL, entry


def test_the_guide_stays_plain_markdown_the_window_can_draw():
    assert "<" not in re.sub(r"`[^`]*`", "", GUIDE), "no HTML: Qt's Markdown text would show it literally"
    assert not re.search(r"(?<!!)\[[^\]]+\]\([^)]+\)", GUIDE), "no links: their colour cannot follow the theme"
    assert "duckdb" not in GUIDE.lower()
    images = re.findall(r"^!\[[^\]]*\]\(([^)]+)\)$", GUIDE, re.M)
    assert len(images) >= 4 and all((ROOT / "docs" / src).is_file() for src in images)


def test_the_menu_opens_the_guide_with_the_bar_theme_also_before_setup():
    assert PANEL.count('action: "howto"') == 2                      # in the menu, and in the one shown before setup
    assert 'action === "howto") root.openHowto()' in PANEL
    assert '"howto",' in PANEL.split("function openHowto")[1][:300]


def test_the_window_has_its_parts_and_no_more_dependencies():
    for name in ("shell.qml", "logic.js", "Theme.qml"):
        assert (ROOT / "help" / name).is_file(), name
    assert (ROOT / "help" / "Theme.qml").read_text() == (ROOT / "manage" / "Theme.qml").read_text()      # one theme, two copies
    window = (ROOT / "help" / "shell.qml").read_text()
    assert window.count("{") == window.count("}") and "Text.MarkdownText" in window and "Keys.onPressed" in window
    assert "import QtWebEngine" not in window and "WebView" not in window


def test_the_howto_command_starts_the_window_with_the_guide_and_the_screenshots(monkeypatch, capsys):
    seen = {}
    class Proc:
        pid = 9
    monkeypatch.setattr(helpwin.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, env=kw["env"]) or Proc())
    monkeypatch.setattr(helpwin.charts, "float_window", lambda pid, **kw: seen.update(size=kw.get("size")))
    with pytest.raises(SystemExit):
        cli.main(["howto", "--fg", "#fff", "--font", "Mono"])
    assert os.path.basename(seen["cmd"][0]) == "quickshell" and seen["cmd"][1] == "-p" and seen["cmd"][2].endswith("/help")
    assert seen["env"]["LAPBAR_HELP_FILE"].endswith("docs/help.md") and seen["env"]["LAPBAR_DOCS"].endswith("/docs")
    assert seen["env"]["LAPBAR_FG"] == "#fff" and seen["env"]["LAPBAR_FONT"] == "Mono" and seen["size"] == helpwin.WINDOW_SIZE


def test_a_window_can_ask_for_its_own_size_and_still_fits_small_screens():
    monitor = {"width": 1920, "height": 1080, "scale": 1, "x": 0, "y": 0}
    assert charts.window_geometry(monitor)[:2] == (1240, 820)                           # unchanged for the charts
    assert charts.window_geometry(monitor, (980, 820))[:2] == (980, 820)
    small = {"width": 1280, "height": 720, "scale": 1, "x": 0, "y": 0}
    assert charts.window_geometry(small, (980, 820))[:2] == (980, 648)
