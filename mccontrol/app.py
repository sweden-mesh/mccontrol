"""The second interface: a menu bar and key bar around two panels.

Left, the device directory; right, what the cursor is on. Every verb is a function key and
acts on the tagged devices, or the cursor device when nothing is tagged. All state that
matters is in the store; the screen is redrawn from it after every key.
"""

import curses

from . import actions, directory, envelope, store as _store, transport
from .ui import (Button, Dialog, Field, KeyBar, ListView, Menu, MenuBar, MenuItem, Message,
                 Panel, confirm, notice)
from .ui.screen import KEY_ENTER, run as _run

KEYS = ["Help", "Send", "Hist", "Edit", "Disco", "Regns", "All", "Del", "Menu", "Quit"]


class App:
    def __init__(self, screen, store):
        self.screen = screen
        self.store = store
        self.message = Message()
        self.keybar = KeyBar(KEYS)
        self.sort = "name"
        self.devices = []
        w, h = screen.size
        split = w // 2
        self.left = Panel(1, 0, split, h - 3, "Devices")
        self.right = Panel(1, split, w - split, h - 3, "History")
        self.left.active = True
        self.list = ListView(self.left, header=1)
        self.view = ListView(self.right)
        self.right_rows = []                 # (line, full_text) pairs behind the right panel
        self.right_mode = "history"          # "history" | "results" | "found" | "regions"
        self.right_node = None               # whose history the panel shows
        self.menu = MenuBar([
            Menu("Devices", [MenuItem("Discover", "discover", "F5"), MenuItem("Edit", "edit", "F4"),
                             MenuItem("Forget", "forget", "F8"), MenuItem("Sort by name", "sort:name"),
                             MenuItem("Sort by region", "sort:region"), MenuItem("Sort by last sent", "sort:last sent"),
                             MenuItem("Tag all in region", "tag:+", "+"), MenuItem("Untag region", "tag:-", "-")]),
            Menu("Send", [MenuItem("To tagged or cursor", "send", "F2"), MenuItem("To all in a region", "all", "F7"),
                          MenuItem("Read regions", "regions", "F6")]),
            Menu("Keys", [MenuItem("Show public key", "keys")]),
            Menu("Setup", [MenuItem("Broker and credentials", "setup"), MenuItem("Test the broker", "test")]),
            Menu("Help", [MenuItem("Keys on this screen", "help", "F1"), MenuItem("Quit", "quit", "F10")]),
        ])

    # -- state -------------------------------------------------------------------------

    def reload(self):
        """Rows from the store, keeping the cursor on the same device and tags on the same nodes."""
        tagged = {self.devices[i]["node"] for i in self.list.tags if i < len(self.devices)}
        cursor_node = self.devices[self.list.cursor]["node"] if self.devices and self.list.cursor < len(self.devices) else None
        self.devices = directory.sorted_devices(self.store.devices(), self.sort)
        width = self.left.inner[2] - 1
        self.list.set_rows([directory.row(d, width) for d in self.devices], keep=False)
        self.list.tags = {i for i, d in enumerate(self.devices) if d["node"] in tagged}
        if cursor_node:
            for i, d in enumerate(self.devices):
                if d["node"] == cursor_node:
                    self.list.cursor = i
        self.left.footer = directory.tag_footer(len(self.list.tags), len(self.devices))

    def show_history(self, device):
        """The right panel follows the cursor: the device's history, newest first."""
        self.right_mode, self.right_node = "history", device["node"] if device else None
        self.right.title = f"History: {(device.get('name') or device['node']) if device else '-'}"
        rows = []
        for line in reversed(self.store.history(device["node"], limit=200)) if device else []:
            out = (line["reply"] or "OK") if line["code"] == 0 else (
                f"refused {line['code_name']}" if line["code"] is not None else (line["note"] or "no answer"))
            short = f"{line['at'][5:16]} {line['command'][:14]:14} {out}"
            full = [f"{line['at']}  {line['command']}", f"topic {line['topic']}", "", out]
            rows.append((short, full))
        self.right_rows = rows
        self.view.set_rows([r[0] for r in rows], keep=False)
        if not rows:
            self.view.set_rows(["(nothing sent yet)"], keep=False)
            self.right_rows = [("", ["Nothing has been sent to this device yet."])]

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
        y, x, w, _h = self.left.inner
        s.put(y, x + 1, directory.header(w), s.colors.desktop | curses.A_UNDERLINE)
        self.list.draw(s)
        self.view.draw(s)
        self.menu.draw(s)
        self.message.draw(s)
        self.keybar.draw(s)
        s.refresh()

    # -- main loop ---------------------------------------------------------------------

    def run(self):
        self.reload()
        self.show_history(self.cursor_device)
        while True:
            self.draw()
            key = self.screen.key()
            self.message.clear()
            if key == curses.KEY_F10:
                return 0
            if key == 12 or key == curses.KEY_RESIZE:        # Ctrl+L, or the terminal changed size
                self.screen.repaint()
                continue
            if self.menu.wants(key):
                action = self.menu.run(self.screen, key, self.draw)
                if action:
                    self.do(action)
                continue
            if key == curses.KEY_F1:
                self.do("help")
            elif key == curses.KEY_F2:
                self.do("send")
            elif key == curses.KEY_F3 or (key in KEY_ENTER and self.left.active):
                self.do("history")
            elif key in KEY_ENTER and self.right.active:
                self.open_line()
            elif key == curses.KEY_F4:
                self.do("edit")
            elif key == curses.KEY_F5:
                self.do("discover")
            elif key == curses.KEY_F6:
                self.do("regions")
            elif key == curses.KEY_F7:
                self.do("all")
            elif key == curses.KEY_F8:
                self.do("forget")
            elif key == 9:
                self.left.active, self.right.active = self.right.active, self.left.active
            elif key == ord("+") or key == ord("-"):
                self.do("tag:" + chr(key))
            elif self.left.active and self.list.handle(key):
                self.left.footer = directory.tag_footer(len(self.list.tags), len(self.devices))
                if self.right_mode != "history":
                    self.right_mode = "history"        # a moved cursor ends a result view
                self.show_history(self.cursor_device)
            elif self.right.active and self.view.handle(key):
                pass

    def do(self, action):
        if action == "quit":
            raise SystemExit(0)
        if action.startswith("sort:"):
            self.sort = action[5:]
            self.reload()
            self.message.say(f"Sorted by {self.sort}.")
        elif action.startswith("tag:"):
            self.tag_region(action[4:] == "+")
        elif action == "help":
            self.help()
        elif action == "history":
            self.history_window()
        else:
            self.message.say(f"{action}: not built yet.", error=True)

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
        for line in reversed(self.store.history(d["node"], limit=200)):
            out = (line["reply"] or "OK") if line["code"] == 0 else (
                f"refused {line['code_name']}" if line["code"] is not None else (line["note"] or "no answer"))
            lines.append(f"{line['at'][:16]}  {line['command'][:28]:28} {out[:30]}")
        notice(self.screen, f"History: {d.get('name') or d['node']}", lines or ["Nothing sent yet."], self.draw)

    def tag_region(self, on):
        region = self.ask_region("Tag every device in which region?" if on else "Untag which region?")
        if not region:
            return
        rows = set(directory.by_region(self.devices, region))
        self.list.tags = (self.list.tags | rows) if on else (self.list.tags - rows)
        self.left.footer = directory.tag_footer(len(self.list.tags), len(self.devices))

    def ask_region(self, question):
        first = (self.cursor_device or {}).get("region") or (self.store.regions() or [""])[0]
        f = Field("Region", first, width=8)
        d = Dialog("Region", fields=[f], body=[question], width=50)
        if d.run(self.screen, self.draw) is None:
            return None
        return f.value.strip().upper() or None

    def help(self):
        lines = ["F1  this help           F6  read the cursor device's regions",
                 "F2  send to tagged/cursor F7  send to all in a region",
                 "F3  history (Enter too)  F8  forget the cursor device",
                 "F4  edit the device      F9/F10  menu / quit",
                 "F5  discover a region    Ins/Space tag, * invert, +/- by region",
                 "Tab switches panels; Esc closes a dialog or menu."]
        notice(self.screen, "Keys", lines, self.draw)


def run(store):
    return _run(lambda screen, st: App(screen, st).run(), store)
