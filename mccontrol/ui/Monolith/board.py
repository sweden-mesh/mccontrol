"""The board: tiles that redraw themselves from the API every two seconds, a command palette,
overlays that take one decision each, and toasts for what was done.

Every verb is a request through the client; a long one is a job whose progress is drawn as
a bar in the feed tile while `wait_job` polls it, and Esc cancels it with `DELETE /jobs/{id}`.
"""

import curses
import locale
import os
import sys
import time

from mccontrol.apiclient import ApiError

from . import art

TICK_SECS = 2.0                         # how often the tiles ask the API again
TOAST_SECS = 4.0
ONOFF = {"on": True, "yes": True, "true": True, "1": True, "off": False, "no": False, "false": False, "0": False}
UI = "Monolith"                         # the name its preferences are kept under in ui_settings, and its help asked for
HINTS = [(":", "menu"), ("↵", "cmds"), ("␣", "tag"), ("c", "send"), ("a", "all"), ("s", "stat"), ("d", "find"),
         ("k", "keys"), ("Del", "forget"), ("?", "help"), ("q", "quit"), ("n", "names"), ("r", "fleet"), ("h", "history"), ("e", "edit"),
         ("o", "sort"), ("p", "phosphor"), ("S", "setup")]
CTRL_P, CTRL_L, TAB, ESC, BACKSPACES = 16, 12, 9, 27, (curses.KEY_BACKSPACE, 127, 8)
ENTERS = (curses.KEY_ENTER, 10, 13)
TILES = ("devices", "device", "feed", "input", "history")   # what Tab cycles; "input" is the feed's command line
HISTORY_SECS = 30.0                     # how often the history tile asks again unasked; a verb asks at once
HISTORY_PER = 6                         # the commands kept in each device's block
INPUT_HINTS = [("↵", "send"), ("↑↓", "recall"), ("Esc", "leave"), ("Tab", "next tile"), ("^P", "palette"),
               ("F1", "help"), ("F10", "quit")]
HIT_KEYS = {"↵": 10, "␣": 32, "Del": curses.KEY_DC, "Esc": 27, "Tab": 9, "^P": 16, "↑↓": curses.KEY_UP,
            "F1": curses.KEY_F1, "F10": curses.KEY_F10}


PLAIN = str.maketrans({"↵": "Enter", "␣": "Space", "…": "~", "▞": "/", "▚": "\\", "⌫": "BS", "↑": "^", "↓": "v"})


def codeset():
    """The encoding the terminal's locale names. Python's own UTF-8 mode reports utf-8
    even under LANG=C, so the locale is asked; one that cannot be set is ASCII."""
    try:
        locale.setlocale(locale.LC_ALL, "")
        return locale.nl_langinfo(locale.CODESET)
    except (locale.Error, AttributeError):
        return "ascii"


# The jobs a cancel cannot cut short, by what a person calls them.
UNCUT = {"test": "broker test", "register": "registration"}


class Cancel(Exception):
    """An overlay was left with Esc."""


class Quit(Exception):
    """q while a job ran: the job was cancelled, now the board goes."""


class Board:
    def __init__(self, win, client):
        self.win, self.client = win, client
        self.g = art.glyphs(codeset())
        self.palette = self._kept_palette()
        self.colors = self._colors()
        self.started = time.time()
        self.focus = "devices"
        self.sort = "name"
        self.devices, self.dir = [], {}
        self.cursor = self.top = 0
        self.feed, self.feed_cursor, self.feed_top = [], 0, 0
        self.line, self.typed, self.recall = "", [], None     # the command line: text, sent, (list, pos)
        self.notes = []                     # (after job id, text, role): what the command line refused, inline
        self.blocks, self.hist_top, self.last_hist = [], 0, 0.0   # the history tile: (id, lines), newest first
        self.jobs, self.cfg, self.key_info = [], {}, {}
        self.stats, self.history, self.shown_node = [], [], None    # stats: (label, value, age, read)
        self.read_cursor = 0
        self.toasts = []
        self.running = None                 # (title, job) while a job is waited on
        self.hits = []                      # (y, x0, x1, key) of the hint bar, for clicks
        self.last_tick = 0.0
        self.mouse = None
        win.keypad(True)
        try:
            curses.curs_set(0)
            curses.mousemask(curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | getattr(curses, "BUTTON4_PRESSED", 0)
                             | getattr(curses, "BUTTON5_PRESSED", 0))
        except curses.error:
            pass

    # -- colour ------------------------------------------------------------------------

    def _colors(self):
        try:
            if curses.has_colors():
                curses.start_color()
                for n, fg, bg in art.pairs(self.palette, curses.COLORS):
                    curses.init_pair(n, fg, bg)
                attrs = art.roles(curses.color_pair)
                self.win.bkgd(" ", attrs["base"])
                return attrs
        except curses.error:
            pass
        return art.roles(None)

    def _kept_palette(self):
        """The palette the board was left in (`ui_settings.Monolith.palette`); amber when
        none is kept, it is not one of ours, or the API cannot say."""
        try:
            name = self.client.config().get("ui_settings", {}).get(UI, {}).get("palette")
        except ApiError:
            return "amber"
        return name if isinstance(name, str) and name in art.PALETTES else "amber"

    def swap_palette(self):
        """The other palette, kept first: when the API cannot keep it the board stays as it
        is, so the next start is not a surprise."""
        other = "green" if self.palette == "amber" else "amber"
        try:
            self.client.set_config(ui_settings={UI: {"palette": other}})
        except ApiError as exc:
            self.toast(exc.message, "alert")
            return
        self.palette = other
        self.colors = self._colors()
        self.toast(f"{self.palette} phosphor")

    # -- data --------------------------------------------------------------------------

    def tick(self, force=False):
        """Ask the API for what the tiles show, every TICK_SECS or when forced."""
        if not force and time.monotonic() - self.last_tick < TICK_SECS:
            return
        self.last_tick = time.monotonic()
        try:
            tiles = art.layout(*self.size)
            width = tiles["devices"][2] - 6 if tiles else 40
            node = self.device["node"] if self.device else None
            self.dir = self.client.devices(sort=self.sort, width=width)
            self.devices = self.dir["devices"]
            if node:                                        # the cursor stays on its device
                self.cursor = next((i for i, d in enumerate(self.devices) if d["node"] == node), self.cursor)
            self.cursor = max(0, min(self.cursor, len(self.devices) - 1))
            self.jobs = self.client.jobs()["jobs"]
            self.cfg, self.key_info = self.client.config(), self.client.key()
            self.follow(force=True)
            self.build_feed()
            self.load_history(force)
        except ApiError as exc:
            self.toast(exc.message, "alert")

    def follow(self, force=False):
        """The stats tile follows the cursor: what is known about the device, and its history."""
        d = self.device
        node = d["node"] if d else None
        if node == self.shown_node and not force:
            return
        if node != self.shown_node:
            self.read_cursor = 0
        self.shown_node = node
        known = self.client.facts(node) if node else {"facts": [], "raw": []}
        self.stats = ([(f["label"], f["value"], f["age"], f["read"]) for f in known["facts"]]
                      + [(r["command"], r["reply"], r["age"], r["command"]) for r in known["raw"]])
        self.history = self.client.history(node, limit=50)["history"] if node else []

    def load_history(self, force=False):
        """The history tile's blocks: per device its last commands, and per region the
        broadcasts, newest block first. Asked after every verb and every HISTORY_SECS, not on
        each tick: one request per device."""
        if not force and time.monotonic() - self.last_hist < HISTORY_SECS:
            return
        self.last_hist = time.monotonic()
        blocks = []
        for d in self.devices:
            lines = self.client.history(d["node"], limit=HISTORY_PER)["history"]
            if lines:
                blocks.append((lines[-1]["at"], d["node"][:6].lower(), lines))
        regions = {}
        for line in self.client.broadcast_history(50)["history"]:
            parts = (line.get("topic") or "").split("/")
            regions.setdefault(parts[1] if len(parts) > 2 else "?", []).append(line)
        blocks += [(lines[-1]["at"], f"{region}*", lines[-HISTORY_PER:]) for region, lines in regions.items()]
        self.blocks = [(ident, lines) for _at, ident, lines in sorted(blocks, key=lambda b: b[0], reverse=True)]

    def history_rows(self):
        """`(id, text, role)` for each line of the history tile: `> command` and `< reply`,
        the block's id on its first line only."""
        rows = []
        for ident, lines in self.blocks:
            for line in lines:
                reply = self.history_out(line)
                reply = reply[1:].strip() if line["code"] == 0 and reply.startswith(">") else reply
                rows.append((ident, f"> {line['command']}", "bright"))
                rows.append(("", "< " + " ".join(reply.split()), "base" if line["code"] in (0, None) else "alert"))
                ident = ""
        return rows

    @property
    def device(self):
        return self.devices[self.cursor] if self.devices and 0 <= self.cursor < len(self.devices) else None

    @property
    def tagged(self):
        return [d for d in self.devices if d["tagged"]]

    def points(self, node):
        """The telemetry of the newest statistics job for `node`, if any."""
        for job in self.jobs:
            if job["verb"] == "statistics" and job["status"] == "done" and (job["result"] or {}).get("node") == node:
                return job["result"].get("points") or []
        return []

    def build_feed(self):
        """The feed tile's rows, oldest first so the newest sit above the command line: per job
        a header and what came back, and the command line's own refusals after the job that was
        newest when each was said."""
        jobs = list(reversed(self.jobs[:20]))
        shown = {job["id"] for job in jobs}
        rows = [(text, role, [text]) for after, text, role in self.notes if after not in shown]
        for job in jobs:
            rows.append((self.job_title(job), "accent", self.job_detail(job)))
            for text, role in self.job_lines(job):
                rows.append(("  " + text, role, self.job_detail(job)))
            rows += [(text, role, [text]) for after, text, role in self.notes if after == job["id"]]
        following = self.feed_cursor >= len(self.feed) - 1
        self.feed = rows
        self.feed_cursor = len(rows) - 1 if following else max(0, min(self.feed_cursor, len(rows) - 1))

    def note(self, text, role="alert"):
        """A line in the feed that is no job: what the command line could not send, and why."""
        self.notes = (self.notes + [(self.jobs[0]["id"] if self.jobs else None, text, role)])[-50:]
        self.feed_cursor = len(self.feed)               # follow it to the end
        self.build_feed()

    def job_title(self, job):
        result = job.get("result") or {}
        what = (result.get("command") or ", ".join(result.get("regions") or [])
                or (job["outcomes"][0]["command"] if job["outcomes"] else "") or result.get("node") or "")
        mark = {"done": self.g["ok"], "failed": self.g["bad"], "cancelled": "-", "running": self.g["run"]}[job["status"]]
        return f"{mark} {job['started'][11:16]} {job['verb'].upper()} {what}  {job['progress']['text']}"

    def job_lines(self, job):
        """`(text, role)` for each line of a job: the outcomes, or the result's own lines."""
        if job.get("error"):
            return [(job["error"]["message"], "alert")]
        out = []
        for o in job["outcomes"]:
            role = "alert" if o["verified"] is False else ("dim" if o["ok"] is None else ("bright" if o["ok"] else "accent"))
            seal = f" {self.g['ok']}sig" if o["verified"] else ""
            out.append((f"{o['name'][:14]:14} {o['line']}{seal}", role))
        result = job.get("result") or {}
        if not out and result.get("steps") and isinstance(result["steps"][0], dict):
            out = [(f"{'ok  ' if s['ok'] else 'FAIL'} {s['step']}: {s['detail']}", "base" if s["ok"] else "alert")
                   for s in result["steps"]]
        elif not out and "detail" in result:
            out = [(result["detail"], "base" if result.get("ok") else "alert")]
        elif not out and result.get("lines"):
            out = [(line, "base") for line in result["lines"] if line]
        return out

    def job_detail(self, job):
        lines = [self.job_title(job), ""]
        for o in job["outcomes"]:
            lines += [f"{o['name']} ({o['node']})  {o['command']}", f"  {o['line']}"]
            if o["reply"] and o["reply"] not in o["line"]:
                lines.append(f"  reply: {o['reply']}")
            if o["verified"]:
                lines.append("  signed by the node's key")
        lines += [line for line, _r in self.job_lines(job)] if not job["outcomes"] else []
        return lines

    # -- drawing -----------------------------------------------------------------------

    @property
    def size(self):
        h, w = self.win.getmaxyx()
        return w, h

    def fold(self, text):
        """`text` as the terminal can show it: ASCII where the locale cannot carry the rest."""
        if self.g is art.ASCII:
            return text.translate(PLAIN).encode("ascii", "replace").decode("ascii")
        return text

    def put(self, y, x, text, role="base", attr=0):
        w, h = self.size
        text = self.fold(text)
        if not (0 <= y < h) or x >= w or x < 0:
            return
        try:
            self.win.addnstr(y, x, text, max(0, w - x - (1 if y == h - 1 else 0)), self.colors[role] | attr)
        except curses.error:
            pass

    def frame(self, rect, title, focused=False, footer=""):
        y, x, w, h = rect
        g, role = self.g, "bright" if focused else "frame"
        self.put(y, x, g["tl"] + g["h"] * (w - 2) + g["tr"], role)
        for row in range(y + 1, y + h - 1):
            self.put(row, x, g["v"], role)
            self.put(row, x + w - 1, g["v"], role)
        self.put(y + h - 1, x, g["bl"] + g["h"] * (w - 2) + g["br"], role)
        label = f" {g['focus'] if focused else g['dot']} {title.upper()} "
        self.put(y, x + 2, label[:w - 4], "cursor" if focused else "bright")
        if footer:
            self.put(y + h - 1, x + max(2, w - len(footer) - 4), f" {footer} "[:w - 4], "dim")
        return y + 1, x + 2, w - 4, h - 2              # the inside, one column of air each side

    def draw(self):
        self.win.erase()
        w, h = self.size
        tiles = art.layout(w, h)
        if not tiles:
            self.put(0, 0, f"Monolith needs {art.MIN_W}x{art.MIN_H}; this terminal is {w}x{h}.", "alert")
            self.win.noutrefresh()
            curses.doupdate()
            return
        self.draw_header(tiles["header"])
        self.draw_devices(tiles["devices"])
        self.draw_device(tiles["device"])
        self.draw_feed(tiles["feed"])
        self.draw_status(tiles["status"])
        self.draw_history(tiles["history"])
        if "clock" in tiles:
            self.draw_clock(tiles["clock"])
        self.draw_hints(tiles["hints"])
        self.draw_toasts(tiles["hints"][0] - 1)
        self.win.noutrefresh()

    def draw_header(self, rect):
        y, x, w, h = rect
        now = time.strftime("%H:%M:%S")
        if h >= 3:
            for i, row in enumerate(art.title(self.g)):
                self.put(y + i, x + 2, row, "bright")
            self.put(y, w - 30, f"mccontrol {self.g['scan'] * 3} fleet control", "dim")
            self.put(y + 1, w - 30, f"{now}  up {art.duration(time.time() - self.started)}", "accent")
            self.put(y + 2, x, (self.g["scan"] * w)[:w], "dim")
            return
        self.put(y, x, " " * w, "cursor")
        self.put(y, x + 1, "▞▚ M O N O L I T H ▚▞" if self.g is art.GLYPHS else "<> M O N O L I T H <>", "cursor")
        self.put(y, x + 24, f"mccontrol  {self.palette}", "cursor")
        self.put(y, w - len(now) - 2, now, "cursor")

    def draw_devices(self, rect):
        footer = (self.dir.get("footer") or "") + f"  by {self.sort}"
        y, x, w, h = self.frame(rect, "Devices", self.focus == "devices", footer)
        self.put(y, x + 2, (self.dir.get("header") or "")[:w - 2], "dim", curses.A_UNDERLINE)
        rows = h - 1
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + rows:
            self.top = self.cursor - rows + 1
        for i, d in enumerate(self.devices[self.top:self.top + rows]):
            n = self.top + i
            mark = self.g["tag"] if d["tagged"] else " "
            role = "cursor" if n == self.cursor else ("bright" if d["tagged"] else ("accent" if d["stale"] else "base"))
            self.put(y + 1 + i, x, f"{mark} {d['row']}"[:w].ljust(w), role)
        if not self.devices:
            self.put(y + 2, x, "No devices: d discovers a region's nodes."[:w], "dim")

    def draw_device(self, rect):
        d = self.device
        y, x, w, h = self.frame(rect, f"Stats {(d['name'] or d['node']) if d else '-'}"[:30], self.focus == "device",
                                "↵ reads again  s statistics")
        if not d:
            return
        fleet = d.get("fleet") or {}
        info = f"{d['node']}  {d['region']}  {d['cells']['ver'].strip() or 'ver ?'}"
        if fleet.get("battery_mv"):
            info += f"  {fleet['battery_mv'] / 1000:.2f} V"
        self.put(y, x, info[:w], "bright")
        row = y + 1
        pts = self.points(d["node"])
        for label, field in (("BATT", "batteryMv"), ("NOISE", "noiseFloorDb"), ("RXERR", "receiveErrors"),
                             ("TX AIR", "airtimeTxSecs")):
            if not pts or row >= y + h - 2:
                break
            values = [p.get(field) for p in pts]
            last = next((v for v in reversed(values) if v is not None), None)
            tail = f" {last:.0f}" if last is not None else ""
            self.put(row, x, f"{label:6}", "dim")
            self.put(row, x + 7, art.sparkline(values, w - 8 - len(tail), self.g), "accent")
            self.put(row, x + w - len(tail), tail, "base")
            row += 1
        if row < y + h:
            self.put(row, x, self.g["scan"] * w, "dim")
            row += 1
        if not self.stats:
            if row < y + h:
                self.put(row, x, "nothing known yet: n reads name and version"[:w], "dim")
            return
        rows = y + h - row
        top = max(0, self.read_cursor - rows + 1)
        label_w = min(17, max(len(s[0]) for s in self.stats))
        for i, (label, value, age, _read) in enumerate(self.stats[top:top + rows]):
            here = self.focus == "device" and top + i == self.read_cursor
            value = " ".join(value.split())
            room = w - label_w - 1 - len(age) - 1
            line = f"{label[:label_w]:{label_w}} {value[:max(0, room)]:{max(0, room)}} {age}"[:w]
            if here:
                self.put(row + i, x, line.ljust(w), "cursor")
            else:
                self.put(row + i, x, line[:label_w], "dim")
                self.put(row + i, x + label_w + 1, line[label_w + 1:w - len(age)], "base")
                self.put(row + i, x + w - len(age), age, "dim")

    @staticmethod
    def history_out(line):
        if line["code"] == 0:
            return line["reply"] or "OK"
        return f"refused {line['code_name']}" if line["code"] is not None else (line["note"] or "no answer")

    def draw_feed(self, rect):
        typing = self.focus == "input"
        footer = "↵ sends  ↑↓ recall  Esc leaves" if typing else ("↵ opens  Tab types" if self.feed else "Tab types")
        y, x, w, h = self.frame(rect, "Results", self.focus in ("feed", "input"), footer)
        rows = [(text, role) for text, role, _d in self.feed]
        if self.running:                    # the job being waited on, inline at the bottom
            title, job = self.running
            p = job["progress"]
            frac = p["answered"] / p["expected"] if p["expected"] else (p["elapsed"] / p["wait"] if p["wait"] else 0)
            rows += [(f"{self.g['run']} {title}", "bright"), (art.bar(frac, max(4, min(30, w - 2)), self.g), "accent"),
                     (f"Esc cancels  {p['text']}", "base")]          # the key first: a long text is cut
            rows += [("  " + text, role) for text, role in self.job_lines(job)]
        view = h - 1                        # the last row is the command line
        if self.running or (self.focus != "feed" and self.feed_cursor >= len(self.feed) - 1):
            # following the end: the newest lines over the command line, but never past the
            # head of the newest job, so a long result shows its start
            head = len(self.feed) if self.running else max(
                (i for i, (text, _r, _d) in enumerate(self.feed) if not text.startswith("  ")), default=0)
            top = max(0, min(len(rows) - view, head))
        else:
            cursor = self.feed_cursor
            top = max(0, min(cursor - view + 1, len(rows) - view)) if cursor >= view else 0
        self.feed_top = top
        for i, (text, role) in enumerate(rows[top:top + view]):
            focused = self.focus == "feed" and top + i == self.feed_cursor and not self.running
            self.put(y + i, x, text[:w].ljust(w) if focused else text[:w], "cursor" if focused else role)
        if not rows:
            self.put(y, x, "Nothing run yet: type below."[:w], "dim")
        self.draw_input(y + h - 1, x, w)

    def target(self):
        """Who a send goes to, as the command line says it: the tagged, or the cursor device."""
        if self.tagged:
            return f"{len(self.tagged)} tagged"
        return (self.device["name"] or self.device["node"]) if self.device else "no device"

    def draw_input(self, y, x, w):
        prefix = f"{self.target()[:16]} {self.g['run']} "
        if self.focus != "input" and not self.line:
            self.put(y, x, (prefix + "Tab: type a command")[:w], "dim")
            return
        self.put(y, x, prefix[:w], "bright" if self.focus == "input" else "dim")
        room = max(1, w - len(prefix))
        text = self.line + ("_" if self.focus == "input" else "")
        self.put(y, x + len(prefix), text[-room:], "cursor" if self.focus == "input" else "base")

    def draw_status(self, rect):
        y, x, w, h = self.frame(rect, "Status", False)
        cfg, key = self.cfg, self.key_info
        reg = (key or {}).get("registered")
        test = next((j for j in self.jobs if j["verb"] == "test" and j["status"] in ("done", "failed")), None)
        lines = [
            ("BROKER", f"{cfg.get('broker', '?')}:{cfg.get('port', '?')}", "base"),
            ("KEY", (key["public"][:8] + "…") if key.get("public") else "NONE  :generate key",
             "base" if key.get("public") else "alert"),
            ("REG", f"{reg['at'][:10]}" if reg else "no  :register", "base" if reg else "accent"),
            ("LINK", ("ok " if (test["result"] or {}).get("ok") else "FAIL ") + test["started"][11:16] if test else "untested  :test",
             "base" if test and (test["result"] or {}).get("ok") else "accent"),
            ("SIGN-IN", (f"{cfg['username']}, password {'set' if cfg.get('password_set') else 'not set'}"
                         if cfg.get("username") else "by key"), "base"),
            ("JOBS", f"{sum(j['status'] == 'running' for j in self.jobs)} running, {len(self.jobs)} kept", "base"),
        ]
        for i, (label, value, role) in enumerate(lines[:h]):
            self.put(y + i, x, f"{label:8}", "dim")
            self.put(y + i, x + 8, value[:w - 8], role)

    def draw_history(self, rect):
        """The operator's format: the short id once, then `> sent` and `< answered`."""
        y, x, w, h = self.frame(rect, "History", self.focus == "history", "↑↓ scrolls" if self.focus == "history" else "")
        rows = self.history_rows()
        self.hist_top = max(0, min(self.hist_top, len(rows) - h))
        for i, (ident, text, role) in enumerate(rows[self.hist_top:self.hist_top + h]):
            if i == 0 and not ident:                    # scrolled into a block: say whose it is
                ident = next(r[0] for r in reversed(rows[:self.hist_top + 1]) if r[0])
            self.put(y + i, x, f"{ident:6} {self.g['v']}", "accent" if ident else "dim")
            self.put(y + i, x + 9, text[:w - 9], role)
        if not rows:
            self.put(y, x, "Nothing sent yet."[:w], "dim")

    def draw_clock(self, rect):
        y, x, w, h = self.frame(rect, "Clock", False)
        for i, row in enumerate(art.bigtext(art.clock(), self.g)):
            self.put(y + i, x + max(0, (w - len(row)) // 2), row, "bright")
        self.put(y + 3, x, f"session {art.duration(time.time() - self.started)}"[:w].center(w), "dim")
        self.put(y + 4, x, time.strftime("%a %d %b %Y")[:w].center(w), "dim")

    def draw_hints(self, rect):
        y, x, w, _h = rect
        self.put(y, x, " " * (w - 1), "dim")
        self.hits, col = [], 1
        for key, label in (INPUT_HINTS if self.focus == "input" else HINTS):
            shown = self.fold(key)              # measured as drawn: Enter, not the arrow
            if col + len(shown) + len(label) + 1 >= w:
                break
            self.put(y, col, shown, "cursor")
            self.put(y, col + len(shown) + 1, label, "dim")
            self.hits.append((y, col, col + len(shown) + len(label), key))
            col += len(shown) + len(label) + 2

    def draw_toasts(self, bottom):
        """The newest toast on the bottom frame line, from the left, where it covers no
        tile's content: the history and the command line stay readable."""
        now = time.monotonic()
        self.toasts = [t for t in self.toasts if t[0] > now]
        w, _h = self.size
        if self.toasts:
            _e, text, role = self.toasts[-1]
            self.put(bottom, 2, f" {text} "[:w - 4], "alert" if role == "alert" else "cursor")

    def toast(self, text, role="base"):
        self.toasts.append((time.monotonic() + TOAST_SECS, text, role))

    # -- keys --------------------------------------------------------------------------

    def key(self, timeout=500):
        """One key, or -1 after `timeout` ms. Esc followed at once by a character is Alt+it,
        returned negative."""
        curses.doupdate()
        self.win.timeout(timeout)
        key = self.win.getch()
        if key == curses.KEY_MOUSE:
            try:
                _i, mx, my, _z, state = curses.getmouse()
            except curses.error:
                return -1
            self.mouse = (my, mx, state)
            return key
        if key == CTRL_L:                               # draw every cell again
            self.win.clearok(True)
            return -1
        if key != ESC:
            return key
        # curses has already waited ESCDELAY for a sequence: what follows Esc at once (Alt+x)
        # is in its buffer now, so the peek does not wait
        self.win.timeout(0)
        nxt = self.win.getch()
        if nxt == -1:
            return ESC
        if 32 <= nxt < 127 and nxt not in (ord("["), ord("O")):
            return -nxt
        while self.win.getch() != -1:                 # an unrecognised sequence: drained
            pass
        return -1

    def run(self):
        self.tick(force=True)
        while True:
            self.draw()
            key = self.key()
            if key == -1:
                self.tick()
                continue
            if self.step(key) == "quit":
                return 0

    def step(self, key):
        """One key: its verb, then the tiles asked again -- unless it only moved around,
        which changes nothing on the server and is drawn from what the board holds."""
        try:
            done = self.handle(key)
        except Quit:
            return "quit"
        except Cancel:
            done = None
        except ApiError as exc:
            self.toast(exc.message, "alert")
            done = None
        if done not in ("quit", "nav"):
            self.draw()                 # what the board holds, at once: a closed overlay is gone
            curses.doupdate()           # before the six requests of the refresh
            self.tick(force=True)
        return done

    def handle(self, key):
        """Do what `key` means: "quit", "nav" for a key that only moved around, else None."""
        if self.focus == "input":
            done = self.typing(key)
            if done != "pass":
                return done
        if key in (ord("q"), curses.KEY_F10):
            return "quit"
        if key == curses.KEY_RESIZE:
            self.resize()
        elif key in (ord(":"), CTRL_P):
            return self.palette_run()
        elif key == TAB:
            self.focus = TILES[(TILES.index(self.focus) + 1) % len(TILES)]
            return "nav"
        elif key == curses.KEY_BTAB:
            self.focus = TILES[(TILES.index(self.focus) - 1) % len(TILES)]
            return "nav"
        elif key == curses.KEY_MOUSE:
            return self.click()
        elif key < 0 and chr(-key) in "123456789":
            self.do("alt", int(chr(-key)))
        elif key in ENTERS:
            self.do({"devices": "commands", "device": "reread", "feed": "open"}[self.focus])
        elif key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE, curses.KEY_HOME, curses.KEY_END):
            self.move(key)
            return "nav"
        elif key in (ord(" "), curses.KEY_IC):
            d = self.device
            if d:
                (self.client.untag if d["tagged"] else self.client.tag)(devices=[d["node"]])
                self.cursor = min(len(self.devices) - 1, self.cursor + 1)
        elif key in (curses.KEY_F1, ord("?")):
            self.do("help")
        elif key == curses.KEY_DC:
            self.do("forget")
        elif key in (ord("+"), ord("-")):
            self.do("tagregion", key == ord("+"))
        elif 32 < key < 127 and chr(key) in KEYS:
            return self.do(KEYS[chr(key)])

    def typing(self, key):
        """A key on the command line: text, Backspace, Up/Down through what was sent, Enter
        sends, Esc leaves. "pass" for a key the line does not take (Tab, F-keys, Ctrl+P...)."""
        if key == ESC:
            self.focus, self.recall = "devices", None
            return "nav"
        if key in ENTERS:
            return self.submit()
        if key in BACKSPACES:
            self.line = self.line[:-1]
            return "nav"
        if key in (curses.KEY_UP, curses.KEY_DOWN):
            if self.recall is None:
                seen = [line["command"] for line in self.history]
                seen += [line["command"] for line in self.client.broadcast_history(50)["history"]] + self.typed
                back = list(dict.fromkeys(reversed(seen)))[::-1]          # each once, where last sent
                self.recall = (back, len(back))
            back, pos = self.recall
            pos = max(0, pos - 1) if key == curses.KEY_UP else min(len(back), pos + 1)
            self.recall = (back, pos)
            if back:
                self.line = back[pos] if pos < len(back) else ""
            return "nav"
        if 32 <= key < 127:
            self.line += chr(key)
            return "nav"
        return "pass"

    def submit(self):
        """Enter on the command line: the line is sent as `c` sends it -- to the tagged devices
        or the cursor device, a caution asked about first -- and a refusal is said inline."""
        command = " ".join(self.line.split())
        if not command:
            return "nav"
        self.line, self.recall = "", None
        self.typed = [c for c in self.typed if c != command] + [command]
        try:
            self.send(command)
        except ApiError as exc:
            self.note(f"{self.g['bad']} {command}: {exc.message}")
        except Cancel as exc:
            self.note(f"- {command}: {exc.args[0] if exc.args else 'not sent'}", "dim")
        return None

    def resize(self):
        """The terminal changed size: take the size it has now, even where LINES and
        COLUMNS in the environment would pin the old one, and lay the tiles out again."""
        try:
            cols, rows = os.get_terminal_size(sys.__stdout__.fileno())
            if (rows, cols) != self.win.getmaxyx():
                curses.resizeterm(rows, cols)
            curses.update_lines_cols()
        except (OSError, ValueError, curses.error):
            pass
        self.win.clearok(True)
        self.last_tick = 0

    def move(self, key):
        step = {curses.KEY_UP: -1, curses.KEY_DOWN: 1, curses.KEY_PPAGE: -10,
                curses.KEY_NPAGE: 10, curses.KEY_HOME: -10 ** 6, curses.KEY_END: 10 ** 6}[key]
        if self.focus in ("feed", "input"):
            self.feed_cursor = max(0, min(len(self.feed) - 1, self.feed_cursor + step))
        elif self.focus == "history":
            self.hist_top = max(0, min(self.hist_top + step, len(self.history_rows()) - 1))
        elif self.focus == "device":
            self.read_cursor = max(0, min(len(self.stats) - 1, self.read_cursor + step))
        else:
            self.cursor = max(0, min(len(self.devices) - 1, self.cursor + step))
            self.follow()

    def click(self):
        my, mx, state = self.mouse
        tiles = art.layout(*self.size)
        if state & getattr(curses, "BUTTON4_PRESSED", 0):
            self.move(curses.KEY_UP)
            return "nav"
        if state & getattr(curses, "BUTTON5_PRESSED", 0):
            self.move(curses.KEY_DOWN)
            return "nav"
        for y, x0, x1, key in self.hits:
            if my == y and x0 <= mx <= x1:
                return self.handle(HIT_KEYS.get(key) or ord(key))
        for name in TILES:
            y, x, w, h = tiles.get(name, (0, 0, 0, 0))
            if y <= my < y + h and x <= mx < x + w:
                self.focus = name
                if name == "devices" and my - y - 2 >= 0:
                    self.cursor = max(0, min(len(self.devices) - 1, self.top + my - y - 2))
                    self.follow()
                elif name == "feed" and my == y + h - 2:            # the command line
                    self.focus = "input"
                elif name == "feed" and my - y - 1 >= 0:
                    self.feed_cursor = max(0, min(len(self.feed) - 1, self.feed_top + my - y - 1))
        return "nav"

    # -- overlays ----------------------------------------------------------------------

    def box(self, title, width, height, role="base"):
        """A centred overlay frame; returns its inside `(y, x, w, h)`."""
        w, h = self.size
        width, height = min(width, w), min(height, h - 2)
        y, x = (h - height) // 2, (w - width) // 2
        for row in range(y, y + height):
            self.put(row, x, " " * width, role)
        return self.frame((y, x, width, height), title, True)

    def pick(self, title, entries, multi=False, chosen=(), query=True, note=""):
        """Choose from `entries` -- `(label, value, why_not, aside)`, `why_not` None when the
        entry can be chosen -- with a fuzzy filter typed above the list. Enter picks (with
        `multi`, the toggled set), Space toggles, Esc raises Cancel."""
        text, cur, on = "", 0, set(chosen)
        while True:
            shown = art.ranked(text, entries, key=lambda e: e[0] + " " + (e[3] or "")) if query else entries
            cur = max(0, min(cur, len(shown) - 1))
            self.draw()
            w, h = self.size
            y, x, iw, ih = self.box(title, min(84, w - 4), min(len(entries) + 5 + bool(note), h - 4))
            if query:
                self.put(y, x, f"{self.g['run']} {text}_"[:iw], "bright")
            rows = ih - (1 if query else 0) - bool(note)
            top = max(0, cur - rows + 1)
            for i, (label, value, why, aside) in enumerate(shown[top:top + rows]):
                mark = (self.g["tag"] if value in on else " ") + " " if multi else ""
                line = f"{mark}{label}"
                if aside:
                    line = line[:iw - len(aside) - 1].ljust(iw - len(aside)) + aside
                role = "cursor" if top + i == cur else ("dim" if why else "base")
                self.put(y + (1 if query else 0) + i, x, line[:iw].ljust(iw), role)
            if note:
                self.put(y + ih - 1, x, note[:iw], "dim")
            self.win.noutrefresh()
            key = self.key(timeout=-1)
            if key == ESC:
                raise Cancel
            if key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE):
                cur += {curses.KEY_UP: -1, curses.KEY_DOWN: 1, curses.KEY_PPAGE: -rows, curses.KEY_NPAGE: rows}[key]
            elif key in ENTERS and shown:
                label, value, why, _a = shown[cur]
                if multi:
                    return [e[1] for e in entries if e[1] in on]
                if why:
                    self.toast(f"{label.split('  ')[0]}: {why}", "alert")
                    continue
                return value
            elif key == ord(" ") and multi and shown:
                on ^= {shown[cur][1]}
            elif key in BACKSPACES and query:
                text = text[:-1]
            elif query and 32 <= key < 127:
                text += chr(key)
                cur = 0

    def form(self, title, fields, body=()):
        """Edit `fields` -- `[label, value, secret]` -- in one overlay: Up/Down or Tab moves,
        Enter on the last field (or F2 anywhere) saves, Esc raises Cancel. Returns the
        values."""
        values = [f[1] for f in fields]
        cur = 0
        label_w = max(len(f[0]) for f in fields) + 2
        body = [line for b in body for line in art.wrap(b, 70)]
        while True:
            self.draw()
            y, x, iw, _ih = self.box(title, 76, len(fields) + len(body) + 4)
            for i, line in enumerate(body):
                self.put(y + i, x, line[:iw], "dim")
            for i, (label, _v, secret) in enumerate(fields):
                value = "*" * len(values[i]) if secret else values[i]
                field_w = iw - label_w
                shown = value[-(field_w - 1):] + ("_" if i == cur else "")
                self.put(y + len(body) + 1 + i, x, f"{label:{label_w}}", "bright" if i == cur else "dim")
                self.put(y + len(body) + 1 + i, x + label_w, shown.ljust(field_w)[:field_w], "cursor" if i == cur else "base")
            self.win.noutrefresh()
            key = self.key(timeout=-1)
            if key == ESC:
                raise Cancel
            if key in ENTERS:
                if cur == len(fields) - 1:
                    return values
                cur += 1
            elif key == curses.KEY_F2:
                return values
            elif key in (TAB, curses.KEY_DOWN):
                cur = (cur + 1) % len(fields)
            elif key in (curses.KEY_BTAB, curses.KEY_UP):
                cur = (cur - 1) % len(fields)
            elif key in BACKSPACES:
                values[cur] = values[cur][:-1]
            elif 32 <= key < 127:
                values[cur] += chr(key)

    def ask(self, title, label, initial="", body=(), history=(), secret=False):
        """One line of text; Up/Down walk `history`. Esc raises Cancel."""
        value, back = initial, list(history)
        pos = len(back)
        body = [line for b in body for line in art.wrap(b, 70)]
        while True:
            self.draw()
            y, x, iw, _ih = self.box(title, 76, len(body) + 4)
            for i, line in enumerate(body):
                self.put(y + i, x, line[:iw], "dim")
            shown = ("*" * len(value) if secret else value)[-(iw - len(label) - 3):] + "_"
            self.put(y + len(body) + 1, x, f"{label} {self.g['run']} ", "bright")
            self.put(y + len(body) + 1, x + len(label) + 3, shown, "cursor")
            self.win.noutrefresh()
            key = self.key(timeout=-1)
            if key == ESC:
                raise Cancel
            if key in ENTERS:
                return value.strip()
            if key in BACKSPACES:
                value = value[:-1]
            elif key == curses.KEY_UP and back:
                pos = max(0, pos - 1)
                value = back[pos]
            elif key == curses.KEY_DOWN and back:
                pos = min(len(back), pos + 1)
                value = back[pos] if pos < len(back) else ""
            elif 32 <= key < 127:
                value += chr(key)

    def confirm(self, title, lines):
        """A decision that takes something away: y goes ahead, anything else does not."""
        body = [line for b in lines for line in art.wrap(b, 66)]
        self.draw()
        y, x, iw, _ih = self.box(title, 72, len(body) + 4, "alert")
        for i, line in enumerate(body):
            self.put(y + i, x, line[:iw], "alert")
        self.put(y + len(body) + 1, x, "y  go ahead      any other key  cancel"[:iw], "alert")
        self.win.noutrefresh()
        key = -1
        while key == -1:
            key = self.key(timeout=-1)
        return key in (ord("y"), ord("Y"))

    def sheet(self, title, lines, links=(), keys=""):
        """Lines to read, scrolled with the arrows. `links` -- `(line, start, end, target)`
        -- are cycled with Tab and followed with Enter (the target returned); a key in `keys`
        is returned; Esc closes."""
        top, link = 0, 0
        w, h = self.size
        if not links:                       # long lines wrap where nothing points into them
            lines = [part for line in lines for part in art.wrap(line, min(100, w - 2) - 4)]
        while True:
            self.draw()
            w, h = self.size
            y, x, iw, ih = self.box(title, min(100, w - 2), min(len(lines) + 3, h - 2))
            ih -= 1
            for i, line in enumerate(lines[top:top + ih]):
                self.put(y + i, x, line[:iw])
            if links:
                ln, s, e, _t = links[link]
                if top <= ln < top + ih:
                    self.put(y + ln - top, x + s, lines[ln][s:e], "cursor")
            hint = ("Tab link  ↵ follow  ⌫ back  " if links else "") + "↑↓ scroll  Esc close"
            self.put(y + ih, x, hint[:iw], "dim")
            self.win.noutrefresh()
            key = self.key(timeout=-1)
            if key in (ESC, ord("q")):
                return None
            if keys and 32 <= key < 127 and chr(key) in keys:
                return chr(key)
            if key == TAB and links:
                link = (link + 1) % len(links)
                top = max(0, min(links[link][0] - ih // 2, len(lines) - ih))
            elif key in ENTERS and links:
                return links[link][3]
            elif key in BACKSPACES:
                return "\b"
            elif key in (curses.KEY_DOWN, curses.KEY_NPAGE):
                top = min(max(0, len(lines) - ih), top + (1 if key == curses.KEY_DOWN else ih))
            elif key in (curses.KEY_UP, curses.KEY_PPAGE):
                top = max(0, top - (1 if key == curses.KEY_UP else ih))

    # -- jobs --------------------------------------------------------------------------

    def wait(self, title, job):
        """Draw the job's progress bar in the feed tile until it finishes; Esc cancels it."""
        self.running = (title, job)

        quitting = []

        def on_progress(j):
            self.running = (title, j)
            self.draw()
            key = self.key(timeout=0)
            if key in ((curses.KEY_F10,) if self.focus == "input" else (ord("q"), curses.KEY_F10)) and not quitting:
                quitting.append(key)
                self.toast("cancelling the job, then quitting" if j.get("cancellable", True) else
                           f"waiting for the {UNCUT.get(j['verb'], j['verb'])} to finish, then quitting")
            return key == ESC or bool(quitting)

        try:
            job = self.client.wait_job(job["id"], on_progress=on_progress, poll=0.15)
        finally:
            self.running = None
        if quitting:
            raise Quit
        self.tick(force=True)
        if job["status"] == "failed":
            self.toast(f"{title}: {job['error']['message']}", "alert")
        elif job["status"] == "cancelled":
            self.toast(f"{title}: cancelled; what landed is kept")
        return job

    def start(self, title, start, **kw):
        """Start a job; a caution comes back as 409 and is asked about before the retry."""
        try:
            job = start(**kw)
        except ApiError as exc:
            if exc.code != "confirm_required":
                raise
            if not self.confirm(f"Confirm: {title}", [exc.message.replace(" Send again with confirm: true.", "")]):
                raise Cancel from None
            job = start(confirm=True, **kw)
        return self.wait(title, job)

    def aim(self, region=None):
        """Where a send goes, as the job's target arguments, and a label for it."""
        if region:
            return {"region": region}, f"every node in {region}"
        tagged = self.tagged
        if tagged:
            if len({d["region"] for d in tagged}) > 1:
                why = "the tagged devices span more than one region; a send is to one region"
                self.toast(why, "alert")
                raise Cancel(why)
            return {"tagged": True}, f"{len(tagged)} tagged in {tagged[0]['region']}"
        if not self.device:
            self.toast("no device: d discovers a region's nodes", "alert")
            raise Cancel("no device: d discovers a region's nodes")
        d = self.device
        return {"devices": [d["node"]]}, d["name"] or d["node"]

    # -- verbs -------------------------------------------------------------------------

    def actions(self):
        """The interface's own entries for the palette: `(label, (verb, arg))`."""
        out = [("send a command", ("send", None)), ("send to every node in a region", ("region", None)),
               ("run a command set", ("runset", None)), ("save a command set", ("saveset", None)),
               ("remove a command set", ("removeset", None)), ("discover a region", ("discover", None)),
               ("names and versions", ("names", None)), ("fleet refresh: battery and seen", ("fleet", None)),
               ("statistics 24 h", ("stats", "24h")), ("statistics 48 h", ("stats", "48h")),
               ("statistics 7 d", ("stats", "168h")), ("history of the device", ("history", None)),
               ("edit the device", ("edit", None)), ("forget the device", ("forget", None)),
               ("keys: show", ("keys", None)), ("generate key", ("generate", None)), ("import a seed", ("import", None)),
               ("register key", ("register", None)), ("setup: the control broker", ("setup", None)),
               ("test the broker", ("test", None)), ("directory columns", ("columns", None)),
               ("shortcuts Alt+1..9", ("shortcuts", None)), ("tag a region", ("tagregion", True)),
               ("untag a region", ("tagregion", False)), ("invert tags", ("invert", None)),
               ("clear tags", ("untagall", None)), ("help", ("help", None)), ("phosphor: amber / green", ("phosphor", None)),
               ("quit", ("quit", None))]
        out += [(f"sort by {s}", ("sort", s)) for s in self.dir.get("sorts", ())]
        out += [(f"Alt+{n} {c}", ("alt", n)) for n, c in enumerate(self.client.shortcuts()["shortcuts"], 1) if c]
        return out

    def command_entries(self):
        """The API's command list for the cursor device (or the plain table), as palette
        entries: what the firmware does not admit is greyed with the build it needs."""
        d = self.device
        listing = self.client.device_commands(d["node"]) if d else self.client.commands()
        entries = []
        for group in listing["groups"]:
            for c in group["commands"]:
                label = c["text"] + {"none": "", "choice": " <" + "|".join(x or "-" for x in c["choices"]) + ">",
                                     "multi": " <...>", "text": " <...>"}[c["kind"]]
                why = None
                if c["admitted"] is False:
                    why = f"needs {c['needs'] or 'a newer build'}; {listing['version'] or 'the version is unknown (n reads it)'}"
                    label += f"  (needs {c['needs'] or '?'})"
                elif c["refusal"]:
                    why = f"the node refuses it: {c['refusal']}"
                entries.append((label, ("command", c), why, group["group"]))
        return entries, listing

    def palette_run(self):
        entries = [(label, value, None, "") for label, value in self.actions()]
        commands, _l = self.command_entries()
        picked = self.pick("Palette", entries + commands, note="type to filter  ↵ run  Esc close")
        return self.do(*picked)

    def do(self, verb, arg=None):
        """Run a verb; "quit" comes back for the main loop."""
        return "quit" if verb == "quit" else getattr(self, "v_" + verb)(arg)

    def v_commands(self, _arg):
        entries, listing = self.command_entries()
        greyed = listing.get("greyed") or 0
        note = f"{greyed} greyed: need a newer build than {listing.get('firmware') or '?'}" if greyed else "↵ runs"
        _verb, c = self.pick(f"Commands {self.g['run']} {(self.device or {}).get('name') or ''}", entries, note=note)
        self.v_command(c)

    def v_command(self, c):
        """A table entry: its parameter asked for the way it needs, then sent."""
        if c["kind"] == "none":
            command = c["text"]
        elif c["kind"] == "choice":
            value = self.pick(c["text"], [(x or "(empty)", x, None, "") for x in c["choices"]], query=False)
            command = f"{c['text']} {value}".strip()
        elif c["kind"] == "multi":
            values = self.pick(c["text"], [(x, x, None, "") for x in c["choices"]], multi=True, query=False,
                               note="␣ toggles  ↵ sends")
            command = " ".join([c["text"]] + values)
        else:
            value = self.ask(c["text"], "value", body=["Examples: " + "   ".join(c["examples"])] if c["examples"] else ())
            if not value:
                raise Cancel
            command = f"{c['text']} {value}"
        self.send(command)

    def send(self, command, region=None):
        target, label = self.aim(region)
        job = self.start(f"{command[:30]} {self.g['run']} {label}", self.client.start_send, command=command, **target)
        if job["status"] == "done":
            r = job["result"]
            self.toast(f"{command[:24]}: {r['answered']} of {r['asked']} answered")

    def v_send(self, _arg, region=None):
        target, label = self.aim(region)
        seen = [line["command"] for line in self.history] + [line["command"] for line in self.client.broadcast_history(50)["history"]]
        command = self.ask("Send", "command", body=[f"To {label}.", "Up recalls earlier commands; a tree line with | cannot travel."],
                           history=list(dict.fromkeys(reversed(seen)))[::-1])
        if command:
            self.send(command, region)

    def v_region(self, _arg):
        region = self.ask_region("Send to every node in which region?")
        self.v_send(None, region=region)

    def ask_region(self, question, hint=None):
        first = (self.device or {}).get("region") or (self.client.regions()["regions"] or [""])[0]
        region = self.ask("Region", "region", first, body=[question] + ([hint] if hint else []))
        if not region:
            raise Cancel
        return region.upper()

    def v_alt(self, n):
        command = self.client.shortcuts()["shortcuts"][n - 1]
        if not command:
            self.toast(f"Alt+{n} is not set: : shortcuts", "alert")
            return
        self.send(command)

    def v_runset(self, _arg):
        target, label = self.aim()
        sets = self.client.sets()["sets"]
        if not sets:
            self.toast("no command sets: : save a command set", "alert")
            return
        name = self.pick("Run a command set", [(n, n, None, " ; ".join(s)[:40]) for n, s in sorted(sets.items())])
        arg = None
        if any("{arg}" in s for s in sets[name]):
            arg = self.ask(name, "{arg}", body=[" ; ".join(sets[name])])
        job = self.start(f"set {name} {self.g['run']} {label}", self.client.start_run_set, name=name, arg=arg, **target)
        if job["status"] == "done":
            stopped = [r for r in job["result"]["runs"] if r["stopped_at"] is not None]
            self.toast("every step answered ok" if not stopped else f"stopped at step {stopped[0]['stopped_at'] + 1}",
                       "alert" if stopped else "base")

    def v_saveset(self, _arg):
        sets = self.client.sets()
        name = self.ask("Save a command set", "name", body=["Sets: " + (", ".join(sorted(sets["sets"])) or "none")])
        if not name:
            raise Cancel
        current = sets["sets"].get(name.lower(), [])
        steps = self.form(f"Set {name}", [[f"step {i + 1}", current[i] if i < len(current) else "", False]
                                          for i in range(sets["max_steps"])],
                          body=["{arg} in a step is asked for when the set runs. F2 saves."])
        saved = self.client.save_set(name, [s for s in steps if s.strip()])
        self.toast(f"saved {saved['name']}: {len(saved['steps'])} steps")

    def v_removeset(self, _arg):
        sets = self.client.sets()["sets"]
        name = self.pick("Remove a command set", [(n, n, None, " ; ".join(s)[:40]) for n, s in sorted(sets.items())])
        self.client.remove_set(name)
        self.toast(f"removed {name}")

    def v_discover(self, _arg):
        region = self.ask_region("Find the nodes that accept your key in which region?",
                                 "One IATA code, several with commas, or * for every region you have.")
        job = self.wait(f"discover {region}", self.client.start_discover(region))
        found = (job.get("result") or {}).get("found") or []
        if job["status"] != "done":
            return
        if not found:
            self.toast("no node answered: none there holds your key, or its control plane is off", "alert")
            return
        new = [e for e in found if e["known_as"] is None]          # "" is a known device without a name
        if not new:
            self.toast(f"{len(found)} answered, every one already known")
            return
        nodes = self.pick(f"Found {len(found)}: adopt which?",
                          [(f"{e['node']} {e['name'] or e['outcome']}"[:50], e["node"], None, e["region"]) for e in new],
                          multi=True, chosen=[e["node"] for e in new], query=False, note="␣ toggles  ↵ adopts")
        added = 0
        for e in new:
            if e["node"] not in nodes:
                continue
            name = e["name"]
            if not name:
                try:
                    name = self.ask("Name the device", "name", body=[f"{e['node']} in {e['region']}; empty names it later."])
                except Cancel:
                    continue
            self.client.adopt(e["node"], e["region"], key=e["key"], name=name)
            added += 1
        self.toast(f"{added} device(s) remembered")

    def v_names(self, _arg):
        target = {"tagged": True} if self.tagged else ({"devices": [self.device["node"]]} if self.device else None)
        if not target:
            raise Cancel
        count = len(self.tagged) or 1
        if count > 3 and not self.confirm("Names and versions", [f"{count} devices, two signed reads each.",
                                                                  "Each read spends one of the node's command slots this hour."]):
            raise Cancel
        job = self.wait("names and versions", self.client.start_refresh_names(**target))
        if job["status"] == "done":
            r = job["result"]
            self.toast(f"{r['versioned']} of {r['asked']} gave a version; {r['named']} named")

    def v_fleet(self, _arg):
        job = self.wait("fleet: Beacon", self.client.start_refresh_fleet(tagged=True if self.tagged else None))
        if job["status"] == "done":
            r = job["result"]
            self.toast(f"refreshed {r['updated']} of {r['asked']} from Beacon")

    def v_stats(self, range_=None):
        d = self.device
        if not d:
            raise Cancel
        range_ = range_ or "24h"
        interval = "24h" if range_ == "168h" else "6h"
        job = self.wait(f"statistics {d['name'] or d['node']} {range_}",
                        self.client.start_statistics(d["node"], range=range_, interval=interval))
        if job["status"] == "done":
            self.toast(job["result"]["lines"][0][:60])

    def v_history(self, _arg):
        d = self.device
        if not d:
            raise Cancel
        lines = [f"{line['at'][:16]}  {line['command'][:28]:28} {self.history_out(line)}"
                 for line in reversed(self.client.history(d["node"])["history"])]
        self.sheet(f"History {d['name'] or d['node']}", lines or ["Nothing sent yet."])

    def v_reread(self, _arg):
        """Enter on a row of the stats tile: ask the device again what it said."""
        if not self.stats:
            raise Cancel
        command = self.stats[min(self.read_cursor, len(self.stats) - 1)][3]
        if command.startswith("stats"):
            return self.v_stats(None)
        target = {"devices": [self.device["node"]]}
        job = self.start(f"{command[:30]} {self.g['run']} {self.device['name'] or self.device['node']}",
                         self.client.start_send, command=command, **target)
        if job["status"] == "done":
            self.toast(f"{command[:24]}: {job['result']['answered']} of {job['result']['asked']} answered")

    def v_open(self, _arg):
        if self.feed:
            self.sheet("Result", self.feed[self.feed_cursor][2])

    def v_edit(self, _arg):
        d = self.device
        if not d:
            raise Cancel
        name, region, notes = self.form(f"Edit {d['name'] or d['node']}", [["name", d["name"], False], ["region", d["region"], False],
                                                                           ["notes", d["notes"], False]],
                                        body=[f"Node {d['node']}, added {d['added'] or '?'}"])
        self.client.edit_device(d["node"], name=name, region=region, notes=notes)
        self.toast("saved")

    def v_forget(self, _arg):
        d = self.device
        if not d:
            raise Cancel
        if self.confirm("Forget device", [f"{d['name'] or d['node']} ({d['node']}, {d['region']})",
                                          "Nothing is sent to it; its history file stays."]):
            self.client.forget(d["node"])
            self.toast(f"forgot {d['name'] or d['node']}")

    def v_keys(self, _arg):
        k = self.client.key()
        if not k["exists"]:
            self.toast("no signing key: : generate key", "alert")
            return
        reg = k["registered"]
        lines = ["Provision each repeater over its serial console with:", ""] + [f"  {line}" for line in k["provision"]] + [
            "", f"The private seed is in {k['path']}", "and never leaves this machine inside anything but a signature.", "",
            f"Registered with {reg['broker']} on {reg['at'][:10]}." if reg else "Not registered with the broker yet."]
        lines += ["", "g generates a new key   r registers this one with the broker"]
        choice = self.sheet("Signing key", lines, keys="gr")
        if choice == "g":
            self.v_generate(None)
        elif choice == "r":
            self.v_register(None)

    def v_generate(self, _arg):
        exists = self.client.key()["exists"]
        if exists and not self.confirm("Replace key", ["Every repeater provisioned with the current key will stop accepting "
                                                       "commands until it is re-provisioned over serial."]):
            raise Cancel
        self.client.create_key(overwrite=exists)
        self.toast("key created: k shows the provisioning lines")

    def v_import(self, _arg):
        seed = self.ask("Import a seed", "seed", body=["Paste the 64-hex private seed."], secret=True)
        exists = self.client.key()["exists"]
        if exists and not self.confirm("Replace key", ["The current key is replaced by the imported one."]):
            raise Cancel
        self.client.create_key(overwrite=exists, seed=seed)
        self.toast("key imported")

    def v_register(self, _arg):
        job = self.wait("register the key", self.client.start_register())
        if job["status"] == "done":
            r = job["result"]
            self.toast(("registered: " if r["ok"] else "not registered: ") + r["detail"], "base" if r["ok"] else "alert")

    def v_setup(self, _arg):
        c = self.client.config()
        fields = [["broker", c["broker"], False], ["port", str(c["port"]), False], ["tls", "on" if c["tls"] else "off", False],
                  ["username", c["username"], False], ["password", "", True], ["wait seconds", str(c["wait_secs"]), False],
                  ["statistics URL", c["beacon_url"], False]]
        while True:
            values = self.form("Control broker", fields, body=["The broker a node's mqtt.cmd.slot points at, and its credentials "
                                                               f"(the password is {'set; empty keeps it' if c['password_set'] else 'not set'}). "
                                                               "TLS on 8883 is the command plane; plain 1883 takes no commands. "
                                                               "Statistics come from a Beacon server, read-only."])
            tls = ONOFF.get(values[2].strip().lower())
            if tls is not None:
                break
            for f, v in zip(fields, values):           # the form opens again as it was left
                f[1] = v
            self.toast("tls is on or off", "alert")
        try:
            port, wait = int(values[1]), float(values[5])
        except ValueError:
            self.toast("port and wait seconds are numbers", "alert")
            return
        change = dict(broker=values[0].strip(), port=port, tls=tls, username=values[3].strip(), wait_secs=wait,
                      beacon_url=values[6].strip())
        if values[4]:                                      # the password travels only when typed
            change["password"] = values[4]
        self.client.set_config(**change)
        self.toast("broker saved: : test the broker")

    def v_test(self, _arg):
        job = self.wait(f"test {self.cfg.get('broker')}", self.client.start_test())
        if job["status"] == "done":
            ok = job["result"]["ok"]
            self.toast("broker test passed" if ok else "broker test failed: Enter on the feed for the steps",
                       "base" if ok else "alert")

    def v_columns(self, _arg):
        picked = self.pick("Directory columns", [(c, c, None, "") for c in self.dir["all_columns"]], multi=True,
                           chosen=self.dir["columns"], query=False, note="␣ toggles  ↵ saves")
        self.client.set_config(columns=picked or list(self.dir["all_columns"]))
        self.toast("columns saved")

    def v_shortcuts(self, _arg):
        slots = self.client.shortcuts()["shortcuts"]
        values = self.form("Shortcuts", [[f"Alt+{n}", s, False] for n, s in enumerate(slots, 1)],
                           body=["A command per key, sent to the tagged or cursor device. Empty frees a slot. F2 saves."])
        self.client.set_shortcuts([v.strip() for v in values])
        self.toast("shortcuts saved")

    def v_tagregion(self, on):
        region = self.ask_region("Tag every device in which region?" if on else "Untag which region?")
        (self.client.tag if on else self.client.untag)(region=region)

    def v_invert(self, _arg):
        self.client.invert_tags()

    def v_untagall(self, _arg):
        self.client.untag()

    def v_sort(self, how):
        self.sort = how
        self.toast(f"sorted by {how}")

    def v_sortnext(self, _arg):
        sorts = self.dir.get("sorts") or ["name"]
        self.v_sort(sorts[(sorts.index(self.sort) + 1) % len(sorts)] if self.sort in sorts else sorts[0])

    def v_phosphor(self, _arg):
        self.swap_palette()

    def v_help(self, _arg):
        """The help the API has for the board, opening on its keys: Tab to a link, Enter
        follows, Backspace goes back."""
        trail = [self.client.help_index(ui=UI)["context"].get("main", "keys")]
        while trail:
            t = self.client.help_topic(trail[-1], ui=UI)
            links = [(l["line"], l["start"], l["end"], l["target"]) for l in t["links"]]
            nxt = self.sheet(t["title"], t["lines"], links)
            if nxt is None:
                return
            if nxt == "\b":
                trail.pop()
            else:
                trail.append(nxt)


# One key per verb on the board; the palette has every one of them by name.
KEYS = {"c": "send", "a": "region", "s": "stats", "n": "names", "r": "fleet", "d": "discover", "h": "history",
        "e": "edit", "k": "keys", "o": "sortnext", "*": "invert", "+": "tagregion", "-": "tagregion", "p": "phosphor",
        "S": "setup"}

# Every key the board answers, by the name the help texts give it; tests/test_help.py checks
# this interface's help against it. "y" answers a confirmation, "g" and "r" the key sheet.
KEY_NAMES = (":", "Ctrl+P", "Enter", "Space", "Ins", "Del", "Tab", "Shift+Tab", "Up", "Down", "PgUp", "PgDn", "Home",
             "End", "Backspace", "Esc", "F1", "F2", "F10", "Alt+1..9", "Ctrl+L", "?", "q", "y", "g",
             *KEYS)
