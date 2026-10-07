"""Everything mccontrol remembers, under one directory the user owns.

    ~/.config/mccontrol/
        key.seed          the private signing seed, 64 hex, mode 0600; never printed
        config.json       broker, region, defaults
        devices.json      the devices you manage: name, node hex, notes, last seen
        history/<node>.jsonl   every command sent to that device and what came back

Override the directory with MCCONTROL_HOME. Nothing here is ever sent anywhere except the
signed envelope, and the seed is never part of it.
"""

import datetime
import json
import os
import pathlib
import stat

from . import envelope

# The configuration is the control broker and nothing else: the one that takes the signed
# commands and carries the results back, with its own username and password. A region is a
# property of each device, since one person may run repeaters in several.
DEFAULT_CONFIG = {
    "broker": "mqtt.louie.se",
    "port": 1883,
    "username": "",
    "password": "",
    "ttl_secs": envelope.DEFAULT_TTL_SECS,
    "wait_secs": 20,
    # Statistics come from a Beacon server (read-only HTTP, no credentials); one URL, since
    # one Beacon covers a country, and another region's Beacon is this one setting.
    "beacon_url": "https://beacon.meshat.se/api/v1",
}

# Alt+1..9 on the main screen and `mccontrol alt <n>` on the command line: a command each,
# sent without a dialog. Reads by default; a user may put anything there, and a caution
# still asks.
# A command set: a named list sent in order to a device, stopping at the first refusal or
# silence. `{arg}` in a step is filled in when the set is run. One shipped as the example.
DEFAULT_SETS = {
    "add-scope": ["region put {arg} *", "region allowf {arg}", "region save", "region"],
}
SET_STEPS = 6

SHORTCUT_SLOTS = 9
DEFAULT_SHORTCUTS = ["region", "ver", "get name", "get autoreply", "get radio", "get repeat"]


class StoreError(Exception):
    pass


def home(env=None):
    env = os.environ if env is None else env
    path = env.get("MCCONTROL_HOME")
    if path:
        return pathlib.Path(path)
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME", "~"), ".config")
    return pathlib.Path(os.path.expanduser(base)) / "mccontrol"


class Store:
    def __init__(self, root=None, env=None):
        self.root = pathlib.Path(root) if root else home(env)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "history").mkdir(exist_ok=True)

    # -- key ---------------------------------------------------------------------------

    @property
    def key_path(self):
        return self.root / "key.seed"

    def has_key(self):
        return self.key_path.is_file()

    def create_key(self, overwrite=False):
        """A new signing key. Refuses to replace one unless told to: every node provisioned
        with the old public key would stop listening."""
        if self.has_key() and not overwrite:
            raise StoreError(f"a key already exists at {self.key_path}; "
                             f"replacing it would silence every node provisioned with it")
        seed = envelope.generate_seed()
        self._write_private(self.key_path, seed + "\n")
        return envelope.public_key_hex(seed)

    def import_key(self, seed_hex, overwrite=False):
        if self.has_key() and not overwrite:
            raise StoreError(f"a key already exists at {self.key_path}")
        public = envelope.public_key_hex(seed_hex)      # validates before anything is written
        self._write_private(self.key_path, seed_hex.strip() + "\n")
        return public

    def seed(self):
        if not self.has_key():
            raise StoreError("no signing key yet: create one first")
        mode = stat.S_IMODE(self.key_path.stat().st_mode)
        if mode & 0o077:
            raise StoreError(f"{self.key_path} is readable by others (mode {mode:o}); "
                             f"chmod 600 it")
        return self.key_path.read_text(encoding="utf-8").strip()

    def public_key(self):
        return envelope.public_key_hex(self.seed())

    @staticmethod
    def _write_private(path, text):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(path, 0o600)

    # -- config ------------------------------------------------------------------------

    def config(self):
        data = dict(DEFAULT_CONFIG)
        data.update(self._read_json(self.root / "config.json", {}))
        return data

    def save_config(self, **changes):
        data = self.config()
        data.update({k: v for k, v in changes.items() if v is not None})
        self._write_json(self.root / "config.json", data)
        return data

    def sets(self):
        """Named command sets, the shipped example included until it is removed or renamed."""
        sets = dict(DEFAULT_SETS)
        sets.update(self.config().get("sets", {}))
        return {n: [str(s).strip() for s in steps if str(s).strip()] for n, steps in sets.items() if steps}

    def save_set(self, name, steps):
        name = (name or "").strip().lower()
        if not name or " " in name:
            raise StoreError("a set's name is one word")
        sets = dict(self.config().get("sets", {}))
        sets.setdefault(name, [])
        sets.update({n: s for n, s in DEFAULT_SETS.items() if n not in sets})
        sets[name] = [s.strip() for s in steps if s and s.strip()]
        if not sets[name]:
            raise StoreError("a set needs at least one command")
        self.save_config(sets=sets)
        return sets[name]

    def remove_set(self, name):
        sets = dict(self.config().get("sets", {}))
        sets.update({n: s for n, s in DEFAULT_SETS.items() if n not in sets})
        if name not in sets:
            raise StoreError(f"no set named {name}")
        sets[name] = []                   # an empty list is a removed set, the shipped one included
        self.save_config(sets=sets)

    def shortcuts(self):
        """Nine slots, Alt+1 first; an empty string is an unused slot."""
        slots = list(self.config().get("shortcuts", DEFAULT_SHORTCUTS))
        slots = [str(s).strip() for s in slots[:SHORTCUT_SLOTS]]
        return slots + [""] * (SHORTCUT_SLOTS - len(slots))

    def set_shortcut(self, n, command):
        if not 1 <= n <= SHORTCUT_SLOTS:
            raise StoreError(f"a shortcut is Alt+1 to Alt+{SHORTCUT_SLOTS}")
        slots = self.shortcuts()
        slots[n - 1] = (command or "").strip()
        self.save_config(shortcuts=slots)
        return slots

    # -- devices -----------------------------------------------------------------------

    def devices(self):
        """Your devices, in the order you added them."""
        return self._read_json(self.root / "devices.json", [])

    def regions(self):
        """The regions your devices are in, each once, in first-seen order."""
        return list(dict.fromkeys(d.get("region", "") for d in self.devices() if d.get("region")))

    def device(self, ref):
        """By name, by node hex, or by a unique prefix of either; None if nothing matches."""
        ref = (ref or "").strip()
        if not ref:
            return None
        # A whole public key names the node by its first eight characters, like the topics.
        key = ref.upper()[:envelope.NODE_HEX_LEN] if len(ref) >= envelope.NODE_HEX_LEN and all(
            c in "0123456789abcdefABCDEF" for c in ref) else None
        exact = [d for d in self.devices() if d["name"] and d["name"].lower() == ref.lower()
                 or d["node"].upper() == ref.upper() or (key and d["node"].upper() == key)]
        if len(exact) == 1:
            return exact[0]
        prefix = [d for d in self.devices() if (d["name"] and d["name"].lower().startswith(ref.lower()))
                  or d["node"].upper().startswith(ref.upper())]
        return prefix[0] if len(prefix) == 1 else None

    def add_device(self, name, node, region, notes=""):
        try:
            node = envelope.node_id(node)
        except envelope.NodeIdError as exc:
            raise StoreError(str(exc)) from None
        name = name.strip()          # empty is allowed: the id stands in until you name it
        region = (region or "").strip().upper()
        if not (2 <= len(region) <= 8 and region.isalnum()):
            raise StoreError("a device needs its region: the IATA code its observers use, e.g. JKG")
        devices = self.devices()
        if name and any(d["name"].lower() == name.lower() for d in devices):
            raise StoreError(f"a device named {name!r} already exists")
        if any(d["node"] == node for d in devices):
            raise StoreError("that node is already in the list")
        entry = {"name": name, "node": node, "region": region, "notes": notes.strip(), "added": _now(),
                 "last_sent": None, "last_result": None}
        devices.append(entry)
        self._write_json(self.root / "devices.json", devices)
        return entry

    def update_device(self, node, **changes):
        devices = self.devices()
        for entry in devices:
            if entry["node"] == node:
                entry.update(changes)
                self._write_json(self.root / "devices.json", devices)
                return entry
        raise StoreError("no such device")

    def remove_device(self, node):
        devices = [d for d in self.devices() if d["node"] != node]
        self._write_json(self.root / "devices.json", devices)

    # -- history -----------------------------------------------------------------------

    def record(self, node, command, topic, result, note=""):
        """One line per command sent: what, where, and what came back (or nothing)."""
        line = {"at": _now(), "command": command, "topic": topic,
                "code": None if result is None else result.code,
                "code_name": None if result is None else result.code_name,
                "reply": None if result is None else result.reply, "note": note}
        path = self.root / "history" / f"{node or 'all'}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
        if node:
            changes = {"last_sent": line["at"], "last_result": (None if result is None else result.code_name)}
            if result is not None:
                changes["last_heard"] = line["at"]      # any answer, a refusal too, proves it is there
            self.update_device(node, **changes)
        return line

    def history(self, node, limit=200):
        path = self.root / "history" / f"{node or 'all'}.jsonl"
        if not path.is_file():
            return []
        lines = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                lines.append(json.loads(raw))
            except ValueError:
                continue
        return lines[-limit:]

    # -- files -------------------------------------------------------------------------

    @staticmethod
    def _read_json(path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    @staticmethod
    def _write_json(path, data):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat(sep=" ")
