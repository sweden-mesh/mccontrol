"""The help topics: plain files under `help/`, with `[[topic]]` links.

The same files serve the F1 window and `mccontrol help`. A topic's first line is its title.
"""

import pathlib
import re

HERE = pathlib.Path(__file__).parent / "help"
LINK = re.compile(r"\[\[([a-z0-9_-]+)\]\]")


class Topic:
    def __init__(self, name, title, lines, links):
        self.name, self.title, self.lines, self.links = name, title, lines, links


def topics():
    return sorted(p.stem for p in HERE.glob("*.txt"))


def load(name):
    """A topic by name, with links resolved to (line, start, end, target); None if there is none."""
    path = HERE / f"{name}.txt"
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    title, body = raw[0], raw[2:] if len(raw) > 1 and raw[1] == "" else raw[1:]
    lines, links = [], []
    for i, line in enumerate(body):
        shown, offset = "", 0
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


def as_text(name):
    """The topic for a terminal: links shown as [topic]."""
    topic = load(name)
    if topic is None:
        return None
    out = [topic.title, ""]
    for i, line in enumerate(topic.lines):
        for _l, start, end, target in sorted((l for l in topic.links if l[0] == i), key=lambda l: -l[1]):
            line = line[:start] + f"[{target}]" + line[end:]
        out.append(line)
    return "\n".join(out)


# Which topic F1 opens from where.
CONTEXT = {"main": "screen", "send": "send", "discover": "discover", "edit": "devices",
           "forget": "devices", "keys": "signing", "setup": "broker", "test": "broker", "shortcuts": "shortcuts",
           "region": "send", "found": "discover"}
