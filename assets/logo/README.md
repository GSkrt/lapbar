# LapBar logo

`lapbar_horiz_white.svg` and `lapbar_horiz_black.svg` are the logo: the name and a dashed lap trail with arrows.
`black` is for light backgrounds and `white` for dark ones.

`../icon.png` is the app icon (the trail on a dark rounded square), built from `source/lapbar_icon.svg`. It is what
the setup wizard suggests as the icon for your own Strava app.

## The shipped files contain no fonts

The letters are outlines, and the dashes and arrowheads are plain filled shapes (no strokes, no markers, no
font names in the styles). That way the logo looks the same on every system, and it renders in Qt, whose SVG
support ignores markers and would otherwise lose the arrowheads. A test in `tests/test_assets.py` fails if a font,
a `<text>` element or a marker sneaks back in.

## Editing the logo

`source/lapbar_horiz_black_editable.svg` is the original drawing, with live text (Roboto Condensed) and a dashed,
marked stroke, so it stays editable in Inkscape. `source/lapbar_horiz_white_editable.svg` is the same drawing with
the colours inverted (`#000000` to `#ffffff`, `#4d4d4d` to `#b2b2b2`, `#6c6c6c` to `#939393`). Edit these, then
regenerate the shipped files:

1. Save a copy, ungroup everything, then Path > Object to Path (letters), then Path > Stroke to Path (dashes and
   markers). Save as plain SVG.
2. Remove any `font-*` and `-inkscape-font-specification` properties left in the styles.
3. Render it next to the editable source and check that it looks the same.

The logo and the icon are part of this project and released under the same license as the code,
GPL-3.0-or-later. Please do not use them in a way that suggests your project is LapBar or is endorsed by it.

The popup and the chart window show this logo on top and larger than Strava's "Powered by Strava" logo, as
Strava's brand guidelines ask (see `../strava/NOTICE.md`).
