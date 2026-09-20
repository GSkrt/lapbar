"""Static checks on Panel.qml for the parts that protect the user's Strava request allowance."""
import re
from pathlib import Path

PANEL = (Path(__file__).resolve().parent.parent / "Panel.qml").read_text()


def test_every_refresh_says_whether_it_is_manual_or_automatic():
    calls = re.findall(r"root\.refresh\(([^)]*)\)", PANEL)
    assert calls, "no refresh calls found"
    for arg in calls:
        manual = arg.split(",")[0].strip()          # a second argument, if any, is the FTP just saved
        assert manual in ("true", "false"), f"root.refresh({arg}) must say explicitly if it is manual"


def test_only_the_timer_refreshes_automatically():
    autos = [m.start() for m in re.finditer(r"root\.refresh\(false\)", PANEL)]
    assert len(autos) == 1
    before = PANEL[max(0, autos[0] - 300):autos[0]]
    assert "Timer {" in before and "refreshIntervalSec" in before          # it is the interval timer


def test_manual_refreshes_are_passed_to_the_cli_as_manual():
    assert 'cmd.push("--manual")' in PANEL and "manual !== false" in PANEL


def test_the_interval_can_go_down_to_a_minute_and_the_menu_offers_it():
    assert 'Math.max(60, Number(setting("refreshIntervalSec"' in PANEL
    assert "{ sec: 60," in PANEL and "refreshIntervalSec" in PANEL and '"bar", "set", root.moduleName' in PANEL


def test_settings_written_from_the_widget_get_the_environment_omarchy_needs():
    # `omarchy bar set` aborts with "OMARCHY_PATH: unbound variable" in a stripped environment, which made the
    # FTP and refresh-interval menu entries do nothing at all.
    assert re.search(r'"OMARCHY_PATH"\]', PANEL) and 'OMARCHY_PATH: "/usr/share/omarchy"' in PANEL
    for call in re.findall(r'"bar", "set".{0,400}', PANEL, re.S):
        assert "desktopEnvironment" in PANEL[PANEL.index(call):PANEL.index(call) + 700] or "ftpProcess" in call


def test_the_menu_can_set_the_ftp_and_the_estimate_button_applies_it_when_clicked():
    assert 'action: "ftp"' in PANEL and "Estimate from my rides" in PANEL
    assert 'onClicked: root.setFtp(root.ftpEstimate.watts)' in PANEL   # the button saves the estimate itself
    assert 'root.ftpDraft = root.ftpEstimate' not in PANEL
    assert PANEL.count("root.setFtp(") == 3                             # Save, Clear and the estimate button; only clicks write it
    assert "ftp_estimate" in PANEL


def test_totals_cover_today_week_month_and_year_with_climb():
    for key in ("summary.today", "summary.week", "summary.month", "summary.year"):
        assert key in PANEL
    assert "t.elevation_m > 0" in PANEL and "cell.info.elevation_m" in PANEL
