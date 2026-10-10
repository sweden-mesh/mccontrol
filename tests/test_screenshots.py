"""A18: scripts/screenshots.py -- its colour terminal, and that the fleet it shows is invented.
The PNG step needs headless Chromium and is not run here; the HTML before it is."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import screenshots  # noqa: E402


def _text(screen):
    return ["".join(ch for ch, _a in row).rstrip() for row in screen.grid]


def test_the_terminal_keeps_colour_insert_mode_scrolling_and_the_deferred_wrap():
    s = screenshots.Screen(10, 4)
    s.feed("\x1b[1;1H\x1b[38;5;214;48;5;234mAB\x1b[0mC")
    assert s.grid[0][0] == ("A", (214, 234, False, False, False, False)) and s.grid[0][2][1][0] is None
    s.feed("\x1b[1;2H\x1b[4hX\x1b[4l")                       # insert mode shifts the rest right
    assert _text(s)[0] == "AXBC"
    s.feed("\x1b[2;1Hrow2\x1b[1;4r\x1b[1T")                   # scroll down one in the region
    assert _text(s)[:3] == ["", "AXBC", "row2"]
    s.feed("\x1b[4;1H0123456789Z")                             # the 11th character wraps only when it comes
    assert _text(s)[3] == "Z" and _text(s)[2] == "0123456789"
    html = screenshots.to_html(s, "t")
    assert "#ffaf00" in html and "font:15px/18px" in html     # 256-colour 214 as the palette has it


def test_the_fleet_is_invented_and_the_board_shows_it(tmp_path):
    screenshots.check_invented()
    home, script, beacon = screenshots.build_home(tmp_path)
    devices = json.loads((home / "devices.json").read_text())
    real = {n["publicKey"][:8].upper() for n in json.loads((ROOT / "tests" / "data" / "beacon-fake.json").read_text())["/nodes"]["items"]}
    assert len(devices) == 10 and not {d["node"] for d in devices} & real
    assert {d["region"] for d in devices} == set(screenshots.REGIONS)
    screen = screenshots.capture("Monolith", 80, 24, [(screenshots.DOWN, 0.4)], home, script, beacon)
    text = "\n".join(_text(screen))
    assert "Church-Spire" in text and "10 devices" in text and "HISTORY" in text and "STATS CHURCH-SPIRE" in text
    assert not any(node in text for node in real)
