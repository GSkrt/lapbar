#!/usr/bin/env python3
"""Draw the numbered markers of the README's popup guide on docs/screenshots/popup.png.

The numbers match the list in README.md ("The popup, and where to find things") and docs/help.md. Positions are
pixels in popup.png (1002 x 1006); when the popup layout changes, retake popup.png and adjust MARKS.
Needs Pillow: python3 -m pip install pillow
"""
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
MARKS = [  # number, x, y
    (1, 5, 92), (2, 274, 100), (3, 330, 100), (4, 5, 158), (5, 100, 540), (6, 46, 681), (7, 171, 681),
    (8, 5, 712), (9, 5, 910), (10, 5, 981),                                          # left column
    (11, 334, 66), (12, 564, 23), (13, 334, 591), (14, 334, 697), (15, 334, 781),   # middle column
    (16, 664, 26), (17, 664, 299), (18, 812, 981),                                  # right column
]


def main() -> None:
    font_file = subprocess.run(["fc-match", "-f", "%{file}", "DejaVu Sans:bold"], capture_output=True, text=True).stdout
    im = Image.open(ROOT / "docs/screenshots/popup.png").convert("RGB")
    draw = ImageDraw.Draw(im)
    font = ImageFont.truetype(font_file, 13)
    for number, x, y in MARKS:
        r = 9
        draw.ellipse((x - r, y - r, x + r, y + r), fill="#ffd54a", outline="#1b1b22", width=2)
        text = str(number)
        draw.text((x - draw.textlength(text, font=font) / 2, y - 8), text, fill="#1b1b22", font=font)
    im.save(ROOT / "docs/screenshots/popup-guide.png", optimize=True)


if __name__ == "__main__":
    main()
