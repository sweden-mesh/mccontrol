"""The screens: the main menu, Work with Devices and everything behind its options, each a
request to the API through the client. Nothing here signs, sends or reads a file."""

import curses

from mccontrol.apiclient import ApiError

from . import fmt
from .panels import OptList, confirm, form, show
from .terminal import ENTER, EXIT, fkey

MENU = [("1", "Work with devices"), ("2", "Send a command"), ("3", "Send a command to every node in a region"),
        ("4", "Run a command set"), ("5", "Discover devices"), ("6", "Work with command sets"),
        ("7", "Work with shortcuts"), ("8", "Work with signing key"), ("9", "Change configuration"),
        ("10", "Test the broker connection"), ("11", "Change directory columns"),
        ("12", "Display broadcast history"), ("13", "Help contents"), ("90", "Sign off")]
DEVICE_LEGEND = ["1=Tag/untag  2=Change  4=Forget  5=Display  6=Statistics  7=Commands",
                 "8=Last answers  9=Send  10=Regions  11=Name and version"]
DEVICE_KEYS = fmt.fkeys(["F1=Help", "F3=Exit", "F5=Refresh", "F6=Discover", "F7=Batt/Seen", "F9=Send",
                         "F10=Names", "F11=Sort", "F12=Cancel", "F13-F21=Shortcuts"])
LIST_KEYS = ["F1=Help   F3=Exit   F5=Refresh   F12=Cancel"]
VIEW_KEYS = ["F1=Help   F3=Exit   F12=Cancel"]


class App:
    def __init__(self, term, client):
        self.t, self.c = term, client
        self.sort = "name"
        self.contexts = None
        term.on_help = self.help

    # -- plumbing ------------------------------------------------------------------------

    def call(self, fn, *a, **k):
        """A request; an ApiError becomes the message line and None."""
        try:
            return fn(*a, **k)
        except ApiError as exc:
            self.t.message = exc.message
            return None

    def job(self, start, *a, about="", **k):
        """Start a job and wait for it, its progress on the message line; F12 or Esc
        cancels (DELETE /jobs/{id}) and what landed is kept. A command that takes something
        away comes back 409 confirm_required: the Confirm screen, then once more with
        confirm. Returns the finished job, or None with the reason on the message line."""
        try:
            job = start(*a, **k)
        except ApiError as exc:
            if exc.code == "confirm_required" and "confirm" in k and not k["confirm"]:
                lines = [f"'{c['command']}' {c['caution']}" for c in exc.cautions] or [
                    exc.message.replace(" Send again with confirm: true.", "")]
                lines += [f"To:  {about}"] if about else []
                if confirm(self.t, "Confirm Command", lines, "This takes something away. Press Enter to send it, F12 to cancel."):
                    return self.job(start, *a, about=about, **dict(k, confirm=True))
                self.t.message = "Nothing was sent."
                return None
            self.t.message = ("Not sent: " if exc.code == "refused" else "") + exc.message
            return None

        def tick(j):
            p = j["progress"]
            clock = f"   {p['elapsed']:.0f}/{p['wait']:.0f} s" if p.get("wait") else ""
            self.t.say(f"{p['text']}{clock}" + ("   F12=Cancel" if j.get("cancellable", True) else ""))
            return self.t.poll() in EXIT
        self.t.say("Working...   F12=Cancel")
        try:
            job = self.c.wait_job(job["id"], on_progress=tick)
        except ApiError as exc:
            self.t.message = exc.message
            return None
        finally:
            self.t.discard()
        if job["status"] == "failed":
            self.t.message = (job.get("error") or {}).get("message", "failed")
            return None
        self.t.message = "Cancelled: what landed is kept." if job["status"] == "cancelled" else ""
        return job

    def devices(self):
        data = self.call(self.c.devices, sort=self.sort, width=self.t.size[0] - 9)
        return data or {"devices": [], "header": "", "footer": "", "sorts": [self.sort]}

    def tagged(self):
        return [d for d in self.devices()["devices"] if d["tagged"]]

    def resolve(self, text):
        """Devices named in a field: names or node ids, commas between; blank or *TAGGED is
        the tagged ones. None, with the reason said, when one does not match."""
        refs = [r.strip() for r in text.split(",") if r.strip()]
        if not refs or [r.upper() for r in refs] == ["*TAGGED"]:
            found = self.tagged()
            if not found:
                self.t.message = "Nothing is tagged: type 1 on a row in Work with devices, or name a device."
            return found or None
        out = []
        for ref in refs:
            d = self.call(self.c.device, ref)
            if d is None:
                return None
            out.append(d)
        return out

    # -- the main menu -------------------------------------------------------------------

    def main(self):
        command = ""
        while True:
            t = self.t
            w, h = t.size
            t.frame("MCCONTROL Main Menu", ["F1=Help   F3=Exit   F5=Refresh   F12=Cancel"])
            t.put(2, 1, "Select one of the following:")
            for i, (n, label) in enumerate(MENU):
                t.put(4 + i, 6, f"{n:>2}. {label}")
            cfg, signing, data = self.call(self.c.config) or {}, self.call(self.c.key) or {}, self.devices()
            reg = signing.get("registered")
            info = [("Broker", f"{cfg.get('broker')}:{cfg.get('port')}"),
                    ("Signing key", "present" if signing.get("exists") else "NONE - select 8"),
                    ("Registered", reg["at"][:10] if reg else "no - select 8"),
                    ("Devices", data.get("footer", ""))]
            for i, (label, value) in enumerate(info):
                t.put(5 + i, 52, f"{label:12} {value}"[: w - 54])
            t.put(h - 6, 1, "Selection or command")
            t.put(h - 5, 1, "===>", "bright")
            key, command = t.edit(h - 5, 6, w - 8, command)
            if key == fkey(1):
                self.help("main")
                continue
            if key in EXIT:
                return 0
            if key not in ENTER:
                continue
            choice, command = command.strip(), ""
            t.message = ""
            if choice == "90" or choice.lower() in ("signoff", "quit"):
                return 0
            if choice:
                self.select(choice)

    def select(self, choice):
        actions = {"1": self.work_with_devices, "2": lambda: self.send_form(None), "3": self.region_form,
                   "4": lambda: self.run_set_form(None), "5": lambda: self.discover(None), "6": self.work_with_sets,
                   "7": self.shortcuts_form, "8": self.keys_screen, "9": self.config_form, "10": self.test,
                   "11": self.columns, "12": self.broadcast_history, "13": lambda: self.help(None)}
        if choice in actions:
            actions[choice]()
        else:
            self.verb(choice, None)

    def verb(self, line, targets):
        """A typed command. `targets` is the devices a list acts on (tagged, else the
        cursor), or None on the main menu, where `send` names its device first."""
        verb, rest = fmt.split_verb(line)
        here = targets or []
        if verb == "help":
            self.help(None, rest.strip() or None)
        elif verb in ("send", "sndcmd"):
            if targets is None:
                ref, _s, command = rest.partition(" ")
                devices = self.resolve(ref)
                if devices and command.strip():
                    self.send(devices, None, command.strip())
                elif devices:
                    self.send_form(devices)
            elif rest.strip():
                self.send(here, None, rest.strip())
            else:
                self.send_form(here)
        elif verb == "all":
            region, _s, command = rest.partition(" ")
            if region and command.strip():
                self.send(None, region.upper(), command.strip())
            else:
                self.region_form(region.upper())
        elif verb in ("discover", "disco"):
            self.discover(rest.strip() or None)
        elif verb == "run":
            name, _s, arg = rest.partition(" ")
            self.run_set_form(here or None, name, arg.strip())
        elif verb == "alt" and rest.strip().isdigit():
            self.shortcut(int(rest), here or self.tagged())
        elif verb == "names":
            self.names(here or self.tagged())
        elif verb == "fleet":
            self.fleet(here if targets and any(d["tagged"] for d in here) else None)
        elif verb == "stats" and here:
            self.statistics(here[0], rest.strip() or "24h")
        elif verb == "tag" and rest.strip():
            self.call(self.c.tag, region=rest.strip())
        elif verb == "untag":
            self.call(self.c.untag, region=rest.strip() or None)
        elif verb in ("invert", "*"):
            self.call(self.c.invert_tags)
        elif verb == "sort" and rest.strip():
            sorts = self.devices().get("sorts", [])
            if rest.strip().lower() in sorts:
                self.sort = rest.strip().lower()
                self.t.message = f"Sorted by {self.sort}."
            else:
                self.t.message = "Sort by " + ", ".join(sorts) + "."
        elif verb in ("devices", "wrkdev"):
            self.work_with_devices()
        else:
            self.t.message = f"Command {line.strip()[:40]} is not valid here."

    # -- Work with Devices ---------------------------------------------------------------

    def work_with_devices(self):
        lst = OptList(self.t, "Work with Devices", DEVICE_KEYS, legend=DEVICE_LEGEND, ident=lambda d: d["node"],
                      resize_returns=True)
        while True:
            data = self.devices()
            lst.header = "  " + data["header"]
            lst.note = f"{data['footer']}, by {self.sort}"
            lst.set_rows([(("> " if d["tagged"] else "  ") + d["row"], "bright" if d["tagged"] else "normal", d)
                          for d in data["devices"]])
            key, picks, command = lst.run()
            cursor = lst.payload()
            targets = [d for d in data["devices"] if d["tagged"]] or ([cursor] if cursor else [])
            if key in ENTER:
                self.t.message = ""
                if command:
                    self.verb(command, targets)
                else:
                    self.device_options(picks, data["devices"])
            elif key in EXIT:
                self.t.message = ""
                return
            elif key == fkey(5):
                self.t.message = ""
            elif key == curses.KEY_RESIZE:
                pass                            # the rows again, laid out to the new width
            elif key == fkey(6):
                self.discover(None)
            elif key == fkey(7):
                self.fleet([d for d in data["devices"] if d["tagged"]] or None)
            elif key == fkey(9):
                self.send_form(targets)
            elif key == fkey(10):
                self.names(targets)
            elif key == fkey(11):
                sorts = data.get("sorts") or [self.sort]
                self.sort = sorts[(sorts.index(self.sort) + 1) % len(sorts)] if self.sort in sorts else sorts[0]
                self.t.message = f"Sorted by {self.sort}."
            elif fkey(13) <= key <= fkey(21):
                self.shortcut(key - fkey(12), targets)
            elif key < 0 and chr(-key) in "123456789":
                self.shortcut(int(chr(-key)), targets)

    def device_options(self, picks, devices):
        tagged = {d["node"] for d in devices if d["tagged"]}
        toggled = {d["node"] for opt, d in picks if opt == "1"}
        if toggled:
            self.call(self.c.set_tags, sorted(tagged ^ toggled))
        sends = [d for opt, d in picks if opt == "9"]
        names = [d for opt, d in picks if opt == "11"]
        for opt, d in picks:
            if opt in ("1", "9", "11"):
                continue
            action = {"2": self.change_device, "4": self.forget, "5": self.display_device, "6": self.statistics,
                      "7": lambda d: self.commands_for([d]), "8": self.reads,
                      "10": lambda d: self.send([d], None, "region")}.get(opt)
            if action is None:
                self.t.message = f"Option {opt} is not valid; the options are listed above the column headings."
                return
            action(d)
        if sends:
            self.send_form(sends)
        if names:
            self.names(names)

    def change_device(self, d):
        key, values = form(self.t, "Change Device", [
            {"label": "Name", "value": d.get("name") or "", "width": 30},
            {"label": "Region", "value": d.get("region") or "", "width": 8},
            {"label": "Notes", "value": d.get("notes") or "", "width": 44}],
            head=[f"Node . . . . . . . . . . . :  {d['node']}", f"Added  . . . . . . . . . . :  {d.get('added') or '?'}"],
            context="edit")
        if key in ENTER and self.call(self.c.edit_device, d["node"], name=values[0], region=values[1], notes=values[2]):
            self.t.message = "Saved."

    def forget(self, d):
        if confirm(self.t, "Confirm Forget of Device", [
                f"Name . . . :  {d.get('name') or '(unnamed)'}", f"Node . . . :  {d['node']}",
                f"Region . . :  {d.get('region') or '?'}", "",
                "The device is forgotten here. Nothing is sent to it; its history file stays."],
                "Press Enter to confirm your choice for 4=Forget, F12 to return."):
            if self.call(self.c.forget, d["node"]):
                self.t.message = "Forgotten."

    def display_device(self, d):
        """5=Display: the device's fields, then its history newest first; 5 on an entry
        shows the whole of it."""
        lst = OptList(self.t, "Display Device", LIST_KEYS, legend=["5=Display entry"],
                      header=f"{'Sent':16}  {'Command':28}  Answer", prompt=None, context="edit")
        while True:
            d = self.call(self.c.device, d["node"]) or d
            hist = (self.call(self.c.history, d["node"]) or {}).get("history", [])
            fleet = d.get("fleet") or {}
            cells = d.get("cells") or {}
            lst.head = [f"Name . . . :  {(d.get('name') or '(unnamed)')[:24]:24}  Node . . . . :  {d['node']}",
                        f"Region . . :  {(d.get('region') or '?'):24}  Version  . . :  {(d.get('ver') or '-')[:30]}",
                        f"Last sent  :  {(d.get('last_sent') or '-'):24}  Last result  :  {d.get('last_result') or '-'}",
                        f"Battery  . :  {(cells.get('batt') or '-').strip() or '-':24}  Seen . . . . :  "
                        f"{fleet.get('seen') or '-'}{'   (nothing heard for 7 days)' if d.get('stale') else ''}",
                        f"Notes  . . :  {d.get('notes') or ''}"]
            lst.set_rows([(fmt.history_row(line), "normal", line) for line in reversed(hist)])
            key, picks, _c = lst.run()
            if key == fkey(5):
                continue
            if key not in ENTER:
                return
            for _opt, line in picks:
                show(self.t, "Display History Entry", [f"Sent . . . :  {line.get('at')}", f"Command  . :  {line.get('command')}",
                                                       f"Topic  . . :  {line.get('topic')}", "", fmt.history_out(line)])

    def reads(self, d):
        """8=Last answers: what the device last answered to each read; 1 reads it again."""
        lst = OptList(self.t, "Display Last Answers", LIST_KEYS, legend=["1=Read again  5=Display"],
                      header=f"{'Age':>5}  {'Read':18} Answer", head=[f"Device . . :  {d.get('name') or d['node']}  ({d['node']})"],
                      prompt=None, context="reads")
        while True:
            rows = (self.call(self.c.reads, d["node"]) or {}).get("reads", [])
            lst.set_rows([(f"{r['age']:>5}  {r['command'][:18]:18} {r['reply']}", "normal", r) for r in rows])
            key, picks, _c = lst.run()
            if key == fkey(5):
                continue
            if key not in ENTER:
                return
            for opt, r in picks:
                if opt == "1":
                    self.send([d], None, r["command"])
                elif opt == "5":
                    show(self.t, "Display Answer", [f"Read . . . :  {r['command']}", f"Answered . :  {r['at']}", "", r["reply"]])

    # -- sending -------------------------------------------------------------------------

    def recall(self, devices):
        """Earlier commands, oldest first, for Up and Down in the command field: the
        broadcasts and the first device's, or every device's when none is named yet."""
        lines = (self.call(self.c.broadcast_history, limit=50) or {}).get("history", [])
        for d in devices[:1] if devices else self.devices()["devices"]:
            lines += (self.call(self.c.history, d["node"], limit=50) or {}).get("history", [])
        lines.sort(key=lambda l: l.get("at") or "")
        seen = list(dict.fromkeys(l["command"] for l in reversed(lines)))
        return list(reversed(seen))

    def send_form(self, devices, command=""):
        """The Send a Command prompt: the devices (names or node ids; *TAGGED) and the
        command. F4 prompts the command from the device's list."""
        names = ", ".join(d.get("name") or d["node"] for d in devices) if devices else "*TAGGED"
        fields = [{"label": "Devices", "value": names, "width": 44},
                  {"label": "Command", "value": command, "width": 44, "recall": self.recall(devices)}]
        while True:
            key, values = form(self.t, "Send a Command", fields, keys=["F1=Help   F3=Exit   F4=Prompt   F12=Cancel"],
                               head=["Nothing is sent until you press Enter. Up and Down recall earlier commands.",
                                     "A region tree line with '|' cannot travel; use 'region put' one at a time."],
                               context="send")
            if values is None:
                return
            fields[0]["value"], fields[1]["value"] = values
            targets = self.resolve(values[0])
            if not targets:
                continue
            self.t.message = ""
            if key == fkey(4):
                picked = self.commands_for(targets, pick=True)
                if picked:
                    fields[1]["value"] = picked
                continue
            if key not in ENTER:
                continue
            if not values[1].strip():
                self.t.message = "Type a command, or F4 to prompt one."
                continue
            self.send(targets, None, values[1].strip())
            return

    def region_form(self, region=""):
        first = region or ((self.call(self.c.regions) or {}).get("regions") or [""])[0]
        key, values = form(self.t, "Send a Command to Every Node in a Region",
                           [{"label": "Region", "value": first, "width": 8},
                            {"label": "Command", "value": "", "width": 44, "recall": self.recall(None)}],
                           head=["One signed broadcast; every node in the region that holds your key acts on it."],
                           context="region")
        if key in ENTER and values[0].strip() and values[1].strip():
            self.send(None, values[0].strip().upper(), values[1].strip())

    def send(self, devices, region, command):
        """Sign and send, to the devices (one publish each) or to every node of a region;
        then the results with each answer's signature."""
        if region:
            to = f"EVERY node in {region}"
        else:
            to, why = fmt.target_line(devices)
            if why:
                self.t.message = why
                return
        target = {"region": region} if region else {"devices": [d["node"] for d in devices]}
        job = self.job(self.c.start_send, command, about=to, confirm=False, **target)
        if job is None:
            return
        result = job.get("result") or {}
        headline = f"{result.get('answered', 0)} of {result.get('asked', 0)} answered." + (" (dry run)" if result.get("dry_run") else "")
        self.results(command, to, headline, job["outcomes"])

    def results(self, command, to, headline, outcomes):
        lst = OptList(self.t, "Command Results", VIEW_KEYS, legend=["5=Display"], prompt=None, context="send",
                      head=[f"Command  . :  {command}", f"To . . . . :  {to}", headline],
                      header=f"{'Device':18} {'Signature':17} Result")
        lst.set_rows([(fmt.outcome_row(o), "error" if o.get("verified") is False else "normal", o) for o in outcomes])
        while True:
            key, picks, _c = lst.run()
            if key not in ENTER:
                return
            for _opt, o in picks:
                show(self.t, "Display Result", [f"Device . . :  {o.get('name')}  ({o.get('node')})", f"Command  . :  {o.get('command')}",
                                                f"Signature  :  {fmt.signature(o)}", f"Code . . . :  {o.get('code_name') or '-'}", "",
                                                o.get("line") or "", "", "Reply:", o.get("reply") or "-"])

    def shortcut(self, n, targets):
        slots = (self.call(self.c.shortcuts) or {}).get("shortcuts", [])
        command = slots[n - 1] if 0 < n <= len(slots) else ""
        if not command:
            self.t.message = f"Shortcut {n} is not set; main menu option 7."
            return
        self.send(targets, None, command)

    def commands_for(self, targets, pick=False):
        """7=Commands: every command the tool offers, by group; what the devices' firmware
        does not admit is grey with the build it needs, and is not sent. Typed text filters.
        1 asks the parameter and sends (or, with `pick`, returns the command)."""
        to, why = fmt.target_line(targets)
        if why:
            self.t.message = why
            return None
        lst = OptList(self.t, "Select Command", LIST_KEYS, legend=["1=Select  5=Display"], prompt="Filter",
                      header=f"{'Command':44} Admitted", context="commands")
        filt, merged = "", None
        while True:
            if merged is None:          # first, and on F5: what the firmware admits may have changed
                lists = []
                for d in targets:
                    got = self.call(self.c.device_commands, d["node"])
                    if got is None:
                        return None
                    lists.append(got)
                merged = fmt.merge_commands(lists)
                lst.head = [f"To . . . . :  {to}", fmt.firmware_note(merged["greyed"], merged["revisions"])]
            rows = []
            for g in merged["groups"]:
                cs = [c for c in g["commands"] if filt.lower() in c["text"].lower()]
                if cs:
                    rows.append((f"-- {g['group']} --", "bright", None))
                    rows += [(f"{fmt.command_label(c)[:44]:44} {fmt.command_note(c) or 'yes'}",
                              "grey" if fmt.command_note(c) else "normal", c) for c in cs]
            lst.set_rows(rows)
            key, picks, typed = lst.run()
            if key == fkey(5):
                filt, merged = "", None
                continue
            if key not in ENTER:
                return None
            if typed or not picks:
                filt = typed
                continue
            for opt, c in picks:
                if opt == "5":
                    show(self.t, "Display Command", [f"Command  . :  {fmt.command_label(c)}", f"Group  . . :  {c['group']}",
                                                     f"Needs  . . :  {c.get('needs') or 'no build admits it'}",
                                                     f"Admitted . :  {fmt.command_note(c) or 'yes'}",
                                                     f"Examples . :  {'   '.join(c.get('examples') or []) or '-'}",
                                                     f"Caution  . :  {c.get('caution') or '-'}"])
                elif opt == "1":
                    if fmt.command_note(c):
                        self.t.message = f"{c['text']}: {fmt.command_note(c)}; not sent."
                        break
                    command = self.parameters(c)
                    if command is None:
                        break
                    if pick:
                        return command
                    self.send(targets, None, command)
                    return None
                else:
                    self.t.message = f"Option {opt} is not valid here."

    def parameters(self, c):
        """The command text for a table entry, asking its parameter: none, one of the
        choices, several, or text with examples. None when backed out of."""
        if c["kind"] == "none":
            return c["text"]
        if c["kind"] in ("choice", "multi"):
            many = c["kind"] == "multi"
            lst = OptList(self.t, "Select Value", VIEW_KEYS, legend=["1=Select" + ("  (several may be)" if many else "")],
                          header="Value", head=[f"Command  . :  {c['text']} <value>"], prompt=None, context="commands")
            lst.set_rows([(v or "-", "normal", v) for v in c["choices"]])
            while True:
                key, picks, _c = lst.run()
                if key not in ENTER:
                    return None
                chosen = [v for opt, v in picks if opt == "1"]
                if chosen:
                    return fmt.build(c["text"], chosen if many else chosen[:1])
                self.t.message = "Type 1 by a value, or F12."
        key, values = form(self.t, "Specify Value", [{"label": "Value", "value": "", "width": 44}], context="commands",
                           head=[f"Command  . :  {c['text']} <value>",
                                 "Examples:  " + "   ".join(c.get("examples") or []) if c.get("examples") else ""])
        while key in ENTER and not values[0].strip():
            self.t.message = "Type a value, or F12."
            key, values = form(self.t, "Specify Value", [{"label": "Value", "value": "", "width": 44}], context="commands",
                               head=[f"Command  . :  {c['text']} <value>"])
        return fmt.build(c["text"], [values[0].strip()]) if key in ENTER else None

    # -- the long verbs ------------------------------------------------------------------

    def discover(self, regions):
        """Ask one or more regions (`JKG, GOT`, `*` for all of yours) which nodes accept
        your key; then Work with Found Devices, where 1 adds one and F6 adds all."""
        if regions is None:
            first = ((self.call(self.c.regions) or {}).get("regions") or [""])[0]
            key, values = form(self.t, "Discover Devices", [{"label": "Region(s)", "value": first, "width": 30}],
                               head=["One signed read per region; only a node that verified it answers.",
                                     "One IATA code, several with commas, or * for every region you have."], context="discover")
            if key not in ENTER or not values[0].strip():
                return
            regions = values[0].strip()
        job = self.job(self.c.start_discover, regions)
        if job is None:
            return
        found = (job.get("result") or {}).get("found", [])
        asked = ", ".join((job.get("result") or {}).get("regions", [])) or regions
        if not found:
            self.t.message = "No node answered: none there holds your key, or its control plane is off."
            return
        lst = OptList(self.t, "Work with Found Devices", fmt.fkeys(["F1=Help", "F3=Exit", "F6=Add all", "F12=Cancel"]),
                      legend=["1=Add"], header=f"{'Node':8}  {'Reg':5} {'Name or answer':34} Known as",
                      head=[f"Found in {asked}: {len(found)}"], prompt=None, context="found")
        while True:
            lst.set_rows([(fmt.found_row(e), "normal" if e.get("known_as") is not None else "bright", e) for e in found])
            key, picks, _c = lst.run()
            if key == fkey(6):
                picks = [("1", e) for e in found]
            elif key not in ENTER:
                return
            added = 0
            for opt, e in picks:
                if opt != "1" or e.get("known_as") is not None:
                    continue
                name = e.get("name") or ""
                if not name and key in ENTER:
                    k, values = form(self.t, "Name the Device", [{"label": "Name", "value": "", "width": 30}], context="found",
                                     head=[f"{e['node']} in {e['region']} ({(e.get('outcome') or '')[:30]})", "Leave it empty to name it later."])
                    if values is None:
                        continue
                    name = values[0].strip()
                entry = self.call(self.c.adopt, e["node"], e["region"], key=e.get("key"), name=name)
                if entry:
                    e["known_as"] = entry.get("name") or entry["node"]
                    added += 1
            if added or not self.t.message:
                self.t.message = f"{added} device(s) remembered."

    def run_set_form(self, devices, name="", arg=""):
        sets = (self.call(self.c.sets) or {}).get("sets", {})
        if not sets:
            self.t.message = "No command sets; main menu option 6 defines one."
            return
        names = ", ".join(d.get("name") or d["node"] for d in devices) if devices else "*TAGGED"
        key, values = form(self.t, "Run a Command Set", [
            {"label": "Command set", "value": name or sorted(sets)[0], "width": 20},
            {"label": "Value for {arg}", "value": arg, "width": 30},
            {"label": "Devices", "value": names, "width": 44},
            {"label": "Or every node in region", "value": "", "width": 8}],
            head=["Sets: " + ", ".join(sorted(sets)), "Each step is sent in turn; a refusal or silence stops the rest."], context="runset")
        if key not in ENTER:
            return
        setname, arg, refs, region = (v.strip() for v in values)
        if region:
            target, to = {"region": region.upper()}, f"EVERY node in {region.upper()}"
        else:
            targets = self.resolve(refs)
            if not targets:
                return
            to, why = fmt.target_line(targets)
            if why:
                self.t.message = why
                return
            target = {"devices": [d["node"] for d in targets]}
        job = self.job(self.c.start_run_set, setname, arg=arg or None, about=to, confirm=False, **target)
        if job is None:
            return
        result = job.get("result") or {}
        stops = [r["stopped_at"] for r in result.get("runs", []) if r.get("stopped_at") is not None]
        headline = f"Stopped at step {stops[0] + 1}." if stops else "Every step answered ok."
        show(self.t, f"Set: {setname[:28]}", [f"To . . . . :  {to}", headline, ""] + result.get("lines", []), context="runset")

    def names(self, devices):
        """11, F10 or `names`: get name and ver; the unnamed take the fleet's name."""
        if not devices:
            self.t.message = "No device under the cursor."
            return
        if len(devices) > 3 and not confirm(self.t, "Confirm Names and Versions",
                                            [f"{len(devices)} devices, two signed reads each.",
                                             "Each read spends one of the node's command slots this hour."]):
            return
        job = self.job(self.c.start_refresh_names, devices=[d["node"] for d in devices])
        if job:
            r = job.get("result") or {}
            show(self.t, "Names and Versions", [f"{r.get('versioned', 0)} of {len(devices)} gave a version; {r.get('named', 0)} named.", ""]
                 + r.get("lines", []))

    def fleet(self, devices):
        """F7 or `fleet`: Batt and Seen from Beacon for the tagged devices, or all."""
        job = self.job(self.c.start_refresh_fleet, devices=[d["node"] for d in devices] if devices else None)
        if job:
            r = job.get("result") or {}
            self.t.message = (f"Refreshed {r.get('updated', 0)} of {r.get('asked', 0)} from Beacon"
                              + (f"; {r['failed']} without statistics." if r.get("failed") else "."))

    def statistics(self, d, range_="24h"):
        """6=Statistics: get stats live, then what Beacon holds; F10 and F11 widen it."""
        ranges = {"24h": ("24h", "6h"), "48h": ("48h", "6h"), "7d": ("168h", "24h"), "168h": ("168h", "24h")}
        range_ = range_ if range_ in ranges else "24h"
        while True:
            rng, interval = ranges[range_]
            job = self.job(self.c.start_statistics, d["node"], range=rng, interval=interval)
            if job is None:
                self.t.message = f"No statistics: {self.t.message}"
                return
            key = show(self.t, f"Statistics: {(d.get('name') or d['node'])[:28]}", (job.get("result") or {}).get("lines", []),
                       keys=["F3=Exit   F10=48 hours   F11=7 days   F12=Cancel"], done=(fkey(10), fkey(11)), context="stats")
            if key == fkey(10):
                range_ = "48h"
            elif key == fkey(11):
                range_ = "7d"
            else:
                return

    def test(self):
        job = self.job(self.c.start_test)
        if job:
            r = job.get("result") or {}
            show(self.t, "Broker Test", ["Broker test passed." if r.get("ok") else "Broker test failed.", ""] + fmt.test_lines(r),
                 context="test")

    # -- sets, shortcuts, keys, configuration, columns, history ---------------------------

    def work_with_sets(self):
        lst = OptList(self.t, "Work with Command Sets", fmt.fkeys(["F1=Help", "F3=Exit", "F5=Refresh", "F6=Create", "F12=Cancel"]),
                      legend=["2=Change  4=Remove  5=Display  9=Run"], header=f"{'Set':20} Steps", prompt=None, context="sets")
        while True:
            data = self.call(self.c.sets) or {"sets": {}, "max_steps": 6}
            sets = data["sets"]
            lst.set_rows([(f"{n:20} {'; '.join(sets[n])}", "normal", n) for n in sorted(sets)])
            key, picks, _c = lst.run()
            if key == fkey(6):
                self.set_form("", [], data["max_steps"])
            elif key not in ENTER and key != fkey(5):
                return
            for opt, n in picks:
                if opt == "2":
                    self.set_form(n, sets[n], data["max_steps"])
                elif opt == "4":
                    if confirm(self.t, "Confirm Remove of Command Set", [f"Set  . . . :  {n}"] + sets[n],
                               "Press Enter to confirm your choice for 4=Remove, F12 to return."):
                        if self.call(self.c.remove_set, n):
                            self.t.message = f"Removed {n}."
                elif opt == "5":
                    show(self.t, f"Display Command Set: {n}", [f"{i + 1}. {s}" for i, s in enumerate(sets[n])], context="sets")
                elif opt == "9":
                    self.run_set_form(None, n)
                else:
                    self.t.message = f"Option {opt} is not valid here."

    def set_form(self, name, steps, max_steps):
        fields = [{"label": "Name", "value": name, "width": 20}] + [
            {"label": f"Step {i + 1}", "value": steps[i] if i < len(steps) else "", "width": 44} for i in range(max_steps)]
        key, values = form(self.t, "Change Command Set" if name else "Create Command Set", fields, context="sets",
                           head=["{arg} in a step is asked for when the set runs. Empty steps are dropped."])
        if key in ENTER and values[0].strip():
            saved = self.call(self.c.save_set, values[0].strip(), [v.strip() for v in values[1:] if v.strip()])
            if saved:
                self.t.message = f"Saved {saved['name']}."

    def shortcuts_form(self):
        slots = (self.call(self.c.shortcuts) or {}).get("shortcuts", [])
        key, values = form(self.t, "Work with Shortcuts",
                           [{"label": f"Shift+F{n} / Alt+{n}", "value": s, "width": 40} for n, s in enumerate(slots, 1)],
                           head=["A command per key, sent to the tagged or cursor device without a prompt;",
                                 "on Work with devices, or `alt <n>` on a command line. Empty frees a slot."], context="shortcuts")
        if key in ENTER and self.call(self.c.set_shortcuts, [v.strip() for v in values]):
            self.t.message = "Shortcuts saved."

    def keys_screen(self):
        """Work with Signing Key: the provisioning commands; register, replace, create, import."""
        command = ""
        while True:
            k = self.call(self.c.key) or {}
            t = self.t
            w, h = t.size
            t.frame("Work with Signing Key", ["F1=Help   F3=Exit   F12=Cancel"])
            if k.get("exists"):
                reg = k.get("registered")
                lines = ["Provision each repeater over its serial console with:", ""] + [
                    "  " + l for l in fmt.pubkey_lines(k["public"], w)] + [
                    "", f"The private seed is in {k.get('path')}",
                    "and never leaves this machine inside anything but a signature.", "",
                    (f"Registered with {reg['broker']} on {reg['at'][:10]}." if reg else
                     "Not registered with the broker yet: until then it cannot check your commands.")]
                choices = ["1. Register with the broker", "2. Replace the key (every repeater provisioned with it stops listening)"]
            else:
                lines = ["No signing key yet."]
                choices = ["1. Create a key", "2. Import an existing 64-hex seed"]
            for i, line in enumerate(lines + [""] + choices):
                t.put(2 + i, 1, line)
            t.put(h - 6, 1, "Selection")
            t.put(h - 5, 1, "===>", "bright")
            key, command = t.edit(h - 5, 6, 20, command)
            if key == curses.KEY_RESIZE:
                continue
            if key == fkey(1):
                self.help("keys")
                continue
            if key in EXIT:
                return
            choice, command = command.strip(), ""
            t.message = ""
            if choice == "1" and k.get("exists"):
                job = self.job(self.c.start_register)
                if job:
                    r = job.get("result") or {}
                    t.message = ("Registered: " if r.get("ok") else "Not registered: ") + str(r.get("detail"))
            elif choice == "2" and k.get("exists"):
                if confirm(t, "Confirm Replace of Signing Key", ["Every repeater provisioned with the current key will stop accepting "
                                                                "commands until it is re-provisioned over serial."],
                           "Press Enter to replace the key anyway, F12 to keep it."):
                    if self.call(self.c.create_key, overwrite=True):
                        t.message = "Key replaced. Re-provision every repeater with the new public key."
            elif choice == "1":
                if self.call(self.c.create_key):
                    t.message = "Key created."
            elif choice == "2":
                key, values = form(t, "Import a Seed", [{"label": "Seed (64 hex)", "value": "", "width": 64, "secret": True}],
                                   context="keys")
                if key in ENTER and values[0].strip() and self.call(self.c.create_key, seed=values[0].strip()):
                    t.message = "Key imported."
            elif choice:
                t.message = f"Selection {choice} is not valid."

    def config_form(self):
        """Change Configuration: the control broker and the Beacon URL; a value that is not
        a number keeps the screen, with the message. F10 saves and tests."""
        cfg = self.call(self.c.config)
        if cfg is None:
            return
        spec = [("Broker", "broker", 40, False), ("Port", "port", 6, False), ("Username", "username", 30, False),
                (f"Password ({'set' if cfg.get('password_set') else 'not set'})", "password", 30, True),
                ("Wait seconds", "wait_secs", 4, False), ("Statistics URL", "beacon_url", 44, False)]
        fields = [{"label": label, "value": cfg.get(k, ""), "width": wd, "secret": s} for label, k, wd, s in spec]
        at = 0
        while True:
            key, values = form(self.t, "Change Configuration", fields,
                               head=["The broker a node's mqtt.cmd.slot points at, and its credentials.",
                                     "Statistics come from a Beacon server, read-only, no credentials.",
                                     "The password is never shown: type one to change it, *NONE to remove it."],
                               keys=["F1=Help   F3=Exit   F10=Test   F12=Cancel"], context="setup", at=at)
            if key not in ENTER and key != fkey(10):
                return
            for f, v in zip(fields, values):
                f["value"] = v
            try:
                wait = float(values[4])
                changes = {"broker": values[0].strip(), "port": int(values[1]), "username": values[2].strip(),
                           "wait_secs": int(wait) if wait.is_integer() else wait, "beacon_url": values[5].strip()}
            except ValueError:
                self.t.message = "Port and wait seconds must be numbers."
                at = 4 if values[1].strip().isdigit() else 1
                continue
            if values[3]:                   # sent only when changed
                changes["password"] = "" if values[3].strip().upper() == "*NONE" else values[3]
            if self.call(self.c.set_config, **changes):
                self.t.message = "Saved."
                if key == fkey(10):
                    self.test()
                return

    def columns(self):
        cfg = self.call(self.c.config) or {}
        data = self.devices()
        every = data.get("all_columns") or ["reg", "sent", "batt", "seen", "ver", "result"]
        chosen = cfg.get("columns") or every
        lst = OptList(self.t, "Change Directory Columns", VIEW_KEYS, legend=["1=Show  (blank hides the column)"],
                      header="Column", head=["Name and node always show; on a narrow terminal pick what matters."],
                      prompt=None, context="main")
        lst.set_rows([(c, "normal", c) for c in every], {i: "1" for i, c in enumerate(every) if c in chosen})
        key, picks, _c = lst.run()
        if key not in ENTER:
            return
        picked = [c for opt, c in picks if opt == "1"] or every
        saved = self.call(self.c.set_config, columns=picked)
        if saved:
            self.t.message = "Columns: " + ", ".join(saved.get("columns", picked)) + "."

    def broadcast_history(self):
        hist = (self.call(self.c.broadcast_history) or {}).get("history", [])
        show(self.t, "Display Broadcast History", [fmt.history_row(l) for l in reversed(hist)] or ["(nothing broadcast yet)"])

    # -- help ----------------------------------------------------------------------------

    def help(self, context, topic=None):
        """F1: the topic for this screen; links are followed with Tab and Enter, F12 goes
        back, F11 is the contents."""
        if topic is None and context is not None:
            if self.contexts is None:
                self.contexts = (self.call(self.c.help_index) or {}).get("context", {})
            topic = self.contexts.get(context, "screen")
        stack = [topic] if topic else []
        while True:
            if not stack:
                index = self.call(self.c.help_index) or {"topics": []}
                lst = OptList(self.t, "Help Contents", VIEW_KEYS, legend=["5=Display"], header=f"{'Topic':14} Title",
                              prompt=None, context=None)
                lst.set_rows([(f"{t['name']:14} {t['title']}", "normal", t["name"]) for t in index["topics"]])
                key, picks, _c = lst.run()
                if key not in ENTER or not picks:
                    return
                stack.append(picks[0][1])
            topic = self.call(self.c.help_topic, stack[-1])
            if topic is None:
                stack.pop()
                if not stack:
                    return
                continue
            key = self.help_page(topic)
            if key == "back":
                stack.pop()
                if not stack:
                    return
            elif key == "index":
                stack = []
            elif key == "exit":
                return
            else:
                stack.append(key[1])

    def help_page(self, topic):
        """One topic: `back`, `exit`, `index`, or `("follow", topic)` for a link."""
        t, top, at = self.t, 0, 0
        links = topic["links"]
        while True:
            w, h = t.size
            size = h - 6
            t.frame(f"Help: {topic['title']}", ["F3=Exit help   F11=Contents   F12=Back   Tab=Next link   Enter=Follow"])
            for i, line in enumerate(topic["lines"][top:top + size]):
                t.put(2 + i, 1, line)
            for n, l in enumerate(links):
                if top <= l["line"] < top + size:
                    t.put(2 + l["line"] - top, 1 + l["start"], l["target"], "cursor" if n == at else "bright")
            more = "More..." if top + size < len(topic["lines"]) else "Bottom"
            t.put(2 + size, w - len(more) - 2, more)
            key = t.key()
            if key == curses.KEY_F3:
                return "exit"
            if key in (curses.KEY_F12, 27, curses.KEY_BACKSPACE, 127, 8):
                return "back"
            if key == fkey(11):
                return "index"
            if key in ENTER and links:
                return ("follow", links[at]["target"])
            if key == 9 and links:
                at = (at + 1) % len(links)
                top = max(0, min(links[at]["line"] - size // 2, len(topic["lines"]) - size)) if not (top <= links[at]["line"] < top + size) else top
            elif key == curses.KEY_BTAB and links:
                at = (at - 1) % len(links)
            elif key == curses.KEY_NPAGE and top + size < len(topic["lines"]):
                top += size
            elif key == curses.KEY_PPAGE:
                top = max(0, top - size)
