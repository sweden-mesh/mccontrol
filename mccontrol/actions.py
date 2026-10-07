"""What the screens and the command line both do, so they cannot disagree."""

from . import envelope, transport

# Commands that take something away from a repeater. Each is sent only after the person has
# been told what it does; the node itself does not second-guess a signed command.
CAUTIONS = (
    ("region denyf *", "stops this repeater forwarding every message that carries no scope. "
                       "It may be the only link between two places that rely on that."),
    ("region denyf", "stops this repeater forwarding that scope."),
    ("region remove", "deletes that region from the repeater. Messages scoped to it are dropped."),
    ("set autoreply off", "stops this repeater answering test requests."),
    ("set repeat off", "stops this repeater forwarding anything at all."),
    ("set mqtt.cmd off", "switches the control plane off. The next command must come over serial."),
    ("erase", "erases the node."),
)


def caution(command):
    """A sentence saying what a command takes away, or None for a harmless one."""
    text = " ".join(command.lower().split())
    for prefix, why in CAUTIONS:
        if text == prefix or text.startswith(prefix + " ") or (prefix.endswith("*") and text.startswith(prefix)):
            return why
    return None


def outcome_line(result):
    """One line describing a result, as the screens and the log show it."""
    if result is None:
        return "no answer"
    if result.ok:
        return result.reply or "OK"
    return f"refused: {result.code_name}" + (f" -- {result.help}" if result.help else "")


def send(store, command, device=None, region=None, dry_run=False, wait_secs=None):
    """Sign and send `command` to one device, or with `region` to every node in that region.

    Returns `(topic, payload, {node: Result}, lines)`, where `lines` is what to show.
    Raises `envelope.EnvelopeError`, `transport.BrokerError` or `store.StoreError`.
    """
    config = store.config()
    broadcast = device is None
    if broadcast and not region:
        raise envelope.EnvelopeError("a broadcast needs a region")
    if broadcast and envelope.is_transmit(command):
        raise envelope.EnvelopeError("a command that transmits may not be sent to every node; "
                                     "the firmware refuses it, and so does this")
    region = (region or device.get("region") or "").upper()
    if not region:
        raise envelope.EnvelopeError(f"{device['name']} has no region; change the device and set it")
    topic = envelope.command_topic(region, None if broadcast else device["node"])
    hear = ([d["node"] for d in store.devices() if (d.get("region") or "").upper() == region]
            if broadcast else [device["node"]])
    if dry_run:
        payload = envelope.build(store.seed(), topic, 0, command)
        return topic, payload, {}, [f"would publish to {topic}", payload, "not sent (dry run)"]
    payload, answers = transport.send(dict(config, region=region), store.seed(), topic, command,
                                      nodes_to_hear=hear,
                                      wait_secs=wait_secs or config.get("wait_secs", 20))
    lines = []
    names = {d["node"]: (d["name"] or d["node"]) for d in store.devices()}
    for node in hear:
        result = answers.get(node)
        store.record(node, command, topic, result, note="broadcast" if broadcast else "")
        lines.append(f"{names.get(node, node[:8])[:12]:12} {outcome_line(result)}")
    if broadcast:
        store.record(None, command, topic, None,
                     note=f"{len(answers)} of {len(hear)} answered")
    return topic, payload, answers, lines


def fill(steps, arg):
    """The set's commands with `{arg}` filled in; refuses a set that needs one and has none."""
    if any("{arg}" in s for s in steps) and not (arg or "").strip():
        raise envelope.EnvelopeError("this set needs a value for {arg}")
    return [s.replace("{arg}", (arg or "").strip()) for s in steps]


def run_set(store, steps, device=None, region=None, dry_run=False, wait_secs=None, on_step=None):
    """Send `steps` in order to one device (or every node in `region`), stopping at the first
    refusal or silence. Returns `(lines, stopped_at)`: what to show, and the index of the
    step that stopped it, or None when every step was answered ok."""
    lines, stopped = [], None
    for i, command in enumerate(steps):
        if on_step:
            on_step(i, command)
        _t, _p, answers, step_lines = send(store, command, device=device, region=region,
                                           dry_run=dry_run, wait_secs=wait_secs)
        lines.append(f"-- {i + 1}/{len(steps)}: {command}")
        lines += step_lines
        if dry_run:
            continue
        expected = 1 if device else len([d for d in store.devices()
                                         if (d.get("region") or "").upper() == region.upper()])
        if len(answers) < expected or any(not r.ok for r in answers.values()):
            stopped = i
            lines.append(f"stopped at step {i + 1}: not every node answered ok; "
                         f"{len(steps) - i - 1} step(s) not sent")
            break
    return lines, stopped


def regions_asked(text, store):
    """`JKG`, `JKG, GOT` or `*` (every region the directory spans) as a list of IATA codes."""
    text = (text or "").strip()
    if text == "*":
        return store.regions()
    seen = []
    for part in text.replace(";", ",").replace(" ", ",").split(","):
        code = part.strip().upper()
        if code and code not in seen:
            seen.append(code)
    return seen


def discover(store, region, wait_secs=None):
    """The nodes in `region` that answer a command signed with your key, marked if known.
    `region` may name several (`JKG, GOT`) or `*`; one signed read per region, one list."""
    regions = regions_asked(region, store)
    if not regions:
        raise envelope.EnvelopeError("discovery needs a region: the IATA code, e.g. JKG; "
                                     "several with commas, or * for every region you have")
    if len(regions) > 1:
        found = []
        for one in regions:
            found += discover(store, one, wait_secs)
        return found
    region = regions[0]
    config = store.config()
    known = {d["node"]: d["name"] for d in store.devices()}
    found = transport.discover(config, store.seed(), region,
                               wait_secs=wait_secs or config.get("wait_secs", 20))
    for entry in found:
        entry["known_as"] = known.get(entry["node"])
        entry["outcome"] = outcome_line(envelope.Result(entry["node"], 0, entry["code"],
                                                        transport.DISCOVERY_COMMAND, entry["reply"]))
        entry["name"] = ""
    if found:
        # A second read for the names, which only firmware that admits `get name` answers;
        # the rest stay unnamed and are named by hand.
        try:
            _p, names = transport.send(dict(config, region=region.upper()), store.seed(),
                                       envelope.command_topic(region), "get name",
                                       nodes_to_hear=[e["node"] for e in found],
                                       wait_secs=min(10, wait_secs or config.get("wait_secs", 20)))
        except transport.BrokerError:
            names = {}
        for entry in found:
            r = names.get(entry["node"])
            if r is not None and r.ok and r.reply:
                entry["name"] = r.reply.lstrip("> ").strip()[:40]
    return found


def adopt(store, entry, name=""):
    """Remember a discovered node; unnamed until you name it, since the control plane
    carries no name (a name-plus-number is used if the name is taken)."""
    name = (name or "").strip()
    base, n = name, 2
    while name and any(d["name"].lower() == name.lower() for d in store.devices()):
        name = f"{base}-{n}"
        n += 1
    return store.add_device(name, entry["node"], entry["region"], "found by discovery")
