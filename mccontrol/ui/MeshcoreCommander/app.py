"""The second interface: a menu bar and key bar around two panels.

Left, the device directory; right, what the cursor is on. Every verb is a function key and
acts on the tagged devices, or the cursor device when nothing is tagged. All state that
matters is in the store; the screen is redrawn from it after every key.
"""

import curses

from ... import actions, beacon, commands, denylist, directory, envelope, firmware, helptext, store as _store, transport
from .dialogs import Button, ChoiceBox, Dialog, Field, ListBox, Progress, confirm, notice, waiting
from .help import HelpWindow
from .menu import Menu, MenuBar, MenuItem
from .widgets import KeyBar, ListView, Panel
from .screen import KEY_ENTER, run as _run

KEYS = ["Help", "Send", "Hist", "Edit", "Disco", "Regns", "All", "Del", "Menu", "Quit"]


class App:
    def __init__(self, screen, store):
        self.screen = screen
        self.store = store
        self.keybar = KeyBar(KEYS)
        self.sort = "name"
        self.devices = []
        w, h = screen.size
        split = w // 2
        total = h - 2                                # the menu bar above, the key bar below
        lower_h = max(5, total // 3)                 # "Last answers" under the history
        self.left = Panel(1, 0, split, total, "Devices")
        self.right = Panel(1, split, w - split, total - lower_h, "History")
        self.lower = Panel(1 + total - lower_h, split, w - split, lower_h, "Last answers")
        self.left.active = True
        self.list = ListView(self.left, header=1)
        self.view = ListView(self.right)
        self.reads_view = ListView(self.lower)
        self.reads_rows = []                 # (command, reply, at) behind the lower panel
        self.found = []                      # the last discovery's entries
        self.right_rows = []                 # (line, full_text) pairs behind the right panel
        self.right_mode = "history"          # "history" | "results" | "found" | "regions"
        self.right_node = None               # whose history the panel shows
        self.helpwin = HelpWindow(helptext.load)
        self.context = "main"                # which help topic F1 opens
        self.menu = MenuBar([
            Menu("Devices", [MenuItem("Discover", "discover", "F5"), MenuItem("Edit", "edit", "F4"),
                             MenuItem("Forget", "forget", "F8"), MenuItem("Sort by name", "sort:name"),
                             MenuItem("Sort by region", "sort:region"), MenuItem("Sort by last sent", "sort:last sent"),
                             MenuItem("Sort by version", "sort:version"), MenuItem("Names and versions", "names", "n"),
                             MenuItem("Tag all in region", "tag:+", "+"), MenuItem("Untag region", "tag:-", "-")]),
            self.send_menu(),
            Menu("View", [MenuItem("Statistics", "stats:24h:6h", "s"), MenuItem("Last 48 hours", "stats:48h:6h"),
                          MenuItem("Last 7 days", "stats:168h:24h"), MenuItem("Refresh Batt/Seen", "fleet", "r")]),
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
                  for n, cmd in enumerate(self.store.shortcuts(), 1) if cmd]
        return Menu("Send", items)

    def shortcut(self, n):
        """Alt+n: the slot's command to the tagged or cursor device, no dialog."""
        command = self.store.shortcuts()[n - 1]
        if not command:
            self.refuse(f"Alt+{n} is not set; Options > Shortcuts.")
            return
        self.send(initial=command, immediate=True)

    def shortcuts_dialog(self):
        slots = self.store.shortcuts()
        fields = [Field(f"Alt+{n}", slots[n - 1], width=40, hotkey=str(n)) for n in range(1, len(slots) + 1)]
        d = Dialog("Shortcuts", fields=fields, width=60,
                   body=["A command per key, sent to the tagged or cursor device without a dialog.",
                         "Leave a slot empty to free it."])
        if d.run(self.screen, self.draw) is None:
            return
        self.store.save_config(shortcuts=[f.value.strip() for f in fields])
        self.menu.menus[1] = self.send_menu()
        self.outcome("Shortcuts", "Shortcuts saved.")

    def reload(self):
        """Rows from the store, keeping the cursor on the same device and tags on the same nodes."""
        tagged = {self.devices[i]["node"] for i in self.list.tags if i < len(self.devices)}
        cursor_node = self.devices[self.list.cursor]["node"] if self.devices and self.list.cursor < len(self.devices) else None
        self.devices = directory.sorted_devices(self.store.devices(), self.sort)
        width = self.left.inner[2] - 1
        columns = self.store.config().get("columns")
        self.list.set_rows([directory.row(d, width, columns=columns) for d in self.devices], keep=False)
        self.list.tags = {i for i, d in enumerate(self.devices) if d["node"] in tagged}
        if cursor_node:
            for i, d in enumerate(self.devices):
                if d["node"] == cursor_node:
                    self.list.cursor = i
        self.left.footer = directory.tag_footer(len(self.list.tags), len(self.devices))
        self.show_reads(self.cursor_device)

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

    def show_reads(self, device):
        """The lower right panel: what this device last answered to each read, newest first."""
        self.reads_node = (device or {}).get("node")
        self.reads_rows = self.store.reads(device) if device else []
        width = self.lower.inner[2] - 1
        rows = []
        for command, reply, at in self.reads_rows:
            age = directory.relative(at)
            rows.append(f"{age:>5} {command[:12]:12} {reply}"[:width])
        self.reads_view.set_rows(rows or ["(nothing read yet)"], keep=False)

    def reread(self):
        """Enter on a line of the lower panel: send that read again to the cursor device."""
        if not self.reads_rows or self.reads_view.cursor >= len(self.reads_rows):
            return
        command = self.reads_rows[self.reads_view.cursor][0]
        self.send(initial=command, immediate=True)

    def activate(self, which):
        for panel in (self.left, self.right, self.lower):
            panel.active = panel is which

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
        s.put(y, x + 1, directory.header(w, self.store.config().get("columns")), s.colors.desktop | curses.A_UNDERLINE)
        self.list.draw(s)
        self.view.draw(s)
        self.reads_view.draw(s)
        self.menu.draw(s)
        self.keybar.draw(s)
        s.refresh()

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
            elif key == 9:                                   # Tab cycles the three panels
                order = (self.left, self.right, self.lower)
                current = next((i for i, p in enumerate(order) if p.active), 0)
                self.activate(order[(current + 1) % 3])
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
            elif key == ord("+") or key == ord("-"):
                self.do("tag:" + chr(key))
            elif key < 0 and chr(-key) in "123456789":          # Alt+digit
                self.shortcut(int(chr(-key)))
            elif self.left.active and self.list.handle(key):
                self.left.footer = directory.tag_footer(len(self.list.tags), len(self.devices))
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
        elif self.list.click(y, x):
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
        elif action == "help":
            self.help()
        elif action.startswith("help:"):
            self.helpwin.run(self.screen, action[5:], self.draw)
        elif action == "history":
            self.history_window()
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
        for line in reversed(self.store.history(d["node"], limit=200)):
            out = (line["reply"] or "OK") if line["code"] == 0 else (
                f"refused {line['code_name']}" if line["code"] is not None else (line["note"] or "no answer"))
            lines.append(f"{line['at'][:16]}  {line['command'][:28]:28} {out[:30]}")
        notice(self.screen, f"History: {d.get('name') or d['node']}", lines or ["Nothing sent yet."], self.draw)

    def keys_dialog(self):
        """The public key on one line, the provisioning commands, create or import."""
        if self.store.has_key():
            try:
                public = self.store.public_key()
            except _store.StoreError as exc:
                notice(self.screen, "Signing key", [str(exc)], self.draw)
                return
            reg = self.store.registration()
            body = ["Provision each repeater over its serial console with:", "",
                    f"  set mqtt.cmd.pubkey {public}", "  set mqtt.cmd on", "",
                    f"The private seed is in {self.store.key_path}", "and never leaves this machine inside anything but a signature.", "",
                    (f"Registered with {reg['broker']} on {reg['at'][:10]}." if reg else
                     "Not registered with the broker yet: it cannot check your commands before they fan out.")]
            d = Dialog("Signing key", body=body, width=min(92, self.screen.size[0] - 4),
                       buttons=[Button("OK", None, default=True), Button("Register", "register"), Button("Replace key", "replace")])
            choice = d.run(self.screen, self.draw)
            if choice == "register":
                progress, box = self.waiting("Registering", f"Publishing your public key to {self.store.config()['broker']}",
                                             lambda nodes: "waiting for the broker's acknowledgement")
                box.tick("waiting for the broker's acknowledgement", 0.0)
                try:
                    ok, detail = actions.register(self.store)
                except (envelope.EnvelopeError, transport.BrokerError, _store.StoreError) as exc:
                    ok, detail = False, str(exc)
                finally:
                    box.close()
                if ok:
                    self.outcome("Register", "Registered: " + detail)
                else:
                    self.refuse("Not registered: " + detail, title="Register")
                return
            if choice == "replace":
                if confirm(self.screen, "Replace key", ["Every repeater provisioned with the current key will stop accepting commands."],
                           "silences every node until each is re-provisioned over serial.", yes="Replace", redraw_under=self.draw):
                    self.store.create_key(overwrite=True)
                    self.outcome("Signing key", "Key replaced. Re-provision every repeater with the new public key.")
            return
        d = Dialog("Signing key", body=["No signing key yet."], width=50,
                   buttons=[Button("Create", "create", default=True), Button("Import", "import"), Button("Cancel", None)])
        choice = d.run(self.screen, self.draw)
        try:
            if choice == "create":
                self.store.create_key()
                self.outcome("Signing key", "Key created; open Keys again for the provisioning command.")
            elif choice == "import":
                f = Field("Seed", "", width=64, secret=True)
                d2 = Dialog("Import a seed", fields=[f], body=["Paste the 64-hex private seed."], width=76)
                if d2.run(self.screen, self.draw):
                    self.store.import_key(f.value)
                    self.outcome("Signing key", "Key imported.")
        except (_store.StoreError, envelope.EnvelopeError) as exc:
            self.refuse(str(exc))

    def setup_dialog(self):
        cfg = self.store.config()
        fields = [Field("Broker", cfg.get("broker", ""), width=40), Field("Port", str(cfg.get("port", 1883)), width=6),
                  Field("Username", cfg.get("username", ""), width=30), Field("Password", cfg.get("password", ""), width=30, secret=True),
                  Field("Wait seconds", str(cfg.get("wait_secs", 20)), width=4),
                  Field("Statistics URL", cfg.get("beacon_url", ""), width=44, hotkey="t")]
        d = Dialog("Control broker", fields=fields, width=70,
                   body=["The broker a node's mqtt.cmd.slot points at, and its credentials.",
                         "Statistics come from a Beacon server, read-only, no credentials."])
        if d.run(self.screen, self.draw) is None:
            return
        try:
            self.store.save_config(broker=fields[0].value.strip(), port=int(fields[1].value), username=fields[2].value.strip(),
                                   password=fields[3].value, wait_secs=int(fields[4].value),
                                   beacon_url=fields[5].value.strip())
            self.outcome("Control broker", "Saved.")
        except ValueError:
            self.refuse("Port and wait seconds must be numbers.")

    def test_dialog(self):
        cfg = self.store.config()
        box = Progress(self.screen, "Broker test", f"Trying {cfg.get('broker')}:{cfg.get('port')}", self.draw)
        box.tick("connecting, signing in, subscribing, publishing", 0.0)
        try:
            steps = transport.test_connection(cfg, seed_hex=self.store.seed() if self.store.has_key() else None)
        finally:
            box.close()
        lines = [f"{'ok  ' if ok else 'FAIL'}  {step:28} {detail}" for step, ok, detail in steps]
        lines.append(f"{'ok  ' if self.store.has_key() else 'FAIL'}  {'signing key':28} {'present' if self.store.has_key() else 'none: see Keys'}")
        reg = self.store.registration()
        lines.append(f"{'ok  ' if reg else 'note'}  {'registered with broker':28} "
                     + (f"{reg['broker']} on {reg['at'][:10]}" if reg else "not yet: Keys > Register"))
        passed = all(ok for _s, ok, _d in steps)
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
            self.store.update_device(d["node"], name=name.value.strip(), region=region.value.strip().upper(),
                                     notes=notes.value.strip())
        except _store.StoreError as exc:
            self.refuse(str(exc))
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
        self.store.remove_device(d["node"])
        self.reload()
        self.show_history(self.cursor_device)
        self.outcome("Forget", "Forgotten.")

    def outcome(self, title, headline, lines=()):
        """What an action came to, as a window over the work: the headline first, then the
        lines (the results, which the right panel also keeps). The message row is for state
        changes made in a dialog (saved, sorted); an outcome a person waited for is a window."""
        notice(self.screen, title, [headline] + ([""] + list(lines) if lines else []), self.draw)

    def refuse(self, why, title="Not done"):
        """Why nothing was done, as a window: a precondition that failed or an error raised."""
        notice(self.screen, title, [why], self.draw)

    def waiting(self, title, line, describe):
        """A Progress box over the work, as the transport's progress callback."""
        return waiting(self.screen, title, line, self.draw, describe)

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
        sets = self.store.sets()
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
            steps = sets.get(name.value.strip().lower())
            if not steps:
                d.message = "No set of that name."
                continue
            try:
                steps = actions.fill(steps, arg.value)
            except envelope.EnvelopeError as exc:
                d.message = str(exc)
                continue
            break
        cautions = [f"{s}: {actions.caution(s)}" for s in steps if actions.caution(s)]
        if cautions and not confirm(self.screen, "Confirm", [f"Set: {name.value.strip()}", f"To: {target_line}"] + cautions[:3],
                                    "One or more steps take something away.", redraw_under=self.draw):
            return
        lines, stopped = [], None
        setname = name.value.strip()
        try:
            if region:
                progress, box = self.waiting(f"Running {setname}", f"Every node in {region}, {len(steps)} steps",
                                             lambda nodes: f"{len(nodes)} answered this step")
                try:
                    lines, stopped = actions.run_set(self.store, steps, region=region, progress=progress)
                finally:
                    box.close()
            else:
                for dev in targets:
                    progress, box = self.waiting(f"Running {setname}", f"{dev.get('name') or dev['node']}, {len(steps)} steps",
                                                 lambda nodes: "answered" if nodes else "waiting for the answer")
                    try:
                        l, s = actions.run_set(self.store, steps, device=dev, progress=progress)
                    finally:
                        box.close()
                    lines += l
                    stopped = s if stopped is None else stopped
                    if box.cancelled:
                        break
        except (envelope.EnvelopeError, transport.BrokerError, _store.StoreError) as exc:
            self.refuse(str(exc))
            return
        self.reload()
        self.show_results(f"Set: {name.value.strip()[:28]}", lines)
        self.outcome(f"Set: {name.value.strip()[:28]}",
                     "Every step answered ok." if stopped is None else f"Stopped at step {stopped + 1}.", lines)

    def statistics(self, range_="24h", interval="6h"):
        """s, or View > Statistics: what Beacon holds for the cursor device, in the right panel."""
        d = self.cursor_device
        if not d:
            self.refuse("No device under the cursor.")
            return
        name = d.get("name") or d["node"]
        progress, box = self.waiting("Statistics", f"get stats to {name}, then Beacon",
                                     lambda nodes: "answered" if nodes else "waiting for the live sample")
        try:
            lines = actions.statistics(self.store, d, range_, interval, live=True, progress=progress)
        except beacon.BeaconError as exc:
            box.close()
            self.show_results(f"Stats: {name[:28]}", [f"no statistics: {exc}"])
            self.refuse(f"No statistics: {exc}", title=f"Stats: {name[:28]}")
            return
        box.close()
        if not lines[0].startswith("live"):                 # a live answer is kept by send() itself
            summary = ", ".join(l.split(None, 1)[1].strip() if " " in l else l for l in lines[1:4] if l)
            try:
                self.store.remember_read(d["node"], "stats (Beacon)", summary[:60])
            except _store.StoreError:
                pass
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
        box = Progress(self.screen, "Batt and Seen", f"Asking Beacon about {len(devices)} device(s)", self.draw)
        box.tick("one read each, a few seconds", 0.0)
        try:
            updated, failed = actions.refresh_fleet(self.store, devices)
        finally:
            box.close()
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
        progress, box = self.waiting("Names and versions", f"get name and ver to {len(devices)} device(s)",
                                     lambda nodes: "answered" if nodes else "waiting for the answer")
        try:
            named, versioned, lines = actions.refresh_names(self.store, devices, progress=progress)
        finally:
            box.close()
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
        versions = [d.get("ver") for d in targets]
        tags = sorted({firmware.entry_for(v)[0] for v in versions})
        items = []
        greyed = 0
        for group, cs in commands.grouped():
            rows = []
            for c in cs:
                probe = commands.build(c, [c.choices[-1]] if c.kind in ("choice", "multi") and c.choices
                                       else ([c.examples[0]] if c.kind == "text" and c.examples and not c.examples[0].startswith("<") else []))
                if c.text in ("get", "set"):
                    probe = "get autoreply" if c.text == "get" else "set txdelay 0.5"
                label = c.text + {"none": "", "choice": "  <" + "|".join(x or "-" for x in c.choices) + ">",
                                  "multi": "  <...>", "text": "  <...>"}[c.kind]
                if all(firmware.admits(v, probe) for v in versions):
                    rows.append((label, c))
                else:
                    greyed += 1
                    needs = firmware.needs(probe) or "no build"
                    rows.append((f"{label}  (needs {needs})", c, needs))      # the mark survives monochrome
            items.append((group, rows))
        box = ListBox(f"Commands  to {target_line[:40]}", items, width=66, height=min(22, self.screen.size[1] - 2))
        if greyed:
            reported = ", ".join(firmware.revision(v) and f"ar-{firmware.revision(v)}" or "unknown" for v in versions[:3])
            box.note = f"{greyed} greyed: need a newer build; this device reports {reported} (n reads the version)"
        picked = box.run(self.screen, self.draw)
        if picked is None:
            return
        command = self.parameters(picked)
        if command is None:
            return
        self.send(initial=command, immediate=True)

    def parameters(self, c):
        """The command text for a table entry, asking for its parameter: none, one choice,
        several, or free text with examples. None when the person backs out."""
        if c.kind == "none":
            return c.text
        if c.kind == "choice":
            value = ChoiceBox(c.text, c.choices).run(self.screen, self.draw)
            return None if value is None else commands.build(c, [value])
        if c.kind == "multi":
            values = ChoiceBox(c.text, c.choices, multi=True).run(self.screen, self.draw)
            return None if values is None else commands.build(c, values)
        field = Field("Value", "", width=44)
        body = [f"{c.text} <value>"] + (["Examples: " + "   ".join(c.examples)] if c.examples else [])
        d = Dialog(c.text, fields=[field], body=body, width=66,
                   buttons=[Button("Run", True, default=True), Button("Cancel", None)])
        while True:
            if not d.run(self.screen, self.draw):
                return None
            value = field.value.strip()
            if not value:
                d.message = "Type a value, or Esc."
                continue
            return commands.build(c, [value])

    def columns_dialog(self):
        """Options > Directory columns: which optional columns the directory shows."""
        box = ChoiceBox("columns", list(directory.COLUMNS), multi=True)
        box.chosen = set(directory.chosen(self.store.config().get("columns")))
        picked = box.run(self.screen, self.draw)
        if picked is None:
            return
        self.store.save_config(columns=picked or list(directory.COLUMNS))
        self.reload()
        self.outcome("Directory columns", "Columns: " + ", ".join(directory.chosen(picked)) + ".")

    def sets_dialog(self):
        """Options > Command sets: one set at a time, by name; empty steps are dropped."""
        sets = self.store.sets()
        first = sorted(sets)[0] if sets else ""
        current = sets.get(first, [])
        name = Field("Name", first, width=20)
        steps = [Field(f"Step {i + 1}", current[i] if i < len(current) else "", width=44, hotkey=str(i + 1))
                 for i in range(_store.SET_STEPS)]
        d = Dialog("Command Sets", fields=[name] + steps, width=66,
                   body=["Sets: " + (", ".join(sorted(sets)) or "none"), "{arg} in a step is asked for when the set runs."],
                   buttons=[Button("Save", True, default=True), Button("Remove", "remove"), Button("Cancel", None)])
        choice = d.run(self.screen, self.draw)
        if choice is None:
            return
        try:
            if choice == "remove":
                self.store.remove_set(name.value.strip().lower())
                self.outcome("Command sets", f"Removed {name.value.strip()}.")
            else:
                self.store.save_set(name.value, [f.value for f in steps])
                self.outcome("Command sets", f"Saved {name.value.strip().lower()}.")
        except _store.StoreError as exc:
            self.refuse(str(exc))

    def send(self, region=None, initial="", immediate=False):
        """F2: one dialog, then the signed publish, then the results in the right panel."""
        aimed = self.aim(region)
        if not aimed:
            return
        targets, target_line = aimed
        history = list(dict.fromkeys(l["command"] for l in reversed(self.store.history(None, 50))))
        for d in targets[:1]:
            history = list(dict.fromkeys(history + [l["command"] for l in reversed(self.store.history(d["node"], 50))]))
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
        refused = denylist.refusal(command)
        if refused:
            self.refuse(f"Not sent: the node would refuse it ({refused}).", title="Not sent")
            return
        why = actions.caution(command)
        if why and not confirm(self.screen, "Confirm", [f"Command: {command}", f"To: {target_line}"],
                               f"It {why}", redraw_under=self.draw):
            return
        try:
            if region:
                expected = [d["node"] for d in self.store.devices() if (d.get("region") or "").upper() == region]
                progress, box = self.waiting("Sending", f"{command[:40]}  to every node in {region}",
                                             lambda nodes: f"{len(nodes)} of {len(expected)} known answered")
                try:
                    _t, _p, answers, lines = actions.send(self.store, command, region=region, progress=progress)
                finally:
                    box.close()
                asked = len(expected)
            else:
                answers, lines, asked = {}, [], 0
                for i, d in enumerate(targets):        # one signed publish per device, each to its own topic
                    label = d.get("name") or d["node"]
                    progress, box = self.waiting("Sending", f"{command[:40]}  to {label} ({i + 1} of {len(targets)})",
                                                 lambda nodes: "answered" if nodes else "waiting for the answer")
                    try:
                        _t, _p, a, l = actions.send(self.store, command, device=d, progress=progress)
                    finally:
                        box.close()
                    answers.update(a)
                    lines += l
                    asked += 1
                    if box.cancelled:
                        break
        except (envelope.EnvelopeError, transport.BrokerError, _store.StoreError) as exc:
            self.refuse(str(exc))
            return
        self.reload()
        self.show_results(f"Results: {command[:30]}", lines)
        self.outcome(f"Sent: {command[:40]}", f"{len(answers)} of {asked} answered.", lines)

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
        asked = ", ".join(actions.regions_asked(region, self.store)) or region
        known = {d["node"] for d in self.devices}

        def describe(nodes):
            new = sum(1 for n in nodes if n not in known)
            return f"Found {new} new, {len(nodes) - new} old"
        progress, box = self.waiting("Discovering", f"Asking every node in {asked} with one signed read per region",
                                     describe)
        try:
            found = actions.discover(self.store, region, progress=progress)
        except (envelope.EnvelopeError, transport.BrokerError, _store.StoreError) as exc:
            box.close()
            self.refuse(str(exc))
            return
        finally:
            box.close()
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
            entry = actions.adopt(self.store, e, name)
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
        rows = set(directory.by_region(self.devices, region))
        self.list.tags = (self.list.tags | rows) if on else (self.list.tags - rows)
        self.left.footer = directory.tag_footer(len(self.list.tags), len(self.devices))

    def ask_region(self, question, hint=None):
        first = (self.cursor_device or {}).get("region") or (self.store.regions() or [""])[0]
        f = Field("Region", first, width=20 if hint else 8)
        d = Dialog("Region", fields=[f], body=[question] + ([hint] if hint else []), width=70 if hint else 50)
        if d.run(self.screen, self.draw) is None:
            return None
        return f.value.strip().upper() or None

    def help(self):
        """F1: the topic for where the cursor is -- the found list, a dialog's subject, or the screen."""
        context = "found" if self.right_mode == "found" else self.context
        self.helpwin.run(self.screen, helptext.CONTEXT.get(context, "screen"), self.draw)


def run(store):
    return _run(lambda screen, st: App(screen, st).run(), store)
