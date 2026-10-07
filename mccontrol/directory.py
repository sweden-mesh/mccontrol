"""The device directory as rows: what the left panel shows, computed from the store."""

import datetime

SORTS = ("name", "region", "last sent")


def relative(when, now=None):
    """`2 h`, `3 d`, `now`, or `-` for never: the age of an ISO timestamp, short enough for a column."""
    if not when:
        return "-"
    try:
        then = datetime.datetime.fromisoformat(when)
    except ValueError:
        return "?"
    now = now or datetime.datetime.now()
    secs = max(0, int((now - then).total_seconds()))
    if secs < 60:
        return "now"
    if secs < 3600:
        return f"{secs // 60} min"
    if secs < 86400:
        return f"{secs // 3600} h"
    return f"{secs // 86400} d"


def sorted_devices(devices, by="name"):
    if by == "region":
        return sorted(devices, key=lambda d: ((d.get("region") or "").upper(), (d.get("name") or "~" + d["node"]).lower()))
    if by == "last sent":
        return sorted(devices, key=lambda d: d.get("last_sent") or "", reverse=True)
    if by == "version":
        return sorted(devices, key=lambda d: (d.get("ver") or "~", (d.get("name") or "~" + d["node"]).lower()))
    return sorted(devices, key=lambda d: (d.get("name") or "~" + d["node"]).lower())


def row(device, width, now=None):
    """One list row for a device, laid out to `width` columns.

    name (18)  node (8)  reg (4)  last sent (7)  batt (5)  seen (5)  ver (12)  result (rest)

    `batt` and `seen` are what Beacon last said (`device["fleet"]`, filled by a refresh):
    battery in volts and the age of the node's last status. Blank until asked for.
    """
    name = (device.get("name") or "(unnamed)")[:18].ljust(18)
    node = device["node"][:8].ljust(8)
    region = (device.get("region") or "?")[:4].ljust(4)
    sent = relative(device.get("last_sent"), now)[:7].ljust(7)
    fleet = device.get("fleet") or {}
    batt = (f"{fleet['battery_mv'] / 1000:.2f}" if fleet.get("battery_mv") else "")[:5].ljust(5)
    seen = (relative(fleet.get("seen"), now) if fleet else "")[:5].ljust(5)
    ver = version_short(device.get("ver"))[:12].ljust(12)
    result = (device.get("last_result") or "")
    line = f"{name} {node} {region} {sent} {batt} {seen} {ver} {result}"
    return line[: width - 1].ljust(width - 1)


def header(width):
    return f"{'Name':18} {'Node':8} {'Reg':4} {'Sent':7} {'Batt':5} {'Seen':5} {'Ver':12} Result"[: width - 1]


def version_short(ver):
    """`esp32s3-v1.17.1-ar-10-observer-42f48ce9` as `1.17.1-ar-10`: the version and the
    auto-reply revision, which is what differs across a fleet."""
    if not ver:
        return ""
    import re
    m = re.search(r"v?(\d+\.\d+(?:\.\d+)*(?:-ar-\d+)?)", ver)
    return m.group(1) if m else ver[:12]


def tag_footer(tagged, total):
    if not total:
        return "no devices"
    if tagged:
        return f"{tagged} of {total} tagged"
    return f"{total} device{'s' if total != 1 else ''}"


def by_region(devices, region):
    """Indexes of the devices in `region`, for tagging a region at once."""
    region = (region or "").upper()
    return [i for i, d in enumerate(devices) if (d.get("region") or "").upper() == region]
