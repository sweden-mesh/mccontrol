"""Monolith: its pure helpers, its isolation from the backend, and walks of every tile and
overlay under a pseudo-terminal against an in-process API over the scripted broker."""
import ast
import curses
import json
import os
import pathlib
import pty
import re
import select
import sys
import time

from conftest import BEACON_FAKE, NODES, SCRIPT, closed_port
from mccontrol.ui.Monolith import art

PACKAGE = pathlib.Path(__file__).parents[1] / "mccontrol" / "ui" / "Monolith"


# -- the isolation ---------------------------------------------------------------------

def imports(path, package="mccontrol.ui.Monolith"):
    """Every module `path` imports, relative imports resolved against `package`."""
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = ".".join(package.split(".")[:len(package.split(".")) - node.level + 1]) if node.level else ""
            module = ".".join(p for p in (base, node.module) if p)
            names += [module] if node.module else [f"{module}.{a.name}" for a in node.names]
    return names


def test_monolith_imports_the_client_and_nothing_else_of_the_package():
    backend = ("store", "actions", "transport", "beacon", "envelope", "firmware", "denylist", "commands", "directory",
               "helptext", "api", "app", "cli", "ui")
    seen = []
    for path in PACKAGE.glob("*.py"):
        for name in imports(path):
            seen.append(name)
            if name.startswith("mccontrol.ui.Monolith") or name == "mccontrol.apiclient":
                continue
            assert not name.startswith("mccontrol"), f"{path.name} imports {name}"
            assert name.split(".")[0] in sys.stdlib_module_names, f"{path.name} imports {name}, not the stdlib"
    assert "mccontrol.apiclient" in seen and "curses" in seen
    assert not [n for n in seen if n.split(".")[:2] in [["mccontrol", b] for b in backend]
                and not n.startswith("mccontrol.ui.Monolith")]


def test_monolith_has_its_entry_and_a_one_line_description():
    from mccontrol.ui import Monolith
    assert callable(Monolith.run) and "\n" not in Monolith.DESCRIPTION and Monolith.DESCRIPTION.startswith("Monolith")


# -- the pure helpers ------------------------------------------------------------------

def test_a_sparkline_scales_from_the_lowest_to_the_highest_and_fits_its_width():
    assert art.sparkline([1, 2, 3, 4, 5, 6, 7, 8], 8) == "▁▂▃▄▅▆▇█"
    assert art.sparkline([5, 5, 5], 10) == "▄" * 9               # flat sits in the middle, stretched
    assert art.sparkline([1, None, 8], 6) == "▁▁  ██"            # a gap stays a gap
    assert len(art.sparkline(list(range(100)), 20)) == 20         # buckets when there are more values
    assert art.sparkline(list(range(100)), 20)[0] == "▁" and art.sparkline(list(range(100)), 20)[-1] == "█"
    assert art.sparkline([], 10) == "" and art.sparkline([1, 2], 0) == ""
    assert art.sparkline([1, 8], 2, art.ASCII) == " #"


def test_a_bar_fills_to_its_fraction():
    assert art.bar(0.5, 10) == "█" * 5 + "░" * 5
    assert art.bar(2, 4) == "████" and art.bar(-1, 3) == "░░░"


def test_big_digits_and_the_title_are_three_and_two_rows_of_even_width():
    rows = art.bigtext("12:34")
    assert len(rows) == 3 and len({len(r) for r in rows}) == 1
    title = art.title()
    assert len(title) == 2 and len({len(r) for r in title}) == 1 and len(title[0]) < 80
    assert set("".join(art.bigtext("09", art.ASCII))) <= set("#',. ")
    assert art.duration(3725) == "01:02:05" and art.duration(90061) == "1d 01:01"


def _inside(rect, w, h):
    y, x, rw, rh = rect
    return y >= 0 and x >= 0 and y + rh <= h and x + rw <= w and rw > 0 and rh > 0


def _overlap(a, b):
    return not (a[1] + a[2] <= b[1] or b[1] + b[2] <= a[1] or a[0] + a[3] <= b[0] or b[0] + b[3] <= a[0])


def test_the_grid_reflows_at_80x24_120x40_and_200x60():
    for (w, h), columns, has_clock, head in (((80, 24), 2, False, 1), ((120, 40), 3, True, 3), ((200, 60), 3, True, 3)):
        tiles = art.layout(w, h)
        assert {"header", "hints", "devices", "device", "feed", "status"} <= set(tiles)
        assert ("clock" in tiles) == has_clock and tiles["header"][3] == head
        names = list(tiles)
        for name in names:
            assert _inside(tiles[name], w, h), (w, h, name, tiles[name])
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                assert not _overlap(tiles[a], tiles[b]), (w, h, a, b)
        covered = sum(r[2] * r[3] for r in tiles.values())
        assert covered == w * h, (w, h, covered)                  # the grid uses what it gets
        assert len({tiles[n][1] for n in ("devices", "device", "status")}) == columns
        assert tiles["devices"][2] >= 44 and tiles["device"][3] >= 7 and tiles["feed"][3] >= 7
    assert art.layout(79, 24) == {} and art.layout(80, 23) == {}


def test_the_palettes_have_one_accent_and_a_terminal_without_colour_still_tells_the_roles_apart():
    for name in art.PALETTES:
        assert all(0 <= c < 8 for _n, fg, bg in art.pairs(name, 8) for c in (fg, bg))
        assert any(c >= 8 for _n, fg, bg in art.pairs(name, 256) for c in (fg, bg))
        fg, _dim, accent, bg = art.PALETTES[name][256]
        assert len({fg, accent, bg}) == 3
    mono = art.roles(None)
    assert set(mono) == set(art.ROLES)
    assert mono["cursor"] & curses.A_REVERSE and mono["alert"] & curses.A_REVERSE and mono["bright"] & curses.A_BOLD
    assert len({mono[r] for r in ("base", "dim", "bright", "accent", "cursor", "alert")}) == 6
    coloured = art.roles(lambda n: n << 8)
    assert coloured["accent"] == 3 << 8 and coloured["bright"] == (1 << 8) | curses.A_BOLD
    assert art.glyphs("UTF-8") is art.GLYPHS and art.glyphs("ANSI_X3.4-1968") is art.ASCII


def test_the_palette_filter_is_fuzzy_and_ranks_word_starts_first():
    items = ["send a command", "statistics 24 h", "discover a region", "get name", "set name"]
    assert art.ranked("stat", items)[0] == "statistics 24 h"
    assert art.ranked("dr", items) == ["discover a region"]
    assert art.ranked("sn", items)[0] == "set name"
    assert art.ranked("", items) == items and art.ranked("zz", items) == []
    assert art.wrap("one two three", 7) == ["one two", "three"]


# -- the walks -------------------------------------------------------------------------

CHILD = """
import sys
sys.path.insert(0, {cwd!r})
from mccontrol import store, transport
from mccontrol.api import serve
from mccontrol.apiclient import Client
from mccontrol.ui import Monolith
transport.FAKE_WAIT_SECS = {wait}
_server, url = serve(store.Store({home!r}), transport={script!r})
sys.exit(Monolith.run(Client(url)))
"""


def _home(tmp_path, prepare=None):
    from mccontrol import store as _store
    home = tmp_path / "home"
    st = _store.Store(home)
    st.create_key()
    port = closed_port()                    # nothing here may reach a real broker or Beacon
    st.save_config(broker="127.0.0.1", port=port, beacon_url=f"http://127.0.0.1:{port}")
    for name, node, region in NODES:
        st.add_device(name, node, region)
    (home / "history").mkdir(exist_ok=True)
    (home / "history" / "4AC61A7F.jsonl").write_text(json.dumps(
        {"at": "2026-10-07 11:00:00", "command": "region", "topic": "t", "code": 0, "code_name": "ok", "reply": "> * F", "note": ""}) + "\n")
    if prepare:
        prepare(home)
    return home


def walk(tmp_path, keys, cols=80, rows=24, script=None, wait=0.3, prepare=None, settle=1.2, raw=False):
    """Start Monolith in a pty against a fresh API, send `keys` as `(bytes, delay)` pairs,
    and return the text drawn (escapes stripped) with the home directory."""
    import fcntl, signal, struct, termios
    home = _home(tmp_path, prepare)
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.environ.update(TERM="xterm", COLUMNS=str(cols), LINES=str(rows), MCCONTROL_HOME=str(home),
                          MCCONTROL_FAKE_BEACON=BEACON_FAKE, LANG="C.UTF-8", LC_ALL="C.UTF-8")
        os.environ.pop("MCCONTROL_FAKE_RESULTS", None)
        code = CHILD.format(cwd=os.getcwd(), wait=wait, home=str(home), script=dict(script or SCRIPT))
        os.execvp(sys.executable, [sys.executable, "-c", code])
    out = b""

    def drain(secs):
        nonlocal out
        end = time.time() + secs
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    return

    drain(settle)
    for k, delay in keys:
        os.write(fd, k)
        drain(delay)
    os.write(fd, b"\x0c")                   # Ctrl+L: the whole last frame is drawn again
    drain(1.0)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    if raw:
        return out.decode("utf-8", "replace"), home
    return re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0-9A-B]|\x1b[=>]", b" ", out).decode("utf-8", "replace"), home


DOWN, UP, ESC, ENTER, TAB = b"\x1bOB", b"\x1bOA", b"\x1b", b"\r", b"\t"
