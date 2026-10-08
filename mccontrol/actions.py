"""What the screens and the command line both do, so they cannot disagree."""

import datetime

from . import beacon, envelope, store as _store, transport
from .store import _now as _store_now

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


def send(store, command, device=None, region=None, dry_run=False, wait_secs=None, progress=None):
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
                                      wait_secs=wait_secs or config.get("wait_secs", 20),
                                      progress=progress)
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


def run_set(store, steps, device=None, region=None, dry_run=False, wait_secs=None, on_step=None, progress=None):
    """Send `steps` in order to one device (or every node in `region`), stopping at the first
    refusal or silence. Returns `(lines, stopped_at)`: what to show, and the index of the
    step that stopped it, or None when every step was answered ok."""
    lines, stopped = [], None
    for i, command in enumerate(steps):
        if on_step:
            on_step(i, command)
        _t, _p, answers, step_lines = send(store, command, device=device, region=region,
                                           dry_run=dry_run, wait_secs=wait_secs, progress=progress)
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


def statistics(store, device, range_="24h", interval="6h"):
    """The lines to show for a device's statistics: the sample with its age, then the
    telemetry buckets. Raises `beacon.BeaconError` with the reason when there are none."""
    sample = beacon.stats(store, device)
    lines = beacon.stat_lines(sample)
    try:
        points = beacon.telemetry(store, device, range_, interval)
    except beacon.BeaconError as exc:
        return lines + ["", f"no telemetry: {exc}"]
    return lines + ["", f"last {range_} at {interval}:"] + beacon.telemetry_lines(points)


def refresh_fleet(store, devices, now=None):
    """Battery and last-seen from Beacon for each device, stored as `fleet` on the device.
    Returns `(updated, failed)` counts; a device Beacon does not know keeps nothing stale:
    its `fleet` is cleared."""
    updated = failed = 0
    for d in devices:
        try:
            sample = beacon.stats(store, d, now=now)
        except beacon.BeaconError:
            store.update_device(d["node"], fleet=None)
            failed += 1
            continue
        seen = datetime.datetime.fromtimestamp(sample["sampled_at"]).isoformat(timespec="seconds")
        store.update_device(d["node"], fleet={"battery_mv": sample["stats"].get("battery_mv"),
                                               "seen": seen, "firmware": sample["firmware"],
                                               "fetched": _store_now()})
        updated += 1
    return updated, failed


def refresh_names(store, devices, wait_secs=None, progress=None):
    """`get name` and `ver` to each device, one signed read each: an unnamed device takes the
    fleet's name, every device stores `ver`. Returns `(named, versioned, lines)`. A node whose
    firmware admits neither answers not-allowed and keeps what it had."""
    named = versioned = 0
    lines = []
    for d in devices:
        label = d.get("name") or d["node"]
        changes = {}
        for command in ("get name", "ver"):
            try:
                _t, _p, answers, _l = send(store, command, device=d, wait_secs=wait_secs, progress=progress)
            except (envelope.EnvelopeError, transport.BrokerError, _store.StoreError) as exc:
                lines.append(f"{label[:12]:12} {command}: {exc}")
                continue
            r = answers.get(d["node"])
            if r is None or not r.ok or not r.reply:
                lines.append(f"{label[:12]:12} {command}: {outcome_line(r)}")
                continue
            value = r.reply.lstrip("> ").strip()
            if command == "ver":
                changes["ver"] = value[:40]
                versioned += 1
            elif not d.get("name") and value:
                changes["name"] = value[:40]
                named += 1
            lines.append(f"{label[:12]:12} {command}: {value[:40]}")
        if changes:
            store.update_device(d["node"], **changes)
    return named, versioned, lines


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


def discover(store, region, wait_secs=None, progress=None):
    """The nodes in `region` that answer a command signed with your key, marked if known.
    `region` may name several (`JKG, GOT`) or `*`; one signed read per region, one list."""
    regions = regions_asked(region, store)
    if not regions:
        raise envelope.EnvelopeError("discovery needs a region: the IATA code, e.g. JKG; "
                                     "several with commas, or * for every region you have")
    if len(regions) > 1:
        found = []
        for one in regions:
            found += discover(store, one, wait_secs, progress)
        return found
    region = regions[0]
    config = store.config()
    known = {d["node"]: d["name"] for d in store.devices()}
    found = transport.discover(config, store.seed(), region,
                               wait_secs=wait_secs or config.get("wait_secs", 20), progress=progress)
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
