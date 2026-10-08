"""The control plane's deny list, mirrored from the firmware.

Must match `mqttCtrlCommandDenied` and the admitted verbs in `mqttCtrlCommandAllowed`
(`src/helpers/MqttControlLogic.h` in the Observer auto-reply firmware, 2026-10-08). The
firmware is the authority: a node refuses what it refuses whatever this says. This copy
exists so the Send dialog and the command line can say *why* before signing, instead of
spending a command slot to be told `not-allowed`.

A node on firmware older than 2026-10-08 admits far less (an allowlist of fourteen
entries); it answers `not-allowed` to the rest, and the tool reports that as the node did.
"""

# Families: the boundary rule (end of string, a space or a dot after the prefix).
DENIED_FAMILIES = (
    ("set prv.key", "identity: a changed key is a different node"),
    ("set public.key", "identity: a changed key is a different node"),
    ("set mqtt.cmd", "the control plane itself: it would take the node off it"),
    ("get mqtt.cmd.pubkey", "the command key"),
    ("set wifi.pwd", "a credential"), ("get wifi.pwd", "a credential"),
    ("set wifi.ssid", "which network the node is on"), ("get wifi.ssid", "which network the node is on"),
    ("set password", "a credential"), ("get password", "a credential"), ("password", "a credential"),
    ("set guest.password", "a credential"), ("get guest.password", "a credential"),
    ("set alert.psk", "a credential"), ("get alert.psk", "a credential"),
    ("set bridge.secret", "a credential"), ("get bridge.secret", "a credential"),
    ("set snmp.community", "a credential"), ("get snmp.community", "a credential"),
    ("set mqtt.email", "ownership"), ("get mqtt.email", "ownership"),
    ("set mqtt.owner", "ownership"), ("get mqtt.owner", "ownership"),
    ("set owner.info", "ownership"), ("get owner.info", "ownership"),
    ("set tls", "TLS material"), ("get tls", "TLS material"), ("tls.bundletest", "serial-only"),
    ("get prv.key", "the private key"),
    ("erase", "destructive, no way back"),
    ("start", "the SoftAP paths (webconfig, hand-uploaded OTA) need someone at the node"),
    ("ota update", "waits for a signed image and rollback in the firmware"),
    ("poweroff", "no way back from the road"), ("shutdown", "no way back from the road"),
    ("set bootloader.ver", "destructive"),
    ("set lat", "position is the owner's to publish, per node"), ("set lon", "position is the owner's to publish, per node"),
    ("get lat", "position is the owner's to publish, per node"), ("get lon", "position is the owner's to publish, per node"),
    ("set role", "the node's role"),
    ("set repeat", "the remote kill switch: how a leaked key would silence a region"),
    ("set freq", "the radio: a wrong value takes the node off the mesh"),
    ("set radio", "the radio: a wrong value takes the node off the mesh"),
    ("set tx", "the radio: a wrong value takes the node off the mesh"),
    ("set extra.sf", "the radio"), ("set dutycycle", "the radio"), ("tempradio", "the radio"),
    ("advert", "a transmission; trigger is the one sanctioned transmit"),
    ("alert", "a transmission"),
    ("set advert.interval", "airtime"), ("set flood.advert.interval", "airtime"),
    ("time", "steps the clock that judges expiry"),
    ("log", "serial-only"), ("stats-core", "serial-only"), ("stats-packets", "serial-only"),
    ("stats-radio", "serial-only"), ("stats-radio-diag", "serial-only"),
)

# The secrets: denied by bare prefix, no boundary, as the firmware does.
DENIED_PREFIXES = (
    "get prv", "set prv", "get password", "set password", "get guest.password",
    "get wifi.pwd", "set wifi.pwd", "get alert.psk", "set alert.psk",
    "get bridge.secret", "set bridge.secret", "get snmp.community", "set snmp.community",
    "get mqtt.cmd.pubkey", "set mqtt.cmd", "get tls", "set tls",
)

SLOT_CREDENTIAL_LEAVES = (".password", ".token", ".username")

# What a command must begin with; (text, exact).
VERBS = (
    ("set", False), ("get", False), ("region", False), ("trigger test", False),
    ("trigger private", False), ("neighbor.remove", False), ("sensor", False),
    ("gps", False), ("powersaving", False), ("clock", False),
    ("ver", True), ("reboot", True), ("clkreboot", True), ("neighbors", True),
    ("board", True), ("memory", True), ("clear stats", True), ("ota check", True),
)


def _boundary(text, prefix):
    if not text.lower().startswith(prefix.lower()):
        return False
    return len(text) == len(prefix) or text[len(prefix)] in " ."


def _slot_credential_read(text):
    low = text.lower()
    if not low.startswith("get mqtt") or len(low) < 10 or not low[8].isdigit():
        return False
    rest = low[9:]
    return any(rest.startswith(leaf) and (len(rest) == len(leaf) or rest[len(leaf)] in " .")
               for leaf in SLOT_CREDENTIAL_LEAVES)


def refusal(command):
    """Why the firmware will refuse `command`, or None when it travels.

    The reason is for the person; the node says only `not-allowed`."""
    text = " ".join((command or "").split())
    if not text:
        return "an empty command"
    if "|" in text:
        return "'|' is the envelope's field separator; region put one name at a time"
    low = text.lower()
    for prefix, why in DENIED_FAMILIES:
        if _boundary(text, prefix):
            return f"denied: {why}"
    for prefix in DENIED_PREFIXES:
        if low.startswith(prefix):
            return "denied: a secret"
    if _slot_credential_read(text):
        return "denied: a broker slot's credential"
    if low in ("set", "get"):
        return "names no setting"
    for verb, exact in VERBS:
        if (low == verb) if exact else _boundary(text, verb):
            return None
    return "not a command the control plane admits"
