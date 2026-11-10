"""The help topics: plain files under `help/`, with `[[topic]]` links.

The same files serve every interface's help and `mccontrol help`. A topic's first line is its
title. `help/<interface>/<topic>.txt` is that interface's own text for the topic and wins over
the shared one for it; a topic with no such file is the shared text everywhere. The shared
texts say what holds for every interface; how an interface does it is in its own.
"""

import pathlib
import re

HERE = pathlib.Path(__file__).parent / "help"
UI = pathlib.Path(__file__).parent / "ui"
LINK = re.compile(r"\[\[([a-z0-9_-]+)\]\]")


class Topic:
    def __init__(self, name, title, lines, links):
        self.name, self.title, self.lines, self.links = name, title, lines, links


def interfaces():
    """The names `ui` may take: every interface package under `mccontrol/ui/`."""
    return sorted(p.name for p in UI.iterdir() if (p / "__init__.py").is_file())


def _dirs(ui):
    """Where a topic is looked for, the interface's own directory first."""
    if ui is None:
        return [HERE]
    if ui not in interfaces():
        raise ValueError(f"no interface {ui!r}; there are {', '.join(interfaces())}")
    return [HERE / ui, HERE]


def topics(ui=None):
    return sorted({p.stem for d in _dirs(ui) for p in d.glob("*.txt")})


def load(name, ui=None):
    """A topic by name, as `ui` shows it, with links resolved to (line, start, end, target);
    None if there is none. A ValueError for an interface that is not there."""
    if name not in topics(ui):
        return None
    path = next(p for p in (d / f"{name}.txt" for d in _dirs(ui)) if p.is_file())
    raw = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    title, body = raw[0], raw[2:] if len(raw) > 1 and raw[1] == "" else raw[1:]
    lines, links = [], []
    for i, line in enumerate(body):
        shown = ""
        pos = 0
        for m in LINK.finditer(line):
            shown += line[pos:m.start()]
            start = len(shown)
            shown += m.group(1)
            links.append((i, start, len(shown), m.group(1)))
            pos = m.end()
        shown += line[pos:]
        lines.append(shown)
    return Topic(name, title, lines, links)


def as_text(name, ui=None):
    """The topic for a terminal: links shown as [topic]."""
    topic = load(name, ui)
    if topic is None:
        return None
    out = [topic.title, ""]
    for i, line in enumerate(topic.lines):
        for _l, start, end, target in sorted((l for l in topic.links if l[0] == i), key=lambda l: -l[1]):
            line = line[:start] + f"[{target}]" + line[end:]
        out.append(line)
    return "\n".join(out)


def context(ui=None):
    """Which topic help opens from where, in `ui`."""
    _dirs(ui)
    return dict(CONTEXT, **CONTEXT_UI.get(ui, {}))


# Which topic F1 opens from where.
CONTEXT = {"main": "screen", "send": "send", "discover": "discover", "edit": "devices",
           "forget": "devices", "keys": "signing", "setup": "broker", "test": "broker", "shortcuts": "shortcuts", "sets": "sets", "runset": "sets", "stats": "stats", "commands": "commandlist", "reads": "reads",
           "region": "send", "found": "discover"}

# Where an interface's help opens elsewhere: the board has no screens, so its help opens on
# its keys.
CONTEXT_UI = {"Monolith": {"main": "keys"}}
