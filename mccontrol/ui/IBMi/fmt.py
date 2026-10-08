"""The text of the screens: pure functions from what the API answered to the lines shown.
No curses here, so each is a unit test."""

import textwrap


def history_out(line):
    """What came back for one history line: the reply, the refusal, or the note."""
    if line.get("code") == 0:
        return line.get("reply") or "OK"
    if line.get("code") is not None:
        return f"refused {line.get('code_name')}"
    return line.get("note") or "no answer"


def history_row(line):
    return f"{(line.get('at') or '')[:16]}  {(line.get('command') or '')[:28]:28}  {history_out(line)}"


def signature(outcome):
    """How far a result can be trusted: `verified` (signed by the node's key), `unverified
    result` (an r1 from older firmware: confirm it with a read), `BAD SIGNATURE`, or `-`
    when nothing came back."""
    if outcome.get("code") is None and outcome.get("ok") is None:
        return "-"
    if outcome.get("verified") is True:
        return "verified"
    if outcome.get("verified") is False:
        return "BAD SIGNATURE"
    return "unverified result"


def outcome_row(outcome):
    return f"{(outcome.get('name') or outcome.get('node') or '')[:18]:18} {signature(outcome):17} {outcome.get('line') or ''}"


def target_line(devices):
    """Who a send goes to, as the confirm and the results say it; an error string when the
    devices span more than one region (a send is to one region)."""
    if not devices:
        return None, "No device to send to: type 1 on a row to tag it, or name one."
    regions = {(d.get("region") or "").upper() for d in devices}
    if len(regions) > 1:
        return None, "The devices span more than one region; a send is to one region."
    if len(devices) == 1:
        d = devices[0]
        return f"{d.get('name') or d['node']}  ({d['node']}, {d.get('region') or '?'})", None
    return f"{len(devices)} tagged devices in {devices[0].get('region') or '?'}", None


def command_label(c):
    """A table entry and the parameter it takes."""
    hint = {"none": "", "choice": "  <" + "|".join(x or "-" for x in c.get("choices", [])) + ">",
            "multi": "  <...>", "text": "  <...>"}.get(c.get("kind"), "")
    return c["text"] + hint


def command_note(c):
    """Why an entry is greyed, or "" when it can be run: the deny list first, then the build."""
    if c.get("refusal"):
        return f"denied: {c['refusal']}"
    if c.get("admitted") is False:
        return f"needs {c.get('needs') or 'no build'}"
    return ""


def firmware_note(greyed, versions):
    """The line over the command list when entries are greyed: what the devices report."""
    if not greyed:
        return ""
    reported = ", ".join(_revision(v) or "unknown" for v in versions[:3])
    return f"{greyed} greyed: need a newer build; this device reports {reported}"


def _revision(version):          # until GET /devices/{ref}/commands carries `revision` (T-02)
    text = (version or "").lower()
    at = text.find("-ar-")
    if at < 0:
        return None
    digits = ""
    for ch in text[at + 4:]:
        if not ch.isdigit():
            break
        digits += ch
    return f"ar-{digits}" if digits else None


def merge_commands(lists):
    """The command lists of several devices as one: an entry is admitted only when every
    device admits it, and the build it needs is the newest any of them needs."""
    first = lists[0]
    groups = []
    for gi, group in enumerate(first["groups"]):
        rows = []
        for ci, c in enumerate(group["commands"]):
            c = dict(c)
            others = [l["groups"][gi]["commands"][ci] for l in lists[1:]]
            if any(o.get("admitted") is False for o in others):
                c["admitted"] = False
            rows.append(c)
        groups.append({"group": group["group"], "commands": rows})
    greyed = sum(1 for g in groups for c in g["commands"] if c.get("admitted") is False)
    return {"groups": groups, "greyed": greyed, "versions": [l.get("version") for l in lists]}


def opt_typed(current, ch):
    """The Opt field after a digit is typed into it: two positions, the third overwrites."""
    return (current + ch) if len(current) < 2 else ch


def fkeys(labels, width=80):
    """Function-key labels laid into at most two rows, three spaces apart."""
    rows, line = [], ""
    for label in labels:
        if line and len(line) + 3 + len(label) > width - 2:
            rows.append(line)
            line = label
        else:
            line = f"{line}   {label}" if line else label
    rows.append(line)
    return rows[:2]


def pubkey_lines(public, width=80):
    """The provisioning commands: the key on one line when it fits, split over two when not."""
    first = f"set mqtt.cmd.pubkey {public}"
    if len(first) <= width - 4:
        return [first, "set mqtt.cmd on"]
    return [f"set mqtt.cmd.pubkey {public[:40]}", " " * 20 + public[40:], "set mqtt.cmd on"]


def test_lines(result):
    """A broker test's steps, then the key and the registration."""
    lines = [f"{'ok  ' if s['ok'] else 'FAIL'}  {s['step']:28} {s['detail']}" for s in result.get("steps", [])]
    lines.append(f"{'ok  ' if result.get('key') else 'FAIL'}  {'signing key':28} "
                 + ("present" if result.get("key") else "none: Work with signing key"))
    reg = result.get("registered")
    lines.append(f"{'ok  ' if reg else 'note'}  {'registered with broker':28} "
                 + (f"{reg['broker']} on {reg['at'][:10]}" if reg else "not yet: Work with signing key, option 1"))
    return lines


def found_row(e):
    """A discovered node: its id, the name or the outcome, and what it is known as here."""
    middle = e.get("name") or e.get("outcome") or ""
    tail = f"= {e['known_as'] or '(unnamed)'}" if e.get("known_as") is not None else ""
    return f"{e['node']}  {e.get('region', ''):5} {middle[:34]:34} {tail}"


def wrap(text, width):
    return textwrap.wrap(text, width) or [""]


def split_verb(line):
    """`(verb, rest)` of a command line, the verb lower-cased."""
    words = line.strip().split(None, 1)
    if not words:
        return "", ""
    return words[0].lower(), (words[1] if len(words) > 1 else "")


def build(text, values):
    """A table entry's command with the chosen values appended, a multi's joined by spaces."""
    return f"{text} {' '.join(v for v in values if v)}".strip()


def dots(label, width=26):
    """`Name . . . . . :`, the 5250 prompt leader."""
    return (label + " " + ". " * width)[:width - 1].rstrip().ljust(width) + ":"
