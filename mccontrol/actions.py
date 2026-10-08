"""What the screens and the command line both do, so they cannot disagree."""

import datetime

from . import beacon, commands, denylist, envelope, firmware, nodestats, store as _store, transport
from .store import _now as _store_now

# Commands that take something away from a repeater. Each is sent only after the person has
# been told what it does; the node itself does not second-guess a signed command.
CAUTIONS = (
    ("region denyf *", "stops this repeater forwarding every message that carries no scope. "
                       "It may be the only link between two places that rely on that."),
    ("region denyf", "stops this repeater forwarding that scope."),
    ("region remove", "deletes that region from the repeater. Messages scoped to it are dropped."),
    ("set autoreply off", "stops this repeater answering test requests."),
    # `set repeat off`, `erase` and `set mqtt.cmd*` used to be here; the firmware's deny list of
    # 2026-10-08 refuses them outright (`denylist.py`), so there is nothing to ask.
    # Admitted since that list; each can take the node out of reach for a while or move
    # where it listens, so the person is told first.
    ("reboot", "restarts the node. It is off the air and off the control plane until it is back."),
    ("clkreboot", "restarts the node. It is off the air and off the control plane until it is back."),
    ("set mqtt.iata", "moves the node to another region's topics: this tool must change its region to match."),
    ("set mqtt.presets", "changes how many MQTT slots run; the control plane's slot may stop."),
    ("set mqtt1*", "changes slot 1's broker or credentials; if the control plane rides this slot, it may go silent."),
    ("set mqtt2*", "changes slot 2's broker or credentials; if the control plane rides this slot, it may go silent."),
    ("set mqtt3*", "changes slot 3's broker or credentials; if the control plane rides this slot, it may go silent."),
    ("set mqtt4*", "changes slot 4's broker or credentials; if the control plane rides this slot, it may go silent."),
    ("set mqtt5*", "changes slot 5's broker or credentials; if the control plane rides this slot, it may go silent."),
    ("set mqtt6*", "changes slot 6's broker or credentials; if the control plane rides this slot, it may go silent."),
    ("set name", "renames the node as the whole mesh sees it in every advert."),
)


def caution(command):
    """A sentence saying what a command takes away, or None for a harmless one."""
    text = " ".join(command.lower().split())
    for prefix, why in CAUTIONS:
        if prefix.endswith("*") and not prefix.endswith(" *"):   # `set mqtt1*`: any setting of the slot
            if text.startswith(prefix[:-1]):
                return why
        elif text == prefix or text.startswith(prefix + " "):   # `region denyf *` is a literal star
            return why
    return None


def outcome_line(result, version=None):
    """One line describing a result, as the screens and the log show it. A not-allowed
    from a node whose firmware the table says admits the command is a finding -- the
    table or the version is wrong -- and is said as such rather than hidden."""
    if result is None:
        return "no answer"
    if result.verified is False:
        return f"BAD SIGNATURE: the line on this node's result topic was not signed by its key -- forged or altered; reply ignored"
    tag = " [signed]" if result.verified else ""        # an r1 (older firmware) carries no mark
    if result.ok:
        return (result.reply or "OK") + tag
    line = f"refused: {result.code_name}" + tag
    if result.code == 14 and version and firmware.admits(version, result.command):
        line += f" [the table says {firmware.entry_for(version)[0]} admits this: the table or the version is wrong]"
    return line + (f" -- {result.help}" if result.help else "")


def send(store, command, device=None, region=None, dry_run=False, wait_secs=None, progress=None, script=None):
    """Sign and send `command` to one device, or with `region` to every node in that region.
    `script`, here and below, stands in for the broker in tests (`transport._fake`).

    Returns `(topic, payload, {node: Result}, lines)`, where `lines` is what to show.
    Raises `envelope.EnvelopeError`, `transport.BrokerError` or `store.StoreError`.
    """
    config = store.config()
    why = denylist.refusal(command)
    if why:
        raise envelope.EnvelopeError(f"the node would refuse {command!r}: {why}")
    broadcast = device is None
    if broadcast and not region:
        raise envelope.EnvelopeError("a broadcast needs a region")
    if broadcast and envelope.is_transmit(command):
        raise envelope.EnvelopeError("a command that transmits may not be sent to every node; "
                                     "the firmware refuses it, and so does this")
    region = (region or device.get("region") or "").upper()
    if not region:
        raise envelope.EnvelopeError(f"{device['name']} has no region; change the device and set it")
    topic = envelope.command_topic(region, None if broadcast else device["node"],
                                   None if broadcast else device.get("key"))
    hear = ([d["node"] for d in store.devices() if (d.get("region") or "").upper() == region]
            if broadcast else [device["node"]])
    keys = {d["node"]: d.get("key") for d in store.devices() if d.get("key")}
    if dry_run:
        payload = envelope.build(store.seed(), topic, 0, command)
        return topic, payload, {}, [f"would publish to {topic}", payload, "not sent (dry run)"]
    payload, answers = transport.send(dict(config, region=region), store.seed(), topic, command,
                                      nodes_to_hear=hear, keys=keys,
                                      wait_secs=wait_secs or config.get("wait_secs", 20),
                                      progress=progress, script=script)
    lines = []
    names = {d["node"]: (d["name"] or d["node"]) for d in store.devices()}
    versions = {d["node"]: d.get("ver") for d in store.devices()}
    for node in hear:
        result = answers.get(node)
        store.record(node, command, topic, result, note="broadcast" if broadcast else "")
        if result is not None and result.ok and commands.is_read(command) and node in names:
            store.remember_read(node, command, result.reply or "OK")
        lines.append(f"{names.get(node, node[:8])[:12]:12} {outcome_line(result, versions.get(node))}")
    if broadcast:
        store.record(None, command, topic, None,
                     note=f"{len(answers)} of {len(hear)} answered")
    return topic, payload, answers, lines


def fill(steps, arg):
    """The set's commands with `{arg}` filled in; refuses a set that needs one and has none."""
    if any("{arg}" in s for s in steps) and not (arg or "").strip():
        raise envelope.EnvelopeError("this set needs a value for {arg}")
    return [s.replace("{arg}", (arg or "").strip()) for s in steps]


def run_set(store, steps, device=None, region=None, dry_run=False, wait_secs=None, on_step=None, progress=None,
            script=None):
    """Send `steps` in order to one device (or every node in `region`), stopping at the first
    refusal or silence. Returns `(lines, stopped_at)`: what to show, and the index of the
    step that stopped it, or None when every step was answered ok."""
    lines, stopped = [], None
    for i, command in enumerate(steps):
        if on_step:
            on_step(i, command)
        _t, _p, answers, step_lines = send(store, command, device=device, region=region,
                                           dry_run=dry_run, wait_secs=wait_secs, progress=progress, script=script)
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


def live_statistics(store, device, progress=None, script=None):
    """One signed `get stats` to the device: `(lines, note)`. `lines` is the sample as
    `beacon.stat_lines` lays it out, headed "live, just now"; empty when the node did not
    answer or refused, with `note` saying which (older firmware answers not-allowed)."""
    try:
        _t, _p, answers, _l = send(store, "get stats", device=device, progress=progress, script=script)
    except (envelope.EnvelopeError, transport.BrokerError, _store.StoreError) as exc:
        return [], f"live: {exc}"
    r = answers.get(device["node"])
    if r is None:
        return [], "live: no answer"
    if not r.ok:
        return [], f"live: refused {r.code_name}" + (" (older firmware: the read needs the 2026-10-08 build)" if r.code == 14 else "")
    block = nodestats.parse(r.reply)
    if not block:
        return [], f"live: {r.reply[:40]}"
    sample = {"stats": block, "sampled_at": None, "age_secs": 0, "firmware": "", "model": "", "name": ""}
    lines = beacon.stat_lines(sample)
    lines[0] = "live, just now (get stats)"
    return lines, ""


def statistics(store, device, range_="24h", interval="6h", live=False, progress=None, script=None, details=False):
    """The lines to show for a device's statistics: the live sample first when asked for
    and answered, then Beacon's sample with its age and the telemetry buckets. Raises
    `beacon.BeaconError` with the reason when Beacon has none and there is no live one.
    With `details`, `(lines, sample, points)`: what Beacon gave, each None where it gave
    nothing, so a dashboard has the numbers without asking Beacon twice."""
    head = []
    if live:
        live_lines, note = live_statistics(store, device, progress=progress, script=script)
        head = live_lines + [""] if live_lines else [note, ""]
    sample = points = None
    try:
        sample = beacon.stats(store, device)
    except beacon.BeaconError as exc:
        if head and head[0].startswith("live, just now"):
            lines = head + [f"Beacon: {exc}"]
            return (lines, None, None) if details else lines
        raise
    lines = beacon.stat_lines(sample)
    lines[0] = "Beacon: " + lines[0]
    device = store.device(device["node"]) or device       # with the observer id the read above found
    try:
        points = beacon.telemetry(store, device, range_, interval)
    except beacon.BeaconError as exc:
        lines = head + lines + ["", f"no telemetry: {exc}"]
    else:
        lines = head + lines + ["", f"last {range_} at {interval}:"] + beacon.telemetry_lines(points)
    return (lines, sample, points) if details else lines


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


def refresh_names(store, devices, wait_secs=None, progress=None, script=None):
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
                _t, _p, answers, _l = send(store, command, device=d, wait_secs=wait_secs, progress=progress,
                                           script=script)
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


def register(store):
    """Register the signing key with the control broker, once: `(ok, detail)`; remembered
    on success so the Keys dialog and the connection test can say so."""
    config = store.config()
    ok, detail = transport.register(config, store.seed(), wait_secs=min(10, config.get("wait_secs", 20)))
    if ok:
        store.remember_registration(config["broker"])
    return ok, detail


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


def discover(store, region, wait_secs=None, progress=None, script=None):
    """The nodes in `region` that answer a command signed with your key, marked if known.
    `region` may name several (`JKG, GOT`) or `*`; one signed read per region, one list."""
    regions = regions_asked(region, store)
    if not regions:
        raise envelope.EnvelopeError("discovery needs a region: the IATA code, e.g. JKG; "
                                     "several with commas, or * for every region you have")
    if len(regions) > 1:
        found = []
        for one in regions:
            found += discover(store, one, wait_secs, progress, script)
        return found
    region = regions[0]
    config = store.config()
    known = {d["node"]: d["name"] for d in store.devices()}
    found = transport.discover(config, store.seed(), region,
                               wait_secs=wait_secs or config.get("wait_secs", 20), progress=progress, script=script)
    for entry in found:
        entry["known_as"] = known.get(entry["node"])
        # a node answering on a whole-key topic (ar-11) tells us its key: keep it on a
        # device we already have, so its next command goes to the topic it listens on
        if entry.get("key") and entry["node"] in known:
            d = store.device(entry["node"])
            if d and d.get("key") != entry["key"]:
                store.update_device(entry["node"], key=entry["key"])
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
                                       keys={e["node"]: e["key"] for e in found if e.get("key")},
                                       wait_secs=min(10, wait_secs or config.get("wait_secs", 20)), script=script)
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
    return store.add_device(name, entry["node"], entry["region"], "found by discovery", key=entry.get("key"))
