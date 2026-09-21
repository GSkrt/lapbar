"""Unit tests for help/logic.js (the Markdown splitter of the how-to window), run under plain node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

LOGIC = Path(__file__).resolve().parent.parent / "help" / "logic.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def js(expression: str):
    source = LOGIC.read_text().replace(".pragma library", "")
    out = subprocess.run(["node", "-e", source + f"\nprocess.stdout.write(JSON.stringify({expression}));"],
                         capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_text_and_images_become_separate_blocks_in_order():
    md = "# Title\\n\\nSome text.\\n\\n![The popup](screenshots/popup.png)\\n\\nMore text.\\n"
    assert js(f'splitMarkdown("{md}")') == [
        {"type": "text", "text": "# Title\n\nSome text."},
        {"type": "image", "alt": "The popup", "src": "screenshots/popup.png"},
        {"type": "text", "text": "More text."}]


def test_an_image_inside_a_code_fence_stays_text_and_inline_images_are_left_alone():
    fence = "```\\n![not an image](x.png)\\n```"
    assert js(f'splitMarkdown("{fence}")') == [{"type": "text", "text": "```\n![not an image](x.png)\n```"}]
    inline = "See ![icon](i.png) here"
    assert js(f'splitMarkdown("{inline}")')[0]["type"] == "text"


def test_empty_input_and_back_to_back_images():
    assert js('splitMarkdown("")') == []
    two = "![a](a.png)\\n![b](b.png)"
    assert [b["src"] for b in js(f'splitMarkdown("{two}")')] == ["a.png", "b.png"]


def test_image_addresses_are_resolved_against_the_docs_folder():
    assert js('imageSource("/home/me/lapbar/docs/", "screenshots/popup.png")') == "file:///home/me/lapbar/docs/screenshots/popup.png"
    assert js('imageSource("/x", "/abs/pic.png")') == "file:///abs/pic.png"
    assert js('imageSource("/x", "https://example.org/p.png")') == "https://example.org/p.png"


def test_the_real_help_text_splits_into_text_and_its_eight_pictures():
    real = json.dumps((LOGIC.parent.parent / "docs" / "help.md").read_text())
    blocks = js(f"splitMarkdown({real})")
    images = [b["src"] for b in blocks if b["type"] == "image"]
    assert images == ["screenshots/popup-guide.png", "screenshots/menu-main.png", "screenshots/menu-interval.png",
                      "screenshots/menu-ftp.png", "screenshots/activity-window.png", "screenshots/fitness.png",
                      "screenshots/chart.png", "screenshots/data-window.png"]
    assert all((LOGIC.parent.parent / "docs" / src).is_file() for src in images)
