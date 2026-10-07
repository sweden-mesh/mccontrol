"""The green screen.

Modelled on the IBM i main menu: a title line, numbered selections, a command line at the
bottom under `===>`, and function keys on the last row. Lists put an *Opt* column before each
row; you type the option number on the row and press Enter, and several rows can carry an
option at once. The same conventions everywhere:

    Enter      run what is typed
    F3 / Esc   exit this screen (Esc twice from the main menu signs off)
    F5         refresh
    F12        cancel, same as F3 on a list
    arrows     move between rows; typing a digit on a row sets its option

Nothing here knows how to sign or send; that is `actions`, shared with the command line.
"""

import curses
import curses.ascii
import datetime
import textwrap

from . import actions, envelope, store as _store, transport

WIDTH = 80
HEIGHT = 24
EXIT_KEYS = (curses.KEY_F3, curses.KEY_F12, 27)


def exit_key(win, key):
    """Was that F3, F12 or Esc? When a terminal sends F3 as Esc O R and curses hands over
    only the Esc, the rest would land in the next field; it is drained here."""
    if key not in EXIT_KEYS:
        return False
    if key == 27:
        win.nodelay(True)
        try:
            while win.getch() != -1:
                pass
        finally:
            win.nodelay(False)
    return True


def cursor(visible):
    """Show or hide the cursor where the terminal allows it, and carry on where it does not."""
    try:
        curses.curs_set(1 if visible else 0)
    except curses.error:
        pass


class Screen:
    """Draws one 80x24 screen in the style and reads input from it."""

    def __init__(self, win, title):
        self.win = win
        self.title = title
        self.message = ""

    def frame(self):
        self.win.erase()
        h, w = self.win.getmaxyx()
        now = datetime.datetime.now().strftime("%d/%m/%y  %H:%M:%S")
        self.put(0, 1, "MCCONTROL", curses.A_BOLD)
        self.put(0, max(0, (w - len(self.title)) // 2), self.title, curses.A_BOLD)
        self.put(0, max(0, w - len(now) - 1), now)
        if self.message:
            self.put(h - 3, 1, self.message[: w - 2], curses.A_REVERSE)

    def put(self, y, x, text, attr=0):
        h, w = self.win.getmaxyx()
        if 0 <= y < h and x < w:
            try:
                self.win.addnstr(y, x, text, max(0, w - x - 1), attr)
            except curses.error:
                pass

    def keys(self, text):
        h, _w = self.win.getmaxyx()
        self.put(h - 1, 1, text)

    def read_line(self, y, x, width, initial="", secret=False):
        """A field: type, Backspace, Enter; F3/F12/Esc return None."""
        buf = list(initial)
        self.put(y, x, "_" * width)
        cursor(True)
        while True:
            self.put(y, x, ("*" * len(buf) if secret else "".join(buf)).ljust(width, "_")[-width:])
            self.win.move(y, min(x + len(buf), x + width - 1))
            self.win.refresh()
            key = self.win.getch()
            if exit_key(self.win, key):
                cursor(False)
                return None
            if key in (curses.KEY_ENTER, 10, 13):
                cursor(False)
                return "".join(buf)
            if key in (curses.KEY_BACKSPACE, 127, 8):
                if buf:
                    buf.pop()
            elif key == curses.KEY_F5:
                return "\x05"
            elif 32 <= key < 127 and len(buf) < width:
                buf.append(chr(key))

    def wait(self, text="Press Enter to continue."):
        h, _w = self.win.getmaxyx()
        self.put(h - 2, 1, text)
        self.keys("F3=Exit   F12=Cancel")
        self.win.refresh()
        while True:
            key = self.win.getch()
            if exit_key(self.win, key) or key in (curses.KEY_ENTER, 10, 13):
                return key


class App:
    def __init__(self, stdscr, store):
        self.stdscr = stdscr
        self.store = store
        cursor(False)
        try:
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
                stdscr.bkgd(" ", curses.color_pair(1))
        except curses.error:
            pass                                   # monochrome is still a green screen in spirit
        stdscr.keypad(True)
        self.win = stdscr

    # -- main menu ---------------------------------------------------------------------

    def main_menu(self):
        s = Screen(self.win, "MCCONTROL Main Menu")
        while True:
            s.frame()
            s.put(2, 1, "Select one of the following:")
            items = [("1", "Work with devices"), ("2", "Send a command to a device"),
                     ("3", "Send a command to all devices"), ("4", "Work with signing key"),
                     ("5", "Change configuration"), ("6", "Display command history"),
                     ("7", "Test the control broker connection"), ("90", "Sign off")]
            for i, (n, label) in enumerate(items):
                s.put(4 + i, 6, f"{n:>2}. {label}")
            cfg = self.store.config()
            s.put(13, 6, f"Regions  . . . . :  {', '.join(self.store.regions()) or '(no devices yet)'}")
            s.put(14, 6, f"Control broker . :  {cfg.get('broker')}:{cfg.get('port')} as {cfg.get('username') or '(no username)'}")
            s.put(15, 6, f"Signing key  . . :  {'present' if self.store.has_key() else 'NONE - select 4'}")
            s.put(16, 6, f"Devices  . . . . :  {len(self.store.devices())}")
            s.put(19, 1, "Selection or command")
            s.put(20, 1, "===>")
            s.keys("F3=Exit   F5=Refresh")
            choice = s.read_line(20, 6, 60)
            s.message = ""
            if choice is None or choice.strip() == "90":
                return
            choice = choice.strip()
            if choice == "1":
                self.work_with_devices()
            elif choice == "2":
                self.pick_and_send()
            elif choice == "3":
                region = self.ask_region(s, "Send to every node in which region?")
                if region:
                    self.send_screen(None, region=region)
            elif choice == "4":
                self.keys_screen()
            elif choice == "5":
                self.config_screen()
            elif choice == "6":
                self.history_screen(None)
            elif choice == "7":
                self.test_screen()
            elif choice.startswith("send ") or choice.startswith("region"):
                # The command line accepts what the shell does: "send <device> <command>".
                words = choice.split()
                if words[0] == "send" and len(words) > 2:
                    device = self.store.device(words[1])
                    if device:
                        self.send_screen(device, initial=" ".join(words[2:]))
                    else:
                        s.message = f"No device matches {words[1]}."
                else:
                    s.message = "Usage on the command line: send <device> <command>"
            elif choice:
                s.message = f"Selection {choice} is not valid."

    # -- devices -----------------------------------------------------------------------

    def work_with_devices(self):
        s = Screen(self.win, "Work with Devices")
        cursor, opts = 0, {}
        while True:
            devices = self.store.devices()
            s.frame()
            s.put(2, 1, "Type options, press Enter.")
            s.put(3, 3, "1=Add   2=Change   4=Remove   5=Display   7=History   8=Regions   9=Send")
            s.put(4, 3, "F6=Discover the nodes in a region that accept your key")
            s.put(5, 1, "Opt  Name                Node      Reg   Last sent            Result")
            if not devices:
                s.put(7, 6, "(no devices yet -- type 1 on the first line to add one)")
            rows = devices or [None]
            for i, d in enumerate(rows[:14]):
                attr = curses.A_REVERSE if i == cursor else 0
                opt = opts.get(i, " ")
                if d is None:
                    s.put(6 + i, 1, f" {opt}   ", attr)
                    continue
                s.put(6 + i, 1, f" {opt}   {d['name'][:19]:19} {d['node'][:8]}  {(d.get('region') or '?')[:4]:4}  "
                                f"{(d['last_sent'] or '-')[:19]:19}  {(d['last_result'] or '')[:14]}", attr)
            s.put(21, 1, "Use arrows to move, type an option on a row, Enter to run.")
            s.keys("F3=Exit   F5=Refresh   F6=Discover   F12=Cancel")
            self.win.refresh()
            key = self.win.getch()
            if exit_key(self.win, key):
                return
            if key == curses.KEY_F5:
                opts = {}
                continue
            if key == curses.KEY_F6:
                self.discover_screen()
                continue
            if key == curses.KEY_DOWN and cursor < len(rows) - 1:
                cursor += 1
            elif key == curses.KEY_UP and cursor > 0:
                cursor -= 1
            elif chr(key) in "1245789" if 0 <= key < 256 else False:
                opts[cursor] = chr(key)
            elif key in (curses.KEY_BACKSPACE, 127, 8, 32):
                opts.pop(cursor, None)
            elif key in (curses.KEY_ENTER, 10, 13):
                for i in sorted(opts):
                    d = rows[i] if i < len(rows) else None
                    self.device_option(opts[i], d)
                opts = {}
                s.message = ""

    def device_option(self, opt, device):
        if opt == "1":
            self.add_device_screen()
            return
        if device is None:
            return
        if opt == "2":
            self.change_device_screen(device)
        elif opt == "4":
            self.remove_device_screen(device)
        elif opt == "5":
            self.display_device_screen(device)
        elif opt == "7":
            self.history_screen(device)
        elif opt == "8":
            self.send_screen(device, initial="region", immediate=True)
        elif opt == "9":
            self.send_screen(device)

    def ask_region(self, s, question):
        """One field for an IATA code, offering the regions your devices already span."""
        s.frame()
        s.put(2, 1, question)
        s.put(4, 3, "Region (IATA)  :")
        known = ", ".join(self.store.regions())
        if known:
            s.put(6, 3, f"Your devices are in: {known}")
        s.keys("F3=Exit   F12=Cancel")
        region = s.read_line(4, 20, 8, initial=(self.store.regions() or [""])[0])
        return (region or "").strip().upper() or None

    def discover_screen(self):
        s = Screen(self.win, "Discover Devices")
        region = self.ask_region(s, "Find the nodes that accept your key in which region?")
        if not region:
            return
        s.frame()
        s.put(2, 1, f"Asking every node in {region} with one signed read, and listening for answers...")
        self.win.refresh()
        try:
            found = actions.discover(self.store, region)
        except (envelope.EnvelopeError, transport.BrokerError, _store.StoreError) as exc:
            s.message = str(exc)[:76]
            s.frame()
            s.wait()
            return
        cursor_row, opts, top = 0, {}, 0
        while True:
            s.frame()
            s.put(2, 1, f"{len(found)} node(s) in {region} answered a command signed with your key.")
            s.put(3, 3, "F6=Add this one   F7=Add all   or type 1 on rows and press Enter. "
                        "Silence says nothing.")
            s.put(5, 1, "Opt  Node      Region  Answer")
            page = found[top:top + 14]
            for i, e in enumerate(page):
                attr = curses.A_REVERSE if top + i == cursor_row else 0
                opt = opts.get(top + i, " ")
                known = f"= {e['known_as']}" if e["known_as"] else ""
                s.put(6 + i, 1, f" {opt}   {e['node'][:8]}  {e['region']:6}  {e['outcome'][:44]:44} {known}", attr)
            if not found:
                s.put(7, 6, "(no answer: no node there holds your key, or its control plane is off)")
            s.put(21, 70, "More..." if top + 14 < len(found) else "Bottom")
            s.keys("F3=Exit   F6=Add one   F7=Add all   F12=Cancel   PageUp/PageDown=Scroll")
            self.win.refresh()
            key = self.win.getch()
            if exit_key(self.win, key):
                return
            if key == curses.KEY_F6 and found:
                added = self.remember(found, [cursor_row])
                s.message = f"{found[cursor_row]['node']} remembered." if added else \
                            f"{found[cursor_row]['node']} is already {found[cursor_row]['known_as']}."
                continue
            if key == curses.KEY_F7 and found:
                added = self.remember(found, range(len(found)))
                s.message = f"{added} device(s) remembered; {len(found) - added} already known."
                continue
            if key == curses.KEY_DOWN and cursor_row < len(found) - 1:
                cursor_row += 1
                if cursor_row >= top + 14:
                    top += 14
            elif key == curses.KEY_UP and cursor_row > 0:
                cursor_row -= 1
                if cursor_row < top:
                    top = max(0, top - 14)
            elif key == curses.KEY_NPAGE and top + 14 < len(found):
                top += 14
                cursor_row = top
            elif key == curses.KEY_PPAGE and top > 0:
                top = max(0, top - 14)
                cursor_row = top
            elif 0 <= key < 256 and chr(key) == "1" and found:
                opts[cursor_row] = "1"
            elif key in (curses.KEY_BACKSPACE, 127, 8, 32):
                opts.pop(cursor_row, None)
            elif key in (curses.KEY_ENTER, 10, 13):
                added = self.remember(found, sorted(opts))
                opts = {}
                s.message = f"{added} device(s) remembered." if added else ""

    def remember(self, found, rows):
        """Add the discovered rows not already known; returns how many were added."""
        added = 0
        for i in rows:
            if not found[i]["known_as"]:
                entry = actions.adopt(self.store, found[i])
                found[i]["known_as"] = entry["name"]
                added += 1
        return added

    def add_device_screen(self):
        s = Screen(self.win, "Add Device")
        while True:
            s.frame()
            s.put(2, 1, "Type choices, press Enter.")
            s.put(4, 3, "Name . . . . . . . . . :")
            s.put(5, 3, "Node: first 8 hex of its public key (a whole key is fine)")
            s.put(7, 3, "Region (IATA)  . . . . :")
            s.put(8, 3, "Notes  . . . . . . . . :")
            s.keys("F3=Exit   F12=Cancel")
            name = s.read_line(4, 30, 30)
            if name is None:
                return
            node = s.read_line(6, 5, 70)
            if node is None:
                return
            region = s.read_line(7, 30, 8, initial=(self.store.regions() or [""])[0])
            if region is None:
                return
            notes = s.read_line(8, 30, 46)
            if notes is None:
                return
            try:
                self.store.add_device(name, node, region, notes)
                return
            except _store.StoreError as exc:
                s.message = str(exc)

    def change_device_screen(self, device):
        s = Screen(self.win, "Change Device")
        s.frame()
        s.put(2, 1, "Type changes, press Enter.")
        s.put(4, 3, f"Node . . . . . . . . . :  {device['node']}")
        s.put(5, 3, "Name . . . . . . . . . :")
        s.put(6, 3, "Region (IATA)  . . . . :")
        s.put(7, 3, "Notes  . . . . . . . . :")
        s.keys("F3=Exit   F12=Cancel")
        name = s.read_line(5, 30, 30, initial=device["name"])
        if name is None:
            return
        region = s.read_line(6, 30, 8, initial=device.get("region", ""))
        if region is None:
            return
        notes = s.read_line(7, 30, 46, initial=device["notes"])
        if notes is None:
            return
        self.store.update_device(device["node"], name=name.strip() or device["name"],
                                 region=region.strip().upper() or device.get("region", ""),
                                 notes=notes.strip())

    def remove_device_screen(self, device):
        s = Screen(self.win, "Confirm Remove of Device")
        s.frame()
        s.put(2, 1, "Press Enter to confirm your choice for 4=Remove.")
        s.put(3, 1, "Press F12 to return to change your choice.")
        s.put(5, 3, f"Name . . . :  {device['name']}")
        s.put(6, 3, f"Node . . . :  {device['node']}")
        s.put(8, 3, "The device is forgotten here. Nothing is sent to it; its history file stays.")
        if s.wait("Enter=Confirm   F12=Cancel") in (curses.KEY_ENTER, 10, 13):
            self.store.remove_device(device["node"])

    def display_device_screen(self, device):
        s = Screen(self.win, "Display Device")
        s.frame()
        s.put(3, 3, f"Name . . . . . :  {device['name']}")
        s.put(4, 3, f"Node . . . . . :  {device['node']}")
        s.put(5, 3, f"Notes  . . . . :  {device['notes']}")
        s.put(6, 3, f"Added  . . . . :  {device['added']}   Region: {device.get('region') or '?'}")
        s.put(7, 3, f"Last sent  . . :  {device['last_sent'] or '-'}")
        s.put(8, 3, f"Last result  . :  {device['last_result'] or '-'}")
        s.put(10, 3, f"Command topic  :  {envelope.command_topic(device.get('region') or '????', device['node'])}")
        s.put(11, 3, f"Result topic . :  {envelope.result_topic(device.get('region') or '????', device['node'])}")
        s.wait()

    # -- sending -----------------------------------------------------------------------

    def pick_and_send(self):
        s = Screen(self.win, "Send a Command")
        s.frame()
        s.put(2, 1, "Type the device name, press Enter.")
        s.put(4, 3, "Device . . . . :")
        s.put(6, 3, "Your devices:  " + ", ".join(d["name"] for d in self.store.devices())[:60])
        s.keys("F3=Exit   F12=Cancel")
        ref = s.read_line(4, 20, 30)
        if ref is None:
            return
        device = self.store.device(ref)
        if device is None:
            s.message = f"No device matches {ref}."
            s.frame()
            s.wait()
            return
        self.send_screen(device)

    def send_screen(self, device, region=None, initial="", immediate=False):
        broadcast = device is None
        s = Screen(self.win, f"Send a Command to All Devices in {region}" if broadcast else "Send a Command")
        command = initial
        while True:
            if not immediate:
                s.frame()
                target = f"EVERY node in {region}" if broadcast else f"{device['name']}  ({device['node'][:8]}, {device.get('region') or '?'})"
                s.put(2, 1, "Type the command, press Enter. Nothing is sent until you confirm.")
                s.put(4, 3, f"To . . . . . . :  {target}")
                s.put(5, 3, "Command  . . . :")
                s.put(8, 3, "Examples:  region            region put se-jkp *      region allowf se-jkp")
                s.put(9, 3, "           region save       get autoreply            set autoreply.hops 8")
                s.put(11, 3, "A region tree line with '|' cannot travel; use 'region put' one name at a time.")
                s.keys("F3=Exit   F12=Cancel")
                command = s.read_line(5, 20, 58, initial=command)
                if command is None:
                    return
                command = command.strip()
                if not command:
                    continue
            immediate = False
            why = actions.caution(command)
            if why and not self.confirm(command, why, region if broadcast else None, device):
                continue
            try:
                s.message = "Sending..."
                s.frame()
                s.put(4, 3, f"Command  . . . :  {command}")
                s.put(6, 3, "Waiting for the node to answer...")
                self.win.refresh()
                _topic, _payload, _answers, lines = actions.send(self.store, command, device=device,
                                                                 region=region)
                self.show_lines("Command Result", [f"Command:  {command}", ""] + lines)
                return
            except (envelope.EnvelopeError, transport.BrokerError, _store.StoreError) as exc:
                s.message = str(exc)[:76]

    def confirm(self, command, why, region, device):
        broadcast = region is not None
        s = Screen(self.win, "Confirm Command")
        s.frame()
        s.put(2, 1, "This command takes something away. Press Enter to send it, F12 to cancel.")
        s.put(4, 3, f"Command  . . . :  {command}")
        s.put(5, 3, f"To . . . . . . :  {f'EVERY node in {region}' if broadcast else device['name']}")
        for i, line in enumerate(textwrap.wrap(f"It {why}", 72)):
            s.put(7 + i, 3, line)
        if broadcast:
            s.put(11, 3, "Sent to every node, this cannot be undone in one step.", curses.A_BOLD)
        return s.wait("Enter=Send   F12=Cancel") in (curses.KEY_ENTER, 10, 13)

    # -- history, keys, config ---------------------------------------------------------

    def history_screen(self, device):
        s = Screen(self.win, f"Command History: {device['name'] if device else 'all devices'}")
        lines = self.store.history(device["node"] if device else None, limit=200)
        rows = []
        for line in reversed(lines):
            out = (line["reply"] or "OK") if line["code"] == 0 else (
                f"refused {line['code_name']}" if line["code"] is not None else (line["note"] or "no answer"))
            rows.append(f"{line['at'][:16]}  {line['command'][:30]:30}  {out[:28]}")
        self.show_lines(s.title, rows or ["(nothing sent yet)"])

    def test_screen(self):
        s = Screen(self.win, "Test Control Broker Connection")
        cfg = self.store.config()
        s.frame()
        s.put(2, 1, f"Trying {cfg.get('broker')}:{cfg.get('port')} as {cfg.get('username') or '(no username)'}...")
        self.win.refresh()
        steps = transport.test_connection(cfg)
        lines = [f"{'ok  ' if ok else 'FAIL'}  {step:30} {detail}" for step, ok, detail in steps]
        lines.append(f"{'ok  ' if self.store.has_key() else 'FAIL'}  {'signing key':30} "
                     f"{'present' if self.store.has_key() else 'none: select 4 on the main menu'}")
        lines.append("")
        lines.append("A node that stays silent after this passes has a different key, or a different")
        lines.append("broker on its command slot, or the control plane switched off.")
        self.show_lines(s.title, lines)

    def keys_screen(self):
        s = Screen(self.win, "Work with Signing Key")
        while True:
            s.frame()
            if self.store.has_key():
                try:
                    public = self.store.public_key()
                except _store.StoreError as exc:
                    public = f"unreadable: {exc}"
                s.put(2, 1, "Your key is present. Provision each repeater over its serial console with:")
                _h, w = self.win.getmaxyx()
                if w >= 86:                              # the whole command fits: one line, copyable
                    s.put(4, 1, "set mqtt.cmd.pubkey " + public)
                else:                                    # 80 columns: the key alone, unbroken
                    s.put(4, 1, "set mqtt.cmd.pubkey   (followed by the key on the next line)")
                    s.put(5, 1, public)
                s.put(6, 1, "set mqtt.cmd on")
                s.put(7, 1, "'mccontrol key' prints the public key alone for copying.")
                s.put(8, 3, f"The private seed is in {self.store.key_path}")
                s.put(9, 3, "It is never shown here and never leaves this machine inside anything but a signature.")
                s.put(11, 3, "1. Replace the key (every repeater provisioned with the old one stops listening)")
            else:
                s.put(2, 1, "No signing key yet.")
                s.put(4, 3, "1. Create a key")
                s.put(5, 3, "2. Import an existing 64-hex seed")
            s.put(19, 1, "Selection")
            s.put(20, 1, "===>")
            s.keys("F3=Exit   F12=Cancel")
            choice = s.read_line(20, 6, 20)
            if choice is None:
                return
            try:
                if choice.strip() == "1":
                    if self.store.has_key():
                        c = Screen(self.win, "Confirm Replace Key")
                        c.frame()
                        c.put(2, 1, "Every repeater provisioned with the current public key will stop accepting commands.")
                        c.put(3, 1, "Press Enter to replace it anyway, F12 to keep it.")
                        if c.wait("Enter=Replace   F12=Cancel") not in (curses.KEY_ENTER, 10, 13):
                            continue
                    self.store.create_key(overwrite=True)
                    s.message = "Key created."
                elif choice.strip() == "2" and not self.store.has_key():
                    seed = s.read_line(7, 3, 64, secret=True)
                    if seed:
                        self.store.import_key(seed)
                        s.message = "Key imported."
            except (_store.StoreError, envelope.EnvelopeError) as exc:
                s.message = str(exc)[:76]

    def config_screen(self):
        s = Screen(self.win, "Change Configuration")
        cfg = self.store.config()
        s.frame()
        s.put(2, 1, "Type changes, press Enter on each. Leave a field as it is to keep it.")
        fields = [("Control broker  . . :", "broker", 40),
                  ("Port  . . . . . . . :", "port", 6), ("Username  . . . . . :", "username", 20),
                  ("Password  . . . . . :", "password", 20), ("Wait for answer, s  :", "wait_secs", 4)]
        values = {}
        s.keys("F3=Exit   F12=Cancel")
        for i, (label, key, width) in enumerate(fields):
            s.put(4 + i, 3, label)
            value = s.read_line(4 + i, 26, width, initial=str(cfg.get(key, "")),
                                secret=(key == "password"))
            if value is None:
                return
            values[key] = value.strip()
        try:
            self.store.save_config(broker=values["broker"],
                                   port=int(values["port"]), username=values["username"],
                                   password=values["password"], wait_secs=int(values["wait_secs"]))
        except ValueError:
            s.message = "Port and wait must be numbers."
            s.frame()
            s.wait()

    # -- helpers -----------------------------------------------------------------------

    def show_lines(self, title, lines):
        s = Screen(self.win, title)
        top = 0
        while True:
            s.frame()
            page = lines[top:top + 18]
            for i, line in enumerate(page):
                s.put(2 + i, 1, line)
            more = "More..." if top + 18 < len(lines) else "Bottom"
            s.put(21, 70, more)
            s.keys("F3=Exit   F12=Cancel   PageUp/PageDown=Scroll")
            self.win.refresh()
            key = self.win.getch()
            if exit_key(self.win, key) or key in (curses.KEY_ENTER, 10, 13):
                return
            if key == curses.KEY_NPAGE and top + 18 < len(lines):
                top += 18
            elif key == curses.KEY_PPAGE and top > 0:
                top = max(0, top - 18)


def run(store):
    import os
    import sys

    def _main(stdscr):
        h, w = stdscr.getmaxyx()
        if h < HEIGHT or w < WIDTH:
            stdscr.addstr(0, 0, f"This screen needs {WIDTH}x{HEIGHT}; the terminal is {w}x{h}. "
                                f"Make it larger and start again, or use the command line.")
            stdscr.getch()
            return 2
        App(stdscr, store).main_menu()
        return 0

    if not sys.stdout.isatty():
        print("mccontrol: the screens need a terminal; use the subcommands instead "
              "(mccontrol --help)", file=sys.stderr)
        return 2
    os.environ.setdefault("ESCDELAY", "150")      # long enough to read F3 as one key, short enough for Esc
    try:
        return curses.wrapper(_main)
    except curses.error as exc:
        print(f"mccontrol: this terminal ({os.environ.get('TERM', '?')}) cannot run the screens: "
              f"{exc}. Use the subcommands instead (mccontrol --help).", file=sys.stderr)
        return 2
