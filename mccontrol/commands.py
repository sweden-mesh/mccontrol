"""The commands a repeater admits over the control plane, as one table the screens draw
from: the command list on Enter, the parameter modals, the reads cache and the checks that
keep them in step with `denylist.py`.

Each entry: `Command(text, group, kind, choices, examples, read)`.
  kind    "none"    runs as typed
          "choice"  one of `choices` is appended
          "multi"   several of `choices`, space-separated, are appended
          "text"    free text is appended; `examples` are shown beside the field
  read    the reply is worth keeping: cached on the device and shown in the reads pane

The firmware is the authority on what exists; this table is what the tool offers. A node on
older firmware answers not-allowed to most of it, which the tool reports as the node said.
"""

from collections import namedtuple

from . import denylist

Command = namedtuple("Command", "text group kind choices examples read")

GROUPS = ("reads", "auto-reply", "regions", "tuning", "MQTT", "node")


def _c(text, group, kind="none", choices=(), examples=(), read=False):
    return Command(text, group, kind, tuple(choices), tuple(examples), read)


ON_OFF = ("on", "off")

TABLE = (
    # reads: what a node will tell you
    _c("ver", "reads", read=True),
    _c("get stats", "reads", read=True),
    _c("get name", "reads", read=True),
    _c("get autoreply", "reads", read=True),
    _c("region", "reads", read=True),
    _c("neighbors", "reads", read=True),
    _c("get radio", "reads", read=True),
    _c("get repeat", "reads", read=True),
    _c("get flood.max", "reads", read=True),
    _c("get flood.max.unscoped", "reads", read=True),
    _c("get txdelay", "reads", read=True),
    _c("get direct.txdelay", "reads", read=True),
    _c("get rxdelay", "reads", read=True),
    _c("get af", "reads", read=True),
    _c("get cad", "reads", read=True),
    _c("get int.thresh", "reads", read=True),
    _c("get mqtt.status", "reads", read=True),
    _c("get mqtt.cmd", "reads", read=True),
    _c("get mqtt.iata", "reads", read=True),
    _c("get mqtt.interval", "reads", read=True),
    _c("board", "reads", read=True),
    _c("memory", "reads", read=True),
    _c("clock", "reads", read=True),
    _c("ota check", "reads", read=True),
    _c("get", "reads", "text", examples=("get autoreply.hops", "get mqtt1.server", "get path.hash.mode"), read=True),
    # auto-reply
    _c("set autoreply", "auto-reply", "choice", ON_OFF),
    _c("set autoreply.mode", "auto-reply", "choice", ("flood", "private", "direct")),
    _c("set autoreply.hops", "auto-reply", "text", examples=("3", "8")),
    _c("set autoreply.delay", "auto-reply", "text", examples=("60", "300")),
    _c("set autoreply.direct.flood", "auto-reply", "choice", ON_OFF),
    _c("set autoreply.private", "auto-reply", "choice", ON_OFF),
    _c("set autoreply.region", "auto-reply", "text", examples=("se-jkp",)),
    _c("set autoreply.slot", "auto-reply", "text", examples=("1", "7")),
    _c("set autoreply.slots", "auto-reply", "text", examples=("16",)),
    _c("set autoreply.window", "auto-reply", "text", examples=("10 120",)),
    _c("trigger test", "auto-reply", "choice", ("", "F", "P", "D", "S")),
    _c("trigger private", "auto-reply", "text", examples=("<64 hex key of one repeater>",)),
    # regions
    _c("region put", "regions", "text", examples=("se-jkp *", "se-jkp-mul se-jkp")),
    _c("region allowf", "regions", "text", examples=("se-jkp",)),
    _c("region denyf", "regions", "text", examples=("se-jkp", "*")),
    _c("region remove", "regions", "text", examples=("se06",)),
    _c("region default", "regions", "text", examples=("se-jkp",)),
    _c("region home", "regions", "text", examples=("se-jkp",)),
    _c("region save", "regions"),
    # tuning
    _c("set txdelay", "tuning", "text", examples=("0.5", "1.0")),
    _c("set direct.txdelay", "tuning", "text", examples=("0.3",)),
    _c("set rxdelay", "tuning", "text", examples=("0", "0.2")),
    _c("set af", "tuning", "text", examples=("9.0",)),
    _c("set cad", "tuning", "choice", ON_OFF),
    _c("set int.thresh", "tuning", "text", examples=("1",)),
    _c("set flood.max", "tuning", "text", examples=("8",)),
    _c("set flood.max.unscoped", "tuning", "text", examples=("2",)),
    _c("set path.hash.mode", "tuning", "choice", ("0", "1", "2")),
    _c("set loop.detect", "tuning", "choice", ("0", "1", "2", "3")),
    _c("set multi.acks", "tuning", "choice", ON_OFF),
    # MQTT
    _c("set mqtt.iata", "MQTT", "text", examples=("JKG", "GOT")),
    _c("set mqtt.interval", "MQTT", "text", examples=("5", "15")),
    _c("set mqtt.status", "MQTT", "choice", ON_OFF),
    _c("set mqtt.packets", "MQTT", "choice", ON_OFF),
    _c("set mqtt.raw", "MQTT", "choice", ON_OFF),
    _c("set mqtt.neighbors", "MQTT", "choice", ON_OFF),
    _c("set mqtt.presets", "MQTT", "text", examples=("2",)),
    _c("set mqtt1.preset", "MQTT", "text", examples=("meshat.se", "custom")),
    _c("set mqtt1.server", "MQTT", "text", examples=("mqtt.louie.se", "mqtt.example.org:1883")),
    _c("set mqtt2.preset", "MQTT", "text", examples=("custom",)),
    _c("set mqtt2.server", "MQTT", "text", examples=("mqtt.louie.se",)),
    _c("set mqtt2.username", "MQTT", "text", examples=("mcoar",)),
    _c("set mqtt2.password", "MQTT", "text", examples=("<the slot's password>",)),
    _c("set mqtt3.preset", "MQTT", "text", examples=("custom",)),
    _c("set mqtt3.server", "MQTT", "text", examples=("mqtt.example.org",)),
    _c("set mqtt4.preset", "MQTT", "text", examples=("custom",)),
    _c("set mqtt5.preset", "MQTT", "text", examples=("custom",)),
    _c("set mqtt6.preset", "MQTT", "text", examples=("custom",)),
    # node
    _c("set name", "node", "text", examples=("SE-JKG-Gisebo-4AC6",)),
    _c("reboot", "node"),
    _c("clkreboot", "node"),
    _c("clear stats", "node"),
    _c("neighbor.remove", "node", "text", examples=("<8 or 64 hex of the neighbour>",)),
    _c("powersaving", "node", "choice", ON_OFF),
    _c("gps", "node", "choice", ON_OFF),
    _c("set", "node", "text", examples=("set <key> <value>: anything the console takes",)),
)

BY_TEXT = {c.text: c for c in TABLE}


def build(command, values):
    """The command text with the chosen values appended (a `multi` joins them by spaces)."""
    tail = " ".join(v for v in values if v)
    return f"{command.text} {tail}".strip()


def is_read(text):
    """Is this command one whose reply is worth keeping? Any `get`, and the listed reads."""
    head = " ".join((text or "").split()).lower()
    if head.startswith("get "):
        return True
    return any(c.read and c.text == head for c in TABLE)


def grouped():
    """`[(group, [Command, ...]), ...]` in GROUPS order, for the list."""
    return [(g, [c for c in TABLE if c.group == g]) for g in GROUPS]


def check():
    """Every entry must travel; returns the ones the deny list refuses (empty is right)."""
    bad = []
    for c in TABLE:
        sample = build(c, [c.choices[-1]] if c.kind in ("choice", "multi") and c.choices
                        else ([c.examples[0]] if c.kind == "text" and c.examples else []))
        if c.text in ("get", "set"):
            sample = c.examples[0] if c.text == "get" else "set txdelay 0.5"
        if denylist.refusal(sample):
            bad.append((c.text, denylist.refusal(sample)))
    return bad
