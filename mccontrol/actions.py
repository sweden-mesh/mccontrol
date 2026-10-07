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


def send(store, command, device=None, broadcast=False, dry_run=False, wait_secs=None):
    """Sign and send `command` to one device or to every node in the region.

    Returns `(topic, payload, {node: Result}, lines)`, where `lines` is what to show.
    Raises `envelope.EnvelopeError`, `transport.BrokerError` or `store.StoreError`.
    """
    config = store.config()
    region = config.get("region")
    if not region:
        raise envelope.EnvelopeError("no region set: the IATA code your observers use, e.g. JKG")
    if broadcast and envelope.is_transmit(command):
        raise envelope.EnvelopeError("a command that transmits may not be sent to every node; "
                                     "the firmware refuses it, and so does this")
    topic = envelope.command_topic(region, None if broadcast else device["node"])
    hear = [d["node"] for d in store.devices()] if broadcast else [device["node"]]
    if dry_run:
        payload = envelope.build(store.seed(), topic, 0, command)
        return topic, payload, {}, [f"would publish to {topic}", payload, "not sent (dry run)"]
    payload, answers = transport.send(config, store.seed(), topic, command, nodes_to_hear=hear,
                                      wait_secs=wait_secs or config.get("wait_secs", 20))
    lines = []
    names = {d["node"]: d["name"] for d in store.devices()}
    for node in hear:
        result = answers.get(node)
        store.record(node, command, topic, result, note="broadcast" if broadcast else "")
        lines.append(f"{names.get(node, node[:8]):18} {outcome_line(result)}")
    if broadcast:
        store.record(None, command, topic, None,
                     note=f"{len(answers)} of {len(hear)} answered")
    return topic, payload, answers, lines


def discover(store, seconds=5.0):
    """Nodes publishing a status in the configured region, marked with whether you have them."""
    config = store.config()
    if not config.get("region"):
        raise envelope.EnvelopeError("no region set: the IATA code your observers use, e.g. JKG")
    known = {d["node"]: d["name"] for d in store.devices()}
    found = transport.discover(config, seconds=seconds)
    for entry in found:
        entry["known_as"] = known.get(entry["node"])
    return found


def adopt(store, entry):
    """Remember a discovered node under its own name, or its name plus a suffix if taken."""
    name = entry["name"] or entry["node"][:8]
    base, n = name, 2
    while any(d["name"].lower() == name.lower() for d in store.devices()):
        name = f"{base}-{n}"
        n += 1
    notes = " ".join(x for x in (entry.get("model"), entry.get("firmware")) if x)
    return store.add_device(name, entry["node"], notes)
