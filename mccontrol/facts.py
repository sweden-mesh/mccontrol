"""What is known about a device: its settings and facts, parsed out of what it answered.

Derived from the store, which is the cache: the last answer to each read (`reads` on the
device) and every command's reply in its history file, oldest first, so a newer answer
replaces an older one and a fact appears the moment a send's result lands. A `get <key>`
answered `> value` is the fact `key`; a `set <key> <value>` answered `OK` is the same fact,
learned by writing it; `ver`, `region` and `get autoreply` have their own shapes. A read
this table does not know is kept raw, under its command.
"""

import re

# fact key: label (17 characters at most), in the order a screen shows them; a key not here follows, by name
LABELS = {
    "name": "name",
    "version": "firmware",
    "txdelay": "tx delay",
    "rxdelay": "rx delay",
    "direct.txdelay": "direct tx delay",
    "autoreply": "autoreply",
    "autoreply.hops": "autoreply hops",
    "autoreply.mode": "autoreply mode",
    "autoreply.direct.flood": "autoreply d.flood",
    "autoreply.private": "autoreply private",
    "autoreply.channel": "autoreply channel",
    "autoreply.region": "autoreply region",
    "autoreply.delay": "autoreply delay",
    "autoreply.slots": "autoreply slots",
    "autoreply.slot": "autoreply slot",
    "autoreply.window": "autoreply window",
    "regions": "regions",
}

# `> on #test-jkg 'test' max 8 hops, direct flood, mode flood, private off` (AutoReply.cpp)
AUTOREPLY = re.compile(r"^(on|off)\s+(\S+)\s+'[^']*'\s+max\s+(\d+)\s+hops,\s+direct\s+(\S+),\s+mode\s+(\S+),"
                       r"\s+private\s+(\S+)")


def _value(reply):
    """The value of a `> value` answer, or None when the answer is not one."""
    reply = (reply or "").strip()
    return reply[1:].strip() if reply.startswith(">") else None


def parse(command, reply):
    """`{fact key: value}` that `command` answered with `reply` carries, or None when this
    table does not know the command (the answer is then kept raw)."""
    words = command.split()
    if not words:
        return None
    verb = words[0].lower()
    if verb == "set" and len(words) >= 3:
        return {words[1].lower(): " ".join(words[2:])} if (reply or "").strip().upper().startswith("OK") else {}
    value = _value(reply)
    if value is None:
        return None
    if words == ["ver"]:
        return {"version": value}
    if words == ["region"]:
        return {"regions": value}
    if verb == "get" and len(words) == 2:
        key = words[1].lower()
        if key == "autoreply":
            m = AUTOREPLY.match(value)
            if not m:
                return {"autoreply": value.split(",")[0].strip()}
            on, channel, hops, direct, mode, private = m.groups()
            return {"autoreply": on, "autoreply.channel": channel, "autoreply.hops": hops,
                    "autoreply.direct.flood": "on" if direct == "flood" else "off", "autoreply.mode": mode,
                    "autoreply.private": private}
        if key == "stats":                  # a block of counters, not one value
            return None
        return {key: value}
    return None


def reader(key):
    """The command that reads `key` again."""
    return {"version": "ver", "regions": "region"}.get(key) or (
        "get autoreply" if key in ("autoreply", "autoreply.channel") else f"get {key}")


def derive(reads, history):
    """`(facts, raw)` from `reads` -- `[(command, reply, at)]` -- and `history` lines:
    `facts` is `{key: {"value", "at", "source"}}`, `raw` `{command: {"reply", "at"}}` for
    the reads this table does not know. Only answers that succeeded count (a read is kept
    only when it did); the newest answer for a key wins."""
    answers = [(at, command, reply, True) for command, reply, at in reads]
    answers += [(line["at"], line["command"], line.get("reply") or "OK", False) for line in history
                if line.get("code") == 0 and line.get("command")]
    facts, raw = {}, {}
    for at, command, reply, kept in sorted(answers, key=lambda a: a[0]):   # stable: history after reads
        command = " ".join(command.split())
        parsed = parse(command, reply)
        if parsed is None:
            if kept:
                raw[command] = {"reply": reply, "at": at}
            continue
        for key, value in parsed.items():
            facts[key] = {"value": value, "at": at, "source": command}
    return facts, raw


def order(key):
    keys = list(LABELS)
    return (keys.index(key), "") if key in LABELS else (len(keys), key)
