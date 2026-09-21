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


def test_icons_follow_stravas_meaning():
    # A medal for a personal record, a cup for a top place on a segment, a thumbs up for kudos.
    assert "kudos: 0xF0513" in PANEL and "pr: 0xF0987" in PANEL and "kom: 0xF0538" in PANEL
    assert 'icon("trophy")' not in PANEL


def test_records_kudos_and_comments_are_one_line_in_the_popup_and_a_window_holds_the_lists():
    header = PANEL.index("// ---------- header ----------")
    section = PANEL.index("// ---------- records, kudos and comments")
    route = PANEL.index("// ---------- route trace ----------")
    assert header < section < route                                   # right under the description, above the route
    assert "root.achievementsSummary" in PANEL and "comments" in PANEL.split("achievementsSummary: {")[1][:500]
    assert '"activity", String(root.shown.id)' in PANEL and "onClicked: root.openDetails()" in PANEL
    for gone in ("shownRecords", "shownKudoers", "detailsProcess", "achievementsPref"):
        assert gone not in PANEL                                      # the lists moved out of the popup


def test_the_calendar_pages_back_to_the_earliest_stored_month_and_reads_older_years_on_demand():
    assert 'setting("historyYears", 99)' in PANEL and 'cmd.push("--history-years"' in PANEL
    assert "readonly property int earliestIdx" in PANEL and "root.viewYear * 12 + root.viewMonth > root.earliestIdx" in PANEL
    assert "shiftMonth(-12)" in PANEL and "shiftMonth(12)" in PANEL                 # jump a year at a time
    assert '"history", String(year)' in PANEL and "onViewYearChanged" in PANEL      # an older year is read when the calendar reaches it
    assert "activitiesOfYear(parseInt(key.slice(0, 4)))" in PANEL


def test_the_calendar_marks_days_whose_full_data_is_stored_and_shows_the_progress():
    assert "archivedSet" in PANEL and "local: root.dayArchived(key)" in PANEL
    assert "cell.modelData.local === true" in PANEL and '"full data stored"' in PANEL   # the dot and its tooltip
    assert "readonly property string archiveLine" in PANEL and "Full time series stored: " in PANEL
    assert 'setting("downloadHistory", 12)' in PANEL


def test_the_menu_opens_the_data_window_and_the_window_has_all_its_parts():
    assert 'action: "manage"' in PANEL and '"manage",' in PANEL.split("function openManage")[1][:300]
    manage = Path(__file__).resolve().parent.parent / "manage"
    for name in ("shell.qml", "Theme.qml", "Btn.qml", "Meter.qml", "Heading.qml", "Caption.qml", "Check.qml", "DatePicker.qml"):
        assert (manage / name).is_file(), name
    window = (manage / "shell.qml").read_text()
    assert window.count("{") == window.count("}")
    for needle in ("Fetch history back to", "Fetching, day by day", "Export to DuckDB", "DuckDB is not installed",
                   "What is in the database", "Notes", "Save the database in", "Keep it up to date", "downloads DuckDB's spatial extension once (about 80 MB)"):
        assert needle in window, needle
    assert "component " not in window        # inline components cannot see the window's ids; the parts are files
    assert '"prefs", "--history-from"' in window and '"prefs", "--continuous"' in window and '"export"' in window
    assert '"prefs", "--spatial"' in window
    # no typing of dates or paths: a calendar and the desktop's folder dialog
    assert "DatePicker {" in window and '"pick-folder"' in window and "Choose folder" in window
    assert "TextInput" not in window and "Field {" not in window


def test_motivational_quotes_are_a_radio_group_in_the_popup_not_a_menu_item():
    assert "Motivational quotes" in PANEL
    for label in ('"Silent"', '"Motivational"', '"Drill sergeant"'):
        assert label in PANEL
    assert '["prefs", "--coach-tone", radio.modelData.id]' in PANEL
    assert 'action: "coach"' not in PANEL and "Nudges" not in PANEL
    assert "notifyCoach" not in PANEL and "coach_events" not in PANEL     # popups come from the CLI, not the widget


def test_excuses_are_chips_in_the_window_and_marked_on_the_calendar():
    assert 'model: [["tired", "weather", "time"], ["unwell", "rest"]]' in PANEL
    assert 'root.coachSet(["excuse", chip.modelData])' in PANEL
    assert 'excuse: root.excuses[key] || ""' in PANEL and "skipped: " in PANEL
    assert "cell.modelData.excuse" in PANEL


def test_a_third_column_holds_the_calendar_totals_controls_and_the_strava_credit():
    col = PANEL[PANEL.index("id: thirdCol"):]
    for part in ("// ---------- calendar of active days", "id: totalsRow", "source: root.stravaLogo"):
        assert part in col
    middle = PANEL[PANEL.index("id: rightCol"):PANEL.index("id: thirdCol")]
    assert "// \"skipping today?\"" in middle and "// \"Motivational quotes\"" in middle
    assert "(width - spacing * 2) / 3" in PANEL and "root.wide ? 1000 : 340" in PANEL
    left = PANEL[PANEL.index("id: leftCol"):PANEL.index("id: rightCol")]
    assert "visible: !!root.summary\n          source: root.stravaLogo" not in left       # the credit left the left column


def test_the_fitness_plot_fills_the_middle_column_and_the_credit_sits_in_the_popup_corner():
    assert "function fitPlot()" in PANEL and "fitnessPlot.width * 1.3" in PANEL           # grows to fill, in proportion
    assert PANEL.count("onImplicitHeightChanged: Qt.callLater(root.fitPlot)") == 3
    corner = PANEL[PANEL.index("the required credit, in the popup's bottom-right corner"):]
    assert "anchors.right: parent.right" in corner and "anchors.bottom: parent.bottom" in corner
