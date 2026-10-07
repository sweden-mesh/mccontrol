"""The second interface: a menu bar and key bar around two panels.

Left, the device directory; right, what the cursor is on. Every verb is a function key and
acts on the tagged devices, or the cursor device when nothing is tagged. All state that
matters is in the store; the screen is redrawn from it after every key.
"""

import curses

from . import actions, directory, envelope, helptext, store as _store, transport
from .ui import (Button, Dialog, Field, HelpWindow, KeyBar, ListView, Menu, MenuBar, MenuItem,
                 Message, Panel, confirm, notice)
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
                             MenuItem("Tag all in region", "tag:+", "+"), MenuItem("Untag region", "tag:-", "-")]),
            self.send_menu(),
            Menu("Keys", [MenuItem("Show public key", "keys")]),
            Menu("Options", [MenuItem("Broker", "setup"), MenuItem("Test the broker", "test"),
                             MenuItem("Shortcuts", "shortcuts"), MenuItem("Command sets", "sets")]),
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
            self.message.say(f"Alt+{n} is not set; Options > Shortcuts.", error=True)
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
        self.message.say("Shortcuts saved.")

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
                if self.right_mode == "found":
                    self.adopt_found(range(len(self.found)), ask=False)
                else:
                    self.do("all")
            elif key == curses.KEY_F8:
                self.do("forget")
            elif key == 9:
                self.left.active, self.right.active = self.right.active, self.left.active
            elif key == 27 and self.right_mode == "found":
                self.right_mode = "history"
                self.left.active, self.right.active = True, False
                self.show_history(self.cursor_device)
            elif key == curses.KEY_MOUSE:
                self.click()
            elif key == ord("+") or key == ord("-"):
                self.do("tag:" + chr(key))
            elif key < 0 and chr(-key) in "123456789":          # Alt+digit
                self.shortcut(int(chr(-key)))
            elif self.left.active and self.list.handle(key):
                self.left.footer = directory.tag_footer(len(self.list.tags), len(self.devices))
                if self.right_mode != "history":
                    self.right_mode = "history"        # a moved cursor ends a result view
                self.show_history(self.cursor_device)
            elif self.right.active and self.view.handle(key):
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
            self.left.active, self.right.active = True, False
            if self.right_mode != "history":
                self.right_mode = "history"
            self.show_history(self.cursor_device)
        elif self.view.click(y, x):
            self.right.active, self.left.active = True, False

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
        elif action.startswith("help:"):
            self.helpwin.run(self.screen, action[5:], self.draw)
        elif action == "history":
            self.history_window()
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
        elif action == "runset":
            self.run_set()
        elif action.startswith("alt:"):
            self.shortcut(int(action[4:]))
        elif action == "all":
            region = self.ask_region("Send to every node in which region?")
            if region:
                self.send(region=region)
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

    def keys_dialog(self):
        """The public key on one line, the provisioning commands, create or import."""
        if self.store.has_key():
            try:
                public = self.store.public_key()
            except _store.StoreError as exc:
                notice(self.screen, "Signing key", [str(exc)], self.draw)
                return
            body = ["Provision each repeater over its serial console with:", "",
                    f"  set mqtt.cmd.pubkey {public}", "  set mqtt.cmd on", "",
                    f"The private seed is in {self.store.key_path}", "and never leaves this machine inside anything but a signature."]
            d = Dialog("Signing key", body=body, width=min(92, self.screen.size[0] - 4),
                       buttons=[Button("OK", None, default=True), Button("Replace key", "replace")])
            if d.run(self.screen, self.draw) == "replace":
                if confirm(self.screen, "Replace key", ["Every repeater provisioned with the current key will stop accepting commands."],
                           "silences every node until each is re-provisioned over serial.", yes="Replace", redraw_under=self.draw):
                    self.store.create_key(overwrite=True)
                    self.message.say("Key replaced.")
            return
        d = Dialog("Signing key", body=["No signing key yet."], width=50,
                   buttons=[Button("Create", "create", default=True), Button("Import", "import"), Button("Cancel", None)])
        choice = d.run(self.screen, self.draw)
        try:
            if choice == "create":
                self.store.create_key()
                self.message.say("Key created; see Keys again for the provisioning command.")
            elif choice == "import":
                f = Field("Seed", "", width=64, secret=True)
                d2 = Dialog("Import a seed", fields=[f], body=["Paste the 64-hex private seed."], width=76)
                if d2.run(self.screen, self.draw):
                    self.store.import_key(f.value)
                    self.message.say("Key imported.")
        except (_store.StoreError, envelope.EnvelopeError) as exc:
            self.message.say(str(exc), error=True)

    def setup_dialog(self):
        cfg = self.store.config()
        fields = [Field("Broker", cfg.get("broker", ""), width=40), Field("Port", str(cfg.get("port", 1883)), width=6),
                  Field("Username", cfg.get("username", ""), width=30), Field("Password", cfg.get("password", ""), width=30, secret=True),
                  Field("Wait seconds", str(cfg.get("wait_secs", 20)), width=4)]
        d = Dialog("Control broker", fields=fields, body=["The broker a node's mqtt.cmd.slot points at, and its credentials."], width=66)
        if d.run(self.screen, self.draw) is None:
            return
        try:
            self.store.save_config(broker=fields[0].value.strip(), port=int(fields[1].value), username=fields[2].value.strip(),
                                   password=fields[3].value, wait_secs=int(fields[4].value))
            self.message.say("Saved.")
        except ValueError:
            self.message.say("Port and wait seconds must be numbers.", error=True)

    def test_dialog(self):
        cfg = self.store.config()
        self.message.say(f"Trying {cfg.get('broker')}:{cfg.get('port')} as {cfg.get('username') or '(no username)'}...")
        self.draw()
        steps = transport.test_connection(cfg)
        lines = [f"{'ok  ' if ok else 'FAIL'}  {step:28} {detail}" for step, ok, detail in steps]
        lines.append(f"{'ok  ' if self.store.has_key() else 'FAIL'}  {'signing key':28} {'present' if self.store.has_key() else 'none: see Keys'}")
        notice(self.screen, "Broker test", lines, self.draw)
        self.message.say("Broker test passed." if all(ok for _s, ok, _d in steps) else "Broker test failed.", error=not all(ok for _s, ok, _d in steps))

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
            self.message.say(str(exc), error=True)
            return
        self.reload()
        self.show_history(self.cursor_device)
        self.message.say("Saved.")

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
        self.message.say("Forgotten.")

    def aim(self, region=None):
        """Where a send goes: `(targets, target_line)`, or None with the reason said."""
        targets = [] if region else self.targets
        if not region and not targets:
            self.message.say("No device under the cursor; add or discover one first.", error=True)
            return None
        if not region and len({(d.get("region") or "").upper() for d in targets}) > 1:
            self.message.say("The tagged devices span more than one region; a send is to one region.", error=True)
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
            self.message.say("No command sets; Options > Command sets.", error=True)
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
        try:
            if region:
                lines, stopped = actions.run_set(self.store, steps, region=region)
            else:
                for dev in targets:
                    self.message.say(f"Running {name.value.strip()} on {dev.get('name') or dev['node']}...")
                    self.draw()
                    l, s = actions.run_set(self.store, steps, device=dev)
                    lines += l
                    stopped = s if stopped is None else stopped
        except (envelope.EnvelopeError, transport.BrokerError, _store.StoreError) as exc:
            self.message.say(str(exc), error=True)
            return
        self.reload()
        self.show_results(f"Set: {name.value.strip()[:28]}", lines)
        self.message.say("Every step answered ok." if stopped is None else f"Stopped at step {stopped + 1}; see the right panel.",
                         error=stopped is not None)

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
                self.message.say(f"Removed {name.value.strip()}.")
            else:
                self.store.save_set(name.value, [f.value for f in steps])
                self.message.say(f"Saved {name.value.strip().lower()}.")
        except _store.StoreError as exc:
            self.message.say(str(exc), error=True)

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
        why = actions.caution(command)
        if why and not confirm(self.screen, "Confirm", [f"Command: {command}", f"To: {target_line}"],
                               f"It {why}", redraw_under=self.draw):
            return
        self.message.say("Sending...")
        self.draw()
        try:
            if region:
                _t, _p, answers, lines = actions.send(self.store, command, region=region)
                asked = len([d for d in self.store.devices() if (d.get("region") or "").upper() == region])
            else:
                answers, lines, asked = {}, [], 0
                for d in targets:                      # one signed publish per device, each to its own topic
                    _t, _p, a, l = actions.send(self.store, command, device=d)
                    answers.update(a)
                    lines += l
                    asked += 1
        except (envelope.EnvelopeError, transport.BrokerError, _store.StoreError) as exc:
            self.message.say(str(exc), error=True)
            return
        self.reload()
        self.show_results(f"Results: {command[:30]}", lines)
        self.message.say(f"{len(answers)} of {asked} answered.", error=(len(answers) < asked))

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
        self.message.say(f"Asking every node in {asked} with one signed read per region...")
        self.draw()
        try:
            found = actions.discover(self.store, region)
        except (envelope.EnvelopeError, transport.BrokerError, _store.StoreError) as exc:
            self.message.say(str(exc), error=True)
            return
        self.found = found
        lines = [self.found_line(e) for e in found]
        self.show_results(f"Found in {asked}: {len(found)}", lines or ["(no node answered)"])
        self.right_mode = "found" if found else "results"
        self.right.active, self.left.active = True, False
        self.message.say(f"{len(found)} answered. Ins tags, F5 adds the tagged, F7 adds all, Esc returns."
                         if found else "No node answered: none there holds your key, or its control plane is off.",
                         error=not found)

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
        self.message.say(f"{added} device(s) remembered.")

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
