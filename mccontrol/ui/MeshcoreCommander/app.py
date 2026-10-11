"""The second interface: a menu bar and key bar around two panels.

Left, the device directory; right, what the cursor is on. Every verb is a function key and
acts on the tagged devices, or the cursor device when nothing is tagged. All state that
matters is behind the API; the screen is redrawn from it after every key, and every verb is
a request through the client.
"""

import curses

from ...apiclient import ApiError
from .dialogs import Button, ChoiceBox, Dialog, Field, ListBox, Progress, confirm, notice
from .help import HelpWindow
from .menu import Menu, MenuBar, MenuItem
from .widgets import KeyBar, ListView, Panel
from .screen import KEY_ENTER, run as _run

UI = "MeshcoreCommander"                # the name the API's help is asked for
ONOFF = {"on": True, "yes": True, "true": True, "1": True, "off": False, "no": False, "false": False, "0": False}
KEYS = ["Help", "Send", "Hist", "Edit", "Disco", "Regns", "All", "Del", "Menu", "Quit"]
# Every key the screens answer, by the name the help texts give it; tests/test_help.py
# checks this interface's help against it. Alt+D/S/V/K/O/H open the menus by their titles.
KEY_NAMES = ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "Shift+F1", "Alt+F1", "Alt+1..9",
             "Alt+D", "Alt+S", "Alt+V", "Alt+K", "Alt+O", "Alt+H", "Ctrl+L", "Enter", "Esc", "Tab", "Shift+Tab",
             "Up", "Down", "Left", "Right", "PgUp", "PgDn", "Ins", "Space", "Backspace",
             "s", "r", "n", "c", "h", "*", "+", "-")
HISTORY_PER = 6                         # commands per device in View > Command history


class Topic:
    """A help topic as the API gives it, in the shape the help window reads."""

    def __init__(self, page):
        self.name, self.title, self.lines = page["name"], page["title"], page["lines"]
        self.links = [(l["line"], l["start"], l["end"], l["target"]) for l in page["links"]]


class App:
    def __init__(self, screen, client):
        self.screen = screen
        self.client = client
        self.keybar = KeyBar(KEYS)
        self.sort = "name"
        self.devices = []
        self.tagged = set()                  # the tagged nodes, as the API holds them
        self.header = ""                     # the directory's column heads, from the last reload
        self.columns, self.all_columns = [], []
        w, h = screen.size
        split = w // 2
        total = h - 3                                # the menu bar above; the command line, the key bar below
        lower_h = max(5, total // 3)                 # "Stats" under the history
        self.left = Panel(1, 0, split, total, "Devices")
        self.right = Panel(1, split, w - split, total - lower_h, "History")
        self.lower = Panel(1 + total - lower_h, split, w - split, lower_h, "Stats")
        self.left.active = True
        self.list = ListView(self.left, header=1)
        self.view = ListView(self.right)
        self.reads_view = ListView(self.lower)
        self.reads_rows = []                 # (command to read it again, the fact) behind the lower panel
        self.line, self.typing, self.typed = "", False, []   # the command line above the key bar
        self.console = []                    # what the command line sent and got, for the right panel
        self.inline = False                  # while the command line sends: a refusal goes to the console
        self.found = []                      # the last discovery's entries
        self.right_rows = []                 # (line, full_text) pairs behind the right panel
        self.right_mode = "history"          # "history" | "results" | "found" | "regions"
        self.right_node = None               # whose history the panel shows
        self.helpwin = HelpWindow(self.topic)
        self.context = "main"                # which help topic F1 opens
        self.menu = MenuBar([
            Menu("Devices", [MenuItem("Discover", "discover", "F5"), MenuItem("Edit", "edit", "F4"),
                             MenuItem("Forget", "forget", "F8"), MenuItem("Sort by name", "sort:name"),
                             MenuItem("Sort by region", "sort:region"), MenuItem("Sort by last sent", "sort:last sent"),
                             MenuItem("Sort by version", "sort:version"), MenuItem("Names and versions", "names", "n"),
                             MenuItem("Tag all in region", "tag:+", "+"), MenuItem("Untag region", "tag:-", "-"),
                             MenuItem("Clear tags", "untagall")]),
            self.send_menu(),
            Menu("View", [MenuItem("Statistics", "stats:24h:6h", "s"), MenuItem("Last 48 hours", "stats:48h:6h"),
                          MenuItem("Last 7 days", "stats:168h:24h"), MenuItem("Refresh Batt/Seen", "fleet", "r"),
                          MenuItem("Command history", "blocks", "h"), MenuItem("Command line output", "console")]),
            Menu("Keys", [MenuItem("Show public key", "keys")]),
            Menu("Options", [MenuItem("Broker", "setup"), MenuItem("Test the broker", "test"),
                             MenuItem("Shortcuts", "shortcuts"), MenuItem("Command sets", "sets"),
                             MenuItem("Directory columns", "columns")]),
            Menu("Help", [MenuItem("Contents", "help:index", "Shift+F1"), MenuItem("This screen", "help", "F1"),
                          MenuItem("Keys", "help:keys"), MenuItem("Commands", "help:commands"),
                          MenuItem("Quit", "quit", "F10")]),
        ])

    # -- state -------------------------------------------------------------------------

    def send_menu(self):
        """The Send menu lists the shortcuts with their keys, as Turbo Vision showed them."""
        items = [MenuItem("To tagged or cursor", "send", "F2"), MenuItem("To all in a region", "all", "F7"),
                 MenuItem("Read regions", "regions", "F6"), MenuItem("Command set", "runset")]
        items += [MenuItem(cmd[:28], f"alt:{n}", f"Alt+{n}")
                  for n, cmd in enumerate(self.client.shortcuts()["shortcuts"], 1) if cmd]
        return Menu("Send", items)

    def shortcut(self, n):
        """Alt+n: the slot's command to the tagged or cursor device, no dialog."""
        command = self.client.shortcuts()["shortcuts"][n - 1]
        if not command:
            self.refuse(f"Alt+{n} is not set; Options > Shortcuts.")
            return
        self.send(initial=command, immediate=True)

    def shortcuts_dialog(self):
        slots = self.client.shortcuts()["shortcuts"]
        fields = [Field(f"Alt+{n}", slots[n - 1], width=40, hotkey=str(n)) for n in range(1, len(slots) + 1)]
        d = Dialog("Shortcuts", fields=fields, width=60,
                   body=["A command per key, sent to the tagged or cursor device without a dialog.",
                         "Leave a slot empty to free it."])
        if d.run(self.screen, self.draw) is None:
            return
        self.client.set_shortcuts([f.value.strip() for f in fields])
        self.menu.menus[1] = self.send_menu()
        self.outcome("Shortcuts", "Shortcuts saved.")

    def reload(self):
        """Rows from the API, keeping the cursor on the same device; the tags are the API's."""
        cursor_node = self.devices[self.list.cursor]["node"] if self.devices and self.list.cursor < len(self.devices) else None
        width = self.left.inner[2]
        listing = self.client.devices(sort=self.sort, width=width)
        self.cfg = self.client.config()
        self.devices = listing["devices"]
        self.header, self.columns, self.all_columns = listing["header"], listing["columns"], listing["all_columns"]
        # a row laid out for the width is cut one short of it: the tag mark takes a column
        self.list.set_rows([d["row"][: width - 2] for d in self.devices], keep=False)
        self.tagged = set(listing["tagged"])
        self.list.tags = {i for i, d in enumerate(self.devices) if d["node"] in self.tagged}
        if cursor_node:
            for i, d in enumerate(self.devices):
                if d["node"] == cursor_node:
                    self.list.cursor = i
        self.left.footer = listing["footer"]
        self.show_reads(self.cursor_device)

    def sync_tags(self):
        """After Ins, Space or *: the rows tagged here become the API's tags."""
        nodes = {self.devices[i]["node"] for i in self.list.tags if i < len(self.devices)}
        if nodes != self.tagged:
            self.show_tags(self.client.set_tags(sorted(nodes)))

    def show_tags(self, tags):
        """The API's tags (`GET /tags` shape) on the rows and the footer."""
        self.tagged = set(tags["tagged"])
        self.list.tags = {i for i, d in enumerate(self.devices) if d["node"] in self.tagged}
        self.left.footer = tags["footer"]

    def topic(self, name):
        """A help topic for the help window, or None when there is no such topic."""
        try:
            return Topic(self.client.help_topic(name, ui=UI))
        except ApiError:
            return None

    def show_history(self, device):
        """The right panel follows the cursor: the device's history, newest first."""
        self.right_mode, self.right_node = "history", device["node"] if device else None
        self.right.title = f"History: {(device.get('name') or device['node']) if device else '-'}"
        rows = []
        for line in reversed(self.client.history(device["node"], limit=200)["history"]) if device else []:
            out = self.said(line)
            short = f"{line['at'][5:16]} {line['command'][:14]:14} {out}"
            full = [f"{line['at']}  {line['command']}", f"topic {line['topic']}", "", out]
            rows.append((short, full))
        self.right_rows = rows
        self.view.set_rows([r[0] for r in rows], keep=False)
        if not rows:
            self.view.set_rows(["(nothing sent yet)"], keep=False)
            self.right_rows = [("", ["Nothing has been sent to this device yet."])]

    @staticmethod
    def said(line):
        """What came back for a history line: the reply, the refusal or the note; BAD
        SIGNATURE before a forged reply, (unsigned) after one from firmware that does not sign."""
        out = (line["reply"] or "OK") if line["code"] == 0 else (
            f"refused {line['code_name']}" if line["code"] is not None else (line["note"] or "no answer"))
        if line.get("verified") is False:
            return f"BAD SIGNATURE {out}"
        return f"{out} (unsigned)" if line["code"] == 0 and line.get("verified") is None else out

    def show_reads(self, device):
        """The lower right panel, Stats: what is known about the device -- its settings,
        firmware and regions, each with its age and `?` where the answer was unsigned (older
        firmware) -- then the reads not parsed into facts, raw."""
        self.reads_node = (device or {}).get("node")
        known = self.client.facts(device["node"]) if device else {"facts": [], "raw": []}
        entries = known["facts"] + [dict(r, label=r["command"], value=r["reply"], read=r["command"]) for r in known["raw"]]
        self.reads_rows = [(e["read"], e) for e in entries]
        width = self.lower.inner[2] - 1
        label_w = min(17, max((len(e["label"]) for e in entries), default=0))
        rows = []
        for e in entries:
            age = e["age"] + ("?" if e["verified"] is None else " ")
            room = max(1, width - label_w - len(age) - 2)
            value = " ".join(e["value"].split())
            value = value if len(value) <= room else value[:room - 1] + "…"
            rows.append(f"{e['label'][:label_w]:{label_w}} {value:{room}} {age}"[:width])
        self.reads_view.set_rows(rows or ["(nothing known yet)"], keep=False)
        self.lower.title = "Stats  (? unsigned)" if any(e["verified"] is None for e in entries) else "Stats"

    def reread(self):
        """Enter on a line of Stats: send the read that taught it again to the cursor device."""
        if not self.reads_rows or self.reads_view.cursor >= len(self.reads_rows):
            return
        command = self.reads_rows[self.reads_view.cursor][0]
        self.send(initial=command, immediate=True)

    def activate(self, which):
        for panel in (self.left, self.right, self.lower):
            panel.active = panel is which

    def show_console(self):
        """The right panel as the command line's console: each command and what came back,
        the newest at the bottom, scrolled to it."""
        self.right_mode, self.right_node = "console", None
        self.right.title = "Command line"
        self.right_rows = [(line, [line]) for line in self.console]
        self.view.set_rows(self.console or ["(nothing sent from the command line yet)"], keep=False)
        self.view.cursor = max(0, len(self.view.rows) - 1)
        self.view._scroll()

    def show_blocks(self):
        """View > Command history: per device its last commands and what came back, newest
        block first, the short id once on the left; a region broadcast is a block of its own."""
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
        rows, width = [], self.right.inner[2] - 1
        for _at, ident, lines in sorted(blocks, key=lambda b: b[0], reverse=True):
            for line in lines:
                reply = (line["reply"] or "OK") if line["code"] == 0 else (
                    f"refused {line['code_name']}" if line["code"] is not None else (line["note"] or "no answer"))
                reply = reply[1:].strip() if line["code"] == 0 and reply.startswith(">") else reply
                if line.get("verified") is False:
                    reply = f"BAD SIGNATURE {reply}"
                elif line["code"] == 0 and line.get("verified") is None:
                    reply += " (unsigned)"
                for text in (f"{ident:6}│ > {line['command']}", f"{'':6}│ < {' '.join(reply.split())}"):
                    rows.append((text if len(text) <= width else text[:width - 1] + "…",
                                 [f"{line['at']}  {line['command']}", "", reply]))
                ident = ""
        self.right_mode, self.right_node = "blocks", None
        self.right.title = "Command history"
        self.right_rows = rows or [("", ["Nothing has been sent yet."])]
        self.view.set_rows([r[0] for r in rows] or ["(nothing sent yet)"], keep=False)

    def show_results(self, title, lines, details=None):
        """After a send or a discovery: what came back, until the cursor moves."""
        self.right_mode, self.right_node = "results", None
        self.right.title = title
        self.right_rows = [(line, (details or {}).get(line, [line])) for line in lines]
        self.view.set_rows(lines, keep=False)

    def follow_cursor(self):
        d = self.cursor_device
        if self.right_mode == "history" and (d or {}).get("node") != self.right_node:
            self.show_history(d)
        if (d or {}).get("node") != getattr(self, "reads_node", None):
            self.reads_node = (d or {}).get("node")
            self.show_reads(d)
        elif self.right_mode == "results" and (d or {}).get("node") != self.right_node:
            pass

    @property
    def cursor_device(self):
        if self.devices and self.list.cursor < len(self.devices):
            return self.devices[self.list.cursor]
        return None

    @property
    def targets(self):
        return [self.devices[i] for i in self.list.selected if i < len(self.devices)]

    # -- drawing -----------------------------------------------------------------------

    def draw(self):
        s = self.screen
        s.erase()
        self.left.draw(s)
        self.right.draw(s)
        self.lower.draw(s)
        y, x, w, _h = self.left.inner
        s.put(y, x + 1, self.header, s.colors.desktop | curses.A_UNDERLINE)
        self.list.draw(s)
        self.view.draw(s)
        self.reads_view.draw(s)
        self.draw_line()
        self.menu.draw(s)
        end = self.menu.positions[-1] + len(self.menu.menus[-1].title) + 3 if self.menu.positions else 0
        for status in self.status():                         # the longest that fits right of them
            if s.size[0] - len(status) - 2 > end:
                s.put(0, s.size[0] - len(status) - 2, status, s.colors.bar)
                break
        self.keybar.draw(s)
        s.refresh()

    def status(self):
        """Right of the menu bar, as Norton Commander kept its clock there: the broker, whether
        the key is registered, and the last broker test kept for this broker."""
        cfg = getattr(self, "cfg", None) or {}
        kept = cfg.get("tested") or {}
        same = (kept.get("broker"), kept.get("port")) == (cfg.get("broker"), cfg.get("port"))
        test = ("test ok" if kept.get("ok") else "test FAILED") if kept and same else "untested"
        reg = "registered" if cfg.get("registered") else "not registered"
        return [f"{cfg.get('broker', '?')}:{cfg.get('port', '?')}  {reg}  {test}", f"{reg}  {test}", test]

    def target(self):
        """Who the command line sends to, as its prompt says it."""
        targets = self.targets
        if len(targets) > 1:
            return f"{len(targets)} tagged"
        return (targets[0].get("name") or targets[0]["node"]) if targets else "no device"

    def draw_line(self):
        """The command line, as Norton Commander kept a DOS prompt above its key bar."""
        s = self.screen
        w, h = s.size
        prompt = f"{self.target()[:16]}>"
        s.put(h - 2, 0, " " * (w - 1), s.colors.bar)
        s.put(h - 2, 0, prompt, s.colors.bar | (curses.A_BOLD if self.typing else 0))
        room = max(1, w - len(prompt) - 3)
        shown = self.line[-room:] if (self.line or self.typing) else "Tab here or click to type a command"
        s.put(h - 2, len(prompt) + 1, shown, s.colors.bar if (self.line or self.typing) else s.colors.bar | curses.A_DIM)
        s.cursor(self.typing)
        if self.typing:
            try:
                s.win.move(h - 2, len(prompt) + 1 + len(self.line[-room:]))
            except curses.error:
                pass

    def type_key(self, key):
        """A key on the command line; False for one it does not take (F-keys, Tab, the mouse)."""
        if key == 27:
            self.typing, self.recall = False, None
            self.activate(self.left)
        elif key in KEY_ENTER:
            command = " ".join(self.line.split())
            self.line, self.recall = "", None
            if command:
                self.typed = [c for c in self.typed if c != command] + [command]
                self.from_line(command)
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.line = self.line[:-1]
        elif key in (curses.KEY_UP, curses.KEY_DOWN):
            if getattr(self, "recall", None) is None:
                seen = [l["command"] for l in self.client.broadcast_history(50)["history"]]
                for d in self.targets[:1]:
                    seen += [l["command"] for l in self.client.history(d["node"], 50)["history"]]
                back = list(dict.fromkeys(reversed(seen + self.typed)))[::-1]
                self.recall = (back, len(back))
            back, pos = self.recall
            pos = max(0, pos - 1) if key == curses.KEY_UP else min(len(back), pos + 1)
            self.recall = (back, pos)
            if back:
                self.line = back[pos] if pos < len(back) else ""
        elif 32 <= key < 127:
            self.line += chr(key)
        else:
            return False
        return True

    def from_line(self, command):
        """Enter on the command line: sent as F2 sends it -- the tagged devices or the cursor
        one, a caution confirmed first -- and the answers land in the right panel's console;
        a refusal is said there too, not in a window."""
        self.console.append(f"{self.target()}> {command}")
        self.inline = True
        try:
            self.send(initial=command, immediate=True)
        finally:
            self.inline = False
        del self.console[:-300]
        self.show_console()

    # -- main loop ---------------------------------------------------------------------

    def run(self):
        self.reload()
        self.show_history(self.cursor_device)
        while True:
            self.draw()
            key = self.screen.key()
            if key == curses.KEY_F10:
                return 0
            if key == 12 or key == curses.KEY_RESIZE:        # Ctrl+L, or the terminal changed size
                self.screen.repaint()
                continue
            if self.typing and key != 9 and self.type_key(key):
                continue
            if key == curses.KEY_F9:                            # the key bar's "9Menu"
                key = curses.KEY_F10
            if self.menu.wants(key):
                action = self.menu.run(self.screen, key, self.draw)
                if action:
                    self.do(action)
                continue
            if key == curses.KEY_F1:
                self.do("help")
            elif key == curses.KEY_F0 + 13:                     # Shift+F1: the contents
                self.do("help:index")
            elif key == curses.KEY_F2:
                self.do("send")
            elif key == curses.KEY_F3:
                self.do("history")
            elif key in KEY_ENTER and self.left.active:
                self.do("commands")
            elif key in KEY_ENTER and self.right.active:
                self.open_line()
            elif key == curses.KEY_F4:
                self.do("edit")
            elif key == curses.KEY_F5:
                self.do("discover")
            elif key == curses.KEY_F6:
                self.do("regions")
            elif key == curses.KEY_F7:
                if self.right_mode == "found":
                    self.adopt_found(range(len(self.found)), ask=False)
                else:
                    self.do("all")
            elif key == curses.KEY_F8:
                self.do("forget")
            elif key == 9:                                   # Tab: the three panels, then the command line
                order = (self.left, self.right, self.lower)
                current = 3 if self.typing else next((i for i, p in enumerate(order) if p.active), 0)
                self.typing = current == 2
                self.activate(None if self.typing else order[(current + 1) % 4])
            elif key in KEY_ENTER and self.lower.active:
                self.reread()
            elif key == 27 and self.right_mode == "found":
                self.right_mode = "history"
                self.activate(self.left)
                self.show_history(self.cursor_device)
            elif key == curses.KEY_MOUSE:
                self.click()
            elif key == ord("s") and self.left.active:
                self.do("stats:24h:6h")
            elif key == ord("r") and self.left.active:
                self.do("fleet")
            elif key == ord("n") and self.left.active:
                self.do("names")
            elif key == ord("h") and self.left.active:
                self.do("blocks")
            elif key == ord("+") or key == ord("-"):
                self.do("tag:" + chr(key))
            elif key < 0 and chr(-key) in "123456789":          # Alt+digit
                self.shortcut(int(chr(-key)))
            elif self.left.active and self.list.handle(key):
                self.sync_tags()
                if self.right_mode != "history":
                    self.right_mode = "history"        # a moved cursor ends a result view
                self.show_history(self.cursor_device)
                self.show_reads(self.cursor_device)
            elif self.right.active and self.view.handle(key):
                pass
            elif self.lower.active and self.reads_view.handle(key):
                pass

    def click(self):
        """A click: a menu title opens it, a key-bar label presses it, a row takes the cursor."""
        if not self.screen.mouse:
            return
        y, x = self.screen.mouse
        w, h = self.screen.size
        if y == 0:
            i = self.menu.hit(x)
            if i is not None:
                action = self.menu.run(self.screen, None, self.draw, index=i)
                if action:
                    self.do(action)
        elif y == h - 1:
            n = KeyBar.hit(x, w)
            if n:
                curses.ungetch(curses.KEY_F0 + n)
        elif y == h - 2:                                    # the command line
            self.typing = True
            self.activate(None)
        elif self.list.click(y, x):
            self.typing = False
            self.activate(self.left)
            if self.right_mode != "history":
                self.right_mode = "history"
            self.show_history(self.cursor_device)
            self.follow_cursor()
        elif self.view.click(y, x):
            self.activate(self.right)
        elif self.reads_view.click(y, x):
            self.activate(self.lower)

    def do(self, action):
        if action == "quit":
            raise SystemExit(0)
        if action.startswith("sort:"):
            self.sort = action[5:]
            self.reload()
            self.outcome("Sort", f"Sorted by {self.sort}.")
        elif action.startswith("tag:"):
            self.tag_region(action[4:] == "+")
        elif action == "untagall":
            self.show_tags(self.client.untag())
        elif action == "help":
            self.help()
        elif action.startswith("help:"):
            self.helpwin.run(self.screen, action[5:], self.draw)
        elif action == "history":
            self.history_window()
        elif action == "blocks":
            self.show_blocks()
            self.activate(self.right)
        elif action == "console":
            self.show_console()
            self.activate(self.right)
        elif action == "commands":
            self.command_list()
        elif action == "send":
            self.send()
        elif action == "discover":
            self.discover()
        elif action == "edit":
            self.edit()
        elif action == "regions":
            d = self.cursor_device
            if d:
                self.list.tags = set()
                self.sync_tags()
                self.send(initial="region", immediate=True)
        elif action == "forget":
            self.forget()
        elif action == "keys":
            self.keys_dialog()
        elif action == "setup":
            self.setup_dialog()
        elif action == "test":
            self.test_dialog()
        elif action == "shortcuts":
            self.shortcuts_dialog()
        elif action == "sets":
            self.sets_dialog()
        elif action == "columns":
            self.columns_dialog()
        elif action == "fleet":
            self.refresh_fleet()
        elif action == "names":
            self.refresh_names()
        elif action.startswith("stats:"):
            _s, range_, interval = action.split(":")
            self.statistics(range_, interval)
        elif action == "runset":
            self.run_set()
        elif action.startswith("alt:"):
            self.shortcut(int(action[4:]))
        elif action == "all":
            region = self.ask_region("Send to every node in which region?")
            if region:
                self.send(region=region)
        else:
            self.refuse(f"{action}: not built yet.")

    def open_line(self):
        """Enter on the right panel: the whole reply in a dialog."""
        if not self.right_rows or self.view.cursor >= len(self.right_rows):
            return
        notice(self.screen, "Reply", self.right_rows[self.view.cursor][1], self.draw)

    def history_window(self):
        """F3: the cursor device's history as a window over the work."""
        d = self.cursor_device
        if not d:
            return
        lines = []
        for line in reversed(self.client.history(d["node"], limit=200)["history"]):
            out = self.said(line)
            lines.append(f"{line['at'][:16]}  {line['command'][:28]:28} {out[:30]}")
        notice(self.screen, f"History: {d.get('name') or d['node']}", lines or ["Nothing sent yet."], self.draw)

    def keys_dialog(self):
        """The public key on one line, the provisioning commands, create or import."""
        try:
            key = self.client.key()
        except ApiError as exc:
            notice(self.screen, "Signing key", [exc.message], self.draw)
            return
        if key["exists"]:
            public = key["public"]
            reg = key["registered"]
            body = ["Provision each repeater over its serial console with:", "",
                    f"  set mqtt.cmd.pubkey {public}", "  set mqtt.cmd on", "",
                    f"The private seed is in {key['path']}", "and never leaves this machine inside anything but a signature.", "",
                    (f"Registered with {reg['broker']} on {reg['at'][:10]}." if reg else
                     "Not registered with the broker yet: it cannot check your commands before they fan out.")]
            d = Dialog("Signing key", body=body, width=min(92, self.screen.size[0] - 4),
                       buttons=[Button("OK", None, default=True), Button("Register", "register"), Button("Replace key", "replace")])
            choice = d.run(self.screen, self.draw)
            if choice == "register":
                try:
                    job, _box = self.job("Registering", f"Publishing your public key to {self.client.config()['broker']}",
                                         lambda j: "waiting for the broker's acknowledgement", self.client.start_register,
                                         first="waiting for the broker's acknowledgement")
                    ok, detail = job["result"]["ok"], job["result"]["detail"]
                except ApiError as exc:
                    ok, detail = False, exc.message
                if ok:
                    self.outcome("Register", "Registered: " + detail)
                else:
                    self.refuse("Not registered: " + detail, title="Register")
                return
            if choice == "replace":
                if confirm(self.screen, "Replace key", ["Every repeater provisioned with the current key will stop accepting commands."],
                           "silences every node until each is re-provisioned over serial.", yes="Replace", redraw_under=self.draw):
                    try:
                        self.client.create_key(overwrite=True)
                    except ApiError as exc:
                        self.refuse(exc.message)
                        return
                    self.outcome("Signing key", "Key replaced. Re-provision every repeater with the new public key.")
            return
        d = Dialog("Signing key", body=["No signing key yet."], width=50,
                   buttons=[Button("Create", "create", default=True), Button("Import", "import"), Button("Cancel", None)])
        choice = d.run(self.screen, self.draw)
        try:
            if choice == "create":
                self.client.create_key()
                self.outcome("Signing key", "Key created; open Keys again for the provisioning command.")
            elif choice == "import":
                f = Field("Seed", "", width=64, secret=True)
                d2 = Dialog("Import a seed", fields=[f], body=["Paste the 64-hex private seed."], width=76)
                if d2.run(self.screen, self.draw):
                    self.client.create_key(seed=f.value.strip())
                    self.outcome("Signing key", "Key imported.")
        except ApiError as exc:
            self.refuse(exc.message)

    def setup_dialog(self):
        cfg = self.client.config()
        fields = [Field("Broker", cfg.get("broker", ""), width=40), Field("Port", str(cfg.get("port", 1883)), width=6),
                  Field("TLS", "on" if cfg.get("tls") else "off", width=5),
                  Field("Username", cfg.get("username", ""), width=30), Field("Password", "", width=30, secret=True),
                  Field("Wait seconds", str(cfg.get("wait_secs", 20)), width=4),
                  Field("Statistics URL", cfg.get("beacon_url", ""), width=44, hotkey="t")]
        d = Dialog("Control broker", fields=fields, width=70,
                   body=["The broker a node's mqtt.cmd.slot points at, and its credentials.",
                         "TLS on 8883 is the command plane; plain 1883 takes no commands.",
                         "Statistics come from a Beacon server, read-only, no credentials.",
                         f"Password {'(set)' if cfg.get('password_set') else '(not set)'}: an empty field keeps it."])
        while d.run(self.screen, self.draw) is not None:
            tls = ONOFF.get(fields[2].value.strip().lower())
            if tls is None:
                d.message = "TLS is on or off."
                continue
            try:
                changes = dict(broker=fields[0].value.strip(), port=int(fields[1].value), tls=tls,
                               username=fields[3].value.strip(), wait_secs=int(fields[5].value),
                               beacon_url=fields[6].value.strip())
                if fields[4].value:                 # the API never shows the password: send it only when typed
                    changes["password"] = fields[4].value
                self.client.set_config(**changes)
                self.outcome("Control broker", "Saved.")
            except ValueError:
                self.refuse("Port and wait seconds must be numbers.")
            except ApiError as exc:
                self.refuse(exc.message)
            return

    def test_dialog(self):
        cfg = self.client.config()
        try:
            job, _box = self.job("Broker test", f"Trying {cfg.get('broker')}:{cfg.get('port')}",
                                 lambda j: "connecting, signing in, subscribing, publishing", self.client.start_test,
                                 first="connecting, signing in, subscribing, publishing")
        except ApiError as exc:
            self.refuse(exc.message)
            return
        result = job["result"]
        lines = [f"{'ok  ' if s['ok'] else 'FAIL'}  {s['step']:28} {s['detail']}" for s in result["steps"]]
        has_key = result["key"] is not None
        lines.append(f"{'ok  ' if has_key else 'FAIL'}  {'signing key':28} {'present' if has_key else 'none: see Keys'}")
        reg = result["registered"]
        lines.append(f"{'ok  ' if reg else 'note'}  {'registered with broker':28} "
                     + (f"{reg['broker']} on {reg['at'][:10]}" if reg else "not yet: Keys > Register"))
        passed = result["ok"]
        self.outcome("Broker test", "Broker test passed." if passed else "Broker test failed.", lines)

    def edit(self):
        """F4: name, region and notes of the cursor device."""
        d = self.cursor_device
        if not d:
            return
        name, region, notes = Field("Name", d.get("name") or "", width=30), Field("Region", d.get("region") or "", width=8), Field("Notes", d.get("notes") or "", width=40)
        dialog = Dialog(f"Edit {d.get('name') or d['node']}", fields=[name, region, notes],
                        body=[f"Node {d['node']}, added {d.get('added', '?')}"], width=64)
        if dialog.run(self.screen, self.draw) is None:
            return
        try:
            self.client.edit_device(d["node"], name=name.value.strip(), region=region.value.strip().upper(),
                                    notes=notes.value.strip())
        except ApiError as exc:
            self.refuse(exc.message)
            return
        self.reload()
        self.show_history(self.cursor_device)
        self.outcome("Device", "Saved.")

    def forget(self):
        """F8: forget the cursor device, after a confirm. Its history file stays."""
        d = self.cursor_device
        if not d:
            return
        if not confirm(self.screen, "Forget device", [f"{d.get('name') or d['node']}  ({d['node']}, {d.get('region') or '?'})",
                                                       "Nothing is sent to it; its history file stays."],
                       "removes it from your list.", yes="Forget", redraw_under=self.draw):
            return
        try:
            self.client.forget(d["node"])
        except ApiError as exc:
            self.refuse(exc.message)
            return
        self.reload()
        self.show_history(self.cursor_device)
        self.outcome("Forget", "Forgotten.")

    def outcome(self, title, headline, lines=()):
        """What an action came to, as a window over the work: the headline first, then the
        lines (the results, which the right panel also keeps). The message row is for state
        changes made in a dialog (saved, sorted); an outcome a person waited for is a window."""
        notice(self.screen, title, [headline] + ([""] + list(lines) if lines else []), self.draw)

    def refuse(self, why, title="Not done"):
        """Why nothing was done, as a window: a precondition that failed or an error raised;
        from the command line, a line in its console instead."""
        if self.inline:
            self.console.append(f"  {why}")
            return
        notice(self.screen, title, [why], self.draw)

    def job(self, title, line, describe, start, *args, first=None, **kwargs):
        """Start a job and wait for it under a Progress box: `describe(job)` is the status
        line while the API reports a wait, `first` a line drawn before anything is reported.
        Esc cancels the job (what landed is kept). Returns `(job, box)`; a job that failed is
        raised as an ApiError, as a refusal to start is."""
        box = Progress(self.screen, title, line, self.draw)
        if first is not None:
            box.tick(first, 0.0)

        def tick(job):
            p = job["progress"]
            if not p["wait"]:
                return False                        # nothing to draw until the wait starts
            return box.tick(describe(job), p["elapsed"] / p["wait"], p["elapsed"], p["wait"])
        try:
            job = self.client.wait_job(start(*args, **kwargs)["id"], on_progress=tick)
        finally:
            box.close()
        if job["status"] == "failed":
            raise ApiError(job["error"]["code"], job["error"]["message"], 0)
        return job, box

    def aim(self, region=None):
        """Where a send goes: `(targets, target_line)`, or None with the reason said."""
        targets = [] if region else self.targets
        if not region and not targets:
            self.refuse("No device under the cursor; add or discover one first.")
            return None
        if not region and len({(d.get("region") or "").upper() for d in targets}) > 1:
            self.refuse("The tagged devices span more than one region; a send is to one region.")
            return None
        if region:
            target_line = f"EVERY node in {region}"
        elif len(targets) == 1:
            target_line = f"{targets[0].get('name') or targets[0]['node']}  ({targets[0]['node']}, {targets[0].get('region') or '?'})"
        else:
            target_line = f"{len(targets)} tagged devices in {targets[0].get('region') or '?'}"
        return targets, target_line

    def run_set(self, region=None):
        """Send > Run a set: a named list of commands in order, stopping at the first refusal."""
        aimed = self.aim(region)
        if not aimed:
            return
        targets, target_line = aimed
        sets = self.client.sets()["sets"]
        if not sets:
            self.refuse("No command sets; Options > Command sets.")
            return
        names = sorted(sets)
        name = Field("Set", names[0], width=20)
        arg = Field("Value for {arg}", "", width=30, hotkey="v")
        d = Dialog("Run a Command Set", fields=[name, arg], width=66,
                   body=[f"To: {target_line}", "", "Sets: " + ", ".join(names)[:56],
                         "Each step is sent in turn; a refusal or silence stops the rest."],
                   buttons=[Button("Run", True, default=True), Button("Cancel", None)])
        while True:
            if not d.run(self.screen, self.draw):
                return
            if name.value.strip().lower() not in sets:
                d.message = "No set of that name."
                continue
            try:
                steps = self.client.filled_set(name.value.strip(), arg=arg.value)["steps"]
            except ApiError as exc:
                d.message = exc.message
                continue
            break
        setname = name.value.strip()
        try:
            cautions = [f"{s}: {c}" for s in steps for c in [self.client.check_command(s)["caution"]] if c]
            if cautions and not confirm(self.screen, "Confirm", [f"Set: {setname}", f"To: {target_line}"] + cautions[:3],
                                        "One or more steps take something away.", redraw_under=self.draw):
                return
            lines, stopped = [], None
            if region:
                job, _box = self.job(f"Running {setname}", f"Every node in {region}, {len(steps)} steps",
                                     lambda j: f"{len(j['progress']['heard'])} answered this step",
                                     self.client.start_run_set, setname, arg=arg.value, region=region, confirm=True)
                lines, stopped = job["result"]["lines"], job["result"]["runs"][0]["stopped_at"]
            else:
                for dev in targets:
                    job, box = self.job(f"Running {setname}", f"{dev.get('name') or dev['node']}, {len(steps)} steps",
                                        lambda j: "answered" if j["progress"]["heard"] else "waiting for the answer",
                                        self.client.start_run_set, setname, arg=arg.value, devices=[dev["node"]], confirm=True)
                    lines += job["result"]["lines"][1:]          # without the job's "== name" line
                    runs = job["result"]["runs"]
                    s = runs[0]["stopped_at"] if runs else None
                    stopped = s if stopped is None else stopped
                    if box.cancelled:
                        break
        except ApiError as exc:
            self.refuse(exc.message)
            return
        self.reload()
        self.show_results(f"Set: {setname[:28]}", lines)
        self.outcome(f"Set: {setname[:28]}",
                     "Every step answered ok." if stopped is None else f"Stopped at step {stopped + 1}.", lines)

    def statistics(self, range_="24h", interval="6h"):
        """s, or View > Statistics: what Beacon holds for the cursor device, in the right panel."""
        d = self.cursor_device
        if not d:
            self.refuse("No device under the cursor.")
            return
        name = d.get("name") or d["node"]
        try:
            job, _box = self.job("Statistics", f"get stats to {name}, then Beacon",
                                 lambda j: "answered" if j["progress"]["heard"] else "waiting for the live sample",
                                 self.client.start_statistics, d["node"], range=range_, interval=interval, live=True)
        except ApiError as exc:
            if exc.code != "beacon":
                self.refuse(exc.message)
                return
            self.show_results(f"Stats: {name[:28]}", [f"no statistics: {exc.message}"])
            self.refuse(f"No statistics: {exc.message}", title=f"Stats: {name[:28]}")
            return
        lines = job["result"]["lines"]
        self.reload()
        self.show_results(f"Stats: {name[:28]}", lines)
        self.outcome(f"Stats: {name[:28]}", lines[0], lines[1:])

    def refresh_fleet(self):
        """r, or View > Refresh: Batt and Seen from Beacon for the tagged devices, or all of
        them when none is tagged. Only when asked; nothing refreshes by itself."""
        devices = self.targets if self.list.tags else list(self.devices)
        if not devices:
            self.refuse("No devices to refresh.")
            return
        try:
            job, _box = self.job("Batt and Seen", f"Asking Beacon about {len(devices)} device(s)",
                                 lambda j: "one read each, a few seconds", self.client.start_refresh_fleet,
                                 devices=[d["node"] for d in devices], first="one read each, a few seconds")
        except ApiError as exc:
            self.refuse(exc.message)
            return
        updated, failed = job["result"]["updated"], job["result"]["failed"]
        self.reload()
        self.outcome("Batt and Seen", f"Refreshed {updated} of {len(devices)} from Beacon"
                     + (f"; {failed} without statistics." if failed else "."))

    def refresh_names(self):
        """n, or Devices > Names and versions: `get name` and `ver` to the tagged devices, or
        the cursor one; an unnamed device takes the fleet's name, every one stores its version."""
        devices = self.targets
        if not devices:
            self.refuse("No device under the cursor.")
            return
        if len(devices) > 3 and not confirm(self.screen, "Confirm", [f"{len(devices)} devices, two signed reads each."],
                                            "Each read spends one of the node's command slots this hour.", redraw_under=self.draw):
            return
        try:
            job, _box = self.job("Names and versions", f"get name and ver to {len(devices)} device(s)",
                                 lambda j: "answered" if j["progress"]["heard"] else "waiting for the answer",
                                 self.client.start_refresh_names, devices=[d["node"] for d in devices])
        except ApiError as exc:
            self.refuse(exc.message)
            return
        named, versioned, lines = job["result"]["named"], job["result"]["versioned"], job["result"]["lines"]
        self.reload()
        self.show_results("Names and versions", lines)
        self.outcome("Names and versions", f"{versioned} of {len(devices)} gave a version; {named} named.", lines)

    def command_list(self):
        """Enter on a device: every command the tool offers, by group; Enter on one runs it,
        asking for its parameter in the way the command needs."""
        if not self.cursor_device:
            self.refuse("No device under the cursor; add or discover one first.")
            return
        aimed = self.aim()
        if not aimed:
            return
        targets, target_line = aimed
        # What the targets' firmware admits: a command any of them would refuse is greyed
        # out and not selectable, so no command slot is spent to be told not-allowed.
        try:
            lists = [self.client.device_commands(d["node"]) for d in targets]
        except ApiError as exc:
            self.refuse(exc.message)
            return
        items = []
        greyed = 0
        for g, group in enumerate(lists[0]["groups"]):
            rows = []
            for i, c in enumerate(group["commands"]):
                if any(l["groups"][g]["commands"][i].get("hidden") for l in lists):
                    continue            # some target's image has no such feature (ruling 10)
                label = c["text"] + {"none": "", "choice": "  <" + "|".join(x or "-" for x in c["choices"]) + ">",
                                     "multi": "  <...>", "text": "  <...>"}[c["kind"]]
                if all(l["groups"][g]["commands"][i]["admitted"] for l in lists):
                    rows.append((label, c))
                else:
                    greyed += 1
                    needs = c["needs"] or "no build"
                    rows.append((f"{label}  (needs {needs})", c, needs))      # the mark survives monochrome
            items.append((group["group"], rows))
        box = ListBox(f"Commands  to {target_line[:40]}", items, width=66, height=min(22, self.screen.size[1] - 2))
        if greyed:
            reported = ", ".join("unknown" if l["revision"] is None else
                                 (f"lou-{l['revision']}" if l["revision"] >= 14 else f"ar-{l['revision']}")
                                 for l in lists[:3])
            box.note = f"{greyed} greyed: need a newer build; this device reports {reported} (n reads the version)"
        picked = box.run(self.screen, self.draw)
        if picked is None:
            return
        try:
            command = self.parameters(picked)
        except ApiError as exc:
            self.refuse(exc.message)
            return
        if command is None:
            return
        self.send(initial=command, immediate=True)

    def build(self, c, values):
        """The command text the table builds for an entry and its values."""
        return self.client.build_command(c["text"], values)["command"]

    def parameters(self, c):
        """The command text for a table entry, asking for its parameter: none, one choice,
        several, or free text with examples. None when the person backs out."""
        if c["kind"] == "none":
            return c["text"]
        if c["kind"] == "choice":
            value = ChoiceBox(c["text"], c["choices"]).run(self.screen, self.draw)
            return None if value is None else self.build(c, [value])
        if c["kind"] == "multi":
            values = ChoiceBox(c["text"], c["choices"], multi=True).run(self.screen, self.draw)
            return None if values is None else self.build(c, values)
        field = Field("Value", "", width=44)
        body = [f"{c['text']} <value>"] + (["Examples: " + "   ".join(c["examples"])] if c["examples"] else [])
        d = Dialog(c["text"], fields=[field], body=body, width=66,
                   buttons=[Button("Run", True, default=True), Button("Cancel", None)])
        while True:
            if not d.run(self.screen, self.draw):
                return None
            value = field.value.strip()
            if not value:
                d.message = "Type a value, or Esc."
                continue
            return self.build(c, [value])

    def columns_dialog(self):
        """Options > Directory columns: which optional columns the directory shows."""
        box = ChoiceBox("columns", list(self.all_columns), multi=True)
        box.chosen = set(self.columns)
        picked = box.run(self.screen, self.draw)
        if picked is None:
            return
        columns = self.client.set_config(columns=picked or list(self.all_columns))["columns"]
        self.reload()
        self.outcome("Directory columns", "Columns: " + ", ".join(columns) + ".")

    def sets_dialog(self):
        """Options > Command sets: one set at a time, by name; empty steps are dropped."""
        listing = self.client.sets()
        sets = listing["sets"]
        first = sorted(sets)[0] if sets else ""
        current = sets.get(first, [])
        name = Field("Name", first, width=20)
        steps = [Field(f"Step {i + 1}", current[i] if i < len(current) else "", width=44, hotkey=str(i + 1))
                 for i in range(listing["max_steps"])]
        d = Dialog("Command Sets", fields=[name] + steps, width=66,
                   body=["Sets: " + (", ".join(sorted(sets)) or "none"), "{arg} in a step is asked for when the set runs."],
                   buttons=[Button("Save", True, default=True), Button("Remove", "remove"), Button("Cancel", None)])
        choice = d.run(self.screen, self.draw)
        if choice is None:
            return
        try:
            if choice == "remove":
                self.client.remove_set(name.value.strip().lower())
                self.outcome("Command sets", f"Removed {name.value.strip()}.")
            else:
                saved = self.client.save_set(name.value, [f.value for f in steps])
                self.outcome("Command sets", f"Saved {saved['name']}.")
        except ApiError as exc:
            self.refuse(exc.message)

    def send(self, region=None, initial="", immediate=False):
        """F2: one dialog, then the signed publish, then the results in the right panel."""
        aimed = self.aim(region)
        if not aimed:
            return
        targets, target_line = aimed
        history = list(dict.fromkeys(l["command"] for l in reversed(self.client.broadcast_history(50)["history"])))
        for d in targets[:1]:
            history = list(dict.fromkeys(history + [l["command"] for l in reversed(self.client.history(d["node"], 50)["history"])]))
        field = Field("Command", initial, width=48, history=list(reversed(history)))
        dialog = Dialog("Send a Command", fields=[field], width=66,
                        body=[f"To: {target_line}", "",
                              "Examples: region   region put se-jkp *   region allowf se-jkp   get autoreply",
                              "Up/Down recall earlier commands. A tree line with '|' cannot travel."],
                        buttons=[Button("Send", True, default=True), Button("Cancel", None)])
        while True:
            if immediate:
                command = initial
                break
            if not dialog.run(self.screen, self.draw):
                return
            command = field.value.strip()
            if not command:
                dialog.message = "Type a command."
                continue
            break
        try:
            check = self.client.check_command(command)
        except ApiError as exc:
            self.refuse(exc.message)
            return
        if check["refusal"]:
            self.refuse(f"Not sent: the node would refuse it ({check['refusal']}).", title="Not sent")
            return
        why = check["caution"]
        if why and not confirm(self.screen, "Confirm", [f"Command: {command}", f"To: {target_line}"],
                               f"It {why}", redraw_under=self.draw):
            return
        try:
            if region:
                expected = [d["node"] for d in self.devices if (d.get("region") or "").upper() == region]
                job, _box = self.job("Sending", f"{command[:40]}  to every node in {region}",
                                     lambda j: f"{len(j['progress']['heard'])} of {len(expected)} known answered",
                                     self.client.start_send, command, region=region, confirm=True)
                answered, lines, asked = job["result"]["answered"], job["result"]["lines"], len(expected)
            else:
                answered, lines, asked = 0, [], 0
                for i, d in enumerate(targets):        # one signed publish per device, each to its own topic
                    label = d.get("name") or d["node"]
                    job, box = self.job("Sending", f"{command[:40]}  to {label} ({i + 1} of {len(targets)})",
                                        lambda j: "answered" if j["progress"]["heard"] else "waiting for the answer",
                                        self.client.start_send, command, devices=[d["node"]], confirm=True)
                    answered += job["result"]["answered"]
                    lines += job["result"]["lines"]
                    asked += 1
                    if box.cancelled:
                        break
        except ApiError as exc:
            self.refuse(exc.message)
            return
        self.reload()
        if self.inline:                     # the command line: the console says it, no window
            self.console += [f"  {answered} of {asked} answered."] + [f"  {line}" for line in lines]
            return
        self.show_results(f"Results: {command[:30]}", lines)
        self.outcome(f"Sent: {command[:40]}", f"{answered} of {asked} answered.", lines)

    def discover(self):
        """F5: ask a region which nodes accept the key; the found list lands in the right panel.
        There, Ins tags, F5 adds the tagged (asking for a name where none came back), F7 adds all."""
        if self.right_mode == "found":
            self.adopt_found(self.view.selected)
            return
        region = self.ask_region("Find the nodes that accept your key in which region?",
                                 hint="One IATA code, several with commas, or * for every region you have.")
        if not region:
            return
        try:
            asked = ", ".join(self.client.resolve_regions(region)["regions"]) or region
        except ApiError as exc:
            self.refuse(exc.message)
            return
        known = {d["node"] for d in self.devices}

        def describe(job):
            nodes = job["progress"]["heard"]
            new = sum(1 for n in nodes if n not in known)
            return f"Found {new} new, {len(nodes) - new} old"
        try:
            job, _box = self.job("Discovering", f"Asking every node in {asked} with one signed read per region",
                                 describe, self.client.start_discover, region)
        except ApiError as exc:
            self.refuse(exc.message)
            return
        found = job["result"]["found"]
        self.found = found
        lines = [self.found_line(e) for e in found]
        self.show_results(f"Found in {asked}: {len(found)}", lines or ["(no node answered)"])
        self.right_mode = "found" if found else "results"
        self.activate(self.right)
        if found:
            self.outcome(f"Found in {asked}", f"{len(found)} answered.",
                         lines + ["", "In the right panel: Ins tags, F5 adds the tagged, F7 adds all, Esc returns."])
        else:
            self.refuse("No node answered: none there holds your key, or its control plane is off.",
                        title=f"Found in {asked}")

    @staticmethod
    def found_line(e):
        """node  name-or-reply  = known: fits the right panel at 80 columns."""
        middle = e["name"] or e["outcome"]
        tail = f" = {e['known_as']}" if e["known_as"] else ""
        room = max(4, 27 - len(tail))
        return f"{e['node']} {middle[:room]:{room}}{tail}"

    def adopt_found(self, rows, ask=True):
        """Remember found rows. With `ask`, a node the fleet did not name gets a name dialog;
        F7 (add all) skips the dialogs and leaves those unnamed for later."""
        added = 0
        for i in rows:
            if i >= len(self.found) or self.found[i]["known_as"]:
                continue
            e = self.found[i]
            name = e["name"]
            if not name and ask:
                f = Field("Name", "", width=30)
                d = Dialog("Name the device", fields=[f], body=[f"{e['node']} in {e['region']} ({e['outcome'][:30]})",
                                                                "Leave empty to name it later."], width=56)
                if d.run(self.screen, self.draw) is None:
                    continue
                name = f.value
            try:
                entry = self.client.adopt(e["node"], e["region"], key=e.get("key"), name=name)
            except ApiError as exc:
                self.refuse(exc.message)
                break
            e["known_as"] = entry["name"] or entry["node"]
            added += 1
        self.reload()
        lines = [self.found_line(e) for e in self.found]
        self.view.set_rows(lines, keep=False)
        self.right_rows = [(l, [l]) for l in lines]
        self.outcome("Remembered", f"{added} device(s) remembered.")

    def tag_region(self, on):
        region = self.ask_region("Tag every device in which region?" if on else "Untag which region?")
        if not region:
            return
        self.show_tags(self.client.tag(region=region) if on else self.client.untag(region=region))

    def ask_region(self, question, hint=None):
        first = (self.cursor_device or {}).get("region") or (self.client.regions()["regions"] or [""])[0]
        f = Field("Region", first, width=20 if hint else 8)
        d = Dialog("Region", fields=[f], body=[question] + ([hint] if hint else []), width=70 if hint else 50)
        if d.run(self.screen, self.draw) is None:
            return None
        return f.value.strip().upper() or None

    def help(self):
        """F1: the topic for where the cursor is -- the found list, a dialog's subject, or the screen."""
        context = "found" if self.right_mode == "found" else self.context
        self.helpwin.run(self.screen, self.client.help_index(ui=UI)["context"].get(context, "screen"), self.draw)


def run(client):
    """The screens over the API at `client`; returns the exit code."""
    return _run(lambda screen, c: App(screen, c).run(), client)
