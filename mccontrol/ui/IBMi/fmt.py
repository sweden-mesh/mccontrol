"""The text of the screens: pure functions from what the API answered to the lines shown.
No curses here, so each is a unit test."""

import textwrap


def tested(cfg):
    """The last broker test kept for the configured broker: `ok 2026-10-10`, `FAILED ...`,
    or how to run one."""
    kept = cfg.get("tested") or {}
    if not kept or (kept.get("broker"), kept.get("port")) != (cfg.get("broker"), cfg.get("port")):
        return "no - select 10"
    return f"{'ok' if kept.get('ok') else 'FAILED'} {(kept.get('at') or '')[:10]}"


def history_out(line):
    """What came back for one history line: the reply, the refusal, or the note."""
    if line.get("code") == 0:
        return line.get("reply") or "OK"
    if line.get("code") is not None:
        return f"refused {line.get('code_name')}"
    return line.get("note") or "no answer"


def said(line):
    """What came back, as the node's signature leaves it: BAD SIGNATURE before a forged
    reply, (unsigned) after one from firmware that does not sign."""
    out = history_out(line)
    if line.get("verified") is False:
        return f"BAD SIGNATURE {out}"
    return f"{out} (unsigned)" if line.get("code") == 0 and line.get("verified") is None else out


def history_row(line):
    return f"{(line.get('at') or '')[:16]}  {(line.get('command') or '')[:28]:28}  {said(line)}"


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


def firmware_note(greyed, revisions):
    """The line over the command list when entries are greyed: the `ar-N` the devices
    report (the API's `revision`), or unknown."""
    if not greyed:
        return ""
    reported = ", ".join("unknown" if r is None else f"ar-{r}" for r in revisions[:3])
    return f"{greyed} greyed: need a newer build; this device reports {reported}"


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
    return {"groups": groups, "greyed": greyed, "revisions": [l.get("revision") for l in lists]}


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


def cut(text, width):
    """`text` in `width` columns, ending in `~` where it was cut."""
    text = " ".join(str(text).split())
    return text if len(text) <= width else (text[:width - 1] + "~" if width > 0 else "")


def fact_row(f):
    """A fact on Display Device Facts: what, its value, its age, and `unsigned` where the
    answer that taught it was not signed (older firmware)."""
    signed = "" if f.get("verified") else "unsigned"
    return f"{cut(f['label'], 18):18} {cut(f['value'], 34):34} {f['age']:>6}  {signed}"


def reply_text(line):
    """The `< ...` of a history line: the reply without the firmware's `> `, BAD SIGNATURE
    first when the signature failed, `(unsigned)` after an unsigned one."""
    out = history_out(line)
    if line.get("code") == 0 and out.startswith(">"):
        out = out[1:].strip()
    if line.get("verified") is False:
        return f"BAD SIGNATURE {out}"
    return f"{out} (unsigned)" if line.get("code") == 0 and line.get("verified") is None else out


def history_blocks(blocks, width=76):
    """The command history in the operator's form, as `(text, attr)` rows: per block the
    short id once, then `> command` and `< reply`, `|` between. `blocks` is `[(id, lines)]`,
    newest first."""
    rows = []
    for ident, lines in blocks:
        for line in lines:
            rows.append((f"{ident:6} | > {cut(line.get('command') or '', width - 11)}", "normal"))
            bad = line.get("verified") is False or line.get("code") not in (0, None)
            unsigned = line.get("code") == 0 and line.get("verified") is None
            rows.append((f"{'':6} | < {cut(reply_text(line), width - 11)}", "error" if bad else ("grey" if unsigned else "normal")))
            ident = ""
    return rows
