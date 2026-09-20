"""The Strava attribution has to actually ship: every asset the QML points at must exist."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QML = [ROOT / "Panel.qml", ROOT / "charts" / "shell.qml"]


def referenced_logos(qml: Path) -> set[str]:
    # Panel.qml loads it relative to itself; the chart window gets the folder from the launcher (LAPBAR_ASSETS).
    return set(re.findall(r"api_logo_pwrdBy_strava_horiz_", qml.read_text()))


def test_both_windows_show_the_powered_by_strava_logo():
    for qml in QML:
        assert referenced_logos(qml), f"{qml.name} does not show the Powered by Strava logo"


def test_the_logo_variants_for_dark_and_light_themes_exist():
    for colour in ("white", "black"):
        assert (ROOT / "assets" / "strava" / f"api_logo_pwrdBy_strava_horiz_{colour}.svg").is_file()
        assert (ROOT / "assets" / "strava" / f"api_logo_pwrdBy_strava_horiz_{colour}.png").is_file()


def test_lapbar_logo_ships_in_both_colours_and_both_windows_show_it_larger_than_strava_s():
    for colour in ("white", "black"):
        svg = (ROOT / "assets" / "logo" / f"lapbar_horiz_{colour}.svg").read_text()
        assert "<svg" in svg and re.search(r"<title[^>]*>LapBar</title>", svg)
        assert "<text" not in svg, f"{colour} logo has live text: outline it, or it depends on an installed font"
        assert "font" not in svg, f"{colour} logo still names a font"
        assert "marker" not in svg, f"{colour} logo uses markers, which Qt's SVG renderer ignores: convert them to shapes"
    for editable in ("black", "white"):
        assert (ROOT / "assets" / "logo" / "source" / f"lapbar_horiz_{editable}_editable.svg").is_file()
    assert (ROOT / "assets" / "icon.png").is_file()
    for qml in QML:
        assert "lapbar_horiz_" in qml.read_text(), f"{qml.name} does not show the LapBar logo"
    panel = (ROOT / "Panel.qml").read_text()
    assert re.search(r"source: root\.lapbarLogo\s+width: parent\.width", panel), "LapBar's logo should fill the left column"
    theirs = [int(h) for h in re.findall(r"source: root\.stravaLogo\s+sourceSize\.height: \d+\s+height: Style\.space\((\d+)\)", panel)]
    assert theirs and all(h <= 20 for h in theirs), "Strava's logo must stay small next to LapBar's"


def test_the_trademark_notice_ships_with_the_logos_and_says_they_are_not_gpl():
    notice = (ROOT / "assets" / "strava" / "NOTICE.md").read_text()
    assert "Strava's trademarks" in notice and "**not** covered by LapBar's GPL license" in notice


def test_links_to_strava_use_the_exact_required_wording_and_colour():
    for qml in QML:
        text = qml.read_text()
        assert '"View on Strava"' in text, f"{qml.name}: link text must be exactly 'View on Strava'"
        assert "#FC5200" in text, f"{qml.name}: link colour must be Strava's #FC5200"


def test_the_readme_thanks_strava_and_stravalib_and_keeps_the_disclaimer():
    readme = (ROOT / "README.md").read_text()
    assert "Powered by Strava" in readme and "stravalib" in readme
    assert "not affiliated with or endorsed by Strava" in readme


def test_stravalib_is_thanked_honestly_lapbar_does_not_depend_on_it():
    readme = (ROOT / "README.md").read_text()
    assert "does\nnot use it" in readme or "does not use it" in readme
    source = "\n".join(p.read_text() for p in (ROOT / "lapbar").rglob("*.py"))
    assert "import stravalib" not in source and "from stravalib" not in source


def test_canvas_fonts_are_quoted_so_family_names_with_spaces_work():
    """Regression: an unquoted 'JetBrainsMono Nerd Font' made Qt's canvas fall back to a default font."""
    for qml in (ROOT / "charts" / "SeriesPanel.qml", ROOT / "charts" / "shell.qml"):
        for line in qml.read_text().splitlines():
            if re.search(r'"(bold )?\d+px "', line):          # every place that builds a canvas font string
                assert "cssFamily" in line or "cssFont" in line, f"{qml.name}: {line.strip()}"


def test_the_readme_screenshots_exist_and_are_real_pictures():
    readme = (ROOT / "README.md").read_text()
    for name in ("popup.png", "chart.png"):
        assert f"docs/screenshots/{name}" in readme
        data = (ROOT / "docs" / "screenshots" / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 20_000
