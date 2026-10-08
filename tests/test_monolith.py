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


class Terminal:
    """Just enough of an xterm to keep the screen curses draws: cursor moves, erases,
    inserts and deletes, scrolling. Colours and modes are ignored."""

    CSI = re.compile(r"\x1b\[([?>]?)([0-9;]*)([A-Za-z@`])")
    OTHER = re.compile(r"\x1b(\][^\x07]*\x07|[()][0-9A-B]|[^\[\]()])")

    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.grid = [[" "] * cols for _ in range(rows)]
        self.y = self.x = self.top = 0
        self.bottom, self.last, self.pending = rows - 1, " ", ""

    def text(self):
        return "\n".join("".join(r).rstrip() for r in self.grid)

    def _scroll(self, at, up=True):
        if up:
            del self.grid[at]
            self.grid.insert(self.bottom, [" "] * self.cols)
        else:
            del self.grid[self.bottom]
            self.grid.insert(at, [" "] * self.cols)

    def feed(self, data):
        data, self.pending, i = self.pending + data, "", 0
        while i < len(data):
            c = data[i]
            if c == "\x1b":
                m = self.CSI.match(data, i) or self.OTHER.match(data, i)
                if not m:
                    self.pending = data[i:]           # a sequence cut by the read
                    return
                i = m.end()
                if m.re is self.CSI and not m.group(1):
                    self._csi([int(v) if v else 0 for v in m.group(2).split(";")] if m.group(2) else [], m.group(3))
                elif m.group(0) == "\x1bM":
                    self._scroll(self.top, up=False) if self.y == self.top else setattr(self, "y", self.y - 1)
                continue
            i += 1
            if c == "\r":
                self.x = 0
            elif c == "\n":
                self._scroll(self.top) if self.y == self.bottom else setattr(self, "y", self.y + 1)
            elif c == "\b":
                self.x = max(0, self.x - 1)
            elif c >= " ":
                self.grid[self.y][min(self.x, self.cols - 1)] = self.last = c
                self.x = min(self.cols, self.x + 1)

    def _csi(self, a, f):
        n = (a[0] if a and a[0] else 1)
        row = self.grid[self.y]
        if f in "Hf":
            self.y, self.x = n - 1, (a[1] if len(a) > 1 and a[1] else 1) - 1
        elif f in "ABCD":
            self.y = max(0, min(self.rows - 1, self.y + {"A": -n, "B": n}.get(f, 0)))
            self.x = max(0, min(self.cols - 1, self.x + {"C": n, "D": -n}.get(f, 0)))
        elif f in "G`":
            self.x = n - 1
        elif f == "d":
            self.y = n - 1
        elif f == "K":
            mode = a[0] if a else 0
            lo, hi = {0: (self.x, self.cols), 1: (0, self.x + 1)}.get(mode, (0, self.cols))
            row[lo:hi] = [" "] * (hi - lo)
        elif f == "J":
            mode = a[0] if a else 0
            rows = range(self.rows) if mode == 2 else range(self.y + 1, self.rows) if mode == 0 else ()
            if mode == 0:
                row[self.x:] = [" "] * (self.cols - self.x)
            for r in rows:
                self.grid[r] = [" "] * self.cols
        elif f == "X":
            row[self.x:self.x + n] = [" "] * len(row[self.x:self.x + n])
        elif f == "P":
            del row[self.x:self.x + n]
            row += [" "] * (self.cols - len(row))
        elif f == "@":
            row[self.x:self.x] = [" "] * n
            del row[self.cols:]
        elif f == "b":
            for _ in range(n):
                if self.x < self.cols:
                    row[self.x] = self.last
                    self.x += 1
        elif f == "r":
            self.top, self.bottom = n - 1, (a[1] if len(a) > 1 and a[1] else self.rows) - 1
            self.y = self.x = 0
        elif f in "LM":
            for _ in range(n):
                self._scroll(self.y, up=f == "M")
        elif f == "S":
            for _ in range(n):
                self._scroll(self.top)


EXITED = []


def walk(tmp_path, keys, cols=80, rows=24, script=None, wait=0.3, prepare=None, settle=1.2, env=None):
    """Start Monolith in a pty against a fresh API, send `keys` as `(bytes, delay)` pairs,
    and return every screen it showed (one after the other, as text) with the home
    directory. The last screen is `walk.screen`."""
    import codecs, fcntl, signal, struct, termios
    home = _home(tmp_path, prepare)
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.environ.update(TERM="xterm", COLUMNS=str(cols), LINES=str(rows), MCCONTROL_HOME=str(home),
                          MCCONTROL_FAKE_BEACON=BEACON_FAKE, LANG="C.UTF-8", LC_ALL="C.UTF-8")
        os.environ.pop("MCCONTROL_FAKE_RESULTS", None)
        os.environ.update(env or {})
        code = CHILD.format(cwd=os.getcwd(), wait=wait, home=str(home), script=dict(script or SCRIPT))
        os.execvp(sys.executable, [sys.executable, "-c", code])
    term, decode, screens = Terminal(cols, rows), codecs.getincrementaldecoder("utf-8")("replace"), []

    def drain(secs):
        end = time.time() + secs
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    term.feed(decode.decode(os.read(fd, 65536)))
                except OSError:
                    return
                if not screens or screens[-1] != term.text():
                    screens.append(term.text())

    drain(settle)
    for k, delay in keys:
        os.write(fd, k)
        drain(delay)
    os.write(fd, b"\x0c")                   # Ctrl+L: the whole last frame is drawn again
    drain(1.0)
    EXITED[:] = [os.waitpid(pid, os.WNOHANG)[0] == pid]
    if not EXITED[0]:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
    walk.screen = term.text()
    return "\n".join(screens), home


DOWN, UP, ESC, ENTER, TAB = b"\x1bOB", b"\x1bOA", b"\x1b", b"\r", b"\t"


def _ver(node, tag):
    """A prepare hook: `node` reports firmware `tag`, so the command list admits what that
    build admits (an unknown version counts as the oldest, ar-10)."""
    def prepare(home):
        devs = json.loads((home / "devices.json").read_text())
        next(d for d in devs if d["node"] == node)["ver"] = f"esp32s3-v1.17.1-{tag}-observer-2c079ed7"
        (home / "devices.json").write_text(json.dumps(devs))
    return prepare


def _history(home, node):
    path = home / "history" / f"{node}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_the_board_at_80x24_shows_the_directory_status_device_results_and_the_hints(tmp_path):
    text, _ = walk(tmp_path, [(DOWN, 0.4)])
    assert "M O N O L I T H" in text and "DEVICES" in text and "STATUS" in text and "RESULTS" in text
    assert "Gisebo" in text and "(unnamed)" in text and "FD700369" in text and "3 devices" in text
    assert "BROKER" in text and "127.0.0.1" in text and "REG" in text and "no  :register" in text
    assert "DEVICE GISEBO" in text and "region" in text and "> * F" in text      # its history follows the cursor
    assert re.search(r":\s+palette", text) and re.search(r"q\s+quit", text)
    assert "CLOCK" not in text                                                     # no room at 80x24


def test_the_board_reflows_to_three_columns_with_the_title_and_a_clock_at_120x40(tmp_path):
    text, _ = walk(tmp_path, [], cols=120, rows=40)
    assert "CLOCK" in text and "session" in text and art.title()[0] in text
    assert re.search(r"Batt\s+Seen", text) is None                # 120 columns: the directory is 48 wide
    text, _ = walk(tmp_path / "wide", [], cols=200, rows=60)
    assert "CLOCK" in text and re.search(r"Batt\s+Seen\s+Ver", text)


def test_space_tags_star_inverts_and_the_footer_counts(tmp_path):
    text, _ = walk(tmp_path, [(b" ", 0.4), (b" ", 0.4)])
    assert "2 of 3 tagged" in text and "◆ Far" in text
    text, _ = walk(tmp_path / "b", [(b" ", 0.4), (b"*", 0.5)])
    assert "2 of 3 tagged" in text and "◆ Gisebo" in text


def test_plus_tags_a_region(tmp_path):
    text, _ = walk(tmp_path, [(DOWN, 0.3), (b"+", 0.5), (b"\r", 0.6)])
    assert "Tag every device in which region?" in text and "2 of 3 tagged" in text


def test_c_sends_to_the_tagged_devices_and_the_feed_shows_each_answer(tmp_path):
    text, home = walk(tmp_path, [(DOWN, 0.3), (b" ", 0.3), (b" ", 0.3), (b"c", 0.5), (b"region", 0.3), (b"\r", 2.0)])
    assert "To 2 tagged in JKG" in text
    assert "SEND region" in text and "2 of 2 answered" in text
    assert "> * F" in text and "refused: not-al" in text
    assert _history(home, "65C1D12C")[-1]["command"] == "region"


def test_a_command_that_takes_something_away_asks_first_and_anything_but_y_sends_nothing(tmp_path):
    text, home = walk(tmp_path, [(DOWN, 0.3), (b"c", 0.5), (b"region denyf *", 0.3), (b"\r", 0.8), (b"n", 0.8)])
    assert "CONFIRM" in text and "stops this repeater forwarding every message" in text
    assert not [h for h in _history(home, "4AC61A7F") if "denyf" in h["command"]]
    text, home = walk(tmp_path / "y", [(DOWN, 0.3), (b"c", 0.5), (b"region denyf *", 0.3), (b"\r", 0.8), (b"y", 1.5)])
    assert _history(home, "4AC61A7F")[-1]["command"] == "region denyf *"


def test_tags_across_two_regions_are_refused(tmp_path):
    text, home = walk(tmp_path, [(b" ", 0.3), (b" ", 0.3), (b"c", 0.8)])
    assert "span more than one region" in text


def test_a_sends_to_every_node_in_a_region(tmp_path):
    text, home = walk(tmp_path, [(DOWN, 0.3), (b"a", 0.5), (b"\r", 0.5), (b"get name", 0.3), (b"\r", 2.0)])
    assert "Send to every node in which region?" in text and "To every node in JKG" in text
    assert _history(home, "4AC61A7F")[-1]["note"] == "broadcast"
    assert not _history(home, "FD700369")                     # Far is in GOT and is not asked


def test_a_command_the_node_would_refuse_is_not_sent_and_the_reason_is_said(tmp_path):
    text, home = walk(tmp_path, [(DOWN, 0.3), (b"c", 0.5), (b"set prv.key 00", 0.3), (b"\r", 1.0)])
    assert "the node would refuse" in text
    assert _history(home, "4AC61A7F")[-1]["command"] == "region"


def test_enter_lists_the_commands_greys_what_the_firmware_lacks_and_says_why(tmp_path):
    text, home = walk(tmp_path, [(DOWN, 0.3), (b"\r", 0.8), (b"get stats", 0.5), (b"\r", 0.8)])
    assert "COMMANDS" in text and "greyed: need a newer build than ar-10" in text
    assert "get stats  (needs ar-11)" in text and "needs ar-11; the version is unknown" in text
    assert _history(home, "4AC61A7F")[-1]["command"] == "region"      # nothing sent


def test_a_choice_command_asks_for_its_value_and_sends_it(tmp_path):
    text, home = walk(tmp_path, [(DOWN, 0.3), (b"\r", 0.8), (b"set autoreply <", 0.5), (b"\r", 0.8), (DOWN, 0.3),
                                 (b"\r", 0.8), (b"y", 1.5)], prepare=_ver("4AC61A7F", "ar-11"))
    assert "SET AUTOREPLY" in text and "stops this repeater answering" in text
    assert _history(home, "4AC61A7F")[-1]["command"] == "set autoreply off"


def test_a_text_command_shows_examples_and_sends_what_was_typed(tmp_path):
    text, home = walk(tmp_path, [(DOWN, 0.3), (b"\r", 0.8), (b"region put", 0.5), (b"\r", 0.8), (b"se-jkp *", 0.3),
                                 (b"\r", 1.5)], prepare=_ver("4AC61A7F", "ar-11"))
    assert "Examples:" in text
    assert _history(home, "4AC61A7F")[-1]["command"] == "region put se-jkp *"


def test_the_palette_filters_verbs_and_commands_and_runs_statistics_into_sparklines(tmp_path):
    text, home = walk(tmp_path, [(DOWN, 0.3), (b":", 0.6), (b"stat 24", 0.5), (b"\r", 2.5)])
    assert "PALETTE" in text and "statistics 24 h" in text
    assert "STATISTICS" in text and "Beacon: sampled" in text
    assert re.search(r"BATT\s+[▁▂▃▄▅▆▇█]{5,}", text) and "NOISE" in text
    assert "stats (Beacon)" in json.dumps(json.loads((home / "devices.json").read_text())) or "get stats" in text


def test_esc_cancels_a_running_job_and_the_bar_says_how(tmp_path):
    text, home = walk(tmp_path, [(b"c", 0.5), (b"ver", 0.3), (b"\r", 1.2), (ESC, 1.5)], wait=6)
    assert "Esc cancels" in text and re.search("[█░]{10,}", text)
    assert "cancelled; what landed is kept" in text


def test_d_discovers_a_region_and_adopts_what_is_new_with_its_name(tmp_path):
    text, home = walk(tmp_path, [(b"d", 0.5), (b"\x7f" * 3 + b"JKG", 0.3), (b"\r", 2.0), (b"\r", 1.0)])
    assert "FOUND 3: ADOPT WHICH?" in text and "AB6B9285" in text
    assert "1 device(s) remembered" in text
    devices = {d["node"]: d for d in json.loads((home / "devices.json").read_text())}
    assert devices["AB6B9285"]["name"] == "SE-JKG-UGORptr"


def test_n_reads_names_and_versions_and_r_refreshes_the_fleet(tmp_path):
    text, home = walk(tmp_path, [(DOWN, 0.3), (DOWN, 0.3), (b"n", 2.0), (b"r", 2.0)])
    assert "1 of 1 gave a version; 1 named" in text
    assert "refreshed 1 of 3 from Beacon" in text
    devices = {d["node"]: d for d in json.loads((home / "devices.json").read_text())}
    assert devices["65C1D12C"]["name"] == "SE-JKG-Vaster-65c1" and devices["4AC61A7F"]["fleet"]["battery_mv"]


def test_k_shows_the_provisioning_lines_and_g_replaces_the_key_after_a_confirm(tmp_path):
    text, home = walk(tmp_path, [(b"k", 0.6)], cols=100)
    public = re.search(r"set mqtt\.cmd\.pubkey ([0-9A-F]{64})", text).group(1)
    assert "set mqtt.cmd on" in text and "Not registered with the broker yet" in text
    before = (home / "key.seed").read_text()
    text, home = walk(tmp_path / "g", [(b"k", 0.6), (b"g", 0.6), (b"y", 0.8)])
    assert "Every repeater provisioned with the current key" in text and "key created" in text
    assert public and (home / "key.seed").read_text() != before


def test_register_publishes_the_key_and_the_status_tile_says_so(tmp_path):
    text, _ = walk(tmp_path, [(b"k", 0.6), (b"r", 1.5)], env={"MCCONTROL_FAKE_REGISTER": "ok"})
    assert "registered: ok" in text and re.search(r"REG\s+20\d\d-", text)


def test_setup_saves_the_broker_and_the_test_shows_each_step(tmp_path):
    erase = b"\x7f" * 20
    text, home = walk(tmp_path, [(b":", 0.5), (b"setup", 0.4), (b"\r", 0.6), (erase + b"127.0.0.2", 0.3),
                                 (curses_key("KEY_F2"), 0.8), (b":", 0.5), (b"test the", 0.4), (b"\r", 2.5)])
    assert "CONTROL BROKER" in text and "broker saved" in text
    assert json.loads((home / "config.json").read_text())["broker"] == "127.0.0.2"
    assert "TEST" in text and "FAIL connect and sign in" in text and re.search(r"LINK\s+FAIL", text)


def curses_key(name):
    return {"KEY_F2": b"\x1bOQ"}[name]


def test_command_sets_run_with_their_value_and_are_saved_and_removed(tmp_path):
    script = dict(SCRIPT, **{"4AC61A7F": [0, "OK"]})
    text, home = walk(tmp_path, [(DOWN, 0.3), (b":", 0.5), (b"run a", 0.4), (b"\r", 0.6), (b"\r", 0.6),
                                 (b"se-jkp", 0.3), (b"\r", 4.0)], script=script, wait=0.1)
    assert "{arg}" in text and "every step answered ok" in text
    assert [h["command"] for h in _history(home, "4AC61A7F")][-4:] == ["region put se-jkp *", "region allowf se-jkp",
                                                                        "region save", "region"]
    text, home = walk(tmp_path / "save", [(b":", 0.5), (b"save a", 0.4), (b"\r", 0.6), (b"probe", 0.3), (b"\r", 0.6),
                                          (b"ver", 0.3), (b"\t", 0.2), (b"get name", 0.3), (curses_key("KEY_F2"), 0.8),
                                          (b":", 0.5), (b"remove a", 0.4), (b"\r", 0.6), (b"add-scope", 0.3), (b"\r", 0.8)])
    sets = json.loads((home / "config.json").read_text())["sets"]
    assert "saved probe: 2 steps" in text and "removed add-scope" in text
    assert sets == {"probe": ["ver", "get name"], "add-scope": []}            # [] is a removed set


def test_alt_digit_sends_the_shortcut_and_an_unset_one_says_so(tmp_path):
    text, home = walk(tmp_path, [(DOWN, 0.3), (b"\x1b2", 1.5), (b"\x1b9", 0.8)])
    assert _history(home, "4AC61A7F")[-1]["command"] == "ver"
    assert "Alt+9 is not set" in text


def test_help_shows_the_keys_and_follows_into_the_topics(tmp_path):
    text, _ = walk(tmp_path, [(b"?", 0.6), (b"\r", 0.6), (b"\r", 0.6)])
    assert "MONOLITH KEYS" in text and "help contents" in text
    assert "HELP: CONTENTS" in text and "THE SCREEN" in text


def test_h_shows_history_e_edits_and_del_forgets_after_a_confirm(tmp_path):
    text, home = walk(tmp_path, [(DOWN, 0.3), (b"h", 0.6), (ESC, 0.4), (b"e", 0.6), (b"\x7f" * 10 + b"Gisebo2", 0.3),
                                 (b"\r", 0.3), (b"\r", 0.3), (b"\r", 0.8)])
    assert "HISTORY GISEBO" in text and "2026-10-07 11:00  region" in text
    assert "saved" in text and "Gisebo2" in json.loads(json.dumps([d["name"] for d in json.loads((home / "devices.json").read_text())]))
    text, home = walk(tmp_path / "f", [(b"\x1b[3~", 0.6), (b"y", 0.8)])
    assert "FORGET DEVICE" in text and "forgot Far" in text
    assert "FD700369" not in (home / "devices.json").read_text()


def test_o_sorts_columns_pick_and_p_swaps_the_phosphor(tmp_path):
    text, home = walk(tmp_path, [(b"o", 0.5), (b"p", 0.5), (b":", 0.5), (b"columns", 0.4), (b"\r", 0.6), (b" ", 0.2),
                                 (b"\r", 0.8)])
    assert "by region" in text and "green phosphor" in text
    assert json.loads((home / "config.json").read_text())["columns"] == ["sent", "batt", "seen", "ver", "result"]


def _click(x, y):
    """An SGR (1006) mouse report, press then release, as xterm sends it; zero-based."""
    return f"\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m".encode()


def test_a_click_moves_the_cursor_and_presses_a_hint(tmp_path):
    text, _ = walk(tmp_path, [(_click(5, 4), 0.6), (_click(1, 23), 0.8)])
    assert "DEVICE GISEBO" in text                              # row 4 is the second device
    assert "PALETTE" in text                                    # the first hint is the palette


def test_q_quits(tmp_path):
    walk(tmp_path, [(b"q", 1.0)])
    assert EXITED == [True]


def test_tab_to_the_device_tile_and_enter_reads_a_read_again(tmp_path):
    def prepare(home):
        devs = json.loads((home / "devices.json").read_text())
        next(d for d in devs if d["node"] == "4AC61A7F")["reads"] = {"get autoreply": {"reply": "> on", "at": "2026-10-07 11:00:00", "seq": 1}}
        (home / "devices.json").write_text(json.dumps(devs))
    text, home = walk(tmp_path, [(DOWN, 0.3), (TAB, 0.4), (b"\r", 1.5)], prepare=prepare)
    assert "get autoreply" in text and "1 of 1 answered" in text
    assert _history(home, "4AC61A7F")[-1]["command"] == "get autoreply"


def test_q_while_a_job_runs_cancels_it_and_quits(tmp_path):
    text, home = walk(tmp_path, [(b"c", 0.5), (b"ver", 0.3), (b"\r", 1.2), (b"q", 2.0)], wait=6)
    assert "Esc cancels" in text and EXITED == [True]


class Counting:
    """A client stand-in that answers the reads a cursor move makes and counts every call."""

    def __init__(self):
        self.calls = []

    def reads(self, node):
        self.calls.append(("reads", node))
        return {"node": node, "reads": []}

    def history(self, node, limit=None):
        self.calls.append(("history", node))
        return {"node": node, "history": []}

    def __getattr__(self, name):
        def call(*a, **k):
            self.calls.append((name, a))
            raise AssertionError(f"a cursor move called {name}")
        return call


def test_moving_the_cursor_asks_only_for_the_new_devices_reads_and_history():
    from mccontrol.ui.Monolith.board import Board
    board = Board.__new__(Board)
    board.client = Counting()
    board.devices = [{"node": n, "tagged": False} for _name, n, _r in NODES]
    board.cursor, board.focus, board.shown_node, board.read_cursor = 0, "devices", NODES[0][1], 0
    for _ in range(3):
        assert board.step(curses.KEY_DOWN) == "nav"
    assert board.client.calls == [("reads", NODES[1][1]), ("history", NODES[1][1]),
                                  ("reads", NODES[2][1]), ("history", NODES[2][1])]   # the third move hits the end


def test_without_a_utf8_locale_the_board_draws_in_ascii(tmp_path):
    walk(tmp_path, [(DOWN, 0.3)], env={"LANG": "C", "LC_ALL": "C"})
    screen = walk.screen
    assert "+--" in screen and "| " in screen and "DEVICES" in screen and "Gisebo" in screen
    assert not set(screen) & set("╭╮╰╯│─▁▂▃▄▅▆▇█◆◉●↵␣")
